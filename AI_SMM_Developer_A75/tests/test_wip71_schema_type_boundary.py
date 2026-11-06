import json
import tempfile
import unittest
from document_recovery import DocumentDraft


class SchemaTypeBoundary(unittest.TestCase):
    def test_schema_requires_integer_not_boolean_or_float(self):
        for invalid in (True, 1.0, '1', None, 2):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                draft = DocumentDraft(directory, 'task_001', 'strategy')
                draft.save('Unreviewed content', False, 'in progress')
                payload = json.loads(draft.path.read_text(encoding='utf-8'))
                payload['schema'] = invalid
                draft.path.write_text(json.dumps(payload), encoding='utf-8')
                before = draft.path.read_bytes()
                with self.assertRaises(ValueError):
                    draft.load()
                with self.assertRaises(ValueError):
                    draft.save('New content', False, 'retry')
                self.assertEqual(draft.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
