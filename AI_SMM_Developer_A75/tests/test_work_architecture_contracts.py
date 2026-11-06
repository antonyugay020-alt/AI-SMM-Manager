"""Cross-component safety contracts, with actual SQLite/files and injected AI."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from plan_governance import govern_plan
from task_artifacts import verify_saved_artifact

class PlanTransactions(unittest.TestCase):
    def setUp(self):
        self.slots=[dict(week=1,day=d,topic=t,brief='Technique',goal='Educate',
                        audience='Customers',format='Post',platform='Telegram')
                    for d,t in [('Вт','Clay textures'),('Пт','Car repair')]]
        self.ids=['1/Вт','1/Пт']
    def govern(self, ask, accepted=()):
        return govern_plan(ask,'fake','Pottery business',{},'',self.slots,set(accepted),expected_ids=self.ids)
    def test_full_checkpoint_requires_real_review(self):
        calls=[]
        def unavailable(*args):
            calls.append(args);raise TimeoutError()
        slots,blocked,available=self.govern(unavailable,self.ids)
        self.assertFalse(available)
        self.assertEqual(set(blocked),set(self.ids))
        self.assertEqual(len(calls),1)
        self.assertEqual(slots,self.slots)
    def test_accepted_violation_blocks_without_rewriting(self):
        original=copy.deepcopy(self.slots)
        slots,blocked,available=self.govern(lambda *a:json.dumps({'issues':[
            {'id':'1/Пт','dimension':'topic','reason':'Wrong business'}]}),self.ids)
        self.assertTrue(available)
        self.assertIn('1/Пт',blocked)
        self.assertEqual(slots,original)
        self.assertEqual(self.slots,original)
    def test_local_approval_cannot_override_global_rejection(self):
        candidate={**self.slots[1],'topic':'Glaze colors'}
        for final in (None, {'issues':[{'id':'1/Вт','dimension':'topic','reason':'Cross-slot conflict'}]}):
            with self.subTest(final=final):
                calls=[]
                def ask(m,s,user):
                    calls.append(json.loads(user))
                    if len(calls)==1:return json.dumps({'issues':[{'id':'1/Пт','dimension':'topic','reason':'Wrong business'}]})
                    if len(calls)==2:return '{"issues":[]}'
                    return 'invalid' if final is None else json.dumps(final)
                with patch('plan_governance.propose_topic_replacement',return_value=[candidate]):
                    revised,blocked,available=self.govern(ask,{'1/Вт'})
                self.assertEqual(len(calls),3)
                self.assertEqual(len(calls[-1]['plan']),2)
                self.assertEqual(revised,self.slots)
                self.assertTrue(blocked)
    def test_malformed_alternative_does_not_crash_or_commit(self):
        with patch('plan_governance.propose_topic_replacement',return_value=[{},None]):
            revised,blocked,available=self.govern(lambda *a:'{"issues":[{"id":"1/Пт","dimension":"topic","reason":"Wrong business"}]}')
        self.assertEqual(revised,self.slots)
        self.assertIn('1/Пт',blocked)
    def test_global_approval_commits_candidate_without_touching_input(self):
        candidate={**self.slots[1],'topic':'Glaze colors'}
        answers=iter(['{"issues":[{"id":"1/Пт","dimension":"topic","reason":"Wrong business"}]}','{"issues":[]}','{"issues":[]}'])
        with patch('plan_governance.propose_topic_replacement',return_value=[candidate]):
            revised,blocked,available=self.govern(lambda *a:next(answers),{'1/Вт'})
        self.assertTrue(available)
        self.assertFalse(blocked)
        self.assertEqual(revised[1],candidate)
        self.assertEqual(self.slots[1]['topic'],'Car repair')

class PersistedDeliveries(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT,updated_at TEXT)')
        self.db.execute("INSERT INTO manager_documents VALUES('doc','A','strategy','Title','Actual bytes','2026-01-01')")
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def export(self,*args):
        text=self.root/'doc.txt';pdf=self.root/'doc.pdf'
        text.write_text(args[-1],encoding='utf-8');pdf.write_bytes(b'%PDF-1.4\nsynthetic contract fixture\n%%EOF')
        return text,pdf,None
    def test_exact_bytes_and_exports_are_required(self):
        self.assertTrue(verify_saved_artifact(self.db,'A','strategy','Actual bytes',self.export))
        self.assertFalse(verify_saved_artifact(self.db,'B','strategy','Actual bytes',self.export))
        self.assertFalse(verify_saved_artifact(self.db,'A','strategy','Old bytes',self.export))
    def test_export_failure_then_retry_never_duplicates_document(self):
        def fail(*a):raise OSError('disk full')
        with self.assertRaises(OSError):verify_saved_artifact(self.db,'A','strategy','Actual bytes',fail)
        self.assertTrue(verify_saved_artifact(self.db,'A','strategy','Actual bytes',self.export))
        self.assertEqual(self.db.execute('SELECT count(*) FROM manager_documents').fetchone()[0],1)
    def test_incomplete_pdf_or_wrong_text_blocks_completion(self):
        for which in ('pdf','text'):
            def bad(*args):
                paths=self.export(*args)
                if which=='pdf':paths[1].write_bytes(b'%PDF-1.4 truncated')
                else:paths[0].write_text('wrong',encoding='utf-8')
                return paths
            self.assertFalse(verify_saved_artifact(self.db,'A','strategy','Actual bytes',bad))
    def test_concurrent_manual_change_cannot_certify_old_delivery(self):
        def changed(*args):
            paths=self.export(*args)
            self.db.execute("UPDATE manager_documents SET content='Manual revision'")
            return paths
        self.assertFalse(verify_saved_artifact(self.db,'A','strategy','Actual bytes',changed))

class ReferenceOptIn(unittest.TestCase):
    def test_negative_or_bare_reference_is_not_permission(self):
        from reference_policy import reference_requested
        for prompt in ('Эталон есть в папке', 'Не используй эталон',
                       'Редактируй без эталона', 'Не учитывай референс',
                       'Do not use reference image', 'Ignore the reference',
                       'Используй эталон. Нет, не используй эталон.'):
            with self.subTest(prompt=prompt):self.assertFalse(reference_requested(prompt))
    def test_explicit_use_is_permission(self):
        from reference_policy import reference_requested
        for prompt in ('Используй эталон', 'Учти референс', 'Сделай цвет как на эталоне',
                       'Use reference image', 'Сравни с эталоном'):
            with self.subTest(prompt=prompt):self.assertTrue(reference_requested(prompt))

class SharedAtomicStorage(unittest.TestCase):
    def test_package_cannot_omit_a_lazy_runtime_module(self):
        import release_guard as gate
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'AI_SMM_INCOMPLETE';root.mkdir()
            for name in gate.REQUIRED_FILES:
                (root/name).write_text('fixture',encoding='utf-8')
            (root/'reference_policy.py').unlink()
            with self.assertRaises(gate.PackagingBlocked):
                gate.package(root,Path(td)/'MISSING_NOT_RELEASE.zip','acceptance')

    def test_transient_windows_lock_retries_without_partial_bytes(self):
        from durable_json import atomic_write_json
        import os
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'state.json'
            atomic_write_json(target,{'old':True})
            real_replace=os.replace
            attempts=[]
            def locked_once(a,b):
                attempts.append(1)
                if len(attempts)==1:
                    self.assertEqual(json.loads(target.read_text()),{'old':True})
                    raise PermissionError('Windows file lock')
                real_replace(a,b)
            with patch('durable_json.os.replace',side_effect=locked_once):
                atomic_write_json(target,{'new':True},delay=0)
            self.assertEqual(len(attempts),2)
            self.assertEqual(json.loads(target.read_text()),{'new':True})
            self.assertEqual(list(Path(td).iterdir()),[target])
    def test_permanent_lock_preserves_prior_checkpoint_and_cleans_temp(self):
        from durable_json import atomic_write_json
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'state.json';target.write_text('{"old":true}')
            with patch('durable_json.os.replace',side_effect=PermissionError('locked')) as replace:
                with self.assertRaises(PermissionError):atomic_write_json(target,{'new':True},retries=2,delay=0)
            self.assertEqual(replace.call_count,2)
            self.assertEqual(json.loads(target.read_text()),{'old':True})
            self.assertEqual(list(Path(td).iterdir()),[target])
