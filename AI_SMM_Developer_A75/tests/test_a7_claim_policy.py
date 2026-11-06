import json
from io import BytesIO
import unittest
from editorial_claim_policy import BUSINESS_FACT_POLICY, REVIEW_FACT_POLICY
from publication_writer import write_single, write_week
from semantic_editor import audit_post
from unittest.mock import patch
from independent_acceptance_editor import _call_editor_once


class SharedClaimPolicyTests(unittest.TestCase):
    def test_unconfirmed_consultation_and_business_process_policy_is_explicit(self):
        self.assertIn('не подтверждает, что консультация', BUSINESS_FACT_POLICY)
        self.assertIn('не пиши «запишитесь на консультацию»', BUSINESS_FACT_POLICY)
        self.assertIn('Планируемый визуал является предложением', BUSINESS_FACT_POLICY)
        self.assertIn('требующую подтверждения до публикации', BUSINESS_FACT_POLICY)
        self.assertIn('task_analysis.unknowns', BUSINESS_FACT_POLICY)
        self.assertIn('не описывай этапы как уже происходящие', BUSINESS_FACT_POLICY)
    def test_batch_and_single_generation_use_same_business_fact_policy(self):
        seen=[]
        def ask(model, system, user):
            seen.append(system)
            return '{"posts":[]}' if len(seen)==1 else '{}'
        slot={'week':1,'day':'Пн','format':'Пост','platform':'Instagram',
              'topic':'Вопрос перед занятием','brief':'Публикация','goal':'Вопросы'}
        write_week(ask,'model','brief',{},'strategy',[slot])
        self.assertIn(BUSINESS_FACT_POLICY,seen[-1])
        write_single(ask,'model','brief',{},'strategy',slot)
        self.assertIn(BUSINESS_FACT_POLICY,seen[-1])

    def test_independent_post_editor_uses_shared_business_fact_policy(self):
        seen=[]
        def ask(model, system, user):
            seen.append(system)
            return json.dumps({'approved':False,'issues':['needs review'],'revised_post':None})
        post={'id':'1/Пн','topic':'Тема','visual':'Сцена','caption':'Текст',
              'cta':'Напишите вопрос','kpi':'Вопросы','audience':'Аудитория',
              'measurement':'Подсчёт','materials':'Макет'}
        audit_post(ask,'model','brief',{}, {'week':1,'day':'Пн','format':'Пост'},post)
        self.assertIn(BUSINESS_FACT_POLICY,seen[0])

    def test_writer_and_reviewer_policies_share_the_same_fact_boundary(self):
        for phrase in ('явно подтверждённых материалах проекта',
                       'не сочиняй его', 'до публикации'):
            self.assertIn(phrase,BUSINESS_FACT_POLICY)
        for phrase in ('единому реестру явно подтверждённых проектных источников',
                       'Ранее сгенерированный документ сам не доказывает факт',
                       'постановочную сцену', 'обычное мнение', 'обычный вопрос CTA'):
            self.assertIn(phrase,REVIEW_FACT_POLICY)

    def test_compound_independent_reviewer_uses_shared_review_policy(self):
        captured={}
        def response(request,timeout):
            captured['payload']=json.loads(request.data)
            return BytesIO(json.dumps({'message':{'content':'{"pass":true,"issues":[]}'}}).encode())
        with patch('independent_acceptance_editor.urllib.request.urlopen',side_effect=response):
            _call_editor_once('brief','strategy','calendar')
        self.assertIn(REVIEW_FACT_POLICY,captured['payload']['messages'][0]['content'])


if __name__=='__main__': unittest.main()
