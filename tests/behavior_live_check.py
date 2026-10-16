"""Own short GPU experiment; never changes another running simulation."""
from datetime import datetime
import json,os,socket,subprocess,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
name='behavior_check_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
folder=ROOT/'output/behavior'/name;folder.mkdir(parents=True)
for port in range(8480,8500):
    with socket.socket() as sock:
        try:sock.bind(('127.0.0.1',port))
        except OSError:continue
        break
else:raise RuntimeError('No free test port')
base=f'http://127.0.0.1:{port}'
def get(path='/api/state',binary=False):
    with urlopen(base+path,timeout=5) as r:return r.read() if binary else json.load(r)
def command(payload,status=202):
    try:
        with urlopen(Request(base+'/api/command',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=5) as r:assert r.status==status
    except HTTPError as e:assert e.code==status,e.read().decode()
def wait(predicate,seconds=240):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            s=get()
            if predicate(s):return s
        except OSError:pass
        assert process.poll() is None,'Own process exited: '+str(folder)
        time.sleep(.15)
    raise TimeoutError(str(folder))

with (folder/'habitat.log').open('w',encoding='utf-8') as log:
    process=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'habitat.py'),'--headless','--monitor','--no-browser','--keep-open','--port',str(port),'--run-name',name],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,PYTHONUTF8='1',PYTHONUNBUFFERED='1'))
    try:
        state=wait(lambda s:s.get('habitat',{}).get('time_s',0)>=.3)
        assert state['metadata']['neurons']==138639
        command(dict(type='pause',enabled=True));state=wait(lambda s:s.get('habitat',{}).get('paused'))
        stamp=state['latest']['brain_time_ms'];directory=Path(state['habitat']['directory']);before_pos=state['habitat']['sense']['position_mm']
        assert len(get('/api/behavior-controls')['fields'])==18
        invalid=[dict(type='behavior_settings',values=dict(mode='bad')),dict(type='behavior_settings',values=dict(wind_strength=True)),dict(type='behavior_settings',values=dict(learning_rate=float('nan'))),
                 dict(type='behavior_needs',values=dict(thirst=1.1)),dict(type='behavior_needs',values=dict(fatigue=True)),dict(type='behavior_reward',reward=2),dict(type='behavior_settings',values=dict(fake=1))]
        for payload in invalid:command(payload,400)
        assert get()['habitat']['behavior_revision']==0
        command(dict(type='behavior_settings',values=dict(mode='groom',wind_enabled=False)))
        command(dict(type='vision',enabled=False))
        command(dict(type='behavior_needs',values=dict(hunger=.2,thirst=.2,fatigue=.1,dust=.9)))
        command(dict(type='arena_edit',action='update',id='food',values=dict(x=before_pos[0],y=before_pos[1],radius=3)))
        state=wait(lambda s:s['habitat']['behavior_revision']>=2 and s['habitat']['arena_revision']>=1)
        assert state['latest']['brain_time_ms']==stamp
        assert state['habitat']['behavior']['needs']['dust']==.9
        retina=get('/api/retina.png',True)
        command(dict(type='arena_edit',action='add',values=dict(kind='cool',x=-5,y=-14,radius=1.5)))
        state=wait(lambda s:s['habitat']['arena_revision']>=2)
        assert get('/api/retina.png',True)!=retina
        assert state['habitat']['sense']['channels_raw']['wind']['strength']>0 and state['habitat']['sense']['channels']['wind']['strength']==0
        command(dict(type='pause',enabled=False))
        state=wait(lambda s:s['habitat']['command']['state']=='groom' and s['latest']['brain_time_ms']>=stamp+1400)
        assert state['habitat']['sense']['foreleg_head_distance_mm'][1]<1.,state['habitat']['sense']
        assert state['habitat']['sense']['upright']>.9
        command(dict(type='pause',enabled=True));state=wait(lambda s:s['habitat']['paused'])
        value=state['habitat']['behavior']['odor_values']['sugar'];revision=state['habitat']['behavior_revision'];stamp=state['latest']['brain_time_ms']
        command(dict(type='behavior_reward',reward=1))
        state=wait(lambda s:s['habitat']['behavior_revision']>revision)
        assert state['habitat']['behavior']['odor_values']['sugar']>value
        assert state['latest']['brain_time_ms']==stamp
        command(dict(type='behavior_memory_reset'))
        state=wait(lambda s:s['habitat']['behavior']['learning_updates']==0 and s['habitat']['behavior']['odor_values']['sugar']==.5)
        command(dict(type='behavior_settings',values=dict(mode='rest')))
        state=wait(lambda s:s['habitat']['config']['behavior_controls']['mode']=='rest')
        command(dict(type='pause',enabled=False));wait(lambda s:s['habitat']['command']['state']=='rest')
        command(dict(type='pause',enabled=True));wait(lambda s:s['habitat']['paused'])
        ready=dict(base=base,folder=str(folder),directory=str(directory),gate=str(folder/'ui_done'))
        (ROOT/'output/behavior/live_ready.json').write_text(json.dumps(ready,indent=2),encoding='utf-8')
        print('Own full-GPU behavior checks passed; paused browser at '+base,flush=True)
        deadline=time.monotonic()+1800
        while not (folder/'ui_done').exists():
            assert process.poll() is None
            if time.monotonic()>deadline:raise TimeoutError('UI phase')
            time.sleep(.5)
        command(dict(type='stop'));wait(lambda s:s['status']=='finished' and (directory/'report.json').exists())
        report=json.loads((directory/'report.json').read_text());assert report['behavior_revision']>=5 and report['groom_ik_errors_mm']
        assert (directory/'behavior.jsonl').stat().st_size>0
        events=[json.loads(row) for row in (directory/'events.jsonl').read_text().splitlines()]
        assert any(e['type']=='behavior_reward' and e['applied'] for e in events)
        command(dict(type='restart'));state=wait(lambda s:s.get('habitat',{}).get('directory') not in (None,str(directory)) and s.get('habitat',{}).get('time_s',0)>=.1)
        assert state['habitat']['config']['behavior_controls']['mode']=='auto' and state['habitat']['behavior_revision']==0
        restarted=Path(state['habitat']['directory']);command(dict(type='stop'));wait(lambda s:s['status']=='finished' and (restarted/'report.json').exists())
        result=dict(status='passed',full_gpu_brain=True,invalid_http_rejected=len(invalid),paused_edits_preserve_brain_time=True,cool_prop_changes_actual_retina=True,raw_wind_preserved=True,
                    real_foreleg_grooming=True,reward_changes_actual_memory=True,clear_memory=True,rest_command=True,behavior_log_and_events_saved=True,restart_defaults=True)
        (folder/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2),flush=True)
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15);process.wait(timeout=15)
