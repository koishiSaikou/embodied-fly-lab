"""Real GPU + web settings check on a dedicated process and port."""
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
name='transmitter_settings_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
folder=ROOT/'output/transmitter_controls'/name
folder.mkdir(parents=True)
for port in range(8480,8500):
    with socket.socket() as sock:
        try: sock.bind(('127.0.0.1',port))
        except OSError: continue
        break
else: raise RuntimeError('No free test port')
base=f'http://127.0.0.1:{port}'
def get(path='/api/state'):
    with urlopen(base+path,timeout=3) as response:return json.load(response)
def command(payload,expected=202):
    request=Request(base+'/api/command',data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(request,timeout=5) as response:assert response.status==expected
    except HTTPError as error:
        assert error.code==expected,error.read().decode()
def wait_for(predicate,seconds=180):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            state=get()
            if predicate(state):return state
        except OSError:pass
        assert process.poll() is None,'Own test exited; see '+str(folder)
        time.sleep(.15)
    raise TimeoutError(str(folder))

with (folder/'habitat.log').open('w',encoding='utf-8') as log:
    process=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'habitat.py'),
        '--headless','--monitor','--no-browser','--keep-open','--port',str(port),'--run-name',name],
        cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1',PYTHONUNBUFFERED='1'),stdout=log,stderr=subprocess.STDOUT)
    try:
        state=wait_for(lambda s:s.get('habitat',{}).get('time_s',0)>=.2)
        directory=Path(state['habitat']['directory'])
        schema=get('/api/brain-controls');defaults=schema['defaults']
        assert len(schema['fields'])==13 and state['habitat']['brain_controls']==defaults
        command(dict(type='pause',enabled=True))
        state=wait_for(lambda s:s.get('habitat',{}).get('paused'))
        before=state['latest']['brain_time_ms']
        changes=dict(p9_gain_a=0,p9_gain_b=.5,p9_bias_hz=10,smoothing_ms=60,feedback_enabled=False,nt_acetylcholine=0,nt_gaba=.5,nt_dopamine=1.2)
        command(dict(type='brain_settings',values=changes))
        state=wait_for(lambda s:all(s.get('habitat',{}).get('brain_controls',{}).get(k)==v for k,v in changes.items()))
        assert state['latest']['brain_time_ms']==before
        for values in [{'nt_gaba':-1},{'nt_dopamine':True},{'nt_acetylcholine':float('nan')},{'nt_unknown':1},{'nt_serotonin':2.1}]:
            command(dict(type='brain_settings',values=values),400)
        assert get()['habitat']['brain_controls']==state['habitat']['brain_controls']
        command(dict(type='pause',enabled=False))
        state=wait_for(lambda s:s['latest']['brain_time_ms']>before+40)
        h=state['habitat'];assert h['motor_drive']==[0,0]
        assert h['command']['p9_rate_hz'][0]==10
        assert abs(h['command']['p9_rate_hz'][1]-(h['command']['policy_p9_rate_hz'][1]*.5+10))<1e-9
        assert state['latest']['activity']['input_rate_hz']==h['command']['p9_rate_hz']
        assert state['latest']['drive_smoothing_ms']==60
        assert state['latest']['transmitter_gains']['acetylcholine']==0
        assert state['latest']['transmitter_gains']['gaba']==.5
        assert state['latest']['transmitter_gains']['dopamine']==1.2
        command(dict(type='brain_settings',values=defaults))
        wait_for(lambda s:s.get('habitat',{}).get('brain_controls')==defaults)
        command(dict(type='pause',enabled=True))
        wait_for(lambda s:s.get('habitat',{}).get('paused'))
        ready=dict(base=base,folder=str(folder),directory=str(directory),gate=str(folder/'ui_done'))
        (ROOT/'output/transmitter_controls/live_ready.json').write_text(json.dumps(ready,indent=2),encoding='utf-8')
        print('API/GPU controls passed; paused own instance ready for browser: '+base,flush=True)
        deadline=time.monotonic()+360
        while not (folder/'ui_done').exists():
            assert process.poll() is None
            if time.monotonic()>deadline:raise TimeoutError('Browser phase timed out')
            time.sleep(.5)
        command(dict(type='stop'))
        wait_for(lambda s:s['status']=='finished' and (directory/'report.json').exists())
        events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
        edits=[event for event in events if event['type']=='brain_settings']
        assert len(edits)>=2 and edits[0]['after']['smoothing_ms']==60
        result=dict(status='passed',real_gpu=True,paused_updates=True,invalid_payloads_rejected=5,
                    actual_stimulus_mapping=True,feedback_zero_brain_continues=True,
                    worker_smoothing_applied=True,transmitter_gains_applied=True,defaults_restored=True,logged_changes=len(edits))
        (folder/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2),flush=True)
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15)
            process.wait(timeout=15)
