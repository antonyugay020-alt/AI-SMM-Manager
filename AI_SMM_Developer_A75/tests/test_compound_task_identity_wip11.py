"""Different assignments with the same request cannot share publication drafts."""
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
from compound_tasks import execute_compound

REQUEST='Разработай стратегию и контент-план на 1 неделю: по 2 публикации в неделю, всего 2 публикации.'

class CapturedSignature(Exception):
    def __init__(self,signature): self.signature=signature

class IndependentCompoundIdentityTests(unittest.TestCase):
    def capture_signature(self,workspace,task_id=None):
        context={'project_workspace':str(workspace)}
        if task_id is not None: context['task_id']=task_id
        with patch('compound_tasks.TaskLedger.open',side_effect=lambda _,sig: (_ for _ in ()).throw(CapturedSignature(sig))):
            with self.assertRaises(CapturedSignature) as caught:
                execute_compound(REQUEST,context)
        return caught.exception.signature

    def test_distinct_ids_with_identical_text_are_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            first=self.capture_signature(Path(td),'a'*32)
            second=self.capture_signature(Path(td),'b'*32)
            self.assertNotEqual(first,second)
            self.assertEqual(len(first),64)
            self.assertEqual(len(second),64)

    def test_resume_of_same_task_uses_same_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self.capture_signature(Path(td),'a'*32),
                             self.capture_signature(Path(td),'a'*32))

    def test_legacy_task_keeps_old_digest(self):
        with tempfile.TemporaryDirectory() as td:
            expected=hashlib.sha256((td+'\n'+REQUEST+'\n1\n2').encode()).hexdigest()
            self.assertEqual(self.capture_signature(Path(td)),expected)

    def test_invalid_task_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):execute_compound(REQUEST,{
                'project_workspace':td,'task_id':'../other-project'})

if __name__=='__main__':unittest.main()
