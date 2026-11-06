import json
import tempfile
import unittest
from document_recovery import DocumentDraft


class RequiredCheckpointFields(unittest.TestCase):
    def test_missing_schema_or_kind_is_not_recoverable(self):
        for field in ('schema', 'kind'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                draft = DocumentDraft(directory, 'task_001', 'strategy')
                draft.save('Saved candidate', False, 'in progress')
                payload = json.loads(draft.path.read_text(encoding='utf-8'))
                del payload[field]
                draft.path.write_text(json.dumps(payload), encoding='utf-8')
                before = draft.path.read_bytes()
                with self.assertRaises(ValueError):
                    draft.load()
                with self.assertRaises(ValueError):
                    draft.save('Other candidate', False, 'retry')
                self.assertEqual(draft.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
