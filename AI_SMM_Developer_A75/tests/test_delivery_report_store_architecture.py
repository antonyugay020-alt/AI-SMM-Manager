"""Executable invariants for the whole draft/final report persistence boundary."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from delivery_report_store import store_delivery_report
from task_provenance import TaskProvenance


class ReportAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.request = 'Create 16 publications for a product launch'
        self.a = 'f' * 16 + '0' * 48
        self.b = 'f' * 16 + '1' * 48

    def report(self, signature, status='BLOCKED'):
        return {'task_signature': signature, 'independent_editor': {'status': status},
                'task_provenance': TaskProvenance.from_request(self.request).as_dict()}

    def test_same_prefix_reports_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            a = store_delivery_report(folder, self.a, self.report(self.a))
            b = store_delivery_report(folder, self.b, self.report(self.b))
            self.assertNotEqual(a, b)
            self.assertEqual(json.loads(a.read_text(encoding='utf-8'))['task_signature'], self.a)
            self.assertEqual(json.loads(b.read_text(encoding='utf-8'))['task_signature'], self.b)
            self.assertEqual(len(list((Path(folder)/'drafts').glob('content_delivery_*.json'))), 2)

    def test_foreign_legacy_short_digest_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            drafts = Path(folder)/'drafts'; drafts.mkdir()
            old = drafts/('content_delivery_'+self.a[:16]+'.json')
            old.write_text(json.dumps(self.report(self.a)), encoding='utf-8')
            before = old.read_bytes()
            current = store_delivery_report(folder, self.b, self.report(self.b))
            self.assertEqual(old.read_bytes(), before)
            self.assertEqual(json.loads(current.read_text(encoding='utf-8'))['task_signature'], self.b)

    def test_matching_legacy_is_archived_without_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            drafts = Path(folder)/'drafts'; drafts.mkdir()
            old = drafts/('content_delivery_'+self.a[:16]+'.json')
            old.write_text(json.dumps(self.report(self.a)), encoding='utf-8')
            path = store_delivery_report(folder, self.a, self.report(self.a, 'PASS'), request=self.request)
            history = list((drafts/'content_delivery_history'/self.a).glob('*.json'))
            self.assertEqual(len(history), 1)
            self.assertEqual(json.loads(history[0].read_text(encoding='utf-8'))['independent_editor']['status'], 'BLOCKED')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['independent_editor']['status'], 'PASS')

    def test_foreign_provenance_does_not_modify_previous_report(self):
        with tempfile.TemporaryDirectory() as folder:
            current = store_delivery_report(folder, self.a, self.report(self.a), request=self.request)
            before = current.read_bytes()
            with self.assertRaises(ValueError):
                store_delivery_report(folder, self.a, self.report(self.a, 'PASS'), request='Different request')
            self.assertEqual(before, current.read_bytes())

    def test_archive_failure_keeps_original_current(self):
        with tempfile.TemporaryDirectory() as folder:
            current = store_delivery_report(folder, self.a, self.report(self.a), request=self.request)
            before = current.read_bytes()
            with patch('delivery_report_store.atomic_write_json', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store_delivery_report(folder, self.a, self.report(self.a, 'PASS'), request=self.request)
            self.assertEqual(before, current.read_bytes())

    def test_symlinked_drafts_folder_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as foreign:
            (Path(folder)/'drafts').symlink_to(foreign, target_is_directory=True)
            with self.assertRaises(ValueError):
                store_delivery_report(folder, self.a, self.report(self.a))
            self.assertEqual(list(Path(foreign).iterdir()), [])


if __name__ == '__main__':
    unittest.main()

class DraftFinalSeparationTests(unittest.TestCase):
    def test_new_draft_cannot_overwrite_previously_accepted_final_report(self):
        signature = '9' * 64
        with tempfile.TemporaryDirectory() as folder:
            accepted = store_delivery_report(folder, signature, {'independent_editor': {'status': 'PASS'}})
            original = accepted.read_bytes()
            draft = store_delivery_report(folder, signature, {'independent_editor': {'status': 'BLOCKED'}},
                                          stage='draft', archive=False)
            self.assertNotEqual(draft, accepted)
            self.assertEqual(accepted.read_bytes(), original)
            self.assertEqual(json.loads(draft.read_text(encoding='utf-8'))['independent_editor']['status'], 'BLOCKED')
