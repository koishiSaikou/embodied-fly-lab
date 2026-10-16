"""Diagnostics for either isolated environment; reports contain no credentials."""
import argparse
import csv
import json
from pathlib import Path
import platform
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, environment, python_for
sys.path.insert(0, str(ROOT))
from project_info import project_info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--body', action='store_true')
    parser.add_argument('--brain', action='store_true')
    parser.add_argument('--source', action='store_true', help='Allow an existing external base Python for source venvs')
    parser.add_argument('--all', action='store_true', help='Check both kit environments')
    args = parser.parse_args()
    if args.all:
        for kind in ('body','brain'):
            command=[str(python_for(kind)), '-B', str(Path(__file__).resolve()), '--'+kind]
            if args.source:command.append('--source')
            subprocess.run(command,cwd=ROOT,env=environment(),check=True)
        return
    result = dict(project=project_info(),profile='brain' if args.brain else 'body', executable=sys.executable,
                  base_prefix=sys.base_prefix, python=platform.python_version(), root=str(ROOT))
    result.update(venv_in_project=Path(sys.executable).resolve().is_relative_to(ROOT.resolve()),
                  base_runtime_in_project=Path(sys.base_prefix).resolve().is_relative_to(ROOT.resolve()))
    assert result['venv_in_project'] and Path(sys.prefix).resolve().is_relative_to(ROOT.resolve()), 'Use the project virtual environments'
    assert args.source or result['base_runtime_in_project'], 'Use the isolated kit environments created by Setup.cmd'
    result['installation_kind'] = 'source' if args.source else 'offline_kit'
    if args.brain:
        assert sys.version_info[:2] == (3, 12), 'Brain environment needs Python 3.12'
        import pyarrow  # must precede torch on Windows
        import torch
        result.update(torch=torch.__version__, cuda_runtime=torch.version.cuda,
                      cuda_available=torch.cuda.is_available(), pyarrow=pyarrow.__version__)
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable. Check NVIDIA driver and GPU; see docs/操作说明.md')
        # A real CUDA kernel catches incompatible devices/drivers, not just detection failures.
        probe = torch.ones(16, device='cuda').sum().item()
        assert probe == 16
        result.update(gpu=torch.cuda.get_device_name(), gpu_capability=torch.cuda.get_device_capability(),
                      compiled_architectures=torch.cuda.get_arch_list(), cuda_kernel='passed')
    else:
        assert sys.version_info[:2] == (3, 13), 'Body environment needs Python 3.13'
        import flygym
        import mujoco
        import numpy
        import pandas
        import fastapi
        import uvicorn
        import websockets
        import imageio_ffmpeg
        import pyarrow.parquet as pq
        from neuron_annotations import annotations
        from play_fly import make_sim
        assert (flygym.assets_dir / 'model/neuromechfly/compound_eye.npz').is_file()
        connectivity = ROOT / 'brain-model/data/2025_Connectivity_783.parquet'
        if not args.source or connectivity.is_file():
            assert pq.ParquetFile(connectivity).metadata.num_rows == 15091983
        else:
            result['connection_table'] = 'absent; not required for body-only source mode'
        with (ROOT / 'brain-model/data/2025_Completeness_783.csv').open(newline='') as stream:
            assert sum(1 for _ in csv.reader(stream)) - 1 == 138639
        catalog = json.loads((ROOT / 'web/activity_catalog.json').read_text(encoding='utf-8'))
        assert catalog['model_nodes'] == 138639 and len(catalog['types']) == 8846
        assert annotations().summary['typed_nodes'] == 137147
        result.update(mujoco=mujoco.__version__, numpy=numpy.__version__, pandas=pandas.__version__,
                      fastapi=fastapi.__version__, uvicorn=uvicorn.__version__, websockets=websockets.__version__,
                      ffmpeg=imageio_ffmpeg.get_ffmpeg_exe(), nodes=138639, weighted_edges=15091983,
                      named_types=8845, local_assets='passed')
        fly, sim, camera, controller, reset = make_sim()
        assert sim.mj_model.nq > 0 and sim.mj_model.ngeom > 0
        result['body_model_compile'] = 'passed'
    destination = ROOT / 'output' / 'setup'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / ('diagnostics-' + result['profile'] + '.json')).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('DIAGNOSTIC FAILED: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
