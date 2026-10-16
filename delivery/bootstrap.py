"""Offline installation into this directory, using bundled isolated interpreters."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, TORCH_NAME, TORCH_SHA256, TORCH_BYTES, TORCH_URL, environment, python_for


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_bundle():
    manifest = json.loads((ROOT / 'bundle-manifest.json').read_text(encoding='utf-8'))
    print(f"Checking {len(manifest['files'])} packaged files...", flush=True)
    for item in manifest['files']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise RuntimeError('Missing or invalid packaged path: ' + item['path'])
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise RuntimeError('Packaged file checksum mismatch: ' + item['path'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--body-only', action='store_true', help='Install the body environment without Torch/GPU')
    parser.add_argument('--check-package', action='store_true', help='Verify packaged files without creating environments')
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('This kit requires Windows x64 (Intel/AMD), not Windows ARM')
    verify_bundle()
    if args.check_package:
        print('Package integrity passed. No environment was created.')
        return
    if not str(ROOT).isascii():
        raise RuntimeError('MuJoCo on Windows cannot reliably open mesh files under non-ASCII paths.\n'
                           'Move/extract this kit to an English path such as D:\\FlyWire, then rerun Setup.cmd.\n'
                           'Spaces are supported. No system Python changes are needed.')
    kinds = ['body'] if args.body_only else ['body', 'brain']
    external = json.loads((ROOT / 'delivery/外部依赖.json').read_text(encoding='utf-8'))['wheels']
    for item in external:
        if not set(item['profiles']).intersection(kinds):
            continue
        wheel = (ROOT / 'downloads' / item['file']).resolve()
        if not wheel.is_relative_to((ROOT / 'downloads').resolve()):
            raise RuntimeError('Invalid external wheel filename')
        if not wheel.is_file():
            raise RuntimeError(f"Missing external wheel: {item['file']}\nDownload: {item['url']}\n"
                               f'Save as: {wheel}\nSee docs/下载说明.md. Setup does not download it automatically.')
        if wheel.stat().st_size != item['bytes'] or digest(wheel) != item['sha256']:
            raise RuntimeError('External wheel checksum mismatch: ' + item['file'])
    wheel = ROOT / 'downloads' / TORCH_NAME
    if not args.body_only:
        if not wheel.is_file():
            raise RuntimeError(f'Missing external GPU wheel. Download:\n{TORCH_URL}\n'
                               f'Save the file as:\n{wheel}\n'
                               'See docs/下载说明.md. Setup does not download it automatically.\n'
                               'For the body only, run: Setup.cmd --body-only')
        print('Checking the external PyTorch wheel SHA256...', flush=True)
        if wheel.stat().st_size != TORCH_BYTES or digest(wheel) != TORCH_SHA256:
            raise RuntimeError('PyTorch wheel checksum mismatch. Check the filename and re-download the exact wheel.')
    if shutil.disk_usage(ROOT).free < (2 if args.body_only else 12) * 1024**3:
        raise RuntimeError('Not enough free disk space in the project drive (body: 2 GiB; full: 12 GiB required)')
    env = environment()
    logs = ROOT / 'output' / 'setup'
    logs.mkdir(parents=True, exist_ok=True)
    uv = ROOT / '_tools' / 'uv.exe'
    transcript = []

    def run(command):
        command = [str(part) for part in command]
        print('\n> ' + subprocess.list2cmdline(command), flush=True)
        transcript.append(command)
        subprocess.run(command, cwd=ROOT, env=env, check=True)

    try:
        for kind in kinds:
            version = '313' if kind == 'body' else '312'
            base = ROOT / '_runtime' / ('python' + version) / 'python.exe'
            target = python_for(kind).parent.parent
            run([uv, '--no-config', '--offline', 'venv', '--no-project', '--allow-existing',
                 '--python', base, target])
            run([uv, '--no-config', '--offline', 'pip', 'install', '--python', python_for(kind),
                 '--no-index', '--find-links', ROOT / 'vendor' / 'wheels',
                 '--find-links', ROOT / 'downloads', '--no-deps', '--require-hashes',
                 '-r', ROOT / 'requirements' / (kind + '-win-x64.lock.txt')])
            run([uv, '--no-config', '--offline', 'pip', 'check', '--python', python_for(kind)])
            run([python_for(kind), ROOT / 'delivery' / 'doctor.py', '--' + kind])
    finally:
        (logs / 'commands.json').write_text(json.dumps(dict(time_utc=datetime.now(timezone.utc).isoformat(),
                                                          commands=transcript), ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / '.installation.json').write_text(json.dumps(dict(root=str(ROOT), profiles=kinds,
                                                           time_utc=datetime.now(timezone.utc).isoformat()), indent=2), encoding='utf-8')
    print('\nInstallation complete. No system Python, global packages, registry or global PATH was changed.')
    print('Next: Start-Habitat.cmd (full) or Start-Body.cmd (body only).')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('\nSETUP FAILED: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
