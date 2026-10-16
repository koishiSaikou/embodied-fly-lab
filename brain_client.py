"""Deadline-bound JSON-lines transport for the habitat brain subprocess.

Only the transport changes here; commands and successful replies retain their
original values. Pipe I/O runs in owned threads because Windows pipes cannot be
polled with select(). No request exists while the body is paused.
"""
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent


def _reject_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def response_timeout(value):
    value = float(value)
    if not math.isfinite(value) or not 0 < value <= 3600:
        raise ValueError("脑响应期限须为大于 0、至多 3600 的有限秒数")
    return value


class BrainResponseError(RuntimeError):
    def __init__(self, message, stage, log_path, last_brain_time_ms, timeout_s=None):
        super().__init__(f"{message}；最近成功脑时间 {last_brain_time_ms:g} ms；日志：{log_path}")
        self.details = dict(stage=stage, log_path=str(log_path),
                            last_brain_time_ms=last_brain_time_ms, timeout_s=timeout_s)


class BrainClient:
    def __init__(self, directory, seed, *, startup_timeout=300, step_timeout=30,
                 progress=None, worker_command=None):
        self.startup_timeout = response_timeout(startup_timeout)
        self.step_timeout = response_timeout(step_timeout)
        self.progress = progress
        self.last_brain_time_ms = 0.0
        self.metadata = None
        self.failed = False
        self.closed = False
        self.process = None
        self._threads = []
        self._replies = queue.Queue(maxsize=2)
        self._writes = queue.Queue()
        self._stopping = threading.Event()
        self.log_path = Path(directory) / "brain_worker.log"
        self.log = self.log_path.open("w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                worker_command or [str(ROOT / ".brain-venv/Scripts/python.exe"),
                                   str(ROOT / "brain_worker.py"), "--habitat", "--seed", str(seed)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                text=True, encoding="utf-8", cwd=ROOT,
                env=dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1"),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            for target in (self._read, self._write):
                thread = threading.Thread(target=target, daemon=True, name="habitat-brain-pipe")
                thread.start()
                self._threads.append(thread)
            ready = self._response("brain_startup", self.startup_timeout)
            if not isinstance(ready.get("ready"), dict):
                raise self._error("脑启动返回缺少 ready 元数据", "brain_startup")
            self.metadata = ready["ready"]
        except BaseException as error:
            self.failed = True
            try:
                self.close()
            except Exception as cleanup_error:
                error.details = {**getattr(error,"details",{}),"cleanup_failed":str(cleanup_error)}
                print(f"Brain construction cleanup failed: {cleanup_error}", flush=True)
            raise

    def _emit(self, value):
        while not self._stopping.is_set():
            try:
                self._replies.put(value, timeout=.1)
                return
            except queue.Full:
                pass

    def _read(self):
        try:
            while not self._stopping.is_set():
                line = self.process.stdout.readline()
                self._emit(line)
                if not line:
                    return
        except (OSError, ValueError, UnicodeError) as error:
            self._emit(error)

    def _write(self):
        try:
            while True:
                payload = self._writes.get()
                if payload is None:
                    return
                self.process.stdin.write(payload)
                self.process.stdin.flush()
        except (OSError, ValueError, UnicodeError) as error:
            self._emit(error)

    def _error(self, message, stage, timeout_s=None):
        self.failed = True
        return BrainResponseError(message, stage, self.log_path, self.last_brain_time_ms, timeout_s)

    def _response(self, stage, timeout_s):
        started = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - started
                if self.progress:
                    self.progress(dict(stage=stage, elapsed_seconds=elapsed, timeout_seconds=timeout_s,
                                       last_brain_time_ms=self.last_brain_time_ms))
                remaining = timeout_s - elapsed
                if remaining <= 0:
                    label = "首次加载" if stage == "brain_startup" else "单步计算"
                    raise self._error(f"脑{label}超时（期限 {timeout_s:g} 秒）", stage, timeout_s)
                try:
                    line = self._replies.get(timeout=min(.5, remaining))
                    break
                except queue.Empty:
                    pass
            if isinstance(line, Exception):
                raise self._error(f"脑通信失败：{line}", stage)
            if not line:
                raise self._error(f"脑进程退出或输出关闭（退出码 {self.process.poll()}）", stage)
            try:
                result = json.loads(line, parse_constant=_reject_constant)
                if not isinstance(result, dict):
                    raise ValueError("expected JSON object")
                return result
            except (ValueError, TypeError) as error:
                raise self._error(f"脑返回了无效 JSON：{error}", stage) from error
        except BaseException:
            self.failed = True
            raise
        finally:
            if self.progress:
                self.progress(None)

    def advance(self, command, milliseconds, capture, smoothing_ms=100.0, transmitter_gains=None):
        if self.closed or self.failed:
            raise self._error("脑通信已停止，须手动开始新一轮", "brain_step")
        self._writes.put(json.dumps({"rate_hz":command["p9_rate_hz"], "duration_ms":milliseconds,
                                    "sugar_rate_hz":command["sugar_rate_hz"], "capture_activity":capture,
                                    "drive_smoothing_ms":smoothing_ms,
                                    "transmitter_gains":transmitter_gains}) + "\n")
        result = self._response("brain_step", self.step_timeout)
        try:
            stamp = result["brain_time_ms"]
            if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= self.last_brain_time_ms:
                raise ValueError("brain_time_ms must advance")
            for key in ("drive", "p9_hz"):
                if len(result[key]) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in result[key]):
                    raise ValueError(f"invalid {key}")
            for key in ("chunk_ms", "chunk_spikes", "total_spikes", "active_neurons", "sugar_spikes"):
                if type(result[key]) not in (int, float) or not math.isfinite(result[key]) or result[key] < 0:
                    raise ValueError(f"invalid {key}")
            if capture and not isinstance(result["activity"].get("events"), list):
                raise ValueError("invalid activity")
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise self._error(f"脑响应字段无效：{error}", "brain_step") from error
        self.last_brain_time_ms = stamp
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.process and self.process.poll() is None:
                if not self.failed:
                    self._writes.put('{"quit":true}\n')
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=3)
        finally:
            self._stopping.set()
            self._writes.put(None)
            for thread in self._threads:
                thread.join(timeout=3)
            # Kill/wait above unblocks both pipe threads before TextIO.close.
            try:
                if any(thread.is_alive() for thread in self._threads):
                    raise RuntimeError("脑通信线程未退出；请关闭服务后检查进程，禁止原地重开")
                close_errors=[]
                for stream in (self.process.stdin, self.process.stdout) if self.process else ():
                    if stream:
                        try:
                            stream.close()
                        except OSError as error:
                            # Windows may flush buffered stdin into an exited
                            # worker and report EINVAL/EPIPE while still closing
                            # the handle successfully. Both ownership checks
                            # must hold before treating that as already closed.
                            if self.process.poll() is None or not stream.closed:
                                close_errors.append(error)
                if close_errors:raise close_errors[0]
            finally:
                self.log.close()
