from contextlib import closing
"""AI owns content meaning; technical contracts remain strict. Opinions injected."""
import json,random,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from task_semantics import semantic_task,ai_content_review_active
from strategy_recovery import produce_checked_strategy
from strategy_integrity import (strategy_integrity_issues,normalize_explicit_cadence,
 normalize_unprovided_category,bind_explicit_task_scope,separate_embedded_publication_section)
from publication_schema import POST_FIELDS
from publication_writer import validate_single,validate_week
from editorial_guard import editorial_risks,add_production_materials,copy_too_similar
from editorial_integrity import content_consistency_issues
from content_quality_engine import critical_issues
from task_artifacts import verify_saved_artifact
import independent_acceptance_editor as editor
from substance_fixture import assessed_reply
F=json.loads(Path(__file__).with_name('r97_received_budget.json').read_text(encoding='utf-8'))
CTX={'semantic_authorized':True,'semantic_execution_kind':'bundle','semantic_schedule':F['schedule']}
def post(i='1/Пн'):return {'id':i,**{k:'Short / 简短 / произвольный текст' for k in POST_FIELDS}}
def slot(i=1):return {'week':i,'day':'Пн','topic':post()['topic']}
def forbidden(*a,**kw):raise AssertionError('Legacy prose authority / unexpected author call')
def wire(payload,**kw):return {'message':{'content':json.dumps(assessed_reply(payload,{'pass':True,'issues':[]}))}}
class AIContentAuthorityTests(unittest.TestCase):
 def produce(self,body=F['strategy'],**kw):
  return produce_checked_strategy(F['request'],3,kw.pop('generate',lambda *a:body),
   kw.pop('validate',forbidden),attempts=kw.pop('attempts',1),**kw)
 def test_exact_received_distribution_reaches_AI_unchanged(self):
  calls=[]
  with semantic_task(CTX),patch('strategy_integrity.scan_unsupported_business_claims',side_effect=forbidden):
   out=self.produce(review=lambda b:calls.append(b) or {'pass':True,'issues':[]})
  self.assertTrue(out[1]);self.assertEqual(calls,[F['strategy']]);self.assertEqual(out[0],F['strategy'])
 def test_arbitrary_languages_amounts_topics_follow_same_boundary(self):
  rng=random.Random(97)
  for topic in ('Дизайн','café','图书','Звук','گرافیک'):
   body=topic+' '+str(rng.randrange(1,900000))+' тенге. Мы всегда рядом. 19 публикаций в неделю.'
   with self.subTest(topic=topic),semantic_task(CTX):self.assertEqual(self.produce(body,review=lambda b:{'pass':True,'issues':[]})[:2],(body,True))
 def test_no_reviewer_cannot_approve(self):
  with semantic_task(CTX):out=self.produce()
  self.assertFalse(out[1]);self.assertIn('AI-редактор',out[2])
 def test_AI_rejection_not_waived_for_budget_or_topic(self):
  for text in ('Факт компании не подтверждён.','Распределение бюджета противоречит задаче.','Описан опасный способ действия.'):
   with semantic_task(CTX):out=self.produce(review=lambda b:{'pass':False,'issues':[{'reason':text,'quote':b[:10]}]})
   self.assertFalse(out[1]);self.assertIn(text,out[2])
 def test_unavailable_review_never_success(self):
  with semantic_task(CTX):out=self.produce(review=lambda b:{'pass':True,'issues':[],'review_state':'unavailable'})
  self.assertFalse(out[1]);self.assertIn('недоступна',out[2])
 def test_malformed_review_fails_closed(self):
  for verdict in (None,True,{}, {'pass':1,'issues':[]},{'pass':True,'issues':None}):
   with self.subTest(verdict=verdict),semantic_task(CTX):out=self.produce(review=lambda b:verdict)
   self.assertFalse(out[1])
 def test_empty_or_malformed_draft_never_reaches_review(self):
  for body in ('',None,{},'   '):
   calls=[]
   with semantic_task(CTX):out=self.produce(body,review=lambda b:calls.append(b) or {'pass':True,'issues':[]})
   self.assertFalse(out[1]);self.assertEqual(calls,[])
 def test_host_schedule_mismatch_remains_blocking(self):
  with semantic_task(CTX):self.assertTrue(strategy_integrity_issues(F['strategy'],F['request'],per_week=2))
 def test_normalizers_cannot_rewrite_semantic_text(self):
  text='Категория: X\n19 публикаций в неделю\n## Календарь\n## Публикация 1\nПроизвольный пример.'
  with semantic_task(CTX):
   for fn,args in [(normalize_explicit_cadence,(text,3)),(normalize_unprovided_category,(text,F['request'])),(bind_explicit_task_scope,(text,F['request'],3)),(separate_embedded_publication_section,(text,))]:self.assertEqual(fn(*args),(text,[]))
 def test_rejected_checkpoint_reviewed_before_edit(self):
  calls=[];saved=[]
  with semantic_task(CTX):out=self.produce(generate=forbidden,repair=forbidden,draft={'content':F['strategy'],'reason':F['reason']},
   review=lambda b:calls.append(b) or {'pass':True,'issues':[]},checkpoint=lambda *a:saved.append(a))
  self.assertTrue(out[1]);self.assertEqual(calls,[F['strategy']]);self.assertEqual(saved[-1],out)
 def test_checkpoint_edit_uses_current_AI_findings(self):
  old=F['strategy'];fixed=old+'\nAn explicit revision.';reviews=[];repairs=[]
  def review(b):
   reviews.append(b);return {'pass':b==fixed,'issues':[] if b==fixed else [{'reason':'Current AI finding','quote':b[:20]}]}
  def repair(b,why,n):repairs.append((b,why));return fixed
  with semantic_task(CTX):out=self.produce(draft={'content':old,'reason':'obsolete host opinion'},repair=repair,review=review)
  self.assertTrue(out[1]);self.assertEqual(reviews,[old,fixed]);self.assertEqual(repairs[0][0],old);self.assertIn('Current AI finding',repairs[0][1]);self.assertNotIn('obsolete',repairs[0][1])
 def test_unchanged_rejected_checkpoint_stops_bounded(self):
  calls=[]
  with semantic_task(CTX):out=self.produce(draft={'content':F['strategy'],'reason':'old'},repair=lambda *a:calls.append(a) or a[0],attempts=3,
    review=lambda b:{'pass':False,'issues':[{'reason':'still rejected','quote':b[:5]}]})
  self.assertFalse(out[1]);self.assertEqual(len(calls),1)
 def test_semantic_context_does_not_leak(self):
  self.assertFalse(ai_content_review_active())
  with semantic_task(CTX):
   self.assertTrue(ai_content_review_active())
   with semantic_task({}):self.assertFalse(ai_content_review_active())
   self.assertTrue(ai_content_review_active())
  self.assertFalse(ai_content_review_active())
 def test_publication_not_rejected_by_phrase_or_length(self):
  value=post();value.update(caption='Мы всегда рядом.',visual='A → B.',kpi='Количество комментариев')
  with semantic_task(CTX):
   self.assertEqual(validate_single(value,slot()),[]);self.assertEqual(critical_issues(value),[])
   self.assertEqual(editorial_risks(value,F['request']),[]);self.assertEqual(content_consistency_issues(value,slot(),F['request']),[])
 def test_missing_or_nonstring_fields_fail(self):
  with semantic_task(CTX):
   for field in ('id',*POST_FIELDS):
    for value in (None,[],2,''):
     item=post();item[field]=value;self.assertTrue(validate_single(item,slot()))
 def test_wrong_slot_or_edited_plan_topic_fails(self):
  with semantic_task(CTX):
   self.assertTrue(validate_single(post('2/Пн'),slot()))
   item=post();item['topic']='Other';self.assertTrue(validate_single(item,slot()))
 def test_batch_order_not_semantic_requirement(self):
  with semantic_task(CTX):self.assertEqual(validate_week([post('2/Пн'),post()],[slot(),slot(2)]),[])
 def test_duplicate_unknown_IDs_wrong_count_fail(self):
  with semantic_task(CTX):
   for posts in ([post(),post()],[post(),post('99/Пн')],[post()]):self.assertTrue(validate_week(posts,[slot(),slot(2)]))
 def test_invalid_or_duplicate_slots_fail(self):
  with semantic_task(CTX):
   for slots in ([slot(),slot()],[slot(),{}]):self.assertTrue(validate_week([post(),post('2/Пн')],slots))
 def test_host_does_not_manufacture_production_fields(self):
  item=post();item.pop('materials');item.pop('measurement')
  with semantic_task(CTX):
   self.assertEqual(add_production_materials(item,slot()),(item,False));self.assertTrue(validate_single(item,slot()))
 def test_fuzzy_copy_opinion_delegated_to_AI(self):
  with semantic_task(CTX):self.assertFalse(copy_too_similar('A'*100,['A'*99+'B']))
 def test_native_final_editor_full_inventory_no_regex(self):
  with patch.object(editor,'request_chat',side_effect=wire),patch('factual_claim_audit.scan_unsupported_business_claims',side_effect=forbidden),\
       patch.object(editor,'_review_artifact_form',return_value=[]) as form,patch.object(editor,'_inventory_business_assertions',return_value=[]) as inventory:
   out=editor.call_editor(F['request'],F['strategy'],'',allow_partial=True,require_inventory=True)
  self.assertTrue(out['pass']);self.assertTrue(out['substance_review']['complete']);self.assertTrue(out['substance_review']['controls']['passed'])
  self.assertTrue(form.called);inventory.assert_called_once()
 def test_native_inventory_failure_blocks(self):
  with patch.object(editor,'request_chat',side_effect=wire),patch.object(editor,'_review_artifact_form',return_value=[]),\
       patch.object(editor,'_inventory_business_assertions',side_effect=ValueError('Inventory unavailable')):
   out=editor.call_editor(F['request'],F['strategy'],'',allow_partial=True,require_inventory=True)
  self.assertFalse(out['pass']);self.assertEqual(out['review_state'],'unavailable')
 def test_native_source_rejection_still_blocks(self):
  finding={'document':'strategy','type':'invented_fact','quote':F['strategy'][:10],'reason':'Unverified company assertion.'}
  with patch.object(editor,'request_chat',side_effect=wire),patch.object(editor,'_review_artifact_form',return_value=[]),\
       patch.object(editor,'_inventory_business_assertions',return_value=[finding]):
   out=editor.call_editor(F['request'],F['strategy'],'',allow_partial=True,require_inventory=True,
    claim_adjudicator=lambda req,findings,**kw:{'blocking':findings,'supported':[]})
  self.assertFalse(out['pass']);self.assertEqual(out['issues'],[finding])
 def test_plan_fuzzy_overlap_is_not_authority(self):
  from semantic_brief import topic_repeats
  with semantic_task(CTX):
   self.assertFalse(topic_repeats('Как выбрать предмет по размеру и цвету',['Как выбрать предмет по размеру и форме']))
   self.assertTrue(topic_repeats('Точная тема',['Точная тема']))
 def test_replacement_uses_AI_instead_of_substring_lock(self):
  from semantic_brief import propose_topic_replacement
  old={'week':1,'day':'Пн','topic':'Неудачная тема','format':'Карусель'}
  def ask(*a,**kw):return json.dumps({'alternatives':[{'topic':'Сон','brief':'A','goal':'B','audience':'C'}]})
  with semantic_task(CTX):
   out=propose_topic_replacement(ask,'author','Не используй Неудачная тема','Strategy',{},old,[],['AI rejected old topic'])
  self.assertEqual(out['topic'],'Сон')
 def test_invalid_slot_argument_and_batch_types_fail(self):
  with semantic_task(CTX):
   self.assertTrue(validate_single(post(),None))
   for slots in (None,{},'wrong'):self.assertTrue(validate_week([post()],slots))
 def test_semantic_plan_count_finding_not_dropped_by_words(self):
  from plan_governance import govern_plan
  value={**slot(),'brief':'A','goal':'B','audience':'C','format':'Карусель','platform':'Instagram'}
  reply={'issues':[{'id':'1/Пн','dimension':'topic','reason':'В плане не хватает публикаций по важной цели.'}]}
  with semantic_task(CTX):
   _,unresolved,available=govern_plan(lambda *a,**kw:json.dumps(reply),'author','Request',{},'Strategy',[value],[],expected_ids=['1/Пн'],review_model='editor',allow_replan=False)
  self.assertTrue(available);self.assertIn('1/Пн',unresolved)
 def test_short_AI_topic_reaches_plan_review_and_cache(self):
  from semantic_brief import plan_slots
  calls=[]
  def ask(*a,**kw):calls.append(a);return json.dumps({'posts':[{'topic':'Сон','brief':'A','goal':'B','audience':'C'}]})
  with semantic_task(CTX):
   slots=plan_slots(ask,'author','任意要求','Strategy',{'channels':'Instagram','channels_basis':'explicit'},1,per_week=1)
   restored=plan_slots(forbidden,'author','任意要求','Strategy',{'channels':'Instagram','channels_basis':'explicit'},1,per_week=1,existing_slots=slots)
  self.assertEqual(restored,slots);self.assertEqual(slots[0]['topic'],'Сон');self.assertEqual(len(calls),1)
 def test_AI_still_reviews_short_topic_plan(self):
  from plan_governance import govern_plan
  value={**slot(),'topic':'Сон','brief':'A','goal':'B','audience':'C','format':'Карусель','platform':'Instagram'}
  calls=[]
  with semantic_task(CTX):
   _,unresolved,available=govern_plan(lambda *a,**kw:calls.append(a) or json.dumps({'issues':[]}),
    'author','Request',{},'Strategy',[value],[],expected_ids=['1/Пн'],review_model='editor',allow_replan=False)
  self.assertEqual(unresolved,{});self.assertTrue(available);self.assertTrue(calls)
 def test_exact_line_endings_export_readback(self):
  with tempfile.TemporaryDirectory() as home:
   root=Path(home)
   with closing(sqlite3.connect(':memory:')) as db:
    db.execute('CREATE TABLE manager_documents(id,project_id,kind,title,content,updated_at)')
    def export(*args):
     text=root/'out.txt';pdf=root/'out.pdf';text.write_bytes(args[-1].encode());pdf.write_bytes(b'%PDF-1.4\n%%EOF');return text,pdf
    for ending in ('\n','\r\n','\r','\n\r\n'):
     body='甲'+ending+'Бюджет '+ending+'900';db.execute('DELETE FROM manager_documents');db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?)',('doc','p','strategy','Title',body,'1'))
     self.assertTrue(verify_saved_artifact(db,'p','strategy',body,export))
     def altered(*args):
      text,pdf=export(*args);text.write_bytes((body+' changed').encode());return text,pdf
     self.assertFalse(verify_saved_artifact(db,'p','strategy',body,altered))
if __name__=='__main__':unittest.main()
