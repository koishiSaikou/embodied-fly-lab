"""Loopback experiment archive API, shared by the workbench and CPU-only viewer."""
from pathlib import Path
import logging
import tempfile
import threading

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from experiment_store import ExperimentStore

MAX_UPLOAD_BYTES = 16 * 1024**3


class ExperimentSaveCoordinator:
    """A requested destination is owned by this round, never by the web tab."""
    def __init__(self, store, monitor):
        self.store, self.monitor = store, monitor
        self.lock = threading.Lock()
        self.finished_run = None
        self.state = dict(status='idle', run_id=None, destination_directory=None, path=None, error=None)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def request(self, directory):
        snap = self.monitor.snapshot()
        if snap.get('status') not in ('running', 'saving', 'finished', 'error', 'interrupted'):
            raise ValueError('请等待实验加载完成后再另存')
        current = Path(snap.get('directory') or (snap.get('habitat') or {}).get('directory') or '')
        run = self.store.run_path(current.name)
        if run.resolve() != current.resolve():
            raise ValueError('当前实验目录无法确认')
        with self.lock:
            if self.state['status'] in ('pending', 'saving'):
                raise ValueError('已有另存任务，请等待完成')
            self.state = dict(status='pending', run_id=run.name,
                              destination_directory=str(directory), path=None, error=None)
            completed = self.finished_run == run.name
            if not completed and snap.get('status') == 'running':
                try:
                    self.monitor.command_callback({'type': 'stop'})
                except Exception as error:
                    self.state.update(status='error', error=str(error))
                    raise
        if completed:
            self.complete(run)
        return self.snapshot()

    def complete(self, directory):
        """Called by the body finalizer after the manifest, even if the tab closed."""
        directory = Path(directory)
        with self.lock:
            self.finished_run = directory.name
            if self.state['status'] != 'pending' or self.state['run_id'] != directory.name:
                return
            self.state['status'] = 'saving'
            destination = self.state['destination_directory']
        try:
            path = self.store.save_to_directory(directory.name, destination)
        except Exception as error:
            with self.lock:
                self.state.update(status='error', error=str(error))
            logging.getLogger(__name__).exception('指定目录另存失败；项目内记录保留')
        else:
            with self.lock:
                self.state.update(status='saved', path=str(path), error=None)
        self.monitor.lifecycle(save_destination=self.snapshot())


def install_experiment_routes(app, root, *, monitor=None):
    root = Path(root).resolve()
    store = ExperimentStore(root)
    saver = ExperimentSaveCoordinator(store, monitor) if monitor else None
    if monitor is not None:
        monitor.experiment_save = saver

    def problem(error, status=400):
        if isinstance(error, FileNotFoundError):
            status = 404
        return JSONResponse({'error': str(error)}, status_code=status)

    def same_origin(request):
        origin = request.headers.get('origin')
        expected = monitor.url.rstrip('/') if monitor else str(request.base_url).rstrip('/')
        return origin in (None, expected)

    def capabilities():
        if monitor is None:
            return dict(can_reproduce=False, reason='独立查看器只读；请在实验场导入复现，或使用下方命令启动。',
                        viewer_only=True)
        state = monitor.snapshot()
        allowed = bool(state.get('status') in ('finished', 'error') and
                       state.get('recovery', {}).get('can_restart') and
                       saver.snapshot()['status'] not in ('pending', 'saving'))
        return dict(can_reproduce=allowed, viewer_only=False,
                    reason='' if allowed else '请先在实验场结束并保存本轮，等待收尾完成后再复现；不会停止正在运行的实验。')

    async def destination_options(request):
        if not same_origin(request):
            raise ValueError('Origin mismatch')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise ValueError('目录选项过大')
        import json
        values = json.loads(raw)
        if not isinstance(values, dict) or set(values) != {'destination_directory'}:
            raise ValueError('请指定 destination_directory')
        from experiment_destination import validate_destination
        return await run_in_threadpool(validate_destination, values['destination_directory'])

    @app.post('/api/experiments/choose-directory')
    async def choose_destination(request: Request):
        if not same_origin(request):
            return problem('Origin mismatch', 403)
        try:
            from experiment_destination import choose_directory
            selected = await run_in_threadpool(choose_directory)
            return JSONResponse(dict(directory=selected, cancelled=selected is None))
        except (ValueError, OSError, RuntimeError) as error:
            return problem(error)

    @app.get('/api/experiments/save-status')
    def save_status():
        return JSONResponse(saver.snapshot() if saver else dict(status='idle'),
                            headers={'Cache-Control': 'no-store'})

    @app.post('/api/experiments/save-current')
    async def save_current(request: Request):
        if not same_origin(request):
            return problem('Origin mismatch', 403)
        if saver is None:
            return problem('独立查看器不拥有实验；请选择一份结束记录后另存', 409)
        try:
            destination = await destination_options(request)
            result = await run_in_threadpool(saver.request, destination)
            return JSONResponse(dict(accepted=True, run_id=result['run_id'],
                                     message='另存请求已登记；收尾后保存实验 ZIP，关闭此页面也会继续保存。'),
                                status_code=202)
        except (ValueError, TypeError, KeyError, OSError) as error:
            return problem(error)

    @app.get('/records')
    def records_page():
        return FileResponse(root / 'web/records.html', headers={'Cache-Control': 'no-store'})

    @app.get('/api/experiments/capabilities')
    def experiment_capabilities():
        return JSONResponse(capabilities(), headers={'Cache-Control': 'no-store'})

    @app.get('/api/experiments')
    def experiment_list():
        try:
            return JSONResponse({'runs': store.list_runs()}, headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError) as error:
            return problem(error)

    @app.post('/api/experiments/import')
    async def experiment_import(request: Request):
        if not same_origin(request):
            return problem('Origin mismatch', 403)
        declared = request.headers.get('content-length')
        if declared:
            try:
                if not 0 < int(declared) <= MAX_UPLOAD_BYTES:
                    return problem('实验包大小须为 1 字节至 16 GiB', 413)
            except ValueError:
                return problem('无效的上传大小')
        incoming = root / 'output/experiment_uploads'
        temporary = None
        try:
            incoming.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=incoming, suffix='.zip.partial', delete=False) as stream:
                temporary = Path(stream.name)
                total = 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        return problem('实验包超过 16 GiB 上限', 413)
                    await run_in_threadpool(stream.write, chunk)
            result = await run_in_threadpool(store.import_bundle, temporary)
            return JSONResponse(result, status_code=201)
        except (ValueError, OSError, RuntimeError) as error:
            return problem(error)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).exception('无法删除本次上传临时文件：%s', temporary)

    @app.get('/api/experiments/{run_id}')
    def experiment_detail(run_id: str):
        try:
            return JSONResponse(store.describe(run_id), headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError) as error:
            return problem(error)

    @app.get('/api/experiments/{run_id}/preview')
    def experiment_preview(run_id: str, kind: str = 'trajectory', limit: int = 500):
        try:
            return JSONResponse(store.preview(run_id, kind, limit=limit), headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError) as error:
            return problem(error)

    @app.get('/api/experiments/{run_id}/file/{name}')
    def experiment_file(run_id: str, name: str):
        try:
            path = store.file_path(run_id, name)
            # HTML and arbitrary scripts are not in the store's file allowlist.
            return FileResponse(path, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        except (ValueError, OSError) as error:
            return problem(error)

    @app.get('/api/experiments/{run_id}/export')
    def experiment_export(run_id: str):
        try:
            path = store.export_run(run_id)
            return FileResponse(path, filename=path.name, media_type='application/zip',
                                headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError, RuntimeError) as error:
            return problem(error)

    @app.post('/api/experiments/{run_id}/save-to')
    async def save_record_to(run_id: str, request: Request):
        if not same_origin(request):
            return problem('Origin mismatch', 403)
        try:
            destination = await destination_options(request)
            path = await run_in_threadpool(store.save_to_directory, run_id, destination)
            return JSONResponse(dict(path=str(path), run_id=run_id, message='实验 ZIP 已保存到指定目录'))
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
            return problem(error)

    @app.post('/api/experiments/{run_id}/reproduce')
    async def experiment_reproduce(run_id: str, request: Request):
        if not same_origin(request):
            return problem('Origin mismatch', 403)
        allowed = capabilities()
        if not allowed['can_reproduce']:
            return problem(allowed['reason'], 409)
        try:
            body = await request.body()
            if len(body) > 2048:
                raise ValueError('复现选项过大')
            import json
            values = json.loads(body)
            if not isinstance(values, dict) or set(values) - {'mode', 'replay_events'}:
                raise ValueError('只支持 mode 和 replay_events 选项')
            mode, replay = values.get('mode', 'initial'), values.get('replay_events', True)
            if mode not in ('initial', 'final') or type(replay) is not bool:
                raise ValueError('复现模式或事件重放开关无效')
            directory = store.file_path(run_id, 'config.json').parent
            if (directory / '.recording').exists() or not any((directory / name).is_file() for name in ('report.json', 'error.json')):
                raise ValueError('源实验仍在运行或没有结束记录，不能作为完整实验复现')
            from experiment_profile import profile_from_run
            profile = await run_in_threadpool(profile_from_run, directory, mode, replay)
            if profile.get('no_brain'):
                return problem('这是无脑工程基线；请用独立命令复现，当前实验场网页需要脑模式。', 409)
            monitor.command_callback({'type': 'reproduce', 'profile': profile})
            return JSONResponse({'accepted': True, 'source_id': run_id,
                                 'warnings': profile.get('provenance', {}).get('compatibility_warnings', []),
                                 'message': '已提交独立新实验；重建实验条件，不是恢复原脑动态状态。'}, status_code=202)
        except (ValueError, TypeError, KeyError, OSError) as error:
            return problem(error)

    return store
