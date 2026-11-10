"""AI-issued media IDs and typed local actions, without lexical routing or APIs."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from application_bootstrap import install_schema
from conversation_runtime import ConversationRuntime
from dialogue_runtime import DialogueContract,dialogue_snapshot,local_media_request,understand_dialogue
from dialogue_execution import validate_live_plan
from local_media_contract import TASKS
from project_media_sources import media_catalog

OWNER='a'*32
OTHER='b'*32


class SemanticMediaContractTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);install_schema(self.db)
        for owner in (OWNER,OTHER):self.db.execute('INSERT INTO projects(id,name,category,description,created_at) VALUES(?,?,?,?,?)',(owner,'Project','','','now'))
        for ident,owner,name in [('photo',OWNER,'one.png'),('clip',OWNER,'one.mp4'),('second',OWNER,'two.mp4'),('foreign',OTHER,'private.png')]:
            self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',(ident,owner,name,'/project/'+owner+'/originals/'+name,'digest','now'))
        self.db.commit()
        self.message='Продолжай с этими материалами, сохрани нужный результат'
        receipt=ConversationRuntime(self.db).accept(OWNER,self.message)
        self.turn=receipt.dialogue_turn_id
        self.snapshot=dialogue_snapshot(self.db,OWNER,current_message_id=receipt.message_id,
            ui_context={'capabilities':{'local_media_tasks':list(TASKS)}})
        self.db.commit()

    def media(self,task,source_ids):
        return {'task':task,'source_ids':source_ids,'width':128,'height':96,
                'start':.1 if task=='video_cut' else None,'end':.4 if task=='video_cut' else None,
                'seconds_per_image':.5 if task=='video_from_images' else None}

    def action(self,media):
        return {'operation':'local_media','artifact_kind':'none','target_ids':[],'source_id':'current',
            'message_ids':[],'reply':'','reason':'Выполнить указанную пользователем операцию над выбранными исходниками',
            'include_current_instruction':True,'new_title':'','control_scope':'none','use_reference':False,
            'start':None,'end':None,'offset':0,'length':1000,'schedule':None,'requested_media':True,'reuse_structure':False,'media':media}

    def test_all_local_tools_validate_issued_project_sources(self):
        for task in TASKS:
            sources=['media:asset:photo'] if task in ('image_resize','video_from_images') else ['media:asset:clip','media:asset:second'] if task=='video_montage' else ['media:asset:clip']
            contract=DialogueContract(self.message,self.snapshot,self.turn)
            plan=contract.validate(self.action(self.media(task,sources)))
            request=local_media_request(plan.snapshot,plan.action['media'])
            self.assertEqual(request['task'],task)
            validate_live_plan(self.db,OWNER,plan)
        public=contract.context()['media_sources']
        self.assertTrue(public);self.assertTrue(all(set(item)=={'id','label','kind'} for item in public))
        self.assertFalse(any('foreign' in item['id'] for item in public))

    def test_paths_foreign_ids_duplicate_ids_and_wrong_kinds_fail_closed(self):
        for ids in (['/etc/passwd'],['media:asset:foreign'],['media:asset:photo','media:asset:photo'],['media:asset:clip']):
            with self.assertRaises(ValueError):DialogueContract(self.message,self.snapshot,self.turn).validate(self.action(self.media('image_resize',ids)))

    def test_stale_source_path_or_label_rejected_before_execution(self):
        action=self.action(self.media('image_resize',['media:asset:photo']))
        plan=DialogueContract(self.message,self.snapshot,self.turn).validate(action)
        self.db.execute("UPDATE assets SET original_name='Renamed source' WHERE id='photo'");self.db.commit()
        with self.assertRaises(ValueError):validate_live_plan(self.db,OWNER,plan)

    def test_unsupported_ai_styling_cannot_be_smuggled_into_local_dispatch(self):
        action=self.action(self.media('image_style',['media:asset:photo']))
        with self.assertRaises(ValueError):DialogueContract(self.message,self.snapshot,self.turn).validate(action)
        snapshot=copy.deepcopy(self.snapshot);snapshot['ui_context']['capabilities']['local_media_tasks']=[]
        self.assertNotIn('local_media',DialogueContract(self.message,snapshot,self.turn).available_operations())

    def test_only_resource_compatible_capabilities_are_advertised(self):
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['media']={key:item for key,item in snapshot['media'].items() if item['kind']=='image'}
        contract=DialogueContract(self.message,snapshot,self.turn)
        self.assertIn('media',contract.schema()['required'])
        variants=contract.schema()['properties']['media']['anyOf'][1:]
        allowed=[variant['properties']['task']['enum'][0] for variant in variants]
        self.assertEqual(set(allowed),{'image_resize','video_from_images'})

    def test_native_schema_encodes_task_source_kinds_cardinality_and_unused_nulls(self):
        contract=DialogueContract(self.message,self.snapshot,self.turn)
        variants=contract.schema()['properties']['media']['anyOf'][1:]
        self.assertEqual(len(variants),len(TASKS))
        for variant in variants:
            properties=variant['properties'];task=properties['task']['enum'][0]
            kind='image' if task in ('image_resize','video_from_images') else 'video'
            ids=properties['source_ids']['items']['enum']
            self.assertTrue(ids)
            self.assertTrue(all(self.snapshot['media'][ident]['kind']==kind for ident in ids))
            for key in ('start','end'):
                self.assertEqual(properties[key]['type'],'number' if task=='video_cut' else 'null')
            if task!='video_from_images':self.assertEqual(properties['seconds_per_image'],{'type':'null'})
            if task in ('image_resize','video_cut','video_format'):self.assertEqual(properties['source_ids']['maxItems'],1)

    def test_actual_semantic_decoder_accepts_media_action_for_arbitrary_wording(self):
        action=self.action(self.media('video_montage',['media:asset:clip','media:asset:second']))
        with patch('central_dialogue_transport.request_central_dialogue',return_value={'message':{'content':json.dumps(action)}}) as model:
            plan=understand_dialogue(self.message,self.snapshot,model='local-fixture',turn_id=self.turn)
        model.assert_called_once();self.assertEqual(plan.action['media']['task'],'video_montage')
        self.assertEqual(plan.utterance,self.message)

if __name__=='__main__':unittest.main()
