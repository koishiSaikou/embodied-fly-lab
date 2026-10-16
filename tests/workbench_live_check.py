"""Dedicated GPU + multi-sensory editor API check. Never modifies another service."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
name='workbench_check_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
folder=ROOT/'output/arena'/name;folder.mkdir(parents=True)
for port in range(8480,8500):
    with socket.socket() as sock:
        try:sock.bind(('127.0.0.1',port))
        except OSError:continue
        break
else:raise RuntimeError('No free own test port')
base=f'http://127.0.0.1:{port}'
def get(path='/api/state',binary=False):
    with urlopen(base+path,timeout=3) as response:return response.read() if binary else json.load(response)
def command(payload,expected=202):
    request=Request(base+'/api/command',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(request,timeout=5) as response:assert response.status==expected
    except HTTPError as error:assert error.code==expected,error.read().decode()
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
        assert state['metadata']['neurons']==138639 and len(state['habitat']['config']['elements'])==7
        assert get('/api/arena')['max_elements']==24
        command(dict(type='pause',enabled=True));state=wait_for(lambda s:s.get('habitat',{}).get('paused'))
        directory=Path(state['habitat']['directory']);before=state['latest']['brain_time_ms'];position=state['habitat']['sense']['position_mm']
        retina_before=get('/api/retina.png',True)
        command(dict(type='arena_edit',action='add',values=dict(kind='fruit',x=-8,y=-10,radius=1.5)))
        state=wait_for(lambda s:s['habitat']['arena_revision']>=1)
        assert len(state['habitat']['config']['elements'])==8
        assert state['latest']['brain_time_ms']==before
        # mj_forward refreshes derived body positions after the last physics tick.
        # The actual qpos/time no-reset invariant is checked bit-for-bit in verify_arena.py.
        assert max(abs(a-b) for a,b in zip(state['habitat']['sense']['position_mm'],position))<.01
        assert get('/api/retina.png',True)!=retina_before
        invalid=[dict(type='arena_edit',action='update',id='food',values=dict(x=30)),
                 dict(type='arena_edit',action='delete',id='food'),
                 dict(type='arena_edit',action='update',id='food',values=dict(x=-3,y=4)),
                 dict(type='sensory_settings',values=dict(thermal_enabled=1)),
                 dict(type='sensory_settings',values=dict(fake=True))]
        for payload in invalid:command(payload,400)
        assert get()['habitat']['arena_revision']==1
        off={key:False for key in ('light_enabled','humidity_enabled','thermal_enabled','bitter_enabled')}
        command(dict(type='sensory_settings',values=off))
        state=wait_for(lambda s:all(s['habitat']['config'][key] is False for key in off))
        assert state['habitat']['sense']['channels']['humidity']==[0,0]
        assert max(state['habitat']['sense']['channels_raw']['humidity'])>0
        danger=next(e for e in state['habitat']['config']['elements'] if e['kind']=='danger')
        danger_original=dict(x=danger['x'],y=danger['y'],radius=danger['radius'])
        command(dict(type='arena_edit',action='update',id=danger['id'],values=dict(x=position[0],y=position[1],radius=3)))
        state=wait_for(lambda s:s['habitat']['arena_revision']>=2)
        assert state['habitat']['sense']['channels_raw']['bitter']>0
        assert state['habitat']['sense']['channels']['bitter']==0
        command(dict(type='sensory_settings',values=dict(thermal_enabled=True,bitter_enabled=True)))
        command(dict(type='pause',enabled=False))
        state=wait_for(lambda s:s['habitat']['command']['state']=='hazard_escape' and s['latest']['brain_time_ms']>before)
        assert all(value<0 for value in state['habitat']['command']['desired_drive'])
        command(dict(type='pause',enabled=True));wait_for(lambda s:s['habitat'].get('paused'))
        command(dict(type='arena_edit',action='update',id=danger['id'],values=danger_original))
        wait_for(lambda s:s['habitat']['arena_revision']>=3)
        command(dict(type='sensory_settings',values=dict(light_enabled=True,humidity_enabled=True)))
        state=wait_for(lambda s:s['habitat']['config']['light_enabled'] and s['habitat']['config']['humidity_enabled'])
        ready=dict(base=base,folder=str(folder),directory=str(directory),gate=str(folder/'ui_done'))
        (ROOT/'output/arena/live_ready.json').write_text(json.dumps(ready,indent=2),encoding='utf-8')
        print('Real GPU scene/sensory API passed; paused browser instance at '+base,flush=True)
        deadline=time.monotonic()+1800
        while not (folder/'ui_done').exists():
            assert process.poll() is None
            if time.monotonic()>deadline:raise TimeoutError('Browser phase timeout')
            time.sleep(.5)
        command(dict(type='stop'))
        wait_for(lambda s:s['status']=='finished' and (directory/'report.json').exists())
        report=json.loads((directory/'report.json').read_text())
        assert report['arena_revision']>=3 and len(report['arena_final']['elements'])>=7
        assert (directory/'senses.jsonl').stat().st_size>0
        events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
        assert len([e for e in events if e['type']=='arena_edit'])>=3
        command(dict(type='restart'))
        state=wait_for(lambda s:s.get('habitat',{}).get('directory') not in (None,str(directory)) and s.get('habitat',{}).get('time_s',0)>=.1)
        assert len(state['habitat']['config']['elements'])==7 and state['habitat']['arena_revision']==0
        restarted=Path(state['habitat']['directory'])
        command(dict(type='stop'));wait_for(lambda s:s['status']=='finished' and (restarted/'report.json').exists())
        result=dict(status='passed',full_gpu_brain=True,paused_editor_keeps_body_and_brain_state=True,
                    real_retina_changes=True,invalid_http_edits_rejected=len(invalid),
                    sensory_ablation_preserves_raw=True,hazard_drives_actual_p9_command=True,
                    scene_and_senses_logged=True,restart_restores_seven_elements=True)
        (folder/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2),flush=True)
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15)
            process.wait(timeout=15)
