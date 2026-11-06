"""Canonical routing/lifecycle boundaries; injected executors, no live models."""
import hashlib
import unittest
from unittest.mock import patch

from compound_tasks import is_compound_request
from task_routing import execution_request, creation_checkpoint_required
from text_workflow import run_text_task


class TaskLifecycle(unittest.TestCase):
    def test_resume_without_valid_pending_request_cannot_be_a_new_creation(self):
        for pending in (None,'','   ',{},17):
            for command in ('Продолжи задание.','Продолжай','Возобнови задание','Продолжи задание и сохрани результаты'):
                with self.subTest(pending=pending,command=command):
                    self.assertEqual(execution_request(command,pending)[1],'resume_missing')
    def test_new_intent_and_genuine_question_do_not_inherit_prior_request(self):
        for message in ('Создай новую стратегию для нового проекта','Продолжи позже, создай новый контент-план',
                        'Какие документы уже готовы?'):
            self.assertEqual(execution_request(message,'An old task')[0],message)
            self.assertEqual(execution_request(message,'An old task')[1],'new')
    def test_existing_resume_keeps_exact_request_bytes(self):
        request='  Составь стратегию и контент‑план.\nСохрани оба документа.  '
        self.assertEqual(execution_request('Продолжи задание',request),(request,'resume'))
    def test_backend_refuses_orphan_resume_before_any_planner_or_executor(self):
        with patch('agent_runtime.plan_task',side_effect=AssertionError('No planner call')), \
             patch('production_pipeline.run_document_task',side_effect=AssertionError('No document generation')):
            with self.assertRaisesRegex(ValueError,'Не найдено исходное задание'):
                run_text_task('Продолжи задание',{'task_mode':'resume_missing'})
    def test_unicode_dashes_and_spacing_only_change_route_not_request(self):
        for separator in ('-','‐','‑','‒','–','—','－',' ','\t','\n','\u00ad',''):
            request='Разработай SMM-стратегию и контент'+separator+'план для проекта.'
            seen=[]
            def execute(req,state,**kw):
                seen.append((req,state));return {'action':'compound_documents','documents':[]}
            out=run_text_task(request,{},compound_runner=execute)
            self.assertEqual(seen[0][0],request)
            self.assertEqual(out['instruction'],request)
            self.assertEqual(seen[0][1]['evidence_context']['sources'][0]['source_id'],
                             'user_request:'+hashlib.sha256(request.encode()).hexdigest()[:16])
    def test_discussion_of_both_artifacts_does_not_create_documents(self):
        for request in ('Сравни стратегию и контент-план','Чем отличаются стратегия и контент‑план?',
                        'Объясни, зачем нужны стратегия и контент-план'):
            self.assertFalse(is_compound_request(request))
            self.assertFalse(creation_checkpoint_required(request))
            out=run_text_task(request,{},planner=lambda *a,**k:{'action':'respond','answer':'An explanation'},
                quality_cycle=lambda req,initial,*a,**kw:initial,
                compound_runner=lambda *a,**k:self.fail('Discussion cannot generate documents'))
            self.assertEqual(out['action'],'respond')
    def test_creation_checkpoint_covers_single_text_and_paired_artifacts(self):
        for request in ('Напиши пост для Telegram','Подготовь отчёт по проекту','Разработай стратегию и контент‑план',
                        'Мне нужна стратегия и контент-план'):
            self.assertTrue(creation_checkpoint_required(request),request)
    def test_clarification_extends_original_and_keeps_executable_route(self):
        request='Составь стратегию и контент‑план.'
        resolved,mode=execution_request('Instagram',request,'На какой площадке?')
        self.assertEqual(mode,'clarification');self.assertTrue(resolved.startswith(request))
        self.assertIn('Instagram',resolved);self.assertTrue(is_compound_request(resolved))
    def test_untrusted_respond_is_not_turned_into_approved_content(self):
        answer='Документы созданы и сохранены.'
        out=run_text_task('Напиши материал',{},planner=lambda *a,**k:{'action':'respond','answer':answer},
                         quality_cycle=lambda req,initial,*a,**kw:initial)
        self.assertEqual(out['action'],'respond');self.assertNotIn('quality_review',out)
        self.assertNotIn('documents',out);self.assertNotIn('completion_verified',out)


if __name__=='__main__':unittest.main()
