"""Short end-to-end verification of an installed Windows kit, with real simulation."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, environment, python_for
from launch import available_port


def main():
    started = time.monotonic()
    env = environment()
    folder = ROOT / 'output/self_test' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True)

    def run(kind, args, label):
        with (folder / (label + '.log')).open('w', encoding='utf-8') as log:
            subprocess.run([str(python_for(kind)), *[str(a) for a in args]], cwd=ROOT, env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)

    print('1/3 Body, physics, local assets and FFmpeg...', flush=True)
    run('body', [ROOT / 'play_fly.py', '--headless', '--seconds', '0.1', '--video'], 'body')
    body = json.loads((ROOT / 'output/body_report.json').read_text(encoding='utf-8'))
    assert body['finite_state'] and body['rendered_frames'] > 0
    print('2/3 Full CUDA brain and sparse-cache construction...', flush=True)
    run('brain', [ROOT / 'brain_worker.py', '--probe'], 'brain')
    brain = json.loads((ROOT / 'output/brain_report.json').read_text(encoding='utf-8'))
    assert brain['metadata']['neurons'] == 138639 and brain['metadata']['weighted_edges'] == 15091983
    assert brain['stimulated']['total_spikes'] > brain['baseline']['total_spikes']
    print('3/3 Full brain/body/vision loop and localhost HTTP assets...', flush=True)
    port = available_port(8480)
    name = 'kit_smoke_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    http = dict(scene=False, state=False, retina=False, catalog=False, javascript=False)
    command = [str(python_for('body')), str(ROOT / 'habitat.py'), '--headless', '--monitor',
               '--no-browser', '--seconds', '0.2', '--port', str(port), '--run-name', name]
    with (folder / 'habitat.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 180
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError('Habitat smoke timed out; see ' + str(folder / 'habitat.log'))
                try:
                    base = f'http://127.0.0.1:{port}'
                    with urlopen(base + '/api/state', timeout=1) as response:
                        state = json.load(response)
                    http['state'] |= bool(state.get('latest'))
                    if not http['scene']:
                        with urlopen(base + '/api/scene', timeout=2) as response:
                            scene = json.load(response)
                        http['scene'] = bool(scene.get('geoms'))
                    if not http['retina']:
                        with urlopen(base + '/api/retina.png', timeout=2) as response:
                            http['retina'] = response.read(8) == b'\x89PNG\r\n\x1a\n'
                    if not http['catalog']:
                        with urlopen(base + '/assets/activity_catalog.json', timeout=2) as response:
                            catalog = json.load(response)
                        http['catalog'] = catalog['model_nodes'] == 138639 and len(catalog['types']) == 8846
                    if not http['javascript']:
                        with urlopen(base + '/assets/neural_activity.js', timeout=2) as response:
                            http['javascript'] = 'javascript' in response.headers.get('Content-Type', '')
                except OSError:
                    pass  # startup and graceful server shutdown can interrupt a poll
                time.sleep(0.1)
            assert process.returncode == 0, 'Habitat failed; see ' + str(folder / 'habitat.log')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
    report = json.loads((ROOT / 'output/habitat' / name / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'finished' and abs(report['body_model_seconds'] - 0.2) < 1e-6
    assert report['brain_enabled'] and report['minimum_upright'] > 0.8
    assert all(http.values()), http
    result = dict(status='passed', root=str(ROOT), wall_seconds=time.monotonic() - started,
                  body=body, brain=dict(baseline_spikes=brain['baseline']['total_spikes'],
                                       stimulated_spikes=brain['stimulated']['total_spikes'],
                                       active_neurons=brain['stimulated']['active_neurons'],
                                       gpu=brain['metadata']['device']),
                  habitat=dict(run=name, model_seconds=report['body_model_seconds'],
                               minimum_upright=report['minimum_upright']), http=http)
    (folder / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('Logs and result:', folder, flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('SMOKE TEST FAILED:', exc, file=sys.stderr)
        raise SystemExit(1)
