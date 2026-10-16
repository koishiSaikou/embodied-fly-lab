"""Own GPU startup checks. Browser callback is intercepted; no unrelated service is controlled."""
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from delivery.common import environment
from startup_support import available_port


def get(url):
    with urlopen(url + 'api/state', timeout=3) as response:
        return json.load(response)


def post(url, message):
    with urlopen(Request(url + 'api/command', data=json.dumps(message).encode(),
                         headers={'Content-Type':'application/json'}, method='POST'), timeout=5) as response:
        assert response.status == 202


def wait(process, predicate, url, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            state = get(url)
            if predicate(state): return state
        except OSError:
            pass
        assert process.poll() is None, 'Owned test exited early'
        time.sleep(.15)
    raise TimeoutError('Owned startup test timed out')


def cleanup_owned(process):
    if process.poll() is None:
        import psutil
        descendants = psutil.Process(process.pid).children(recursive=True)
        # Only descendants of the process just created by this test are eligible.
        for child in reversed(descendants):
            try: child.terminate()
            except psutil.NoSuchProcess: pass
        process.terminate(); process.wait(timeout=15)


def console_child(folder, port, run_name, loading=False, batch=False):
    """Executed only in a dedicated hidden Windows console created by main()."""
    import ctypes
    import psutil
    kernel = ctypes.windll.kernel32
    signal.signal(signal.SIGINT, lambda *_: None)
    env = environment(); env['FLYWIRE_NONINTERACTIVE']='1'
    url = f'http://127.0.0.1:{port}/'
    command=([str(ROOT/'.venv/Scripts/python.exe'),'-B',str(ROOT/'launcher.py'),'habitat'] if not batch else
             ['cmd','/d','/c','Start-Habitat.cmd'])
    command.extend(['--port',str(port),'--no-browser','--no-video','--run-name',run_name])
    with (folder/'console-launch.log').open('w',encoding='utf-8') as log:
        process = subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        try:
            wait(process,(lambda state:state['status']=='loading') if loading else
                 (lambda state:state.get('habitat',{}).get('time_s',0)>=.12),url)
            attached = (ctypes.c_uint32 * 128)()
            count = kernel.GetConsoleProcessList(attached,128)
            owner=json.loads((folder/'console-owner.json').read_text(encoding='utf-8'))
            console_root=psutil.Process(owner['root_pid'])
            assert console_root.parent().pid==owner['driver_pid']
            if loading:
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    workers=[child for child in console_root.children(recursive=True)
                             if 'brain_worker.py' in ' '.join(child.cmdline())]
                    if workers:
                        assert get(url)['status']=='loading'
                        break
                    assert process.poll() is None
                    time.sleep(.025)
                else:raise TimeoutError('Brain worker did not enter the loading window')
            allowed = {console_root.pid} | {child.pid for child in console_root.children(recursive=True)}
            assert os.getpid() in allowed
            assert 1 < count <= 128
            (folder/'console-processes.json').write_text(json.dumps(dict(attached=list(attached[:count]),allowed=sorted(allowed))),encoding='utf-8')
            assert set(attached[:count]).issubset(allowed), 'Console isolation check failed; no event sent'
            assert owner['driver_pid'] not in set(attached[:count])
            assert process.pid in set(attached[:count])
            tracked_apps={child.pid:child.create_time() for child in console_root.children(recursive=True)
                          if child.pid!=os.getpid() and any(name in ' '.join(child.cmdline())
                          for name in ('launcher.py','habitat.py','brain_worker.py'))}
            assert tracked_apps
            assert kernel.GenerateConsoleCtrlEvent(0,0), 'Cannot send Ctrl+C in owned console'
            code = process.wait(timeout=45)
            report = json.loads((ROOT/'output/habitat'/run_name/'report.json').read_text(encoding='utf-8'))
            assert report['status']=='interrupted',report['status']
            assert code==130,code
            records = [json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'output/launch').glob('*_habitat.json')]
            record = next(r for r in records if run_name in r.get('command',[]))
            assert record['status']=='stopped' and record['interrupted']
            assert (ROOT/'output/habitat'/run_name/'trajectory.csv').stat().st_size>0
            remaining=[]
            for pid,created in tracked_apps.items():
                try:
                    child=psutil.Process(pid)
                    if child.create_time()==created and child.is_running():remaining.append(pid)
                except psutil.NoSuchProcess:pass
            assert not remaining,remaining
            (folder/('console-batch-result.json' if batch else 'console-loading-result.json' if loading else 'console-result.json')).write_text(json.dumps(dict(status='passed',exit_code=code,
                report_status=report['status'],model_seconds=report['body_model_seconds'],
                console_processes=count,isolated_console=True,remaining_children=remaining,loading=loading,batch=batch),indent=2),encoding='utf-8')
        finally:
            cleanup_owned(process)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--console-child':
        console_child(Path(sys.argv[2]),int(sys.argv[3]),sys.argv[4],'loading' in sys.argv[5:],'batch' in sys.argv[5:]);return
    tag=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder=ROOT/'output/startup'/tag;folder.mkdir(parents=True)
    env=environment();env['FLYWIRE_NONINTERACTIVE']='1'
    python=ROOT/'.venv/Scripts/python.exe'
    port=available_port(8480);url=f'http://127.0.0.1:{port}/'
    run_name='startup_browser_'+tag
    # Intercept only OS browser opening. The HTTP server, body, brain and restart remain real.
    probe=folder/'browser_probe.py'
    probe.write_text('''import _thread,json,runpy,sys,threading,time,webbrowser
from pathlib import Path
from urllib.request import urlopen
root=Path(sys.argv[1]);folder=Path(sys.argv[2]);port=sys.argv[3];name=sys.argv[4]
sys.path.insert(0,str(root));calls=[]
def browser(url,new=2):
    with urlopen(url+'api/scene',timeout=5) as r:scene=json.load(r)
    with urlopen(url+'api/state',timeout=5) as r:state=json.load(r)
    assert scene.get('geoms')
    calls.append(dict(url=url,scene_ready=True,status=state['status']))
    (folder/'browser-calls.json').write_text(json.dumps(calls),encoding='utf-8')
    return True
webbrowser.open=browser
def stopping():
    while not (folder/'close-browser-probe').exists():time.sleep(.1)
    _thread.interrupt_main()
threading.Thread(target=stopping,daemon=True).start()
sys.argv=[str(root/'habitat.py'),'--headless','--monitor','--keep-open','--seconds','.04','--port',port,'--run-name',name]
runpy.run_path(str(root/'habitat.py'),run_name='__main__')
''',encoding='utf-8')
    with (folder/'browser-probe.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([str(python),'-B',str(probe),str(ROOT),str(folder),str(port),run_name],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            first=wait(process,lambda state:state['status']=='finished' and state.get('habitat',{}).get('time_s',0)>=.04,url)
            old_directory=first['habitat']['directory']
            assert Path(old_directory).resolve()==(ROOT/'output/habitat'/run_name).resolve(), 'Refusing to control a different experiment'
            post(url,dict(type='restart'))
            second=wait(process,lambda state:state['status']=='finished' and state.get('habitat',{}).get('directory') not in (None,old_directory),url)
            calls=json.loads((folder/'browser-calls.json').read_text(encoding='utf-8'))
            assert len(calls)==1 and calls[0]['url']==url and calls[0]['scene_ready']
            assert second['latest']['brain_time_ms']>=40
            (folder/'close-browser-probe').touch()
            assert process.wait(timeout=30)==0
        finally:
            cleanup_owned(process)
    print('Headless browser-ready and no-reopen-on-restart checks passed.',flush=True)

    # Force or observe an occupied default; never close or command an existing service.
    listener=socket.socket()
    try:
        listener.bind(('127.0.0.1',8472));listener.listen()
    except OSError:
        listener.close();listener=None
    run_name='startup_fallback_'+tag
    try:
        with (folder/'cmd-fallback.log').open('w',encoding='utf-8') as log:
            result=subprocess.run(['cmd','/d','/c','Start-Habitat.cmd','--seconds','.04','--exit-when-finished',
                                   '--no-browser','--no-video','--run-name',run_name],cwd=ROOT,env=env,
                                  stdout=log,stderr=subprocess.STDOUT,timeout=180)
        assert result.returncode==0,result.returncode
    finally:
        if listener:listener.close()
    records=[json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'output/launch').glob('*_habitat.json')]
    fallback=next(r for r in records if run_name in r.get('command',[]))
    assert fallback['url']!='http://127.0.0.1:8472/' and fallback['status']=='finished'
    assert 'Browser opened:' not in (folder/'cmd-fallback.log').read_text(encoding='utf-8')
    print('Real CMD fallback, no-browser and finite save/exit checks passed.',flush=True)

    assert sys.platform=='win32','Console test requires Windows'
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    for loading,batch in ((False,False),(True,False),(False,True)):
        port=available_port(8480);run_name=('startup_batch_' if batch else 'startup_loading_' if loading else 'startup_ctrlc_')+tag
        with (folder/('loading-helper.log' if loading else 'console-helper.log')).open('w',encoding='utf-8') as log:
            helper=subprocess.Popen([str(python),'-B',str(Path(__file__).resolve()),'--console-child',str(folder),str(port),run_name,*(['loading'] if loading else ['batch'] if batch else [])],
                                    cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup)
            (folder/'console-owner.json').write_text(json.dumps(dict(root_pid=helper.pid,driver_pid=os.getpid())),encoding='utf-8')
            try:
                assert helper.wait(timeout=180)==0,'See console-helper.log / loading-helper.log and console-launch.log'
            finally:
                cleanup_owned(helper)
    console=json.loads((folder/'console-result.json').read_text(encoding='utf-8'))
    loading=json.loads((folder/'console-loading-result.json').read_text(encoding='utf-8'))
    batch=json.loads((folder/'console-batch-result.json').read_text(encoding='utf-8'))
    result=dict(status='passed',browser_calls=calls,headless_browser_ready=True,restart_browser_calls=1,
                default_port_fallback=True,fallback_url=fallback['url'],no_browser=True,
                finite_cmd_exit_code=fallback['exit_code'],ctrl_c=console,startup_ctrl_c=loading,batch_ctrl_c=batch,original_services_modified=False)
    (folder/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
