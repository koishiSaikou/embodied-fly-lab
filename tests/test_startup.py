"""Startup contracts: port conflicts, browser fallback, wrappers and graceful parent."""
import io
import json
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import launcher
import startup_support


class StartupTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache/tests').mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='Startup With Spaces ', dir=ROOT / '.cache/tests')
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(ROOT / '.cache/tests')

    def tearDown(self):
        self.temporary.cleanup()

    def test_occupied_default_falls_forward_without_touching_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            preferred = listener.getsockname()[1]
            if preferred >= 65500:
                self.skipTest('OS selected a port at the upper boundary')
            chosen = startup_support.available_port(preferred)
            self.assertGreater(chosen, preferred)
            self.assertEqual(listener.getsockname()[1], preferred)

    def test_explicit_busy_port_fails_without_spawning_app(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            with patch.object(launcher, 'ROOT', self.root), patch.object(launcher, 'preflight'), patch.object(launcher.subprocess, 'Popen') as spawn:
                code = launcher.main(['habitat', '--port', str(listener.getsockname()[1])])
                self.assertEqual(code, 1); spawn.assert_not_called()
            result = json.loads(next((self.root / 'output/launch').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'failed')
            self.assertIn('occupied', result['error'])
            self.assertNotEqual(listener.fileno(), -1)

    def test_port_range_and_numerics_rejected(self):
        for port in (True, 0, 65536):
            with self.assertRaises(ValueError): startup_support.available_port(port)
        for options in (['habitat','--seconds','nan'], ['habitat','--seconds','inf'], ['body','--seconds','0'], ['habitat','--port','65536']):
            with self.assertRaises(SystemExit): launcher.parse_arguments(options)

    def test_browser_disabled_does_not_call_os(self):
        with patch.object(startup_support.webbrowser, 'open') as browser:
            self.assertFalse(startup_support.open_dashboard('http://127.0.0.1:8480/', enabled=False))
            browser.assert_not_called()

    def test_browser_failure_keeps_manual_url(self):
        for effect in (False, OSError('No default browser')):
            with patch.object(startup_support.webbrowser, 'open', side_effect=effect if isinstance(effect, Exception) else None, return_value=False), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertFalse(startup_support.open_dashboard('http://127.0.0.1:8480/'))
                self.assertIn('http://127.0.0.1:8480/', output.getvalue())

    def test_continuous_habitat_and_passthrough_preserved(self):
        args, extra = launcher.parse_arguments(['habitat','--no-browser','--no-video','--arena','classic','--seed','7'])
        with patch.object(launcher, 'available_port', return_value=8481): command, url = launcher.build_command(args, extra)
        command = [str(p) for p in command]
        self.assertEqual(command[command.index('--seconds')+1], '0')
        self.assertIn('--keep-open', command); self.assertIn('--headless', command)
        self.assertIn('--no-browser', command); self.assertNotIn('--video', command)
        self.assertEqual(command[-4:], ['--arena','classic','--seed','7'])
        self.assertEqual(url, 'http://127.0.0.1:8481/')

    def test_finite_smoke_can_exit_after_save(self):
        args, extra = launcher.parse_arguments(['habitat','--seconds','.04','--exit-when-finished'])
        with patch.object(launcher, 'available_port', return_value=8481): command, url = launcher.build_command(args, extra)
        self.assertNotIn('--keep-open', command)
        self.assertIn('--video', command)

    def test_diagnostics_work_without_source_installer_in_active_project(self):
        args,extra=launcher.parse_arguments(['check'])
        with patch.object(launcher,'ROOT',self.root):
            command,_=launcher.build_command(args,extra)
            self.assertEqual(str(command[1]),str(self.root/'delivery/doctor.py'))
            self.assertIn('--all',command);self.assertIn('--source',command)
            (self.root/'tools').mkdir();(self.root/'tools/check_source.py').touch()
            command,_=launcher.build_command(args,extra)
            self.assertEqual(str(command[1]),str(self.root/'tools/check_source.py'))
            (self.root/'bundle-manifest.json').touch()
            command,_=launcher.build_command(args,extra)
            self.assertEqual(str(command[1]),str(self.root/'delivery/doctor.py'))
            self.assertNotIn('--source',command)

    def test_missing_brain_gives_install_instruction(self):
        body = self.root / '.venv/Scripts/python.exe'; body.parent.mkdir(parents=True); body.touch()
        with patch.object(launcher, 'ROOT', self.root), patch.object(launcher, 'python_for', side_effect=lambda kind:self.root/('.venv' if kind=='body' else '.brain-venv')/'Scripts/python.exe'):
            with self.assertRaisesRegex(RuntimeError, 'Run Setup.cmd'):
                launcher.preflight('habitat')

    def test_parent_ctrl_c_waits_for_saved_output_instead_of_killing_child(self):
        handlers = {}
        def install(sig, handler): handlers[sig] = handler
        process = Mock(pid=123, wait=Mock(return_value=0))
        def output():
            handlers[signal.SIGINT](signal.SIGINT, None)
            yield 'saving experiment\n'
            yield 'report saved\n'
        process.stdout = output()
        with patch.object(launcher.subprocess, 'Popen', return_value=process), patch.object(launcher.signal,'signal',side_effect=install):
            log = io.StringIO(); code, pid, interrupted = launcher.run_child(['owned-test'], {}, log)
        self.assertEqual(code, 130); self.assertTrue(interrupted)
        self.assertIn('report saved', log.getvalue())
        process.kill.assert_not_called(); process.terminate.assert_not_called(); process.wait.assert_called_once()

    def test_interrupt_during_save_is_deferred_and_handler_restored(self):
        previous=signal.getsignal(signal.SIGINT)
        with startup_support.defer_interrupts_while_saving() as request:
            signal.raise_signal(signal.SIGINT)
            self.assertTrue(request['requested'])
        self.assertEqual(signal.getsignal(signal.SIGINT),previous)

    def test_all_start_commands_use_crlf(self):
        for path in ROOT.glob('Start*.cmd'):
            raw = path.read_bytes()
            self.assertIn(b'\r\n', raw)
            self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))

    def test_git_preserves_cmd_bytes_even_with_autocrlf_enabled(self):
        if not shutil.which('git') or not (ROOT/'.git').is_dir():self.skipTest('Git checkout required')
        content=(ROOT/'Start.cmd').read_bytes()
        filtered=subprocess.run(['git','-c','core.autocrlf=true','hash-object','--path=Start.cmd','--stdin'],cwd=ROOT,input=content,capture_output=True,check=True).stdout
        raw=subprocess.run(['git','hash-object','--no-filters','--stdin'],cwd=ROOT,input=content,capture_output=True,check=True).stdout
        self.assertEqual(filtered,raw)

    @unittest.skipUnless(sys.platform=='win32', 'Native CMD integration')
    def test_real_cmd_space_path_argument_forwarding_exit_and_log(self):
        if not (ROOT / '_tools/uv.exe').is_file(): self.skipTest('Portable uv absent')
        (self.root / 'delivery').mkdir()
        for name in ('Start.cmd','Start-Body.cmd','Start-Brain.cmd','launcher.py','startup_support.py','project_info.py','版本信息.json','delivery/common.py'):
            shutil.copy2(ROOT/name,self.root/name)
        env = launcher.environment(); env['FLYWIRE_NONINTERACTIVE']='1'
        env['UV_CACHE_DIR']=str(self.root/'.cache/uv')
        subprocess.run([str(ROOT/'_tools/uv.exe'),'--no-config','--offline','venv','--no-project','--python',str(Path(sys.base_prefix)/'python.exe'),str(self.root/'.venv')],cwd=self.root,env=env,check=True,capture_output=True)
        (self.root/'play_fly.py').write_text('import json,sys\nprint(json.dumps(sys.argv[1:]),flush=True)\nprint("owned child saved",flush=True)\nraise SystemExit(7)\n',encoding='utf-8')
        result = subprocess.run(['cmd','/d','/c','Start-Body.cmd','--seconds','.04','--headless'],cwd=self.root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,7,result.stdout+result.stderr)
        record=json.loads(next((self.root/'output/launch').glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(record['exit_code'],7)
        log=Path(record['log']).read_text(encoding='utf-8')
        self.assertIn('--headless',log);self.assertIn('0.04',log);self.assertIn('owned child saved',log)
        result = subprocess.run(['cmd','/d','/c','Start-Brain.cmd'],cwd=self.root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,1)
        self.assertIn('environment missing',result.stdout+result.stderr)
        self.assertIn('Setup.cmd',result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
