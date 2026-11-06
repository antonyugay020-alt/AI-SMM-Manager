import json
import tempfile
import unittest
from pathlib import Path
from document_recovery import DocumentDraft


class DuplicateCheckpointKeyTests(unittest.TestCase):
    def test_duplicate_approval_flag_blocks_load(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = DocumentDraft(folder, 'task-signature', 'strategy')
            draft.save('content', False, 'pending')
            raw = draft.path.read_text(encoding='utf-8')
            draft.path.write_text(raw.replace('"valid": false,', '"valid": true, "valid": false,'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate field'):
                draft.load()

    def test_nested_duplicate_history_field_blocks_load(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = DocumentDraft(folder, 'task-signature', 'strategy')
            draft.save('first', False, 'pending')
            draft.save('second', False, 'pending')
            raw = draft.path.read_text(encoding='utf-8')
            raw = raw.replace('"content": "first",', '"content": "first", "content": "other",')
            draft.path.write_text(raw, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate field'):
                draft.load()

    def test_normal_checkpoint_and_history_remain_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = DocumentDraft(folder, 'task-signature', 'strategy')
            draft.save('first', False, 'pending')
            draft.save('second', True, 'reviewed')
            record = draft.load()
            self.assertEqual(record['content'], 'second')
            self.assertEqual(record['history'][-1]['content'], 'first')
