"""Portable experiment records, bounded previews and validated ZIP exchange.

This module deliberately imports no simulation, model, pickle or GPU code.
An experiment archive is data; importing it never executes its contents.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
import uuid
import zipfile


SCHEMA_VERSION = 1
BUNDLE_KIND = "embodied-fly-experiment"
MAX_BUNDLE_BYTES = 16 * 1024 ** 3
MAX_FILES = 2000
MAX_MANIFEST_BYTES = 2 * 1024 ** 2
MAX_DOCUMENT_BYTES = 32 * 1024 ** 2
MAX_PREVIEW_BYTES = 2 * 1024 ** 2
MAX_PREVIEW_LINE_BYTES = 512 * 1024
MAX_PREVIEW_SCAN_BYTES = 64 * 1024 ** 2
MAX_PREVIEW_ROWS = 2000
MAX_COMPRESSION_RATIO = 2000
CHUNK_BYTES = 1024 * 1024

# Flat names are intentional: they also reject absolute paths, traversal,
# Windows ADS names, device names and arbitrary files uploaded by a sender.
ALLOWED_FILES = frozenset({
    "manifest.json", "config.json", "final_config.json", "profile.json",
    "reproduction.json", "report.json", "error.json", "monitor_snapshot.json",
    "trajectory.csv", "neural.jsonl", "behavior.jsonl", "senses.jsonl",
    "retina.jsonl", "events.jsonl", "scene.json.gz", "arena.png", "retina.png",
    "trajectory.png", "occupancy.png", "arena.mp4", "learning_memory.json", "mb_learning.jsonl", "mb_observer.json",
    "数据使用与署名.md", "引用文献.md", "许可说明.md", "GPL-3.0许可证.txt",
    "CC-BY-NC-4.0许可证.txt", "CC-BY-4.0许可证.txt",
})
NOTICE_SOURCES = {
    "数据使用与署名.md": "数据使用与署名.md",
    "引用文献.md": "引用文献.md", "许可说明.md": "许可说明.md",
    "GPL-3.0许可证.txt": "许可证.txt",
    "CC-BY-NC-4.0许可证.txt": "licenses/CC-BY-NC-4.0许可证.txt",
    "CC-BY-4.0许可证.txt": "licenses/CC-BY-4.0许可证.txt",
}
PREVIEW_FILES = {
    "trajectory": "trajectory.csv", "neural": "neural.jsonl",
    "behavior": "behavior.jsonl", "senses": "senses.jsonl",
    "retina": "retina.jsonl", "events": "events.jsonl",
    "mb_learning": "mb_learning.jsonl",
}
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _regular_file(path: Path) -> bool:
    return not _linked(path) and path.is_file()


def _sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            result.update(chunk)
    return result.hexdigest()


def _load_json(path: Path, maximum: int = MAX_DOCUMENT_BYTES) -> dict:
    if not path.exists():
        return {}
    if not _regular_file(path):
        raise ValueError(f"记录文件不是普通文件：{path.name}")
    if path.stat().st_size > maximum:
        raise ValueError(f"记录文件过大，无法直接查看：{path.name}")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(f"记录文件过大：{path.name}")
    value = _decode_json(raw)
    if not isinstance(value, dict):
        raise ValueError(f"记录必须是 JSON 对象：{path.name}")
    return value


def _decode_json(raw: bytes):
    def reject_constant(value):
        raise ValueError(f"JSON 中包含非有限数值：{value}")
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("JSON 浮点数超出有限范围")
        return result
    def bounded_int(value):
        if len(value.lstrip("-")) > 100:
            raise ValueError("JSON 整数位数超过 100 位限制")
        return int(value)
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant,
                          parse_float=finite_float, parse_int=bounded_int)
    except RecursionError as exc:
        raise ValueError("JSON 嵌套层级过深") from exc


def _write_json(path: Path, value: dict) -> None:
    # Only replace our own unpredictable temporary file, never a shared .partial.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_json(path: Path, maximum: int = MAX_DOCUMENT_BYTES) -> tuple[dict, str | None]:
    try:
        return _load_json(path, maximum), None
    except (ValueError, OSError, UnicodeError) as exc:
        return {}, f"{path.name}: {exc}"


def _finite_number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    return None


def _record_files(directory: Path) -> list[Path]:
    result = []
    for name in sorted(ALLOWED_FILES - {"manifest.json"}):
        path = directory / name
        if path.exists() or path.is_symlink():
            if not _regular_file(path):
                raise ValueError(f"实验包含链接或非普通记录文件：{name}")
            result.append(path)
    return result


def _build_manifest(directory, virtual_files=None) -> dict:
    directory = Path(directory)
    if _linked(directory) or not directory.is_dir():
        raise ValueError("实验目录不存在或是链接")
    config, config_error = _safe_json(directory / "config.json")
    report, report_error = _safe_json(directory / "report.json")
    if not report:
        report, fallback_error = _safe_json(directory / "error.json")
        report_error = report_error or fallback_error
    old_manifest, _ = _safe_json(directory / "manifest.json", MAX_MANIFEST_BYTES)
    project = report.get("project") or config.get("project") or {}
    entries = []
    total_bytes = 0
    for path in _record_files(directory):
        before = path.stat()
        total_bytes += before.st_size
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("实验记录超过 16 GiB 导出上限")
        digest = _sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"保存时文件仍在变化：{path.name}")
        entries.append({"name": path.name, "bytes": before.st_size, "sha256": digest})
    for name, raw in (virtual_files or {}).items():
        if name not in NOTICE_SOURCES or any(item["name"] == name for item in entries):
            raise ValueError("虚拟署名附件名称重复或未获允许")
        total_bytes += len(raw)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("实验记录超过 16 GiB 导出上限")
        entries.append({"name": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    dependencies = {}
    for source in (config, report, report.get("brain_metadata") or {}):
        if not isinstance(source, dict):
            continue
        for key in ("dependencies", "versions", "runtime", "runtime_versions"):
            if source.get(key) is not None:
                dependencies[key] = source[key]
    return {
        "schema_version": SCHEMA_VERSION, "kind": BUNDLE_KIND,
        "run_id": directory.name,
        "created_at": old_manifest.get("created_at") or datetime.fromtimestamp(
            directory.stat().st_ctime, timezone.utc).isoformat(),
        "saved_at": _now(), "status": report.get("status", "incomplete"),
        "time_s": _finite_number(report.get("body_model_seconds")),
        "project": project, "dependencies": dependencies,
        "files": entries, "total_bytes": total_bytes,
        "reproducibility": {
            "scope": "saved data and configuration; restart from initial state, not a complete neural/body checkpoint",
            "deterministic_replay_guaranteed": False,
            "recorded_project_identity": bool(project),
            "recorded_dependencies": bool(dependencies),
            "required_external_resources": "matching project version, body/brain environments and separately licensed model/data files",
            "limitations": "GPU kernels, software versions and interactive timing can differ; neural.jsonl contains chunk summaries plus requested worker activity; events can be truncated and are not a complete spike archive",
        },
        "notices": {
            "code_license": "GPL-3.0-or-later; upstream exceptions retained",
            "data": "Derived FlyWire data retain their source terms, including CC BY-NC 4.0 where applicable; see 数据使用与署名.md",
            "privacy": "brain_worker.log, environments and source data tables are excluded; recorded config/report may contain original argument paths",
        },
        "metadata_warnings": [item for item in (config_error, report_error) if item],
    }


def finalize_run(directory) -> Path:
    """Write a checksum manifest after all new-run record writers are closed.

    A .recording marker is allowed so a caller can finalize before removing it.
    This function is only for finalizing a new recording; export uses an in-memory
    manifest and never changes historical recording directories.
    """
    directory = Path(directory)
    manifest = _build_manifest(directory)
    path = directory / "manifest.json"
    _write_json(path, manifest)
    return path


class ExperimentStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.runs = self.root / "output" / "habitat"
        self.exports = self.root / "output" / "experiment_exports"

    def _check_base(self, base: Path) -> None:
        # An existing output parent may not redirect file operations elsewhere.
        for path in (self.root / "output", base):
            if _linked(path):
                raise ValueError("实验存储目录不能是符号链接或联接点")
        if base.resolve().parent != (self.root / "output").resolve():
            raise ValueError("实验存储目录越界")

    def _directory(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or run_id in (".", ".."):
            raise ValueError("无效实验编号")
        self._check_base(self.runs)
        directory = self.runs / run_id
        if _linked(directory) or directory.resolve().parent != self.runs.resolve():
            raise ValueError("实验目录越界或是链接")
        if not directory.is_dir():
            raise FileNotFoundError("实验不存在")
        return directory

    def _summary(self, directory: Path) -> dict:
        report, report_error = _safe_json(directory / "report.json")
        if not report:
            report, fallback_error = _safe_json(directory / "error.json")
            report_error = report_error or fallback_error
        manifest, manifest_error = _safe_json(directory / "manifest.json", MAX_MANIFEST_BYTES)
        config, config_error = _safe_json(directory / "config.json")
        imported, imported_error = _safe_json(directory / ".imported.json", MAX_MANIFEST_BYTES)
        recording = (directory / ".recording").exists()
        status = "recording" if recording else report.get("status", manifest.get("status", "incomplete"))
        return {
            "id": directory.name, "status": status,
            "time_s": _finite_number(report.get("body_model_seconds", manifest.get("time_s"))),
            "created_at": manifest.get("created_at") or datetime.fromtimestamp(
                directory.stat().st_ctime, timezone.utc).isoformat(),
            "imported": bool(imported), "has_profile": any(
                _regular_file(directory / name) for name in ("reproduction.json", "profile.json")),
            "has_report": bool(report), "has_video": _regular_file(directory / "arena.mp4"),
            "has_scene": _regular_file(directory / "scene.json.gz"),
            "has_config": bool(config), "active": recording,
            "project": report.get("project") or config.get("project") or manifest.get("project") or {},
            "warnings": [error for error in (report_error, manifest_error, config_error, imported_error) if error],
        }

    def list_runs(self) -> list[dict]:
        self._check_base(self.runs)
        if not self.runs.exists():
            return []
        rows = []
        for directory in self.runs.iterdir():
            if not _RUN_ID.fullmatch(directory.name) or _linked(directory) or not directory.is_dir():
                continue
            try:
                rows.append(self._summary(directory))
            except (ValueError, OSError) as exc:
                rows.append({"id": directory.name, "status": "unreadable", "time_s": None,
                             "created_at": None, "imported": False, "has_profile": False,
                             "warnings": [str(exc)]})
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)

    def describe(self, run_id: str) -> dict:
        directory = self._directory(run_id)
        return self._describe_directory(directory)

    def _describe_directory(self, directory: Path) -> dict:
        """Build the API result before committing an imported directory."""
        result = self._summary(directory)
        for key, name in (("config", "config.json"), ("report", "report.json"),
                          ("snapshot", "monitor_snapshot.json"), ("profile", "profile.json"),
                          ("reproduction", "reproduction.json"), ("final_config", "final_config.json"),
                          ("manifest", "manifest.json"), ("import_source", ".imported.json")):
            value, error = _safe_json(directory / name)
            if key == "report" and not value:
                value, fallback = _safe_json(directory / "error.json")
                error = error or fallback
            result[key] = value or None
            if error:
                result["warnings"].append(error)
        result["files"] = [{"name": path.name, "bytes": path.stat().st_size}
                           for path in _record_files(directory)]
        if _regular_file(directory / "manifest.json"):
            result["files"].append({"name": "manifest.json", "bytes": (directory / "manifest.json").stat().st_size})
        result["can_export"] = not result["active"] and result["has_report"]
        return result

    def file_path(self, run_id: str, name: str) -> Path:
        if not isinstance(name, str) or name not in ALLOWED_FILES:
            raise ValueError("该文件不在实验下载白名单中")
        path = self._directory(run_id) / name
        if not _regular_file(path):
            raise FileNotFoundError("实验文件不存在或不是普通文件")
        return path

    def run_path(self, run_id: str) -> Path:
        """Return only a validated direct child directory of output/habitat."""
        return self._directory(run_id)

    def finalize_run(self, directory) -> Path:
        directory = Path(directory)
        expected = self._directory(directory.name)
        if directory.resolve() != expected.resolve():
            raise ValueError("实验目录不属于当前项目")
        return finalize_run(directory)

    def export_run(self, run_id: str) -> Path:
        directory = self._directory(run_id)
        if (directory / ".recording").exists():
            raise ValueError("本轮正在运行或保存，请先停止并等待保存完成")
        report = _load_json(directory / "report.json")
        if not report:
            report = _load_json(directory / "error.json")
        if not report or report.get("status") not in ("finished", "error", "interrupted", "stopped"):
            raise ValueError("只可导出已经结束并生成报告的实验")
        self._check_base(self.exports)
        self.exports.mkdir(parents=True, exist_ok=True)
        # Keep notices from an imported bundle intact. Supply missing notices
        # from this project's trusted root without modifying an old recording.
        virtual_files = {}
        for name, relative in NOTICE_SOURCES.items():
            if _regular_file(directory / name):
                continue
            source = self.root / relative
            if _linked(source.parent) or not _regular_file(source) or source.stat().st_size > MAX_DOCUMENT_BYTES:
                raise ValueError(f"缺少可用的实验包来源/许可附件：{name}")
            with source.open("rb") as stream:
                raw = stream.read(MAX_DOCUMENT_BYTES + 1)
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise ValueError(f"来源/许可附件过大：{name}")
            virtual_files[name] = raw
        manifest = _build_manifest(directory, virtual_files)
        manifest_raw = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if len(manifest_raw) > MAX_MANIFEST_BYTES:
            raise ValueError("实验清单过大")
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        target = self.exports / f"{run_id}_{suffix}.zip"
        temporary = target.with_suffix(".zip.partial")
        try:
            with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                                 allowZip64=True) as archive:
                archive.writestr("experiment/manifest.json", manifest_raw)
                for item in manifest["files"]:
                    if (directory / ".recording").exists():
                        raise ValueError("实验再次进入运行状态，已取消导出")
                    if item["name"] in virtual_files:
                        archive.writestr("experiment/" + item["name"], virtual_files[item["name"]])
                        continue
                    path = self.file_path(run_id, item["name"])
                    digest = hashlib.sha256()
                    count = 0
                    with path.open("rb") as source, archive.open("experiment/" + item["name"], "w", force_zip64=True) as dest:
                        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                            count += len(chunk)
                            if count > item["bytes"]:
                                raise ValueError(f"导出时文件变化：{item['name']}")
                            digest.update(chunk)
                            dest.write(chunk)
                    if count != item["bytes"] or digest.hexdigest() != item["sha256"]:
                        raise ValueError(f"导出时文件变化：{item['name']}")
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def save_to_directory(self, run_id: str, destination) -> Path:
        """Copy a verified exchange ZIP to an explicit existing user folder.

        Neither a recorded argument nor any path supplied by an imported bundle
        is used here. The caller supplies the user's separately selected folder.
        Original recordings and the default export ZIP are retained unchanged.
        """
        from experiment_destination import validate_destination

        directory = validate_destination(destination)
        source = self.export_run(run_id)
        temporary = directory / f".fly-experiment-{uuid.uuid4().hex}.zip.partial"
        created = False
        try:
            before = source.stat()
            digest = hashlib.sha256()
            count = 0
            with source.open("rb") as reader, temporary.open("xb") as writer:
                created = True
                for chunk in iter(lambda: reader.read(CHUNK_BYTES), b""):
                    count += len(chunk)
                    if count > before.st_size:
                        raise ValueError("默认导出包在复制时发生变化，请重新保存")
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            after = source.stat()
            if (count != before.st_size or
                    (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
                raise ValueError("默认导出包在复制时发生变化，请重新保存")
            if temporary.stat().st_size != count or _sha256(temporary) != digest.hexdigest():
                raise ValueError("保存后的实验包校验失败，未发布文件，请检查磁盘后重试")
            for _attempt in range(8):
                target = directory / f"{source.stem}_{uuid.uuid4().hex[:12]}.zip"
                try:
                    # validate_destination restricts this path to Windows, where
                    # same-directory os.rename is atomic and fails if target
                    # already exists. Never use os.replace for a user's folder.
                    os.rename(temporary, target)
                    created = False
                    return target
                except FileExistsError:
                    continue
            raise FileExistsError("无法取得不重复的实验包名称，请重新保存")
        except OSError as exc:
            raise OSError(f"无法将实验包保存到指定文件夹，请检查磁盘空间和写入权限：{exc}") from exc
        finally:
            if created:
                # Never remove an existing destination or the default export.
                # This unique temporary file was created by this exact call.
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    raise OSError(f"无法清理本次保存的临时文件 {temporary.name}：{exc}") from exc

    def import_bundle(self, path) -> dict:
        path = Path(path)
        if not _regular_file(path) or path.stat().st_size > MAX_BUNDLE_BYTES:
            raise ValueError("实验包不是普通 ZIP 文件或超过 16 GiB")
        self._check_base(self.runs)
        self.runs.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".import_", dir=self.runs))
        try:
            with zipfile.ZipFile(path, "r") as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_FILES + 1:
                    raise ValueError("实验包文件数无效")
                names = set()
                total = 0
                members = {}
                for info in infos:
                    name = info.filename
                    if name != info.orig_filename:
                        raise ValueError("实验包包含无效成员名称")
                    if name in names:
                        raise ValueError("实验包包含重复文件")
                    names.add(name)
                    # No arbitrary folders, encrypted files, links or devices.
                    if not name.startswith("experiment/") or name.count("/") != 1:
                        raise ValueError("实验包成员路径无效")
                    leaf = name[len("experiment/"):]
                    if leaf not in ALLOWED_FILES or info.is_dir() or info.flag_bits & 1:
                        raise ValueError("实验包包含未允许的文件或加密成员")
                    mode = info.external_attr >> 16
                    if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                        raise ValueError("实验包不允许链接或特殊文件")
                    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        raise ValueError("实验包压缩方法不受支持")
                    total += info.file_size
                    if info.file_size < 0 or total > MAX_BUNDLE_BYTES:
                        raise ValueError("实验包展开后超过 16 GiB")
                    if info.file_size > MAX_COMPRESSION_RATIO * max(1, info.compress_size):
                        raise ValueError("实验包压缩比异常")
                    members[leaf] = info
                if "manifest.json" not in members:
                    raise ValueError("缺少 manifest.json")
                if members["manifest.json"].file_size > MAX_MANIFEST_BYTES:
                    raise ValueError("实验清单过大")
                with archive.open(members["manifest.json"]) as stream:
                    manifest_raw = stream.read(MAX_MANIFEST_BYTES + 1)
                if len(manifest_raw) > MAX_MANIFEST_BYTES:
                    raise ValueError("实验清单过大")
                manifest = _decode_json(manifest_raw)
                if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != SCHEMA_VERSION or manifest.get("kind") != BUNDLE_KIND:
                    raise ValueError("不支持的实验包格式或版本")
                entries = manifest.get("files")
                if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
                    raise ValueError("实验包文件清单无效")
                expected = {}
                for item in entries:
                    if not isinstance(item, dict):
                        raise ValueError("实验包文件清单无效")
                    name = item.get("name")
                    if not isinstance(name, str) or name not in ALLOWED_FILES - {"manifest.json"} or name in expected:
                        raise ValueError("实验包清单名称无效或重复")
                    size, digest = item.get("bytes"), item.get("sha256")
                    if type(size) is not int or not 0 <= size <= MAX_BUNDLE_BYTES or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                        raise ValueError("实验包清单大小或 SHA256 无效")
                    expected[name] = item
                if set(expected) | {"manifest.json"} != set(members):
                    raise ValueError("实验包成员与清单不一致")
                stated_total = manifest.get("total_bytes")
                actual_total = sum(item["bytes"] for item in expected.values())
                if type(stated_total) is not int or stated_total != actual_total:
                    raise ValueError("实验包清单总大小不一致")
                if "config.json" not in expected or not ({"report.json", "error.json"} & expected.keys()):
                    raise ValueError("实验包需要配置和结束报告")
                if actual_total + len(manifest_raw) > MAX_BUNDLE_BYTES:
                    raise ValueError("实验包记录总量超过限制")
                for name, item in expected.items():
                    info = members[name]
                    if info.file_size != item["bytes"]:
                        raise ValueError(f"实验文件大小不匹配：{name}")
                    digest = hashlib.sha256()
                    count = 0
                    with archive.open(info) as source, (temporary / name).open("xb") as dest:
                        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                            count += len(chunk)
                            if count > item["bytes"]:
                                raise ValueError("实验包实际展开量超过清单")
                            digest.update(chunk)
                            dest.write(chunk)
                    if count != item["bytes"] or digest.hexdigest() != item["sha256"]:
                        raise ValueError(f"实验文件校验失败：{name}")
                config = _load_json(temporary / "config.json")
                report = _load_json(temporary / "report.json") or _load_json(temporary / "error.json")
                if not config or report.get("status") not in ("finished", "error", "interrupted", "stopped"):
                    raise ValueError("实验包缺少有效配置或结束报告")
                # Validate only structures needed for metadata/display; actual
                # simulation fields are validated separately by the profile layer.
                documents = [("manifest.json", manifest), ("config.json", config), ("report.json", report)]
                for name in ("error.json", "monitor_snapshot.json", "profile.json", "reproduction.json", "final_config.json"):
                    if name in expected:
                        documents.append((name, _load_json(temporary / name)))
                for name, document in documents:
                    object_fields = ("project",)
                    if name == "manifest.json":
                        object_fields += ("dependencies", "reproducibility", "notices")
                    if name in ("report.json", "error.json"):
                        object_fields += ("brain_metadata",)
                    for field in object_fields:
                        if document.get(field) is not None and not isinstance(document[field], dict):
                            raise ValueError(f"实验元数据字段必须是对象：{name}/{field}")
                (temporary / "manifest.json").write_bytes(manifest_raw)
                # Preserve the exact imported manifest even after a later export
                # refreshes manifest.json. Private metadata is never exported.
                (temporary / ".original_manifest.json").write_bytes(manifest_raw)
            imported_id = "imported_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:12]
            _write_json(temporary / ".imported.json", {
                "imported_at": _now(), "source_filename": path.name,
                "source_sha256": _sha256(path), "source_run_id": manifest.get("run_id"),
                "checksums_verified": True,
            })
            target = self.runs / imported_id
            # Unique fresh names; rename never merges or overwrites run files.
            if target.exists():
                raise FileExistsError("导入目录已存在，请重试")
            result = self._describe_directory(temporary)
            result["id"] = imported_id
            # Detect all response serialization failures before committing any
            # imported data. Return this exact snapshot after the atomic rename.
            try:
                json.dumps(result, ensure_ascii=False, allow_nan=False)
            except (ValueError, TypeError, OverflowError, RecursionError) as exc:
                raise ValueError("实验包元数据无法生成有效预览") from exc
            temporary.rename(target)
            temporary = None
            return result
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, UnicodeError) as exc:
            raise ValueError(f"实验包读取失败：{exc}") from exc
        finally:
            if temporary is not None:
                # This exact directory was created by this import, never chosen
                # from the untrusted archive or any pre-existing recording.
                shutil.rmtree(temporary)

    def preview(self, run_id: str, kind: str, limit: int = 500) -> dict:
        if kind not in PREVIEW_FILES:
            raise ValueError("不支持的实验预览类型")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PREVIEW_ROWS:
            raise ValueError("预览行数须为 1–2000")
        path = self.file_path(run_id, PREVIEW_FILES[kind])
        # Scan at most 64 MiB; offsets sample evenly over that real prefix.
        # Sparse offset samples avoid loading an entire long recording.
        offsets = []
        stride = 1
        valid_rows = 0
        oversized = 0
        invalid = 0
        scanned = 0
        header = None
        with path.open("rb") as stream:
            if kind == "trajectory":
                raw = stream.readline(MAX_PREVIEW_LINE_BYTES + 1)
                scanned += len(raw)
                if len(raw) > MAX_PREVIEW_LINE_BYTES:
                    raise ValueError("轨迹表头过大")
                try:
                    header = next(csv.reader([raw.decode("utf-8-sig")]), [])
                except (csv.Error, UnicodeError) as exc:
                    raise ValueError("轨迹 CSV 表头无效") from exc
                if not header or len(header) > 512:
                    raise ValueError("轨迹表头无效")
            while scanned < MAX_PREVIEW_SCAN_BYTES:
                offset = stream.tell()
                raw = stream.readline(min(MAX_PREVIEW_LINE_BYTES + 1, MAX_PREVIEW_SCAN_BYTES - scanned))
                if not raw:
                    break
                scanned += len(raw)
                if not raw.endswith(b"\n") and stream.tell() < path.stat().st_size:
                    oversized += 1
                    # Skip the rest using bounded reads, counting scan budget.
                    while scanned < MAX_PREVIEW_SCAN_BYTES and raw and not raw.endswith(b"\n"):
                        raw = stream.readline(min(MAX_PREVIEW_LINE_BYTES + 1, MAX_PREVIEW_SCAN_BYTES - scanned))
                        scanned += len(raw)
                    continue
                if not raw.strip():
                    continue
                valid_rows += 1
                # Retain a regularly spaced grid. Thin the entire grid when it
                # fills, instead of silently retaining only the newest records.
                if (valid_rows - 1) % stride == 0:
                    offsets.append((valid_rows, offset))
                if len(offsets) > MAX_PREVIEW_ROWS * 4:
                    offsets = offsets[::2]
                    stride *= 2
            prefix_only = stream.tell() < path.stat().st_size
            offsets.sort()
            chosen = []
            count = min(limit, len(offsets))
            for index in range(count):
                chosen.append(offsets[round(index * (len(offsets) - 1) / max(1, count - 1))])
            rows = []
            payload_bytes = 0
            byte_limited = False
            for row_number, offset in chosen:
                stream.seek(offset)
                raw = stream.readline(MAX_PREVIEW_LINE_BYTES + 1)
                try:
                    if kind == "trajectory":
                        cells = next(csv.reader([raw.decode("utf-8-sig")]))
                        if len(cells) != len(header):
                            raise ValueError("轨迹列数不匹配")
                        value = dict(zip(header, cells))
                    else:
                        value = _decode_json(raw)
                        if not isinstance(value, dict):
                            raise ValueError("记录行不是对象")
                    # Never return unbounded arrays from neural activity or
                    # retina rows; full records remain downloadable separately.
                    value = _preview_value(value)
                    size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                except (ValueError, UnicodeError, csv.Error):
                    invalid += 1
                    continue
                if payload_bytes + size > MAX_PREVIEW_BYTES:
                    byte_limited = True
                    break
                payload_bytes += size
                rows.append(value)
        sampled = len(rows) < valid_rows or oversized > 0 or invalid > 0 or prefix_only
        return {
            "id": run_id, "kind": kind, "file": path.name, "rows": rows,
            "scanned_rows": valid_rows, "returned_rows": len(rows),
            "scanned_bytes": scanned, "file_bytes": path.stat().st_size,
            "sampled": sampled, "prefix_only": prefix_only,
            "oversized_rows_skipped": oversized, "invalid_sample_rows_skipped": invalid,
            "response_byte_limited": byte_limited,
            "note": "来自实际记录的有界预览；最多扫描前 64 MiB，并对扫描范围抽样。嵌套大数组仅保留少量项，完整原始数据请下载文件。" if sampled else "来自实际记录；嵌套大数组仅保留少量项，完整原始数据请下载文件。",
        }


def _preview_value(value, depth=0):
    if depth > 5:
        return "[嵌套内容省略；原始文件可下载]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:80]:
            if key == "activity":
                result[key] = "[逐事件明细不在预览中展开]"
            else:
                result[str(key)[:256]] = _preview_value(item, depth + 1)
        if len(value) > 80:
            result["_preview_omitted_keys"] = len(value) - 80
        return result
    if isinstance(value, list):
        result = [_preview_value(item, depth + 1) for item in value[:32]]
        if len(value) > 32:
            result.append({"_preview_omitted_items": len(value) - 32})
        return result
    if isinstance(value, str):
        return value[:2048] + ("…" if len(value) > 2048 else "")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
