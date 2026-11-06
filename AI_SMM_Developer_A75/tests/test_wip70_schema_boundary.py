import json
import tempfile
import unittest
from pathlib import Path
from document_recovery import DocumentDraft

class SchemaBoundaryTest(unittest.TestCase):
    def test_unknown_checkpoint_schema_fails_closed_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            draft=DocumentDraft(d,'task_001','strategy')
            draft.save('original', False, 'needs review')
            original=json.loads(draft.path.read_text(encoding='utf-8'))
            original['schema']=2
            draft.path.write_text(json.dumps(original), encoding='utf-8')
            before=draft.path.read_bytes()
            with self.assertRaises(ValueError): draft.load()
            with self.assertRaises(ValueError): draft.save('replacement',True,'approved')
            self.assertEqual(before,draft.path.read_bytes())

if __name__=='__main__': unittest.main()
