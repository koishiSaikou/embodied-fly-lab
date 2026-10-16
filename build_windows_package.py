"""Build a Windows x64 offline kit, excluding the separately downloaded CUDA wheel."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from email.parser import BytesParser
from project_info import project_info

ROOT = Path(__file__).resolve().parent
RELEASE = 'FlyWire-Windows-' + project_info()['version']
SKIP = {'.git', '__pycache__', 'build', '.pytest_cache', '.ruff_cache', '.mypy_cache',
        'README_v3.2.0.md', 'columns_v3.2.0.md',
        'site-packages', 'Scripts'}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_tree(source, target, runtime=False):
    skipped = SKIP if runtime else SKIP - {'Scripts'} - {'site-packages'}
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=lambda folder, names: [n for n in names if n in skipped or n.endswith(('.egg-info', '.pyc', '.pyo'))])


def runtime_home(executable):
    return Path(subprocess.check_output([str(executable), '-I', '-c', 'import sys; print(sys.base_prefix)'],
                                        encoding='utf-8').strip())


def write_manifest(stage):
    files = []
    for path in sorted(stage.rglob('*')):
        relative = path.relative_to(stage)
        if not path.is_file() or path.name == 'bundle-manifest.json':
            continue
        if any(p in {'.venv', '.brain-venv', '.cache', '__pycache__', 'output'} for p in relative.parts):
            raise RuntimeError('Unexpected mutable/test directory in release stage: ' + str(relative))
        files.append(dict(path=relative.as_posix(), bytes=path.stat().st_size, sha256=sha256(path)))
    manifest = dict(release=RELEASE, project=project_info(), generated_at_utc=datetime.now(timezone.utc).isoformat(),
                    platform='Windows x64', external_torch_included=False, files=files)
    (stage / 'bundle-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def create_zip(stage, archive=None):
    if not (stage / '.flywire-release-stage').exists():
        raise RuntimeError('Not a release stage created by this builder')
    manifest = write_manifest(stage)
    archive = Path(archive).resolve() if archive else stage.with_suffix('.zip')
    if archive.is_relative_to(stage):
        raise RuntimeError('Archive must be outside the release stage')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        for item in manifest['files']:
            bundle.write(stage / item['path'], arcname=stage.name + '/' + item['path'])
        bundle.write(stage / 'bundle-manifest.json', arcname=stage.name + '/bundle-manifest.json')
    digest = sha256(archive)
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(json.dumps(dict(archive=str(archive), bytes=archive.stat().st_size, sha256=digest,
                          files=len(manifest['files']) + 1), indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / RELEASE)
    parser.add_argument('--uv', type=Path, default=shutil.which('uv'))
    parser.add_argument('--python313', type=Path, default=ROOT / '.venv/Scripts/python.exe')
    parser.add_argument('--python312', type=Path, default=ROOT / '.brain-venv/Scripts/python.exe')
    parser.add_argument('--wheel-cache', type=Path, default=ROOT / '.package-cache/wheels')
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Resume only a stage carrying this release marker')
    parser.add_argument('--zip-stage', type=Path)
    parser.add_argument('--archive', type=Path, help='Optional output ZIP path, outside the stage')
    args = parser.parse_args()
    if args.zip_stage:
        create_zip(args.zip_stage.resolve(), args.archive)
        return
    stage = args.output.resolve()
    marker=stage / '.flywire-release-stage'
    if stage.exists() and not (args.resume and marker.is_file() and marker.read_text(encoding='ascii')==RELEASE):
        raise RuntimeError('Output already exists; choose a new --output directory (nothing was deleted)')
    if not args.uv or not Path(args.uv).is_file():
        raise RuntimeError('Provide --uv PATH_TO_UV_EXE')
    stage.mkdir(parents=True,exist_ok=args.resume)
    (stage / '.flywire-release-stage').write_text(RELEASE, encoding='ascii')
    for path in ROOT.glob('*.py'):
        shutil.copy2(path, stage / path.name)
    for path in ROOT.glob('*.mjs'):
        shutil.copy2(path, stage / path.name)
    for name in ('activity_monitor.html', 'habitat.html', '环境仿真说明.md', '神经元注释说明.md', '脑活动监视说明.md',
                 'AGENTS.md', 'AI协作约定.md', '第三方许可与来源.md', '许可说明.md', '许可证.txt',
                 '数据使用与署名.md', '引用文献.md', '版本信息.json', '版本与更新记录.md', '项目说明.md'):
        shutil.copy2(ROOT / name, stage / name)
    for name in ('web', 'delivery', 'docs', 'licenses'):
        copy_tree(ROOT / name, stage / name)
    # Include source and bundled simplified body meshes, not .git or generated build products.
    copy_tree(ROOT / 'flygym', stage / 'flygym')
    for name in ('code',):
        copy_tree(ROOT / 'brain-model' / name, stage / 'brain-model' / name)
    for name in ('LICENSE', 'README.md'):
        shutil.copy2(ROOT / 'brain-model' / name, stage / 'brain-model' / name)
    (stage / 'brain-model/data').mkdir(parents=True,exist_ok=True)
    for path in (ROOT / 'brain-model/data').iterdir():
        if path.is_file() and path.suffix in ('.csv', '.json', '.parquet'):
            shutil.copy2(path, stage / 'brain-model/data' / path.name)
    for name in ('annotations', 'morphology'):
        copy_tree(ROOT / 'downloads' / name, stage / 'downloads' / name)
    (stage / 'downloads/请将PyTorch安装文件放在这里.txt').write_text(
        '请按 docs/下载说明.md 下载指定版本的 PyTorch 安装文件，放入本文件夹。\n', encoding='utf-8')
    introduction = (ROOT / 'docs/交付包说明.md').read_text(encoding='utf-8')
    for document in (ROOT / 'docs').glob('*.md'):
        introduction = introduction.replace(f']({document.name})', f'](docs/{document.name})')
    introduction = introduction.replace('](../', '](')
    (stage / '开始使用.md').write_text(introduction, encoding='utf-8')
    for version, executable in (('313', args.python313), ('312', args.python312)):
        copy_tree(runtime_home(executable), stage / '_runtime' / ('python' + version), runtime=True)
    (stage / '_tools').mkdir(exist_ok=True)
    shutil.copy2(args.uv, stage / '_tools/uv.exe')
    wheels = stage / 'vendor/wheels'
    wheels.mkdir(parents=True,exist_ok=True)
    lock_dir = stage / 'requirements'
    lock_dir.mkdir(exist_ok=True)
    metadata = {}
    for kind in ('body', 'brain'):
        entries = []
        for wheel in sorted((args.wheel_cache / kind).glob('*.whl')):
            destination = wheels / wheel.name
            if destination.exists() and sha256(destination) != sha256(wheel):
                raise RuntimeError('Conflicting wheel bytes: ' + wheel.name)
            if not destination.exists():
                shutil.copy2(wheel, destination)
            with zipfile.ZipFile(wheel) as archive:
                meta_file = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
                meta = BytesParser().parsebytes(archive.read(meta_file))
                value=lambda key: str(meta[key]) if meta.get(key) is not None else None
                name, version = meta['Name'], meta['Version']
                entries.append(f'{name}=={version} --hash=sha256:{sha256(wheel)}')
                metadata[wheel.name] = dict(name=str(name), version=str(version), sha256=sha256(wheel),
                                            license_expression=value('License-Expression'),
                                            license=value('License'), home_page=value('Home-page'))
        if kind == 'body' and not any(line.startswith('flygym==2.1.0 ') for line in entries):
            raise RuntimeError('Missing locally built FlyGym wheel')
        if kind == 'brain':
            entries.append('torch==2.11.0+cu128 --hash=sha256:7c78215c3af4f62e63f2b2e360f1722fc719b0853c7ac22666483d9810613a4c')
        (lock_dir / (kind + '-win-x64.lock.txt')).write_text('\n'.join(sorted(entries, key=str.casefold)) + '\n', encoding='utf-8')
    (stage / 'licenses/wheel-components.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    (stage / 'licenses/runtime-provenance.json').write_text(json.dumps(dict(
        python313='CPython 3.13.15, python-build-standalone build 20260807',
        python312='CPython 3.12.14, python-build-standalone build 20260901',
        python_source='https://github.com/astral-sh/python-build-standalone',
        uv='0.12.18, Windows x86_64', uv_source='https://github.com/astral-sh/uv/tree/0.12.18',
        excluded='No user site-packages, pip launchers, venvs, system configuration, registry or PATH settings copied'), indent=2), encoding='utf-8')
    def cmd(name, script, mode=''):
        text = ('@echo off\nchcp 65001 >nul\nsetlocal\ncd /d "%~dp0"\n'
                'if not exist "_runtime\\python313\\python.exe" (\n'
                '  echo Missing bundled runtime. Extract the entire ZIP first.\n  pause\n  exit /b 1\n)\n'
                f'"_runtime\\python313\\python.exe" -X utf8 -I "delivery\\{script}.py" {mode} %*\n'
                'set "KIT_EXIT=%ERRORLEVEL%"\nif not "%KIT_EXIT%"=="0" (\n'
                '  echo.\n  echo See docs\\操作说明.md and output\\setup for details.\n)\n'
                'if not "%FLYWIRE_NONINTERACTIVE%"=="1" pause\nexit /b %KIT_EXIT%\n')
        (stage / name).write_bytes(text.replace('\n', '\r\n').encode('utf-8'))
    cmd('Setup.cmd', 'bootstrap')
    cmd('Smoke-Test.cmd', 'smoke_test')
    (stage / '请先阅读.txt').write_text(
        'FlyWire Windows 本地实验场\n\n'
        '1. 完整解压到纯英文路径，例如 D:\\FlyWire（可带空格）。不要在 ZIP 内运行。\n'
        '2. 按 docs\\下载说明.md 下载指定 PyTorch wheel，放进 downloads。\n'
        '3. 双击 Setup.cmd，等待离线安装完成。不会修改系统 Python、PATH 或全局包。\n'
        '4. 双击 Start-Habitat.cmd，在自动打开的网页观察果蝇。\n\n'
        '可选：安装后运行 Smoke-Test.cmd，做短时间完整自检。\n'
        '操作说明：docs\\操作说明.md\n'
        '项目详情：docs\\项目详情.md\n'
        '给 AI 的二次开发文档：docs\\AI二次开发文档.md；先读 AI协作约定.md。\n', encoding='utf-8')
    for name, mode in [('Start-Habitat.cmd', 'habitat'), ('Start-Body.cmd', 'body'),
                       ('Start-Brain.cmd', 'brain'), ('Start-Coupled.cmd', 'coupled'),
                       ('Start-Activity.cmd', 'activity'), ('Check-Environment.cmd', 'check')]:
        cmd(name, 'launch', mode)
    manifest = write_manifest(stage)
    print(json.dumps(dict(stage=str(stage), files=len(manifest['files']),
                          uncompressed_bytes=sum(item['bytes'] for item in manifest['files'])), indent=2), flush=True)
    if not args.stage_only:
        create_zip(stage, args.archive)


if __name__ == '__main__':
    main()
