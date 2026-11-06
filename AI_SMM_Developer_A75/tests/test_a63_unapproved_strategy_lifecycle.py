import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks
from document_recovery import DocumentDraft


class UnapprovedStrategyLifecycleTests(unittest.TestCase):
    def _run(self, already_saved):
        request=('Разработай SMM-стратегию и контент-план на 1 неделю, '
                 'по 2 публикации в неделю, всего 2 публикации.')
        content='## Стратегия\nЧерновик, ожидающий итоговой проверки.'
        issue={'document':'strategy','type':'invented_fact',
               'quote':'Мы гарантируем безопасность каждого клиента.',
               'reason':'Обещание безопасности не подтверждено источниками.'}
        with tempfile.TemporaryDirectory() as home:
            signature=hashlib.sha256((home+'\n'+request+'\n1\n2').encode()).hexdigest()
            draft_store=DocumentDraft(Path(home)/'drafts',signature,'strategy')
            draft_store.save(content,True,'')
            seen={}

            def capture(request, per_week, generate, validate, **kwargs):
                seen.update(kwargs)
                return content,False,'Исправление требуется'

            with patch.object(compound_tasks,'extract_facts',return_value={}), \
                 patch.object(compound_tasks,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), \
                 patch.object(compound_tasks,'produce_checked_strategy',side_effect=capture):
                compound_tasks.execute_compound(request,{'plan_reviewer_model':'independent-reviewer', **({
                    'project_workspace':home,
                    'task_mode':'final_review_repair',
                    'resume_documents':[{'kind':'strategy','content':content,
                                         'already_saved':already_saved}],
                    'final_review_issues':[issue],
                    'strict_document_recovery':True,
                })})
            return seen

    def test_carried_forward_unapproved_strategy_remains_repairable(self):
        seen=self._run(False)
        self.assertIsNone(seen['existing'])
        self.assertFalse(seen['draft']['valid'])
        self.assertIn('Обещание безопасности',seen['draft']['reason'])
        self.assertIn('Мы гарантируем безопасность',seen['draft']['reason'])

    def test_user_accepted_strategy_remains_immutable(self):
        seen=self._run(True)
        self.assertEqual(seen['existing'],'## Стратегия\nЧерновик, ожидающий итоговой проверки.')
        self.assertIsNone(seen['draft'])


if __name__=='__main__':
    unittest.main()
