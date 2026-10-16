"""Authorized synchronization must preserve baseline backups and detect drift."""
import hashlib
from pathlib import Path
import sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from verify_repository import synchronization_evidence


class SynchronizationAuditTests(unittest.TestCase):
    def test_backup_and_both_runtime_copies_are_checked(self):
        (ROOT/'.cache/tests').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.cache/tests') as folder:
            workspace=Path(folder).resolve();assert workspace.is_relative_to(ROOT/'.cache/tests')
            active=workspace/'active';prepared=workspace/'prepared'
            active.mkdir();prepared.mkdir();(active/'backup').write_bytes(b'old')
            for root in (active,prepared):(root/'app.py').write_bytes(b'new')
            sha=lambda data:hashlib.sha256(data).hexdigest()
            batches=[dict(files=[dict(path='app.py',backup='backup',before_sha256=sha(b'old'),
                after_sha256=sha(b'new'),same_as_preparation_copy=True)])]
            first,latest,failures=synchronization_evidence(active,prepared,batches)
            self.assertFalse(failures);self.assertEqual(first['app.py'],sha(b'old'))
            self.assertEqual(latest['app.py'],sha(b'new'))
            (active/'backup').write_bytes(b'changed')
            self.assertIn('backup differs',' '.join(synchronization_evidence(active,prepared,batches)[2]))
            (active/'backup').write_bytes(b'old');(active/'app.py').write_bytes(b'changed')
            self.assertIn('active file differs',' '.join(synchronization_evidence(active,prepared,batches)[2]))
            (active/'app.py').write_bytes(b'new');(prepared/'app.py').write_bytes(b'changed')
            self.assertIn('not synchronized',' '.join(synchronization_evidence(active,prepared,batches)[2]))

    def test_path_escape_is_rejected(self):
        _,_,failures=synchronization_evidence(ROOT,ROOT,[dict(files=[dict(path='../outside',before_sha256=None,after_sha256='x')])])
        self.assertIn('Invalid synchronization target',' '.join(failures))


if __name__=='__main__':unittest.main()
