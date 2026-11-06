from substance_fixture import assessed_reader
"""General role, source-scope and immutable-span recovery regressions."""
import copy
import json
import random
import unittest
from unittest.mock import patch
from artifact_revision import ArtifactRevisionContract
from model_roles import allocate_revision_roles,model_family
from generic_text_review import select_reviewer_models
from claim_support import adjudicate_factual_findings, public_copy_findings
from publication_writer import repair_fields


class RevisionCapacityTests(unittest.TestCase):
    def test_cloud_author_does_not_consume_only_independent_cloud_judge(self):
        pool=['author:cloud','judge:cloud','local-a','local-b','local-c','local-d']
        for seed in range(50):
            roles=allocate_revision_roles(pool[0],pool,chooser=random.Random(seed))
            self.assertIn(roles.revision_author,pool[2:])
            self.assertIn('judge:cloud',roles.reviewer_pool)
            previous={'factual_grounding':'judge:cloud','requirements':'local-a',
                'completeness':'local-b','format_and_tone':'local-c'}
            panel=select_reviewer_models(roles.revision_author,candidates=roles.reviewer_pool,
                previous_reviewers=previous,chooser=random.Random(seed))
            self.assertEqual(panel['factual_grounding'],'judge:cloud')
            self.assertEqual(len({model_family(x) for x in panel.values()}),4)
            self.assertNotIn(roles.revision_author,panel.values())
            self.assertNotIn(pool[0],panel.values())

    def test_compound_can_reassign_backup_to_writer_while_primary_stays_independent(self):
        pool=['author','primary:cloud','backup:cloud','local-a','local-b','local-c']
        roles=allocate_revision_roles('author',pool,fixed_reviewers={0:'primary:cloud'},required_reviewers=2)
        self.assertEqual(roles.revision_author,'backup:cloud')
        self.assertIn('primary:cloud',roles.reviewer_pool)
        self.assertNotIn('backup:cloud',roles.reviewer_pool)

    def test_multiple_rounds_exclude_every_contributing_writer(self):
        pool=['author','old-writer','judge:cloud','judge-b','local-a','local-b']
        roles=allocate_revision_roles('author',pool,fixed_reviewers={0:'judge:cloud'},
            required_reviewers=2,previous_writers=['author','old-writer'])
        self.assertNotIn('old-writer',roles.reviewer_pool)
        self.assertNotIn('author',roles.reviewer_pool)
        self.assertNotIn(roles.revision_author,roles.reviewer_pool)

    def test_explicit_panel_assignment_is_preserved(self):
        fixed={str(n):'local-'+str(n) for n in range(4)}
        roles=allocate_revision_roles('author',['author','writer:cloud',*fixed.values()],fixed_reviewers=fixed)
        self.assertEqual(roles.revision_author,'writer:cloud')
        self.assertEqual(set(roles.reviewer_pool),set(fixed.values()))

    @patch('independent_acceptance_editor.call_substance_reader',new=assessed_reader)
    def test_actual_pipeline_replaces_revising_backup_before_every_review(self):
        from production_pipeline import run_document_task
        from independent_acceptance_editor import ValidatedEditorialVerdict
        from test_windows_acceptance_v4 import TaskRecoveryTests
        prepared=TaskRecoveryTests.result(True)
        prepared['documents'][1]['content']=(
            '| Неделя/день | Площадка и формат | Конкретная тема | Цель | Сценарий/визуал | Текст/ключевые реплики | CTA | KPI | Метод измерения | Целевая аудитория | Необходимые материалы |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'
            '| 1/Вт | Telegram, пост | First topic | Explain | Visual | Unsupported sentence. | Ask | Comments | Count | Readers | Image |\n'
            '| 1/Пт | Telegram, пост | Second topic | Explain | Visual | Accepted sibling. | Ask | Comments | Count | Readers | Image |')
        captured=[];judges=[]
        def choose(author,*,candidates=None,exclude_models=None,**kw):
            pool=candidates or ['primary:cloud','backup:cloud','local-a','local-b']
            blocked={author,*(exclude_models or [])}
            return next(m for m in pool if m not in blocked)
        def editor(req,strategy,calendar,**kw):
            judges.append(kw['model'])
            issues=[] if 'Unsupported sentence.' not in calendar else [{'document':'calendar',
                'type':'invented_fact','quote':'Unsupported sentence.','reason':'There is no source for this claim.',
                'row_context':{'slot':'1/Вт'}}]
            return ValidatedEditorialVerdict({'pass':not issues,'issues':issues,'review_state':'complete'})
        def repair(executor,request,state,*,model,progress=None):
            captured.append((model,copy.deepcopy(state)))
            out=copy.deepcopy(prepared)
            out['documents'][1]['content']=out['documents'][1]['content'].replace('Unsupported sentence.','Useful explanation.')
            return out
        with patch('generic_text_review.select_independent_model',side_effect=choose), \
             patch('generic_text_review.known_quality_model_pool',return_value=['author','primary:cloud','backup:cloud','local-a','local-b']), \
             patch('production_pipeline.run_compound_with_retry',return_value=prepared), \
             patch('production_pipeline.invoke_executor',side_effect=repair), \
             patch('independent_acceptance_editor.call_editor',side_effect=editor):
            result=run_document_task('Create strategy and calendar.',{},model='author')
        self.assertTrue(captured,result)
        self.assertEqual(captured[0][0],'backup:cloud')
        state=captured[0][1]
        self.assertEqual(state['plan_reviewer_model'],'primary:cloud')
        self.assertNotIn('backup:cloud',state['publication_reviewer_models'])
        self.assertNotIn('author',state['publication_reviewer_models'])
        self.assertEqual(judges,['primary:cloud','primary:cloud'])
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')
        self.assertIn('Accepted sibling.',result['documents'][1]['content'])


class SourceInvitationScopeTests(unittest.TestCase):
    def audit(self,text,kind='communication',subject='reader',act='invitation'):
        def ask(model,system,user,**kw):
            body=json.loads(user)
            return {'decisions':{row['finding_id']:{'statement_class':'invitation_cta',
                'speech_act':act,'assertion_subject':subject,'source_relation':'not_needed',
                'source_quote_id':'none','verdict':'supported','invitation_kind':kind,
                'reason':'Reader communication has no promised business action.'} for row in body['findings']}}
        return adjudicate_factual_findings('Create a post.',public_copy_findings(text),
            ask=ask,require_semantic_contract=True)

    def test_communication_to_business_recipient_is_not_service_delivery(self):
        for text in ('Напишите нам в личные сообщения, чтобы описать вашу ситуацию.',
                     'Расскажите нам, какой вопрос остался открытым.',
                     'Tell us what you have already observed.'):
            result=self.audit(text)
            self.assertFalse(result['blocking'],(text,result))
            self.assertTrue(result['non_business'])

    def test_service_access_and_physical_visits_still_need_sources(self):
        for text in ('Запишитесь на консультацию.','Book a paid service.','Приходите к нам завтра.'):
            result=self.audit(text,kind='service_access')
            self.assertTrue(result['blocking'],(text,result))
            self.assertFalse(result['supported'])

    def test_explicit_business_action_cannot_hide_behind_invitation_metadata(self):
        for text in ('Мы бесплатно доставим каждый заказ.','Our team guarantees success.'):
            result=self.audit(text)
            self.assertTrue(result['blocking'],(text,result))

    def test_missing_invitation_scope_does_not_grant_immunity(self):
        result=self.audit('Запишитесь на консультацию.',kind='none')
        self.assertTrue(result['blocking'])


class ImmutableRevisionSpanTests(unittest.TestCase):
    def test_random_fields_preserve_all_bytes_outside_selected_evidence(self):
        rng=random.Random(6001)
        for n in range(100):
            key='field_'+str(rng.randrange(10**9))
            prefix=''.join(rng.choice('абвXYZ ') for _ in range(50))
            suffix=''.join(rng.choice('гдеUVW ') for _ in range(50))
            fields={key:prefix+'REJECTED'+suffix,'other':'Accepted immutable bytes'}
            saved=copy.deepcopy(fields)
            c=ArtifactRevisionContract.create(fields,[{'field':key,'quote':'REJECTED'}],[key])
            out=c.apply({'replacements':{'P001':'Useful replacement'}})
            self.assertEqual(out[key],prefix+'Useful replacement'+suffix)
            self.assertEqual(out['other'],fields['other']);self.assertEqual(fields,saved)
            for bad in ({'replacements':{'P001':''}},{'replacements':{'P001':'REJECTED'}},
                        {'replacements':{'P001':'New','P999':'Forged'}},{'changes':{key:'Different'}}):
                with self.assertRaises(ValueError):c.apply(bad)

    def test_multiple_overlapping_findings_and_offsets_are_host_owned(self):
        fields={'body':'Keep A. Bad one. Keep B. Bad two. Keep C.'}
        c=ArtifactRevisionContract.create(fields,[{'field':'body','quote':'Bad one.'},
            {'field':'body','quote':'Bad one'}, {'field':'body','quote':'Bad two.'}],['body'])
        self.assertEqual(len(c.spans),2)
        out=c.apply({'replacements':{'P001':'Replacement one.','P002':'Replacement two.'}})
        self.assertEqual(out['body'],'Keep A. Replacement one. Keep B. Replacement two. Keep C.')

    def test_ambiguous_quote_expands_scope_explicitly_instead_of_patching_wrong_occurrence(self):
        c=ArtifactRevisionContract.create({'body':'Same. Other. Same.'},[{'field':'body','quote':'Same.'}],['body'])
        self.assertEqual(c.payload()[0]['original'],'Same. Other. Same.')
        with self.assertRaises(ValueError):ArtifactRevisionContract.create({'body':'Actual'},[{'field':'body','quote':'Foreign'}],['body'])

    def test_publication_repair_uses_exact_spans_and_keeps_full_request(self):
        post={'id':'1/Вт','topic':'Approved topic','caption':'Useful introduction. Bad detail. Useful conclusion.',
            'visual':'Existing storyboard','cta':'Existing CTA'}
        slot={'week':1,'day':'Вт','topic':post['topic']}
        request='Instruction '*700+'LAST REQUIRED DETAIL'
        def ask(model,system,user,**kw):
            value=json.loads(user)
            self.assertEqual(value['original_request'],request)
            self.assertEqual(value['revision_spans'],[{'id':'P001','field':'caption','original':'Bad detail.'}])
            return json.dumps({'replacements':{'P001':'Correct explanation.'}})
        out=repair_fields(ask,'writer',request,{},'not a source',slot,post,
            [{'field':'caption','quote':'Bad detail.','reason':'Factual explanation is incorrect.'}])
        self.assertEqual(out['caption'],'Useful introduction. Correct explanation. Useful conclusion.')
        for key in ('id','topic','visual','cta'):self.assertEqual(out[key],post[key])

if __name__=='__main__':unittest.main()
