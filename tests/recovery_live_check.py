"""Owned HTTP/body/GPU recovery checks; synthetic failures are explicitly labelled."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import _thread

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from delivery.common import environment
from startup_support import available_port
from startup_live_check import get, post, wait, cleanup_owned


def probe(folder, kind, port, name):
    if (folder/'close').exists():raise ValueError('Use a fresh probe folder; an old close marker is present')
    import habitat
    import brain_client
    class TrackedClient(brain_client.BrainClient):
        def __init__(self, directory, seed, **kwargs):
            if kind in ('startup_stall','step_stall','step_exit','bad_json'):
                code='import sys,time,json\n'
                if kind=='startup_stall':code+='time.sleep(60)'
                else:
                    code+='print(json.dumps({"ready":{}}),flush=True)\nsys.stdin.readline()\n'
                    code+=dict(step_stall='time.sleep(60)',step_exit='sys.exit(17)',bad_json='print("oops",flush=True)')[kind]
                kwargs['worker_command']=[sys.executable,'-u','-B','-c',code]
            super().__init__(directory,seed,**kwargs)
            (folder/'brain-pid.json').write_text(json.dumps(dict(pid=self.process.pid,directory=str(directory))),encoding='utf-8')
    habitat.BrainClient=TrackedClient
    if kind=='finalize_fault':
        original_renderer=habitat.mj.Renderer
        class Renderer:
            def __init__(self,*args,**kwargs):self.renderer=original_renderer(*args,**kwargs)
            def __getattr__(self,key):return getattr(self.renderer,key)
            def close(self):
                self.renderer.close()
                raise RuntimeError('synthetic renderer close failure (after real close)')
        habitat.mj.Renderer=Renderer
        original_save=habitat.write_json
        def save(path,value):
            if Path(path).name=='report.json':raise OSError('synthetic report save failure')
            return original_save(path,value)
        habitat.write_json=save
    def stopping():
        while not (folder/'close').exists():time.sleep(.05)
        _thread.interrupt_main()
    threading.Thread(target=stopping,daemon=True).start()
    sys.argv=['habitat.py','--headless','--monitor','--no-browser','--keep-open','--port',str(port),
              '--run-name',name,'--seconds','.04' if kind=='finalize_fault' else '0',
              '--brain-startup-timeout','.4' if kind=='startup_stall' else '300',
              '--brain-step-timeout','.4' if kind!='real' and kind!='finalize_fault' else '2']
    restarting=False
    while habitat.main(restarting):restarting=True


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--probe':
        probe(Path(sys.argv[2]),sys.argv[3],int(sys.argv[4]),sys.argv[5]);return
    import psutil
    tag=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder=ROOT/'output/recovery'/tag;folder.mkdir(parents=True)
    results=[]
    for kind in ('startup_stall','step_stall','step_exit','bad_json','finalize_fault','real'):
        case=folder/kind;case.mkdir()
        port=available_port(8490);url=f'http://127.0.0.1:{port}/'
        name='recovery_'+kind+'_'+tag
        with (case/'console.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--probe',str(case),kind,str(port),name],
                                     cwd=ROOT,env=environment(),stdout=log,stderr=subprocess.STDOUT)
            try:
                directory=ROOT/'output/habitat'/name
                if kind=='real':
                    state=wait(process,lambda s:s.get('habitat',{}).get('time_s',0)>=.04,url)
                    assert Path(state['habitat']['directory'])==directory
                    post(url,dict(type='pause',enabled=True))
                    paused=wait(process,lambda s:s.get('habitat',{}).get('paused'),url)
                    time.sleep(2.5)  # Exceeds the 2 s step deadline, with no pending request.
                    still=get(url)
                    assert still['status']=='running' and not still.get('brain_wait')
                    assert still['habitat']['time_s']==paused['habitat']['time_s']
                    assert still['latest']['brain_time_ms']==paused['latest']['brain_time_ms']
                    # Only this just-created process's recorded descendant can be killed.
                    identity=json.loads((case/'brain-pid.json').read_text())
                    child=psutil.Process(identity['pid'])
                    assert child.pid in {c.pid for c in psutil.Process(process.pid).children(recursive=True)}
                    assert identity['directory']==str(directory)
                    child.kill();child.wait(timeout=10)
                    post(url,dict(type='pause',enabled=False))
                state=wait(process,lambda s:s['status']=='error' and s.get('recovery',{}).get('can_restart'),url)
                assert Path(state.get('directory'))==directory
                assert state['recovery']['phase']=='complete'
                report_path=directory/('error.json' if kind=='finalize_fault' else 'report.json')
                report=json.loads(report_path.read_text(encoding='utf-8'))
                assert report['status']=='error'
                assert (directory/'trajectory.csv').exists()
                if kind=='finalize_fault':
                    assert report['body_model_seconds']==.04
                    stages={e['stage'] for e in report['cleanup_errors']}
                    assert {'renderer_close','save_report'}.issubset(stages)
                else:
                    assert report['failure']['type']=='BrainResponseError'
                    assert state['failure']['message']==report['failure']['message']
                if kind=='real':
                    old_report=report_path.read_bytes()
                    time.sleep(.7);assert get(url)['status']=='error'  # No automatic reset.
                    post(url,dict(type='restart'))
                    fresh=wait(process,lambda s:s.get('habitat',{}).get('directory') not in (None,str(directory)) and s['status']=='running',url)
                    fresh_directory=Path(fresh['habitat']['directory'])
                    assert fresh_directory.is_relative_to(ROOT/'output/habitat')
                    post(url,dict(type='stop'))
                    done=wait(process,lambda s:s['status']=='finished' and s.get('recovery',{}).get('can_restart'),url)
                    assert done.get('failure') is None
                    assert report_path.read_bytes()==old_report
                    new_report=json.loads((fresh_directory/'report.json').read_text(encoding='utf-8'))
                    assert new_report['status']=='finished' and new_report['body_model_seconds']>0
                    assert new_report['last_successful_brain_time_ms']>0
                (case/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
                (case/'close').touch()
                exit_code=process.wait(timeout=20)
                assert exit_code==(0 if kind=='real' else 1),exit_code
                if (case/'brain-pid.json').exists():
                    identity=json.loads((case/'brain-pid.json').read_text())
                    assert not psutil.pid_exists(identity['pid'])
                results.append(dict(case=kind,status='passed',exit_code=exit_code,
                    body_seconds=report['body_model_seconds'],last_brain_ms=report['last_successful_brain_time_ms'],
                    real_cuda=kind in ('real','finalize_fault'),synthetic_failure=kind!='real',
                    pause_checked=kind=='real',manual_restart_checked=kind=='real',directory=str(directory)))
                print(f'Recovery check passed: {kind}',flush=True)
            finally:cleanup_owned(process)
    (folder/'report.json').write_text(json.dumps(dict(status='passed',cases=results),ensure_ascii=False,indent=2),encoding='utf-8')
    print(folder/'report.json',flush=True)


if __name__=='__main__':main()
