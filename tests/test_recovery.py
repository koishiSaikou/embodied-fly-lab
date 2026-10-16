"""Synthetic transport failures and independent saving; no fabricated biology."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from brain_client import BrainClient, BrainResponseError, response_timeout
from experiment_recovery import Finalizer, write_json

REPLY = dict(brain_time_ms=20, drive=[.1,.2], p9_hz=[50,60], chunk_ms=20,
             chunk_spikes=2, total_spikes=2, active_neurons=2, sugar_spikes=0,
             activity=dict(events=[]))
COMMAND = dict(p9_rate_hz=[10,20], sugar_rate_hz=0)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache/tests').mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='recovery ', dir=ROOT / '.cache/tests')
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(ROOT / '.cache/tests')
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
            self.assertIsNotNone(client.process.poll())
            self.assertFalse(any(thread.is_alive() for thread in client._threads))
            self.assertTrue(client.log.closed)
        self.temporary.cleanup()

    def client(self, code, **options):
        worker = self.root / 'synthetic_worker.py'
        worker.write_text('import sys,json,time\n' + code, encoding='utf-8')
        client = BrainClient(self.root, 1, worker_command=[sys.executable,'-u','-B',str(worker)],
                             startup_timeout=3, step_timeout=.15, **options)
        self.clients.append(client)
        return client

    def test_startup_timeout_reaps_worker_even_without_assigned_client(self):
        original = subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        with patch('brain_client.subprocess.Popen', side_effect=spawn):
            with self.assertRaises(BrainResponseError) as caught:
                # A 3 s startup deadline includes interpreter startup on Windows.
                self.client('time.sleep(60)')
        self.assertEqual(caught.exception.details['stage'], 'brain_startup')
        self.assertEqual(caught.exception.details['timeout_s'], 3)
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(caught.exception.details['last_brain_time_ms'], 0)

    def test_step_timeout_keeps_last_success_and_progress_then_closes(self):
        progress = []
        client = self.client('print(json.dumps({"ready":{}}),flush=True)\n'
            'sys.stdin.readline()\nprint('+repr(json.dumps(REPLY))+',flush=True)\n'
            'sys.stdin.readline()\ntime.sleep(60)', progress=progress.append)
        self.assertEqual(client.advance(COMMAND,20,True), REPLY)
        with self.assertRaises(BrainResponseError) as caught:
            client.advance(COMMAND,20,True)
        self.assertEqual(caught.exception.details['last_brain_time_ms'], 20)
        self.assertEqual(caught.exception.details['stage'], 'brain_step')
        self.assertTrue(any(p and p['stage']=='brain_startup' for p in progress))
        self.assertTrue(any(p and p['stage']=='brain_step' for p in progress))
        self.assertIsNone(progress[-1])
        start=time.monotonic();client.close()
        self.assertLess(time.monotonic()-start, 4)

    def test_idle_pause_does_not_expire_deadline_or_change_commands(self):
        client=self.client('print(json.dumps({"ready":{}}),flush=True)\n'
            'for line in sys.stdin:\n'
            ' m=json.loads(line)\n'
            ' if m.get("quit"):break\n'
            ' r=json.loads('+repr(json.dumps(REPLY))+')\n'
            ' r["received"]=m\n'
            ' print(json.dumps(r),flush=True)')
        time.sleep(.4)  # Longer than the request deadline, but no outstanding request.
        result=client.advance(COMMAND,20,True,123.5,dict(gaba=.5))
        self.assertEqual(result.pop('received'),dict(rate_hz=[10,20],duration_ms=20,sugar_rate_hz=0,
            capture_activity=True,drive_smoothing_ms=123.5,transmitter_gains=dict(gaba=.5)))
        self.assertEqual(result, REPLY)
        self.assertFalse(client.failed)

    def test_cancel_during_constructor_closes_unassigned_worker(self):
        children=[];original=subprocess.Popen
        def spawn(*args,**kwargs):
            child=original(*args,**kwargs);children.append(child);return child
        def cancel(wait):
            if wait:raise KeyboardInterrupt
        with patch('brain_client.subprocess.Popen',side_effect=spawn),self.assertRaises(KeyboardInterrupt):
            self.client('time.sleep(60)',progress=cancel)
        self.assertIsNotNone(children[0].poll())

    def test_eof_and_malformed_replies_fail_without_waiting_for_deadline(self):
        for name, code in [('exit','sys.exit(7)'), ('json','print("oops",flush=True)'),
                           ('object','print("[]",flush=True)'), ('metadata','print("{}",flush=True)')]:
            with self.subTest(name=name), self.assertRaises(BrainResponseError):
                self.client(code)

    def test_invalid_step_shape_rejected(self):
        for reply in ('{}', '{"brain_time_ms":NaN}', json.dumps(dict(REPLY,drive=[1]))):
            with self.subTest(reply=reply):
                client=self.client('print(json.dumps({"ready":{}}),flush=True)\n'
                    'sys.stdin.readline()\nprint('+repr(reply)+',flush=True)')
                with self.assertRaises(BrainResponseError):client.advance(COMMAND,20,True)
                self.assertEqual(client.last_brain_time_ms,0)
                client.close()

    def test_blocked_write_is_also_deadline_bound(self):
        client=self.client('print(json.dumps({"ready":{}}),flush=True)\ntime.sleep(60)')
        with self.assertRaises(BrainResponseError):
            client.advance(dict(COMMAND,p9_rate_hz=['x'*2_000_000,0]),20,False)
        client.close()

    def test_dead_worker_buffered_stdin_flush_does_not_block_recovery(self):
        client=self.client('print(json.dumps({"ready":{}}),flush=True)\nsys.exit(7)')
        client.process.wait(timeout=3)
        with self.assertRaises(BrainResponseError):client.advance(COMMAND,20,True)
        client.close()
        self.assertTrue(client.process.stdin.closed)
        self.assertTrue(client.process.stdout.closed)
        self.assertFalse(any(thread.is_alive() for thread in client._threads))

    def test_cleanup_errors_do_not_skip_following_steps(self):
        finalizer=Finalizer();calls=[]
        with patch('sys.stderr',new_callable=io.StringIO):
            finalizer.attempt('render',lambda:(_ for _ in ()).throw(RuntimeError('render failed')))
            finalizer.attempt('video',lambda:(_ for _ in ()).throw(OSError('encoder failed')))
            finalizer.attempt('brain_close',lambda:calls.append('brain closed'))
            finalizer.attempt('report',lambda:write_json(self.root/'report.json',dict(cleanup_errors=finalizer.errors)))
        self.assertEqual(calls,['brain closed'])
        self.assertEqual([e['stage'] for e in finalizer.errors],['render','video'])
        self.assertEqual(len(json.loads((self.root/'report.json').read_text(encoding='utf-8'))['cleanup_errors']),2)

    def test_failed_atomic_save_preserves_existing_report_and_independent_fallback(self):
        target=self.root/'report.json';target.write_text('{"previous":true}',encoding='utf-8')
        original=Path.open
        def broken(path,*args,**kwargs):
            if path.name=='report.json.partial':raise OSError('simulated disk full')
            return original(path,*args,**kwargs)
        finalizer=Finalizer()
        with patch.object(Path,'open',broken),patch('sys.stderr',new_callable=io.StringIO):
            finalizer.attempt('save_report',lambda:write_json(target,dict(new=True)))
            finalizer.attempt('save_error',lambda:write_json(self.root/'error.json',dict(errors=finalizer.errors)))
        self.assertEqual(json.loads(target.read_text()),dict(previous=True))
        self.assertIn('simulated disk full',(self.root/'error.json').read_text())

    def test_invalid_timeout_values_rejected(self):
        for value in ('nan','inf',0,-1,3601):
            with self.assertRaises(ValueError):response_timeout(value)


if __name__=='__main__':unittest.main()
