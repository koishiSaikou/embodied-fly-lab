"""Verify the initial source snapshot without importing or running the simulation."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / '仓库文件清单.json'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def synchronization_evidence(source_root, preparation_root, batches):
    """Validate authorized sync chains without rewriting the immutable copy baseline."""
    first,latest,failures={},{},[]
    source_root=source_root.resolve();preparation_root=preparation_root.resolve()
    for batch in batches:
        for row in batch['files']:
            relative=row['path']
            target=(source_root/relative).resolve()
            if not target.is_relative_to(source_root):
                failures.append('Invalid synchronization target: '+relative);continue
            if relative in latest and row['before_sha256']!=latest[relative]:
                failures.append('Broken synchronization chain: '+relative)
            first.setdefault(relative,row['before_sha256'])
            latest[relative]=row['after_sha256']
            if row['before_sha256'] is not None:
                backup=(source_root/(row.get('backup') or '')).resolve()
                if not backup.is_relative_to(source_root) or not backup.is_file() or digest(backup)!=row['before_sha256']:
                    failures.append('Synchronization backup differs: '+relative)
    for relative,checksum in latest.items():
        target=(source_root/relative).resolve()
        if not target.is_file() or digest(target)!=checksum:
            failures.append('Synchronized active file differs: '+relative)
        # The final record defines whether a file is shared byte for byte or merged docs.
        row=next(row for batch in reversed(batches) for row in reversed(batch['files']) if row['path']==relative)
        if row.get('same_as_preparation_copy'):
            current=(preparation_root/relative).resolve()
            if not current.is_relative_to(preparation_root) or not current.is_file() or digest(current)!=checksum:
                failures.append('Runtime change not synchronized: '+relative)
    return first,latest,failures


def repository_files():
    """Audit source candidates rather than ignored local environments/test output."""
    if (ROOT / '.git').is_dir() and shutil.which('git'):
        setting = 'core.excludesFile=' + str(ROOT / '.git/info/exclude')
        paths = set()
        for args in (['ls-files', '--others', '--exclude-standard', '-z'], ['ls-files', '-z']):
            result = subprocess.run(['git', '-c', setting, *args], cwd=ROOT, capture_output=True,
                                    text=True, encoding='utf-8', check=True)
            paths.update(filter(None, result.stdout.split('\0')))
        for name in paths:
            if not (ROOT / name).is_file():
                raise RuntimeError('Unexpected directory or missing source candidate: ' + name)
        return sorted(ROOT / name for name in paths)
    # Downloaded ZIPs need no Git installation; prune runtime trees before walking.
    ignored = {'.git', '.venv', '.brain-venv', '.cache', '.package-cache', '.package-test',
               '.repository-audit', '_tools', '_runtime', 'output', 'dist', 'build',
               '__pycache__', 'node_modules', '.pytest_cache', '.ruff_cache', '.mypy_cache'}
    paths = []
    for directory, directories, names in os.walk(ROOT):
        directories[:] = [d for d in directories if d not in ignored and not d.endswith('.egg-info')]
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith('downloads/') and not (relative.startswith('downloads/annotations/') and path.suffix == '.md'):
                continue
            if relative.startswith('brain-model/data/') and (name == '2025_Connectivity_783.parquet' or name.startswith('weight_')):
                continue
            if path.suffix in ('.pyc', '.pyo', '.log', '.whl') or name == 'MUJOCO_LOG.TXT':
                continue
            paths.append(path)
    return sorted(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Optionally compare copied files with the original project')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    failures = []
    copied = 0
    first_synced,latest_synced={},{}
    sync_record=ROOT/'docs/功能同步记录.json'
    if args.source is not None and sync_record.is_file():
        sync=json.loads(sync_record.read_text(encoding='utf-8'))
        first_synced,latest_synced,sync_failures=synchronization_evidence(args.source,ROOT,sync['batches'])
        failures.extend(sync_failures)
    for item in manifest.get('excluded_originals',[]):
        # Preserve the historical copy evidence for documents intentionally
        # kept private; a public checkout need not contain their local copies.
        if args.source is not None:
            original=(args.source.resolve()/item['source_path']).resolve()
            if not original.is_relative_to(args.source.resolve()) or not original.is_file() or digest(original)!=item['sha256']:
                failures.append('Excluded original reference differs: '+item['path'])
            copied+=1
    for item in manifest['files']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            failures.append('Missing or invalid path: ' + item['path'])
            continue
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            failures.append('Snapshot changed: ' + item['path'])
        original_relative = item.get('source_path') or item.get('original_source_path')
        if args.source is not None and original_relative:
            original = (args.source.resolve() / original_relative).resolve()
            original_checksum = item.get('original_sha256', item['sha256'])
            if original_relative in latest_synced:
                if first_synced[original_relative]!=original_checksum:
                    failures.append('Synchronization does not preserve original baseline: '+item['path'])
                original_checksum=latest_synced[original_relative]
            if not original.is_relative_to(args.source.resolve()) or not original.is_file() or digest(original) != original_checksum:
                failures.append('Original/copy differs: ' + item['path'])
            copied += 1

    forbidden_parts = {'.venv', '.brain-venv', '.cache', '.package-cache', '.package-test',
                       '__pycache__', 'node_modules', 'build', 'dist', 'output'}
    checked_files = []
    for path in repository_files():
        relative = path.relative_to(ROOT)
        if relative.parts[0] in ('.git', '.repository-audit'):
            continue
        if path.is_symlink():
            failures.append('Unexpected link: ' + relative.as_posix())
        if '.git' in relative.parts:
            failures.append('Nested Git metadata: ' + relative.as_posix())
        if not path.is_file():
            continue
        checked_files.append(path)
        if forbidden_parts.intersection(relative.parts):
            failures.append('Generated/runtime file: ' + relative.as_posix())
        if path.stat().st_size >= 50 * 1024**2:
            failures.append('Large repository file: ' + relative.as_posix())
        if path.suffix.lower() in ('.whl', '.pyc', '.pyo', '.log') or path.name in ('weight_coo.pkl', 'weight_csr.pkl', 'MUJOCO_LOG.TXT'):
            failures.append('Excluded artifact: ' + relative.as_posix())

    expected_paths = {item['path'] for item in manifest['files']}
    expected_paths.update(manifest['excluded_self_and_reports'])
    for path in checked_files:
        if path.relative_to(ROOT).as_posix() not in expected_paths:
            failures.append('File absent from snapshot inventory: ' + path.relative_to(ROOT).as_posix())
    resources = json.loads((ROOT / '外部资源清单.json').read_text(encoding='utf-8'))['resources']
    for item in resources:
        path = (ROOT / item['path']).resolve()
        if (not path.is_relative_to(ROOT) or not item['url'].startswith('https://')
                or not isinstance(item['bytes'], int) or item['bytes'] <= 0
                or len(item['sha256']) != 64 or any(c not in '0123456789abcdef' for c in item['sha256'])):
            failures.append('Invalid external resource metadata: ' + item['path'])

    python_files = list(ROOT.glob('*.py'))
    for directory in ('delivery', 'tests', 'tools'):
        python_files.extend((ROOT / directory).glob('*.py'))
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path.relative_to(ROOT)))
        except (SyntaxError, UnicodeError) as error:
            failures.append('Python syntax: ' + str(error))

    ignore_probes = ['.venv/Scripts/python.exe', '.brain-venv/Scripts/python.exe',
                     '.cache/uv/cache', '.package-cache/wheels/x.whl', '.package-test/Friend/data',
                     'output/habitat/example/report.json', 'dist/release.zip',
                     'downloads/torch-example.whl', 'downloads/annotations/example.tsv',
                     'downloads/morphology/example.skel', 'brain-model/data/2025_Connectivity_783.parquet',
                     'brain-model/data/weight_csr.pkl', 'brain-model/data/weight_coo.pkl',
                     'delivery/__pycache__/x.pyc', '.env', 'MUJOCO_LOG.TXT']
    ignored_count = None
    candidates_count = None
    if (ROOT / '.git').is_dir() and shutil.which('git'):
        exclude_setting = 'core.excludesFile=' + str(ROOT / '.git/info/exclude')
        ignored = subprocess.run(['git', '-c', exclude_setting, 'check-ignore', '--no-index', '--stdin', '-z'],
                                 input='\0'.join(ignore_probes) + '\0', cwd=ROOT,
                                 capture_output=True, text=True, encoding='utf-8', check=False)
        if ignored.returncode not in (0, 1):
            failures.append('git check-ignore failed: ' + ignored.stderr)
        actual_ignored = set(ignored.stdout.strip('\0').split('\0'))
        for probe in ignore_probes:
            if probe not in actual_ignored:
                failures.append('Missing ignore rule: ' + probe)
        ignored_count = len(ignore_probes)
        candidates = subprocess.run(['git', '-c', exclude_setting, 'ls-files', '--others', '--exclude-standard', '-z'],
                                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True)
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True,
                                 text=True, encoding='utf-8', check=True)
        candidate_paths = set(filter(None, (candidates.stdout + tracked.stdout).split('\0')))
        candidates_count = len(candidate_paths)
        for path in checked_files:
            if path.relative_to(ROOT).as_posix() not in candidate_paths:
                failures.append('Required source hidden by ignore rules: ' + path.relative_to(ROOT).as_posix())

    result = dict(status='passed' if not failures else 'failed',
                  snapshot_files=len(manifest['files']), original_files_compared=copied,
                  synchronized_active_files_checked=len(latest_synced),
                  private_original_reference_files=len(manifest.get('excluded_originals',[])),
                  python_files_parsed=len(python_files), ignore_rules_probed=ignored_count,
                  external_resources_checked=len(resources),
                  git_candidate_files=candidates_count, total_bytes=sum(p.stat().st_size for p in checked_files),
                  failures=failures,
                  scope='File organization only; no simulation, dependency installation or GPU test was run.')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not failures else 1)


if __name__ == '__main__':
    main()
