"""Dedicated continuous-mode lifecycle check; never controls an existing service."""
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
name = 'continuous_check_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
folder = ROOT / 'output' / name
folder.mkdir()
port = None
for candidate in range(8480, 8500):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', candidate))
        except OSError:
            continue
        port = candidate
        break
assert port is not None
base = f'http://127.0.0.1:{port}'
env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
python = str(ROOT / '.venv/Scripts/python.exe')

def get_state():
    with urlopen(base + '/api/state', timeout=2) as response:
        return json.load(response)

def command(**payload):
    request = Request(base + '/api/command', data=json.dumps(payload).encode(),
                      headers={'Content-Type':'application/json'}, method='POST')
    with urlopen(request, timeout=3) as response:
        assert response.status == 202

def wait_for(predicate, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            state = get_state()
            if predicate(state):
                return state
        except OSError:
            pass
        if process.poll() is not None:
            raise RuntimeError('Own test process exited; see ' + str(folder))
        time.sleep(.15)
    raise TimeoutError('Own test timed out; see ' + str(folder))

def saved(state, directory):
    return state.get('status') == 'finished' and (directory / 'report.json').is_file()

with (folder / 'continuous.log').open('w', encoding='utf-8') as log:
    process = subprocess.Popen([python, str(ROOT / 'habitat.py'), '--headless', '--monitor',
                                '--keep-open', '--no-browser', '--port', str(port), '--run-name', name],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        state = wait_for(lambda s: s.get('habitat', {}).get('time_s', 0) >= .2)
        directory = Path(state['habitat']['directory'])
        config = json.loads((directory / 'config.json').read_text())
        assert config['arguments']['seconds'] == 0
        assert state['status'] == 'running'
        command(type='pause', enabled=True)
        state = wait_for(lambda s: s.get('habitat', {}).get('paused') is True)
        paused_time = state['habitat']['time_s']
        time.sleep(.8)
        assert get_state()['habitat']['time_s'] == paused_time
        command(type='pause', enabled=False)
        wait_for(lambda s: s.get('habitat', {}).get('time_s', 0) > paused_time + .02)
        command(type='stop')
        wait_for(lambda s: saved(s, directory))
        first = json.loads((directory / 'report.json').read_text())
        command(type='restart')
        state = wait_for(lambda s: s.get('habitat', {}).get('directory') not in (None, str(directory))
                         and s.get('habitat', {}).get('time_s', 0) >= .1)
        second_directory = Path(state['habitat']['directory'])
        command(type='stop')
        wait_for(lambda s: saved(s, second_directory))
        second = json.loads((second_directory / 'report.json').read_text())
        assert first['status'] == second['status'] == 'finished'
        assert directory != second_directory and (directory / 'trajectory.csv').is_file()
        print('Continuous, pause/resume, stop/save, same-port restart: passed', flush=True)
    finally:
        if process.poll() is None:
            # Only this newly spawned test process tree; both saved experiments have closed their brain worker.
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=15)
            process.wait(timeout=15)

timed_name = name + '_timed'
with (folder / 'timed.log').open('w', encoding='utf-8') as log:
    subprocess.run([python, str(ROOT / 'habitat.py'), '--headless', '--no-browser',
                    '--seconds', '.2', '--run-name', timed_name], cwd=ROOT, env=env,
                   stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
timed = json.loads((ROOT / 'output/habitat' / timed_name / 'report.json').read_text())
assert timed['status'] == 'finished' and abs(timed['body_model_seconds'] - .2) < 1e-9
result = dict(status='passed', continuous_default_seconds=0, pause_resume=True,
              manual_stop_saved=True, same_service_restart=True, preserved_first_run=True,
              first_seconds=first['body_model_seconds'], second_seconds=second['body_model_seconds'],
              finite_duration_seconds=timed['body_model_seconds'])
(folder / 'report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2), flush=True)
print(str(folder), flush=True)
