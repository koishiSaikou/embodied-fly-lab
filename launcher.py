"""Shared Windows source/kit launch entrypoint; keeps logs and graceful Ctrl+C."""
import argparse
from datetime import datetime, timezone
import json
import math
import signal
import subprocess
import sys

from delivery.common import ROOT, environment, python_for
from startup_support import available_port
from project_info import project_info


def preflight(mode):
    if not str(ROOT).isascii():
        raise RuntimeError('Use an ASCII/English project path (spaces are supported); see docs/启动与停止说明.md')
    kinds = ['body'] if mode in ('body', 'check') else ['brain'] if mode == 'brain' else ['body', 'brain']
    for kind in kinds:
        if not python_for(kind).is_file():
            hint = 'Setup.cmd --body-only' if mode == 'body' else 'Setup.cmd'
            raise RuntimeError(f'{kind} environment missing: {python_for(kind)}\nRun {hint} if included in this checkout; otherwise restore the existing project environments. See docs/启动与停止说明.md')
    required = []
    if mode != 'check':
        required.append('brain_worker.py' if mode == 'brain' else 'habitat.py' if mode == 'habitat' else 'play_fly.py')
    if mode in ('brain', 'habitat', 'coupled', 'activity'):
        required.extend(['brain-model/data/2025_Connectivity_783.parquet',
                         'brain-model/data/2025_Completeness_783.csv',
                         'brain-model/data/neuron_annotations.parquet', 'brain-model/data/annotation_report.json',
                         'brain-model/code/benchmark.py', 'brain-model/code/run_pytorch.py'])
    if mode in ('habitat', 'activity'):
        required.extend(['web/activity_catalog.json', 'activity_monitor.html'])
    if mode == 'habitat':
        required.extend(['web/index.html', 'web/morphology.json', 'web/vendor/three.module.js', 'web/vendor/three.core.js'])
    missing = [name for name in required if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError('Missing project/data files:\n  ' + '\n  '.join(missing)
                           + '\nRestore missing files; source checkout downloads: docs/源码安装说明.md and 外部资源清单.json. Existing local project: docs/启动与停止说明.md')


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['habitat', 'body', 'brain', 'coupled', 'activity', 'check'])
    parser.add_argument('--seconds', type=float)
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--video', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--exit-when-finished', action='store_true', help='Close a finite habitat run after saving (default retains webpage)')
    args, extra = parser.parse_known_args(argv)
    if args.seconds is not None:
        if not math.isfinite(args.seconds) or (args.mode == 'habitat' and args.seconds != 0 and not .02 <= args.seconds <= 3600) or (args.mode != 'habitat' and args.seconds <= 0):
            parser.error('--seconds: habitat accepts 0 (continuous) or [.02,3600]; other modes need a finite positive value')
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error('--port must be in [1024,65535]')
    if args.port is not None and args.mode not in ('habitat', 'activity'):
        parser.error('--port is available for habitat/activity only')
    if args.mode in ('brain', 'check') and (extra or args.seconds is not None or args.video is not None or args.no_browser):
        parser.error('brain/check do not take simulation options')
    if args.exit_when_finished and (args.mode != 'habitat' or args.seconds in (None, 0)):
        parser.error('--exit-when-finished requires a finite habitat --seconds value')
    return args, extra


def build_command(args, extra):
    if args.mode == 'brain':
        return [python_for('brain'), ROOT / 'brain_worker.py', '--probe'], None
    if args.mode == 'check':
        bundled=(ROOT / 'bundle-manifest.json').is_file()
        if not bundled and (ROOT / 'tools/check_source.py').is_file():
            return [python_for('body'), ROOT / 'tools/check_source.py'], None
        command=[python_for('body'), ROOT / 'delivery/doctor.py', '--all']
        if not bundled:command.append('--source')
        return command, None
    port = None
    if args.mode in ('habitat', 'activity'):
        preferred = args.port if args.port is not None else (8472 if args.mode == 'habitat' else 8470)
        port = available_port(preferred, automatic=args.port is None)
        if port != preferred:
            print(f'Port {preferred} is occupied; selected {port}.', flush=True)
    if args.mode == 'habitat':
        command = [python_for('body'), ROOT / 'habitat.py', '--headless', '--monitor',
                   '--seconds', str(args.seconds if args.seconds is not None else 0), '--port', str(port)]
        if not args.exit_when_finished:
            command.append('--keep-open')
    else:
        default_seconds = 3600 if args.mode == 'body' else 300 if args.mode == 'activity' else 30
        command = [python_for('body'), ROOT / 'play_fly.py', '--seconds', str(args.seconds if args.seconds is not None else default_seconds)]
        if args.mode != 'body':
            command.append('--brain')
        if args.mode == 'activity':
            command.extend(['--monitor', '--monitor-port', str(port)])
    if args.video is True or (args.video is None and args.mode == 'habitat'):
        command.append('--video')
    if args.no_browser:
        command.append('--no-browser')
    command.extend(extra)
    return command, f'http://127.0.0.1:{port}/' if port is not None else None


def run_child(command, env, log):
    """The child receives console Ctrl+C; the parent must not kill it while it saves."""
    process = subprocess.Popen([str(part) for part in command], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace')
    interrupted = False
    previous_handler = signal.getsignal(signal.SIGINT)

    def stopping(signum, frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            print('\nStopping the experiment; waiting for records and child processes to close...', flush=True)

    signal.signal(signal.SIGINT, stopping)
    try:
        for line in process.stdout:
            print(line, end='', flush=True)
            log.write(line); log.flush()
        code = process.wait()
        return (130 if interrupted and code == 0 else code), process.pid, interrupted
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def main(argv=None):
    args, extra = parse_arguments(argv)
    logs = ROOT / 'output/launch'
    logs.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f') + '_' + args.mode
    log_path = logs / (stem + '.log')
    result = dict(mode=args.mode, status='starting', project=project_info(), log=str(log_path), started_at_utc=datetime.now(timezone.utc).isoformat())
    code = 1
    with log_path.open('w', encoding='utf-8') as log:
        try:
            preflight(args.mode)
            command, url = build_command(args, extra)
            result.update(command=[str(part) for part in command], url=url)
            print('Keep this console open. Finish/save in the webpage, then Ctrl+C to close the service.'
                  if args.mode=='habitat' else 'Keep this console open. Close the native viewer or use Ctrl+C to stop.', flush=True)
            print('Launch log: ' + str(log_path), flush=True)
            if url:
                print('Dashboard (opens after the service is ready): ' + url, flush=True)
            log.write('Command: ' + subprocess.list2cmdline(result['command']) + '\n'); log.flush()
            code, pid, interrupted = run_child(command, environment(), log)
            result.update(child_pid=pid, interrupted=interrupted, status='stopped' if interrupted else 'finished' if code == 0 else 'failed')
            if code not in (0, 130):
                print(f'START/RUN FAILED (code {code}). See {log_path}', file=sys.stderr)
                print('Run Check-Environment.cmd for diagnosis; missing dependencies: rerun Setup.cmd.', file=sys.stderr)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result.update(status='failed', error=str(error))
            text = 'START FAILED: ' + str(error) + '\nLaunch log: ' + str(log_path)
            print(text, file=sys.stderr); log.write(text + '\n')
        finally:
            result.update(exit_code=code, finished_at_utc=datetime.now(timezone.utc).isoformat())
            (logs / (stem + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
