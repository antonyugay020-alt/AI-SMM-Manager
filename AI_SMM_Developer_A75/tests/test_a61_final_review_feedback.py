import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks
from document_recovery import DocumentDraft


class FinalReviewFeedbackTests(unittest.TestCase):
    def test_complete_review_rejection_reaches_same_unapproved_strategy_draft(self):
        request=('Разработай SMM-стратегию и контент-план на 1 неделю, '
                 'по 2 публикации в неделю, всего 2 публикации.')
        candidate_text='## Стратегия\nПодтверждённые вводные проекта.'
        issue={'document':'strategy','type':'invented_fact',
               'quote':'Мы гарантируем результат в каждом случае.',
               'reason':'Обещание результата не подтверждено источниками.'}
        with tempfile.TemporaryDirectory() as home:
            signature=hashlib.sha256((home+'\n'+request+'\n1\n2').encode()).hexdigest()
            draft_store=DocumentDraft(Path(home)/'drafts',signature,'strategy')
            draft_store.save(candidate_text,True,'')
            seen={}

            def capture(request, per_week, generate, validate, **kwargs):
                seen.update(kwargs)
                return candidate_text,False,'Исправление требуется'

            with patch.object(compound_tasks,'extract_facts',return_value={}), \
                 patch.object(compound_tasks,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(compound_tasks,'produce_checked_strategy',side_effect=capture):
                result=compound_tasks.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({
                    'project_workspace':home,
                    'task_mode':'final_review_repair',
                    'final_review_issues':[issue],
                    'strict_document_recovery':True,
                })})

            self.assertEqual(result['documents'][0]['kind'],'strategy')
            recovery_draft=seen['draft']
            self.assertFalse(recovery_draft['valid'])
            self.assertIn(issue['reason'],recovery_draft['reason'])
            self.assertIn(issue['quote'],recovery_draft['reason'])
            # The final-review handoff is in-memory. The durable original stays
            # intact until a validated repair is checkpointed.
            persisted=draft_store.load()
            self.assertEqual(persisted['content'],candidate_text)
            self.assertTrue(persisted['valid'])


if __name__=='__main__':
    unittest.main()
