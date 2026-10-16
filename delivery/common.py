"""Process-local paths for the Windows kit. Never changes global Python or PATH."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TORCH_NAME = 'torch-2.11.0+cu128-cp312-cp312-win_amd64.whl'
TORCH_SHA256 = '7c78215c3af4f62e63f2b2e360f1722fc719b0853c7ac22666483d9810613a4c'
TORCH_BYTES = 2753189216
TORCH_URL = 'https://download-r2.pytorch.org/whl/cu128/torch-2.11.0%2Bcu128-cp312-cp312-win_amd64.whl'


def environment():
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(key, None)
    cache = ROOT / '.cache'
    (cache / 'tmp').mkdir(parents=True, exist_ok=True)
    env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1', PYTHONNOUSERSITE='1',
               PYTHONPYCACHEPREFIX=str(cache / 'pycache'),
               TEMP=str(cache / 'tmp'), TMP=str(cache / 'tmp'),
               UV_CACHE_DIR=str(cache / 'uv'), UV_NO_CONFIG='1', UV_OFFLINE='1',
               UV_PYTHON_DOWNLOADS='never',
               MPLCONFIGDIR=str(cache / 'matplotlib'), NUMBA_CACHE_DIR=str(cache / 'numba'),
               CUDA_CACHE_PATH=str(cache / 'cuda'), TORCH_HOME=str(cache / 'torch'),
               XDG_CACHE_HOME=str(cache), IMAGEIO_USERDIR=str(cache / 'imageio'),
               FLYGYM_ASSET_CACHE_DIR=str(cache / 'flygym_assets'))
    return env


def python_for(kind):
    return ROOT / ('.brain-venv' if kind == 'brain' else '.venv') / 'Scripts' / 'python.exe'
