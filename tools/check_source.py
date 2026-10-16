"""Run diagnostics with all caches and temporary files inside this checkout."""
from pathlib import Path
import subprocess
import sys

from setup_source import environment, local_path


def main():
    root = Path(__file__).resolve().parents[1]
    local_path('.cache/tmp').mkdir(parents=True, exist_ok=True)
    env = environment(offline=True)
    body = root / '.venv/Scripts/python.exe'
    if not body.is_file():
        raise RuntimeError('Run Setup.cmd first. See docs/源码安装说明.md')
    for kind, python in [('body', body), ('brain', root / '.brain-venv/Scripts/python.exe')]:
        if not python.is_file():
            print('Brain environment absent; body-only installation.')
            continue
        subprocess.run([str(python), '-B', str(root / 'delivery/doctor.py'), '--source', '--' + kind],
                       cwd=root, env=env, check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print('CHECK FAILED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
