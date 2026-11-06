from plan_review_fixture import with_plan_review
"""Stage 70: simulated integrated execution, targeted draft repair, and real resume."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compound_tasks as ct
from publication_writer import repair_single
from test_stage69_scope import good_post, mini_plan
from version_info import APP_VERSION, STAGE_NUMBER, BUILD_LABEL

REQUEST = ('Создай SMM-стратегию и контент-план на 2 недели, '
           'по 2 публикации в неделю, всего 4 публикации')


def bad_post(slot):
    p=good_post(slot)
    p['caption']='Неинформативный черновик.'
    return p


def fake_week(ask,model,request,facts,strategy,slots,previous_topics=None,feedback=''):
    return [bad_post(s) if (s['week'],s['day'])==(1,'Пт') else good_post(s) for s in slots]


def mock_ask(m,sys,user,stage='-',report=None):
    if 'независимая редактура' in stage:
        return json.dumps({'approved':True,'issues':[]})
    return '# Стратегия\n' + ('Цели, целевая аудитория, сегменты, бюджет, каналы, KPI, ' +
                             'план измерений, риски и этапы внедрения. ')*40


class Stage70Recovery(unittest.TestCase):
    def setup_executor(self, mock_repair):
        return (patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}),
                patch.object(ct,'_ask',side_effect=with_plan_review(mock_ask)),
                patch.object(ct,'validate_document',return_value=(True,'')),
                patch.object(ct,'plan_slots',side_effect=mini_plan),
                patch.object(ct,'write_week',side_effect=fake_week),
                patch.object(ct,'repair_single',side_effect=mock_repair),
                patch.object(ct,'copy_too_similar',return_value=False),
                patch.object(ct,'editorial_audit',side_effect=lambda *a,**k:[]))

    def execute_with(self, home, mock_repair):
        patches=self.setup_executor(mock_repair)
        for p in patches:p.start()
        try:return ct.execute_compound(REQUEST,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':home})})
        finally:
            for p in reversed(patches):p.stop()

    def test_partial_failure_preserves_rejected_draft_then_repairs_on_resume(self):
        calls=[]
        phase={'run':0}
        def repair(ask,model,req,facts,strategy,slot,previous_post,issues,previous_topics=()):
            calls.append((phase['run'],slot['week'],slot['day'],previous_post['caption'],list(issues)))
            return (bad_post(slot) if phase['run']==0 else good_post(slot))
        with tempfile.TemporaryDirectory() as folder:
            first=self.execute_with(folder,repair)
            self.assertFalse(first['documents'][-1]['valid'])
            self.assertEqual(first['content_delivery']['checked_posts'],3)
            drafts=Path(folder)/'drafts'
            checkpoint=next(drafts.glob('publication_progress_*.json'))
            repair_file=next(drafts.glob('publication_repair_*.json'))
            earlier=json.loads(checkpoint.read_text(encoding='utf-8'))
            self.assertEqual(earlier['completed'],3)
            rejected=json.loads(repair_file.read_text(encoding='utf-8'))
            self.assertIn('1/Пт',rejected['drafts'])
            self.assertNotIn('1/Пт',earlier['posts'])
            self.assertTrue(calls)
            phase['run']=1
            second=self.execute_with(folder,repair)
            self.assertTrue(second['documents'][-1]['valid'], second['documents'][-1]['reason'])
            self.assertEqual(second['content_delivery']['checked_posts'],4)
            updated=json.loads(checkpoint.read_text(encoding='utf-8'))
            self.assertEqual(updated['completed'],4)
            for ident, accepted in earlier['posts'].items():
                self.assertEqual(updated['posts'][ident],accepted)
            self.assertEqual(json.loads(repair_file.read_text(encoding='utf-8'))['drafts'],{})
            self.assertTrue(any(r==1 for r,*_ in calls))
            stages=[json.loads(l)['stage'] for log in drafts.glob('publication_diagnostics_*.jsonl') for l in log.read_text(encoding='utf-8').splitlines()]
            self.assertIn('targeted_repair_started',stages)
            self.assertIn('single_accepted',stages)

    def test_bounded_targeted_repair_completes_whole_calendar(self):
        calls=[]
        def repair(ask,model,req,facts,strategy,slot,previous_post,issues,previous_topics=()):
            calls.append((slot['week'],slot['day'],list(issues)))
            return good_post(slot)
        with tempfile.TemporaryDirectory() as folder:
            out=self.execute_with(folder,repair)
            self.assertTrue(out['documents'][-1]['valid'],out['documents'][-1]['reason'])
            self.assertEqual(out['content_delivery']['checked_posts'],4)
            self.assertTrue(calls)
            self.assertEqual({f'{week}/{day}' for week,day,_ in calls},{'1/Пт'})

    def test_eight_of_sixteen_recovers_without_touching_accepted_posts(self):
        task=('Разработай стратегию и контент-план на 8 недель, '
              'по 2 публикации в неделю, всего 16 публикаций')
        phase={'run':0}
        def week(ask,model,request,facts,strategy,slots,previous_topics=(),feedback=''):
            # Simulate half of the model outputs failing the quality gate.
            return [bad_post(s) if s['week']<=4 else good_post(s) for s in slots]
        def repair(ask,model,request,facts,strategy,slot,post,issues,previous_topics=()):
            return bad_post(slot) if phase['run']==0 else good_post(slot)
        patches=(patch.object(ct,'analyze',return_value={'channels':'Instagram','channels_basis':'recommendation'}),
                 patch.object(ct,'_ask',side_effect=with_plan_review(mock_ask)),
                 patch.object(ct,'validate_document',return_value=(True,'')),
                 patch.object(ct,'plan_slots',side_effect=mini_plan),
                 patch.object(ct,'write_week',side_effect=week),
                 patch.object(ct,'repair_single',side_effect=repair),
                 patch.object(ct,'copy_too_similar',return_value=False),
                 patch.object(ct,'editorial_audit',side_effect=lambda *a,**k:[]))
        with tempfile.TemporaryDirectory() as folder:
            for p in patches:p.start()
            try:
                first=ct.execute_compound(task,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder})})
                self.assertEqual(first['content_delivery']['checked_posts'],8)
                progress=next((Path(folder)/'drafts').glob('publication_progress_*.json'))
                eight=json.loads(progress.read_text(encoding='utf-8'))['posts']
                self.assertEqual(len(eight),8)
                phase['run']=1
                second=ct.execute_compound(task,{'plan_reviewer_model':'independent-reviewer', **({'project_workspace':folder})})
                self.assertTrue(second['documents'][-1]['valid'],second['documents'][-1]['reason'])
                self.assertEqual(second['content_delivery']['checked_posts'],16)
                sixteen=json.loads(progress.read_text(encoding='utf-8'))['posts']
                self.assertEqual(len(sixteen),16)
                for name,post in eight.items():self.assertEqual(sixteen[name],post)
            finally:
                for p in reversed(patches):p.stop()

    def test_repair_prompt_binds_exact_slot_and_existing_draft(self):
        slot=dict(week=2,day='Вт',topic='Уход за инструментами',format='Карусель')
        previous=bad_post(slot)
        prompt=[]
        def ask(model,system,user):
            prompt.append((system,json.loads(user)))
            return json.dumps(good_post(slot),ensure_ascii=False)
        fixed=repair_single(ask,'mock','проект',{},'стратегия',slot,previous,['Мало конкретики'])
        self.assertEqual(fixed['id'],'2/Вт')
        self.assertEqual(prompt[0][1]['approved_slot'],slot)
        self.assertEqual(prompt[0][1]['draft_to_repair'],previous)
        self.assertIn('Мало конкретики',prompt[0][1]['rejection_reasons'])
        self.assertIn('строго',prompt[0][0])

    def test_ui_build_version_does_not_depend_on_historical_label(self):
        source=Path(__file__).parent
        if not (source/'app.py').is_file():source=source.parent
        ui=(source/'app.py').read_text(encoding='utf-8')
        self.assertTrue(APP_VERSION.startswith(f'{STAGE_NUMBER}.'))
        self.assertGreaterEqual(STAGE_NUMBER,70)
        self.assertIn('self.setWindowTitle(f\'AI SMM Manager — {BUILD_LABEL}\')',ui)
        self.assertIn('sl.addWidget(QLabel(f\'Версия {APP_VERSION} · Stage {STAGE_NUMBER}\'))',ui)
        self.assertNotIn("self.setWindowTitle('AI SMM Manager — Stage 64.1",ui)


if __name__=='__main__':unittest.main()
