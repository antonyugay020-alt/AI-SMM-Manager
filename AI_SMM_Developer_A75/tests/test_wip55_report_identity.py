"""End-to-end contract checks for the durable reviewed artifact identity."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from production_pipeline import run_document_task
from task_provenance import TaskProvenance
from test_windows_acceptance_v4 import FinalEditorialGateTests


class ReviewedReportIdentityTests(unittest.TestCase):
    def setUp(self):
        self.request = 'Два поста в течение одной недели'
        self.signature = hashlib.sha256(b'fixed-report-identity').hexdigest()

    def run_case(self, workspace, request=None):
        fixture = FinalEditorialGateTests.prepared()
        fixture['task_signature'] = self.signature
        def executor(req, context, progress=None):
            return copy.deepcopy(fixture)
        with patch('production_pipeline.run_compound_with_retry',
                   side_effect=lambda ex, req, state, **kw: ex(req, state)):
            return run_document_task(request or self.request,
                {'project_workspace': workspace}, execute=executor,
                strategy_reviewer=lambda *a, **k: {'pass': True, 'issues': []},
                final_reviewer=lambda *a, **k: {'pass': True, 'issues': []})

    def test_disk_report_carries_original_verified_task(self):
        with tempfile.TemporaryDirectory() as home:
            result = self.run_case(home)
            stored = json.loads((Path(home)/'drafts'/
                f'content_delivery_{self.signature}.json').read_text(encoding='utf-8'))
            self.assertEqual(stored['task_provenance'], result['task_provenance'])
            TaskProvenance.restore(stored['task_provenance']).verify(self.request)

    def test_different_request_cannot_reuse_previously_committed_identity(self):
        with tempfile.TemporaryDirectory() as home:
            first = self.run_case(home)
            current = Path(home)/'drafts'/f'content_delivery_{self.signature}.json'
            original_bytes = current.read_bytes()
            with self.assertRaises(ValueError):
                self.run_case(home, 'Два поста в течение двух недель')
            self.assertEqual(current.read_bytes(), original_bytes)
            self.assertEqual(first['task_provenance']['original_request'], self.request)

if __name__ == '__main__':
    unittest.main()
