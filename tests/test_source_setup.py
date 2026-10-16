"""Safety and reproducibility checks for the source installer; no GPU/network needed."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_setup', ROOT / 'tools/setup_source.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SourceSetupTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache/tests').mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / '.cache/tests')
        self.root = Path(self.directory.name).resolve()
        self.patcher = patch.object(setup, 'ROOT', self.root)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.directory.cleanup()

    def item(self, content=b'validated data'):
        return dict(path='downloads/example.bin', url='https://example.invalid/data',
                    bytes=len(content), sha256=hashlib.sha256(content).hexdigest())

    def test_external_paths_rejected(self):
        with self.assertRaises(RuntimeError):
            setup.local_path('../outside')

    def test_environment_cannot_redirect_installs_to_global_python(self):
        hostile = dict(PIP_TARGET='outside', PIP_PREFIX='outside', PYTHONHOME='outside',
                       PYTHONPATH='outside', UV_INDEX='https://example.invalid', UV_PYTHON='outside',
                       VIRTUAL_ENV='outside', CONDA_PREFIX='outside', PATH='unchanged')
        with patch.dict(setup.os.environ, hostile, clear=True):
            env = setup.environment(True)
            self.assertEqual(setup.os.environ, hostile)
        for name in ('PIP_TARGET', 'PIP_PREFIX', 'PYTHONHOME', 'PYTHONPATH', 'UV_INDEX', 'UV_PYTHON', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
            self.assertNotIn(name, env)
        self.assertEqual(env['PATH'], 'unchanged')
        self.assertEqual(env['UV_PYTHON_DOWNLOADS'], 'never')
        self.assertEqual(env['UV_LINK_MODE'], 'copy')
        for name in ('TEMP', 'TMP', 'UV_CACHE_DIR', 'PYTHONPYCACHEPREFIX', 'CUDA_CACHE_PATH'):
            self.assertTrue(Path(env[name]).is_relative_to(self.root))

    def test_existing_bad_data_preserved(self):
        path = self.root / self.item()['path']
        path.parent.mkdir(); path.write_bytes(b'existing user file')
        with self.assertRaises(RuntimeError):
            setup.restore_resource(self.item(), [], True)
        self.assertEqual(path.read_bytes(), b'existing user file')

    def test_supplied_data_copied_not_moved(self):
        directory = self.root / 'supplied'; directory.mkdir()
        original = directory / 'example.bin'; original.write_bytes(b'validated data')
        installed = setup.restore_resource(self.item(), [directory])
        self.assertEqual(installed.read_bytes(), original.read_bytes())
        self.assertNotEqual(installed, original)
        self.assertTrue(original.exists())

    def test_bad_supplied_data_does_not_install(self):
        directory = self.root / 'supplied'; directory.mkdir()
        (directory / 'example.bin').write_bytes(b'corrupt')
        with self.assertRaises(RuntimeError):
            setup.restore_resource(self.item(), [directory])
        self.assertFalse((self.root / self.item()['path']).exists())

    def test_bad_download_never_becomes_final_file(self):
        with patch.object(setup.urllib.request, 'urlopen', return_value=io.BytesIO(b'bad')):
            with self.assertRaises(RuntimeError):
                setup.restore_resource(self.item(), [], True)
        self.assertFalse((self.root / self.item()['path']).exists())

    def test_manual_download_default_has_no_network_side_effect(self):
        with patch.object(setup.urllib.request, 'urlopen') as network:
            with self.assertRaisesRegex(RuntimeError, 'Missing'):
                setup.restore_resource(self.item(), [])
            network.assert_not_called()

    def test_foreign_venv_never_modified(self):
        venv = self.root / '.venv'; venv.mkdir()
        sentinel = venv / 'user.txt'; sentinel.write_text('keep')
        commands = []
        with self.assertRaisesRegex(RuntimeError, 'Refusing'):
            setup.prepare_environment('body', Path('base.exe'), {}, commands.append)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertEqual(commands, [])
        self.assertFalse((venv / setup.OWNER).exists())

    def test_interrupted_owned_install_can_resume_without_clear(self):
        commands = []
        executable = self.root / 'python.exe'
        result = setup.prepare_environment('brain', executable, {}, commands.append)
        self.assertEqual(result, self.root / '.brain-venv/Scripts/python.exe')
        self.assertNotIn('--clear', commands[0])
        setup.prepare_environment('brain', executable, {}, commands.append)
        self.assertEqual(len(commands), 2)

    def test_existing_venv_version_must_match_owner(self):
        directory = self.root / '.venv'
        (directory / 'Scripts').mkdir(parents=True)
        (directory / 'Scripts/python.exe').write_bytes(b'fixture')
        executable = self.root / 'base.exe'
        (directory / setup.OWNER).write_text(json.dumps(dict(root=str(self.root), base_python=str(executable), kind='body')))
        with patch.object(setup, 'python_info', return_value=dict(prefix=str(directory), version=[3, 12], bits=64)):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                setup.prepare_environment('body', executable, {}, lambda _: self.fail('Must not alter a mismatched venv'))

    def test_lock_profiles_keep_separate_cpython_abis_and_no_machine_paths(self):
        data = json.loads((ROOT / 'requirements/依赖来源.json').read_text(encoding='utf-8'))
        for kind, abi in (('body', 'cp313'), ('brain', 'cp312')):
            text = (ROOT / f'requirements/{kind}-win-x64.lock.txt').read_text(encoding='utf-8')
            self.assertNotIn('file://', text)
            self.assertNotIn('E:', text)
            rows = [line for line in text.splitlines() if line and not line.startswith('#')]
            self.assertEqual(len(rows), len(data['packages'][kind]))
            for item in data['packages'][kind]:
                self.assertIn(f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}", rows)
                self.assertTrue(abi in item['wheel'] or '-abi3-' in item['wheel'] or '-none-' in item['wheel'], item['wheel'])

    def test_windows_entrypoints_use_crlf(self):
        for name in ('Setup.cmd', 'Check-Environment.cmd'):
            content = (ROOT / name).read_bytes()
            self.assertIn(b'\r\n', content)
            self.assertNotIn(b'\n', content.replace(b'\r\n', b''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
