"""Fault-injection for a crash between final rejection intent and checkpoint revocation."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import compound_tasks as ct
from document_recovery import DocumentDraft

class CrashReplayTests(unittest.TestCase):
    def test_rejection_intent_outlives_crash_before_slot_revocation(self):
        request='Разработай стратегию и контент-план, Instagram, 1 неделя, 1 публикация в неделю.'
        post={'id':'1/Ср', **dict.fromkeys(('topic','caption','visual','cta','kpi','audience','measurement','materials'),'filled')}
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts'; drafts.mkdir()
            sig=hashlib.sha256((home+'\n'+request+'\n1\n1').encode()).hexdigest()
            (drafts/f'publication_progress_{sig}.json').write_text(json.dumps({'signature':sig,'posts':{'1/Ср':post}}))
            slot=DocumentDraft(drafts,sig,'publication_0'); slot.save(json.dumps(post,ensure_ascii=False,sort_keys=True),True,'candidate')
            original_save=DocumentDraft.save
            def crash_before_revocation(self,content,valid,reason,**kw):
                if valid is False and self.kind == 'publication_0':
                    raise RuntimeError('simulated power loss')
                return original_save(self,content,valid,reason,**kw)
            with patch.object(DocumentDraft,'save',crash_before_revocation), self.assertRaises(RuntimeError):
                ct.execute_compound(request,{'project_workspace':home,'task_mode':'final_review_repair',
                    'final_review_repair_slots':['1/Ср']})
            self.assertTrue((drafts/f'final_review_revocations_{sig}.json').exists())
            self.assertTrue(slot.load()['valid'])
            class StopAfterRecovery(Exception): pass
            with patch.object(ct,'extract_facts',side_effect=StopAfterRecovery), self.assertRaises(StopAfterRecovery):
                ct.execute_compound(request,{'project_workspace':home})
            self.assertFalse(slot.load()['valid'])
            aggregate=json.loads((drafts/f'publication_progress_{sig}.json').read_text())
            self.assertNotIn('1/Ср',aggregate['posts'])
            self.assertFalse((drafts/f'final_review_revocations_{sig}.json').exists())
            self.assertIsNotNone(DocumentDraft(drafts,sig,'publication_rejected_0').load())

    def test_revocation_intent_overrides_stale_repair_checkpoint(self):
        request='Разработай стратегию и контент-план, Instagram, 1 неделя, 1 публикация в неделю.'
        post={'id':'1/Ср', **dict.fromkeys(('topic','caption','visual','cta','kpi','audience','measurement','materials'),'current')}
        stale=dict(post,caption='stale previous draft')
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts'; drafts.mkdir()
            sig=hashlib.sha256((home+'\n'+request+'\n1\n1').encode()).hexdigest()
            (drafts/f'publication_progress_{sig}.json').write_text(json.dumps({'signature':sig,'posts':{'1/Ср':post}}))
            DocumentDraft(drafts,sig,'publication_0').save(json.dumps(post,ensure_ascii=False,sort_keys=True),True,'candidate')
            repair=drafts/('publication_repair_'+hashlib.sha256(sig.encode()).hexdigest()+'.json')
            repair.write_text(json.dumps({'signature':sig,'drafts':{'1/Ср':{'post':stale,'issues':['old finding']}}}))
            intent=drafts/f'final_review_revocations_{sig}.json'
            intent.write_text(json.dumps({'schema':1,'signature':sig,'posts':{'1/Ср':post}}))
            class StopAfterRecovery(Exception): pass
            with patch.object(ct,'extract_facts',side_effect=StopAfterRecovery), self.assertRaises(StopAfterRecovery):
                ct.execute_compound(request,{'project_workspace':home})
            actual=json.loads(repair.read_text())['drafts']['1/Ср']
            self.assertEqual(actual['post'],post)
            self.assertEqual(actual['issues'],['old finding'])
            self.assertTrue(actual['revoked_by_final_review'])
            self.assertFalse(DocumentDraft(drafts,sig,'publication_0').load()['valid'])
            self.assertFalse(intent.exists())

    def test_recovery_after_slot_revocation_but_before_aggregate_commit(self):
        request='Разработай стратегию и контент-план, Instagram, 1 неделя, 1 публикация в неделю.'
        post={'id':'1/Ср', **dict.fromkeys(('topic','caption','visual','cta','kpi','audience','measurement','materials'),'filled')}
        with tempfile.TemporaryDirectory() as home:
            drafts=Path(home)/'drafts'; drafts.mkdir()
            sig=hashlib.sha256((home+'\n'+request+'\n1\n1').encode()).hexdigest()
            aggregate=drafts/f'publication_progress_{sig}.json'
            aggregate.write_text(json.dumps({'signature':sig,'posts':{'1/Ср':post}}))
            slot=DocumentDraft(drafts,sig,'publication_0')
            slot.save(json.dumps(post,ensure_ascii=False,sort_keys=True),True,'candidate')
            real_write=ct.atomic_write_json
            def fail_aggregate_after_intent(path,payload,*a,**kw):
                if Path(path)==aggregate and (drafts/f'final_review_revocations_{sig}.json').exists():
                    raise RuntimeError('crash before aggregate commit')
                return real_write(path,payload,*a,**kw)
            with patch.object(ct,'atomic_write_json',side_effect=fail_aggregate_after_intent),self.assertRaises(RuntimeError):
                ct.execute_compound(request,{'project_workspace':home,'task_mode':'final_review_repair',
                                             'final_review_repair_slots':['1/Ср']})
            self.assertFalse(slot.load()['valid'])
            self.assertIn('1/Ср',json.loads(aggregate.read_text())['posts'])
            class StopAfterRecovery(Exception): pass
            with patch.object(ct,'extract_facts',side_effect=StopAfterRecovery),self.assertRaises(StopAfterRecovery):
                ct.execute_compound(request,{'project_workspace':home})
            self.assertNotIn('1/Ср',json.loads(aggregate.read_text())['posts'])
            self.assertFalse((drafts/f'final_review_revocations_{sig}.json').exists())

if __name__ == '__main__': unittest.main()
