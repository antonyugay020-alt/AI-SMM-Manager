"""State-derived actions and unambiguous host protocol; no user-word routing."""
import copy,json,random,string,unittest
from pathlib import Path
from unittest.mock import patch
from dialogue_runtime import DialogueContract,understand_dialogue,OPERATIONS
from test_r86_semantic_dialogue import decision


def state():
    return {'schema_version':1,'project_id':'p','project':{'name':'P','category':'','description':''},
        'documents':{},'messages':{},'sources':{'current':None},'selected_id':None,'ui_context':{}}


class ActionContractTests(unittest.TestCase):
    def test_empty_project_omits_nonexistent_document_and_pending_actions(self):
        c=DialogueContract('Anything',state());available=set(c.available_operations())
        self.assertTrue({'respond','clarify','create_bundle','create_text','project_control'}<=available)
        self.assertFalse(available&{'revise_saved','open_document','rename_document','resume','answer_clarification','image_edit','video_trim','inspect'})
        self.assertEqual(set(c.schema()['properties']['operation']['enum']),available)
        self.assertEqual(set(c.context()['available_tools']),available)
    def test_availability_is_independent_of_user_utterance(self):
        rng=random.Random(8701);s=state();baseline=DialogueContract('baseline',s).available_operations()
        for _ in range(20):
            text=''.join(rng.choice(string.ascii_letters+' ?!? Жяөқ') for _ in range(rng.randint(2,160)))
            self.assertEqual(DialogueContract(text,s).available_operations(),baseline)
    def test_document_tools_are_enabled_only_by_real_ids(self):
        s=state();s['documents']['doc']={'id':'doc','kind':'plans','source_id':None,'content':'Text','title':'T','sha256':'x','updated_at':'2026'}
        c=DialogueContract('Arbitrary',s)
        self.assertTrue({'revise_saved','open_document','rename_document','inspect'}<=set(c.available_operations()))
        self.assertEqual(c.schema()['properties']['target_ids']['items']['enum'],['doc'])
    def test_pending_is_a_resource_without_blocking_new_creation(self):
        s=state();s['sources']['pending']={'request':'Old','question':'Q','revision_id':None}
        c=DialogueContract('New',s)
        self.assertTrue({'resume','answer_clarification','create_text','create_bundle'}<=set(c.available_operations()))
        fresh=c.validate(decision('create_text',artifact_kind='plans'))
        self.assertEqual(fresh.action['source_id'],'current')
        with self.assertRaises(ValueError):c.validate(decision('create_text',artifact_kind='plans',source_id='pending'))
    def test_clarification_source_enables_answer_without_fake_execution_checkpoint(self):
        s=state();s['sources']['clarification']={'request':'Original','question':'Q'}
        self.assertIn('answer_clarification',DialogueContract('Answer',s).available_operations())
        self.assertNotIn('resume',DialogueContract('Answer',s).available_operations())
    def test_media_availability_matches_file_type_and_tool(self):
        s=state();s['ui_context']={'source_path':'photo.JPG','capabilities':{'image_cloud_optional':True,'video_trim_ffmpeg':True}}
        self.assertIn('image_edit',DialogueContract('Arbitrary',s).available_operations())
        self.assertNotIn('video_trim',DialogueContract('Arbitrary',s).available_operations())
        s['ui_context']['source_path']='movie.mp4'
        self.assertIn('video_trim',DialogueContract('Arbitrary',s).available_operations())
        s['ui_context']['capabilities']['video_trim_ffmpeg']=False
        self.assertNotIn('video_trim',DialogueContract('Arbitrary',s).available_operations())
    def test_missing_resource_action_is_rejected_even_if_model_ignores_enum(self):
        for op in ('revise_saved','open_document','rename_document','resume','answer_clarification','image_edit','video_trim'):
            with self.subTest(op=op),self.assertRaises(ValueError):DialogueContract('Anything',state()).validate(decision(op))
    def test_fixed_output_type_is_derived_from_selected_operation(self):
        c=DialogueContract('Any language',state());schedule={'weeks':2,'per_week':2,'total':4}
        for kind in ('none','bundle','strategy','calendar','plans','tasks','analytics'):
            raw=decision('create_bundle',artifact_kind=kind,schedule=schedule)
            before=copy.deepcopy(raw);plan=c.validate(raw)
            self.assertEqual(plan.action['operation'],'create_bundle');self.assertEqual(plan.action['artifact_kind'],'bundle')
            self.assertEqual(plan.action['schedule'],schedule);self.assertEqual(raw,before)
            self.assertEqual(bool(plan.normalizations),kind!='bundle')
    def test_distinct_create_text_destination_is_not_reclassified(self):
        c=DialogueContract('Any language',state())
        for kind in ('strategy','calendar','plans','tasks','analytics'):
            plan=c.validate(decision('create_text',artifact_kind=kind))
            self.assertEqual(plan.action['artifact_kind'],kind);self.assertFalse(plan.normalizations)
    def test_derived_kind_does_not_relax_schedule_or_resource_validation(self):
        c=DialogueContract('Any language',state())
        for extra in (dict(schedule=None),dict(target_ids=['invented']),dict(schedule={'weeks':2,'per_week':2,'total':5})):
            value=decision('create_bundle',artifact_kind='strategy',schedule={'weeks':2,'per_week':2,'total':4});value.update(extra)
            with self.assertRaises(ValueError):c.validate(value)
    def test_contract_feedback_is_system_metadata_not_new_user_request(self):
        c=state();calls=[]
        responses=[decision('create_bundle',artifact_kind='strategy',schedule=None),decision('create_bundle',artifact_kind='strategy',schedule={'weeks':1,'per_week':1,'total':1})]
        def transport(payload,**kw):calls.append(copy.deepcopy(payload));return {'message':{'content':json.dumps(responses.pop(0))}}
        with patch('ollama_resilience.request_chat',side_effect=transport):plan=understand_dialogue('Original exact task',c,model='fixture')
        self.assertEqual(plan.utterance,'Original exact task');self.assertEqual(plan.action['operation'],'create_bundle')
        feedback=calls[1]['messages'][-1];self.assertEqual(feedback['role'],'system')
        data=json.loads(feedback['content']);self.assertEqual(data['origin'],'application_validation');self.assertTrue(data['user_message_unchanged'])
        self.assertEqual(data['rejected_operation'],'create_bundle')
        self.assertEqual([m for m in calls[0]['messages'] if m['role']=='user'],[m for m in calls[1]['messages'] if m['role']=='user'])
    def test_bounded_repair_never_invents_intent_after_two_invalid_answers(self):
        with patch('ollama_resilience.request_chat',return_value={'message':{'content':json.dumps(decision('revise_saved'))}}) as transport:
            with self.assertRaises(ValueError):understand_dialogue('Unchanged task',state(),model='fixture')
        self.assertEqual(transport.call_count,2)
    def test_received_first_model_response_is_accepted_without_second_call(self):
        data=json.loads((Path(__file__).parent/'r87_received_action.json').read_text())
        raw=json.loads(data['responses'][0]['message']['content'])
        self.assertEqual(raw['operation'],'create_bundle');self.assertEqual(raw['artifact_kind'],'strategy')
        with patch('ollama_resilience.request_chat',return_value=data['responses'][0]) as transport:
            plan=understand_dialogue(data['utterance'],data['snapshot'],model='fixture')
        self.assertEqual(transport.call_count,1);self.assertEqual(plan.action['artifact_kind'],'bundle')
        self.assertEqual(plan.action['schedule'],{'weeks':13,'per_week':3,'total':39})
        self.assertEqual(plan.utterance,data['utterance']);self.assertTrue(plan.normalizations)
    def test_received_second_wrong_operation_is_excluded_by_resource_contract(self):
        data=json.loads((Path(__file__).parent/'r87_received_action.json').read_text());c=DialogueContract(data['utterance'],data['snapshot'])
        self.assertNotIn('revise_saved',c.schema()['properties']['operation']['enum'])
        self.assertNotIn('revise_saved',c.context()['available_tools'])
        with self.assertRaises(ValueError):c.validate(json.loads(data['responses'][1]['message']['content']))

if __name__=='__main__':unittest.main()
