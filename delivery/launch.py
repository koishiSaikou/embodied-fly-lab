"""Compatibility entry for Windows kit launchers; uses the shared root launcher."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launcher import main  # noqa: E402
from startup_support import available_port  # noqa: E402,F401 (legacy smoke-test import)

if __name__ == '__main__':
    raise SystemExit(main())
