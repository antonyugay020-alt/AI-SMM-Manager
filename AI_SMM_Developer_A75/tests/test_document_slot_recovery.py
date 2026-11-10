import tempfile
import unittest
from pathlib import Path
from document_recovery import DocumentDraft

class DocumentSlotRecovery(unittest.TestCase):
    def test_isolated_slots_survive_restart(self):
        with tempfile.TemporaryDirectory() as home:
            first=DocumentDraft(home, 'task_1', 'publication_0')
            second=DocumentDraft(home, 'task_1', 'publication_1')
            first.save('draft one', False, 'pending review')
            second.save('accepted two', True, 'verified')
            first.save('revision one', False, 'still pending')
            self.assertEqual(DocumentDraft(home,'task_1','publication_1').load()['content'],'accepted two')
            self.assertEqual(DocumentDraft(home,'task_1','publication_0').load()['history'][0]['content'],'draft one')
    def test_non_boolean_approval_never_replaces_safe_checkpoint(self):
        with tempfile.TemporaryDirectory() as home:
            slot=DocumentDraft(home, 'task', 'publication_0')
            slot.save('unreviewed text', False, 'pending')
            original=slot.path.read_bytes()
            for invalid in ('false', 'true', 1, 0, None, [], {}):
                with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                    slot.save('unsafe text', invalid, 'reviewed')
                self.assertEqual(slot.path.read_bytes(), original)
            self.assertIs(slot.load()['valid'], False)

    def test_bad_reason_and_empty_content_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as home:
            slot=DocumentDraft(home, 'task', 'calendar')
            slot.save('draft', False, 'pending')
            original=slot.path.read_bytes()
            with self.assertRaises(TypeError):
                slot.save('draft2', True, None)
            with self.assertRaises(ValueError):
                slot.save('  ', True, 'verified')
            self.assertEqual(slot.path.read_bytes(), original)

    def test_review_is_bound_to_exact_document_and_verdict(self):
        from document_recovery import digest
        with tempfile.TemporaryDirectory() as home:
            slot=DocumentDraft(home, 'task', 'publication_0')
            text='checked document'
            proof={'document_sha256': digest(text), 'verdict': True, 'review_id':'review_1'}
            slot.save(text, True, 'verified', review=proof)
            before=slot.path.read_bytes()
            self.assertEqual(slot.load()['review'], proof)
            for bad_text,bad_valid,bad_review in (
                ('new version', True, proof),
                (text, False, proof),
                (text, True, {'document_sha256':digest(text),'verdict':'true','review_id':'review_1'}),
            ):
                with self.assertRaises(ValueError):
                    slot.save(bad_text, bad_valid, 'verified', review=bad_review)
                self.assertEqual(slot.path.read_bytes(),before)
            slot.save('next candidate', False, 'awaiting review')
            self.assertEqual(slot.load()['history'][-1]['review'], proof)

    def test_tampered_review_proof_blocks_restore(self):
        from document_recovery import digest
        import json
        with tempfile.TemporaryDirectory() as home:
            slot=DocumentDraft(home,'task','calendar')
            slot.save('v1', True, 'verified', review={'document_sha256':digest('v1'),'verdict':True,'review_id':'r1'})
            obj=json.loads(slot.path.read_text(encoding='utf-8'))
            obj['review']['document_sha256']=digest('another document')
            slot.path.write_text(json.dumps(obj), encoding='utf-8')
            with self.assertRaises(ValueError):
                slot.load()

    def test_invalid_slot_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as home:
            for kind in ('../../evil','publication_../', 'publication_123456', 'unknown'):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    DocumentDraft(home,'task',kind)
    def test_corrupt_payload_blocks_restore(self):
        with tempfile.TemporaryDirectory() as home:
            a=DocumentDraft(home,'task','calendar')
            a.save('original',False,'pending')
            a.path.write_text(a.path.read_text(encoding='utf-8').replace('original','tampered'), encoding='utf-8')
            with self.assertRaises(ValueError):
                a.load()
    def test_invalid_signature(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(ValueError):
                DocumentDraft(home,'../other','calendar')

if __name__=='__main__': unittest.main()


class RejectedPublicationIsolationTests(unittest.TestCase):
    def test_rejected_revision_does_not_replace_accepted_version(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            approved=DocumentDraft(Path(tmp),'task','publication_0')
            rejected=DocumentDraft(Path(tmp),'task','publication_rejected_0')
            approved.save('accepted content',True,'pre-final-review candidate')
            rejected.save('rejected revision',False,'editor findings')
            self.assertEqual(approved.load()['content'],'accepted content')
            self.assertTrue(approved.load()['valid'])
            self.assertEqual(rejected.load()['content'],'rejected revision')
            self.assertFalse(rejected.load()['valid'])

class CheckpointAddressing(unittest.TestCase):
    def test_distinct_tasks_with_identical_prefix_are_independent(self):
        with tempfile.TemporaryDirectory() as home:
            a=DocumentDraft(home,'sameprefix_123456_A','publication_0')
            b=DocumentDraft(home,'sameprefix_123456_B','publication_0')
            a.save('first',True,'ok')
            b.save('second',False,'needs review')
            self.assertNotEqual(a.path,b.path)
            self.assertEqual(a.load()['content'],'first')
            self.assertEqual(b.load()['content'],'second')

    def test_legacy_recovery_requires_exact_signature(self):
        import json
        from document_recovery import digest
        with tempfile.TemporaryDirectory() as home:
            a=DocumentDraft(home,'prefix123456789_A','publication_0')
            b=DocumentDraft(home,'prefix123456789_B','publication_0')
            a.legacy_path.write_text(json.dumps({'signature':a.signature,'kind':a.kind,'content':'original','sha256':digest('original'),'valid':False,'reason':'review pending','history':[]}),encoding='utf-8')
            self.assertEqual(a.load()['content'],'original')
            self.assertIsNone(b.load())
            b.save('independent draft',False,'awaiting review')
            self.assertEqual(b.load()['content'], 'independent draft')
            self.assertEqual(a.load()['content'], 'original')
            a.save('updated',False,'still pending')
            self.assertEqual(a.load()['content'],'updated')
            self.assertEqual(json.loads(a.legacy_path.read_text(encoding='utf-8'))['content'],'original')
