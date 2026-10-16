"""Install the Windows source checkout into two owned, project-local environments."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
UV_URL = 'https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-pc-windows-msvc.zip'
OWNER = '.source-environment.json'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def local_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise RuntimeError('Path escapes this checkout: ' + str(relative))
    return path


def environment(offline=False):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('UV_', 'PIP_', 'PYTHON')) and k not in ('VIRTUAL_ENV', 'CONDA_PREFIX')}
    cache = local_path('.cache')
    env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1', PYTHONNOUSERSITE='1',
               PYTHONPYCACHEPREFIX=str(cache / 'pycache'),
               TEMP=str(cache / 'tmp'), TMP=str(cache / 'tmp'),
               UV_CACHE_DIR=str(cache / 'uv'), UV_NO_CONFIG='1', UV_PYTHON_DOWNLOADS='never',
               UV_LINK_MODE='copy', UV_HTTP_TIMEOUT='30', UV_HTTP_RETRIES='1',
               UV_OFFLINE='1' if offline else '0',
               MPLCONFIGDIR=str(cache / 'matplotlib'), NUMBA_CACHE_DIR=str(cache / 'numba'),
               CUDA_CACHE_PATH=str(cache / 'cuda'), TORCH_HOME=str(cache / 'torch'),
               XDG_CACHE_HOME=str(cache), IMAGEIO_USERDIR=str(cache / 'imageio'),
               FLYGYM_ASSET_CACHE_DIR=str(cache / 'flygym_assets'))
    return env


def python_info(executable, env):
    code = ('import json,sys,struct,sysconfig;print(json.dumps(dict(version=list(sys.version_info[:2]),'
            'bits=struct.calcsize("P")*8,prefix=sys.prefix,base_prefix=sys.base_prefix,'
            'implementation=sys.implementation.name,gil_disabled=bool(sysconfig.get_config_var("Py_GIL_DISABLED")),'
            'debug=bool(sysconfig.get_config_var("Py_DEBUG")))))')
    result = subprocess.run([str(executable), '-I', '-B', '-c', code], env=env,
                            capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
    return json.loads(result.stdout)


def find_python(uv, supplied, minor, env):
    if supplied:
        executable = Path(supplied).expanduser().resolve(strict=True)
    else:
        result = subprocess.run([str(uv), '--no-config', 'python', 'find', '--no-project', '--system',
                                 '--no-python-downloads', minor], env=env, cwd=ROOT,
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        if result.returncode:
            raise RuntimeError(f'Python {minor} x64 not found. Install it yourself, or pass '
                               f'--python-{"body" if minor == "3.13" else "brain"} "path/to/python.exe". '
                               'No Python will be downloaded or replaced. See docs/源码安装说明.md')
        executable = Path(result.stdout.strip()).resolve(strict=True)
    info = python_info(executable, env)
    if info['version'] != [int(v) for v in minor.split('.')] or info['bits'] != 64:
        raise RuntimeError(f'Expected Python {minor} x64: {executable}; got {info}')
    if info['implementation'] != 'cpython' or info['gil_disabled'] or info['debug']:
        raise RuntimeError('Use a standard CPython build (not free-threaded, debug or PyPy): ' + str(executable))
    if Path(info['prefix']).resolve() != Path(info['base_prefix']).resolve():
        raise RuntimeError('Supply a base Python interpreter, not another project venv: ' + str(executable))
    return executable


def valid_resource(path, item):
    return path.is_file() and path.stat().st_size == item['bytes'] and digest(path) == item['sha256']


def restore_resource(item, resource_dirs, download=False):
    target = local_path(item['path'])
    if target.exists():
        if not valid_resource(target, item):
            raise RuntimeError('Resource checksum mismatch; original file preserved: ' + str(target))
        return target
    original = None
    for directory in resource_dirs:
        for candidate in (directory / Path(item['path']).name, directory / item['path']):
            if candidate.is_file():
                if not valid_resource(candidate, item):
                    raise RuntimeError('Supplied resource checksum mismatch: ' + str(candidate))
                original = candidate
                break
        if original:
            break
    if original is None and not download:
        raise RuntimeError(f'Missing {item["path"]}\nDownload: {item["url"]}\n'
                           f'Bytes: {item["bytes"]}\nSHA256: {item["sha256"]}\n'
                           'Save it at the path above, or use --resource-dir with an existing download folder.')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.download')
    if temporary.is_symlink() or not temporary.resolve().is_relative_to(ROOT):
        raise RuntimeError('Unsafe temporary resource path: ' + str(temporary))
    # Only this owned temporary file is replaced; an existing final file is never overwritten.
    with temporary.open('wb') as output:
        if original:
            with original.open('rb') as inp:
                shutil.copyfileobj(inp, output)
        else:
            print('Downloading data (Ctrl+C to stop): ' + item['url'], flush=True)
            with urllib.request.urlopen(item['url'], timeout=30) as response:
                written = 0
                while block := response.read(1024**2):
                    written += len(block)
                    if written > item['bytes']:
                        raise RuntimeError('Downloaded resource exceeds its declared size')
                    output.write(block)
                    if written // (8 * 1024**2) != (written - len(block)) // (8 * 1024**2):
                        print(f'  {written / 1024**2:.0f} MiB / {item["bytes"] / 1024**2:.0f} MiB', flush=True)
    if not valid_resource(temporary, item):
        raise RuntimeError('Downloaded/copied data checksum mismatch; final file was not installed: ' + str(temporary))
    if target.exists():
        raise RuntimeError('Resource appeared during install; it was not overwritten: ' + str(target))
    temporary.rename(target)
    return target


def prepare_environment(kind, executable, env, run):
    directory = local_path('.venv' if kind == 'body' else '.brain-venv')
    marker = directory / OWNER
    if directory.exists():
        if not marker.is_file():
            raise RuntimeError('Refusing to change an environment not created by this source installer: ' + str(directory))
        owner = json.loads(marker.read_text(encoding='utf-8'))
        if owner.get('root') != str(ROOT) or owner.get('base_python') != str(executable) or owner.get('kind') != kind:
            raise RuntimeError('Environment ownership/base path changed; preserve it and use a fresh checkout: ' + str(directory))
    else:
        directory.mkdir()
        marker.write_text(json.dumps(dict(root=str(ROOT), base_python=str(executable), kind=kind), indent=2), encoding='utf-8')
    python = directory / 'Scripts/python.exe'
    if python.exists():
        info = python_info(python, env)
        expected = [3, 13] if kind == 'body' else [3, 12]
        if Path(info['prefix']).resolve() != directory or info['version'] != expected or info['bits'] != 64:
            raise RuntimeError('Existing venv does not match its owner marker: ' + str(directory))
    else:
        run(['venv', '--no-project', '--allow-existing', '--python', executable, directory])
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--body-only', action='store_true')
    parser.add_argument('--uv', type=Path)
    parser.add_argument('--python-body')
    parser.add_argument('--python-brain')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--wheel-dir', type=Path, action='append', default=[])
    parser.add_argument('--resource-dir', type=Path, action='append', default=[])
    parser.add_argument('--download-data', action='store_true', help='Download the connection table only; Torch remains manual')
    parser.add_argument('--check', action='store_true', help='Check prerequisites without installing or copying resources')
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error('Use Python 3.12+ to run this installer')
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Supported source installation target: Windows x64')
    if not str(ROOT).isascii():
        raise RuntimeError('Use an ASCII checkout path (spaces are supported), e.g. D:\\FlyWire-GitHub')
    if args.offline and args.download_data:
        parser.error('--offline and --download-data cannot be combined')
    if not args.check and shutil.disk_usage(ROOT).free < (2 if args.body_only else 12) * 1024**3:
        raise RuntimeError('Need at least 2 GiB free for body only, or 12 GiB for full setup, on the project drive')
    env = environment(args.offline)
    uv = args.uv or (ROOT / '_tools/uv.exe' if (ROOT / '_tools/uv.exe').is_file() else shutil.which('uv'))
    if not uv:
        raise RuntimeError('uv not found. Download the portable archive, extract uv.exe into _tools (no PATH changes):\n' + UV_URL)
    uv = Path(uv).resolve(strict=True)
    version = subprocess.run([str(uv), '--version'], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    if not version.startswith('uv 0.12.18 '):
        raise RuntimeError('This lock/install flow was validated with uv 0.12.18. Use --uv for that portable executable. Got: ' + version)
    bases = {'body': find_python(uv, args.python_body, '3.13', env)}
    if not args.body_only:
        bases['brain'] = find_python(uv, args.python_brain, '3.12', env)
    wheel_dirs = [p.resolve(strict=True) for p in args.wheel_dir]
    resource_dirs = [p.resolve(strict=True) for p in args.resource_dir]
    if any(not p.is_dir() for p in wheel_dirs + resource_dirs):
        raise RuntimeError('--wheel-dir / --resource-dir must be directories')
    resources = json.loads((ROOT / '外部资源清单.json').read_text(encoding='utf-8'))['resources']
    required = [] if args.body_only else [item for item in resources if item['need'].startswith('required_')]
    for item in required:
        if args.check:
            target = local_path(item['path'])
            if not valid_resource(target, item):
                raise RuntimeError('Required resource absent or checksum incorrect: ' + str(target) + '\nDownload: ' + item['url'])
        else:
            restore_resource(item, resource_dirs, args.download_data and item['need'] == 'required_for_brain')
    if args.check:
        print(json.dumps(dict(status='prerequisites_passed', uv=version, base_pythons={k: str(v) for k, v in bases.items()},
                              required_resources=len(required), note='No environment or packages were installed.'), indent=2))
        return
    local_path('.cache/tmp').mkdir(parents=True, exist_ok=True)
    logs = local_path('output/setup')
    logs.mkdir(parents=True, exist_ok=True)
    transcript = []
    report = dict(status='installing', body_only=args.body_only, uv=version, commands=transcript,
                  started_at_utc=datetime.now(timezone.utc).isoformat())

    def run(arguments):
        command = [str(uv), '--no-config', *[str(a) for a in arguments]]
        print('> ' + subprocess.list2cmdline(command), flush=True)
        transcript.append(command)
        with (logs / 'source-install.log').open('a', encoding='utf-8') as log:
            log.write('\n> ' + subprocess.list2cmdline(command) + '\n'); log.flush()
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            try:
                for line in process.stdout:
                    print(line, end='', flush=True); log.write(line); log.flush()
                if process.wait():
                    raise RuntimeError('Install command failed; see output/setup/source-install.log')
            except BaseException:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=15)
                raise

    lock = local_path('.cache/source-setup.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('Another setup may be running. See .cache/source-setup.lock and the recovery instructions.')
    os.close(descriptor)
    lock.write_text(str(os.getpid()), encoding='ascii')
    try:
        for kind, base in bases.items():
            python = prepare_environment(kind, base, env, run)
            command = ['pip', 'install', '--python', python, '--no-deps', '--require-hashes',
                       '--only-binary', ':all:', '--link-mode', 'copy',
                       '-r', ROOT / 'requirements' / f'{kind}-win-x64.lock.txt', '--find-links', ROOT / 'downloads']
            for directory in wheel_dirs:
                command.extend(['--find-links', directory])
            command.extend(['--offline', '--no-index'] if args.offline else ['--default-index', 'https://pypi.org/simple'])
            run(command)
            if kind == 'body':
                run(['pip', 'install', '--python', python, '--no-deps', '--no-build-isolation',
                     '--offline', '--editable', ROOT / 'flygym'])
            run(['pip', 'check', '--python', python])
            subprocess.run([str(python), '-B', str(ROOT / 'delivery/doctor.py'), '--source', '--' + kind],
                           env=env, cwd=ROOT, check=True)
        report['status'] = 'passed'
        print('Source setup passed. Environments and caches are local; base Pythons were not modified.\n'
              'Next: Start-Body.cmd' if args.body_only else
              'Source setup passed. Start-Habitat.cmd opens the ready dashboard; use the actual address printed in its console.')
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        (logs / '源码安装结果.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        lock.unlink()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nSetup cancelled; original Python and existing experiments were not removed.', file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('\nSOURCE SETUP FAILED: ' + str(error) + '\nSee docs/源码安装说明.md', file=sys.stderr)
        raise SystemExit(1)
