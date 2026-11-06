from plan_review_fixture import with_plan_review
"""Release gates for task-level isolation and cross-week editorial integrity."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import compound_tasks as ct
from task_routing import execution_request

class ReleaseTests(unittest.TestCase):
    def test_resume_only_when_explicit(self):
        old = 'Создай стратегию для цветочного магазина'
        self.assertEqual(execution_request('Создай стратегию для туристического агентства', old, 'Что уточнить?')[1], 'new')
        self.assertEqual(execution_request('продолжи задание', old, 'Часть документов не создана.')[1], 'resume')

    def test_cross_week_duplicate_copy_not_accepted(self):
        def slots(ask,model,request,strategy,brief,weeks,on_week=None,existing_slots=None):
            return [dict(week=w,day=d,topic=f'Тема {w} {d}',format='Reels',platform='Instagram',goal='Польза',audience='Люди',brief='Разбор пользовательского вопроса')
                    for w in range(1,weeks+1) for d in ('Пн','Ср','Пт')]
        def writer(ask,model,request,known,strategy,pending,all_topics,feedback=''):
            return [dict(id=f"{s['week']}/{s['day']}",topic=s['topic'],caption='Одинаковый текст. '*30,
                         visual='Три подробных кадра по теме публикации',cta='Сохраните',kpi='Сохранения',
                         measurement='Instagram',audience='Люди',materials='Схема') for s in pending]
        with tempfile.TemporaryDirectory() as td:
            with patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}), patch.object(ct,'_ask',side_effect=with_plan_review('Стратегия '*300)), \
                 patch.object(ct,'validate_document',return_value=(True,'')), patch.object(ct,'plan_slots',side_effect=slots), \
                 patch.object(ct,'write_week',side_effect=writer), patch.object(ct,'write_single',side_effect=ValueError('модель недоступна')), \
                 patch.object(ct,'validate_single',return_value=[]), patch.object(ct,'critical_issues',return_value=[]), \
                 patch.object(ct,'editorial_audit',return_value=[]), \
                 patch.object(ct,'audit_post',side_effect=lambda ask,model,request,facts,slot,post,previous_posts=(): (post,[])):
                result=ct.execute_compound('Создай стратегию и контент-план на 2 недели',{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':td})})
            self.assertFalse(result['documents'][1]['valid'])
            import json
            file=next((Path(td)/'drafts').glob('publication_progress_*.json'))
            self.assertEqual(json.loads(file.read_text(encoding="utf-8"))['completed'],1)

if __name__ == '__main__': unittest.main()
