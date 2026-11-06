"""Typed semantic boundary, provenance, recovery and absence of lexical GUI routes."""
import ast,copy,ctypes,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dialogue_runtime import DialogueContract,dialogue_snapshot,understand_dialogue,save_turn,FIELDS
from dialogue_execution import compile_execution,validate_live_plan
from task_semantics import semantic_task,current_semantics


def decision(op='respond',**kw):
    value=dict(operation=op,artifact_kind='none',target_ids=[],source_id='current',message_ids=[],reply='Какой результат нужен?',
        reason='Решение по контексту переписки.',include_current_instruction=False,new_title='',control_scope='none',use_reference=False,
        start=None,end=None,offset=0,length=20000,schedule=None,requested_media=False,reuse_structure=False)
    value.update(kw);assert set(value)==set(FIELDS);return value


class SemanticDialogueTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('''CREATE TABLE projects(id TEXT,name TEXT,category TEXT,description TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT);
        CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT,updated_at TEXT);
        CREATE TABLE ai_pending_tasks(project_id TEXT,request TEXT,question TEXT,revision_id TEXT);
        INSERT INTO projects VALUES('a','A','QA','Known A'),('b','B','QA','Known B');
        INSERT INTO messages VALUES(1,'a','user','Original task','2020'),(2,'a','assistant','Derived proposal','2020'),(3,'b','user','Private B','2020');
        INSERT INTO manager_documents VALUES('doc','a','plans','Draft','Original body','2020'),('foreign','b','plans','Private','Secret','2020');''')
        self.snapshot=dialogue_snapshot(self.db,'a',selected_id='doc')
        self.contract=DialogueContract('давай иначе',self.snapshot)
    def tearDown(self):self.db.close()
    def test_any_utterance_has_same_typed_protocol(self):
        for text in ('?!','Третий вариант, только спокойнее','А разве нельзя сделать сразу?','Make it useful','Мне бы увидеть разницу'):
            c=DialogueContract(text,self.snapshot);p=c.validate(decision())
            self.assertEqual(p.utterance,text);self.assertEqual(p.action['operation'],'respond')
    def test_selected_project_isolation(self):
        self.assertNotIn('foreign',self.snapshot['documents']);self.assertNotIn('message:3',self.snapshot['messages'])
        with self.assertRaises(ValueError):self.contract.validate(decision('open_document',target_ids=['foreign']))
    def test_malformed_arrays_and_unhashable_fields(self):
        for field,bad in [('operation',{}),('source_id',[]),('target_ids',[{}]),('message_ids',[[]]),('control_scope',{})]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.contract.validate(decision(**{field:bad}))
    def test_missing_extra_and_wrong_parameter_types(self):
        for key in FIELDS:
            item=decision();item.pop(key)
            with self.subTest(key=key),self.assertRaises(ValueError):self.contract.validate(item)
        for params in ({'extra':True},{'use_reference':1},{'requested_media':'false'},{'offset':True},{'end':float('nan')},{'start':-1}):
            item=decision();item.update(params)
            with self.assertRaises(ValueError):self.contract.validate(item)
    def test_duplicate_or_missing_tool_targets(self):
        for params in [dict(operation='revise_saved'),dict(operation='open_document'),dict(target_ids=['doc','doc']),
                       dict(operation='rename_document',target_ids=['doc']),dict(operation='project_control')]:
            with self.assertRaises(ValueError):self.contract.validate(decision(**params))
    def test_no_fabricated_pending_source(self):
        for op in ('resume','answer_clarification'):
            with self.assertRaises(ValueError):self.contract.validate(decision(op,artifact_kind='plans',source_id='pending'))
    def test_bundle_schedule_must_be_complete_and_consistent(self):
        for schedule in (None,{},dict(weeks=2,per_week=2,total=3),dict(weeks=True,per_week=1,total=1),dict(weeks=27,per_week=1,total=27)):
            with self.assertRaises(ValueError):self.contract.validate(decision('create_bundle',artifact_kind='bundle',schedule=schedule))
    def test_stale_document_and_project_are_rejected(self):
        plan=self.contract.validate(decision('open_document',target_ids=['doc']))
        self.db.execute("UPDATE manager_documents SET content='Edited' WHERE id='doc'")
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',plan)
        with self.assertRaises(ValueError):validate_live_plan(self.db,'b',plan)
        self.db.execute("UPDATE projects SET description='Changed' WHERE id='a'")
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',self.contract.validate(decision()))
    def test_source_snapshot_does_not_follow_external_mutation(self):
        self.snapshot['documents']['doc']['content']='Altered'
        self.assertEqual(self.contract.snapshot['documents']['doc']['content'],'Original body')
    def test_inspect_reads_exact_document_and_marks_ranges(self):
        plan=self.contract.validate(decision('inspect',target_ids=['doc'],offset=2,length=3))
        row=self.contract.inspect(plan)[0];self.assertEqual(row['text'],'igi');self.assertFalse(row['complete'])
        with self.assertRaises(ValueError):self.contract.inspect(self.contract.validate(decision('inspect',target_ids=['doc'],offset=999)))
    def test_source_can_be_read_without_document(self):
        plan=self.contract.validate(decision('inspect',source_id='message:1'))
        row=self.contract.inspect(plan)[0];self.assertEqual(row['text'],'Original task');self.assertTrue(row['complete'])
    def test_long_source_preview_explicitly_requires_reading(self):
        self.db.execute("UPDATE messages SET content=? WHERE id=1",('x'*2200,))
        c=DialogueContract('Что там?',dialogue_snapshot(self.db,'a'))
        source=c.context()['request_sources']['message:1']
        self.assertTrue(source['excerpt_only']);self.assertEqual(source['request_characters'],2200)
        self.assertEqual(len(c.inspect(c.validate(decision('inspect',source_id='message:1')))[0]['text']),2200)
    def test_model_can_inspect_then_choose_without_host_language_rules(self):
        replies=[decision('inspect',target_ids=['doc']),decision('open_document',target_ids=['doc'])]
        calls=[]
        def model(payload,**kw):
            calls.append(copy.deepcopy(payload));return {'message':{'content':json.dumps(replies.pop(0))}}
        with patch('ollama_resilience.request_chat',side_effect=model):
            plan=understand_dialogue('а это?',self.snapshot,model='fixture')
        self.assertEqual(plan.action['operation'],'open_document');self.assertEqual(len(calls),2)
        self.assertIn('Original body',calls[-1]['messages'][-1]['content'])
    def test_model_contract_correction_bounded_without_lexical_fallback(self):
        with patch('ollama_resilience.request_chat',return_value={'message':{'content':'invalid'}}) as model:
            with self.assertRaises(ValueError):understand_dialogue('создай стратегию и контент-план',self.snapshot,model='fixture')
            self.assertEqual(model.call_count,2)
    def test_model_outage_propagates_without_side_effects(self):
        with patch('ollama_resilience.request_chat',side_effect=RuntimeError('Offline')):
            with self.assertRaisesRegex(RuntimeError,'Offline'):understand_dialogue('сделай',self.snapshot,model='fixture')
        self.assertEqual(self.db.execute('SELECT count(*) FROM manager_documents').fetchone()[0],2)
    def test_inspect_budget_is_bounded(self):
        value=decision('inspect',target_ids=['doc'])
        with patch('ollama_resilience.request_chat',return_value={'message':{'content':json.dumps(value)}}) as model:
            with self.assertRaises(ValueError):understand_dialogue('прочитай',self.snapshot,model='fixture')
            self.assertEqual(model.call_count,5)
    def test_explicit_user_sources_and_assistant_memory_have_distinct_authority(self):
        plan=self.contract.validate(decision('create_text',artifact_kind='analytics',message_ids=['message:1','message:2']))
        req,ctx=compile_execution(self.db,plan,'/tmp/not-written')
        self.assertIn('Original task',req);self.assertNotIn('Derived proposal',req)
        verified=ctx['verified_project_materials']
        self.assertTrue(any(r['content']=='Original task' for r in verified));self.assertFalse(any(r['content']=='Derived proposal' for r in verified))
        self.assertEqual(ctx['recent_messages'][-1]['role'],'assistant')
    def test_clarification_restores_original_and_exact_answer(self):
        self.db.execute('INSERT INTO ai_dialogue_questions(project_id,request,question) VALUES(?,?,?)',('a','Неявное поручение','Какой результат нужен?'))
        c=DialogueContract('Анализ с примерами',dialogue_snapshot(self.db,'a'))
        plan=c.validate(decision('answer_clarification',artifact_kind='analytics',source_id='clarification'))
        req,ctx=compile_execution(self.db,plan,'/tmp/not-written')
        for value in ('Неявное поручение','Анализ с примерами'):self.assertIn(value,req)
        self.assertEqual(ctx['clarification_question'],'Какой результат нужен?')
        self.assertEqual(ctx['task_mode'],'clarification')
    def test_new_task_preserves_unselected_pending_work(self):
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',('a','Old request','Question',None))
        c=DialogueContract('New work',dialogue_snapshot(self.db,'a'))
        plan=c.validate(decision('create_text',artifact_kind='plans'))
        request,context=compile_execution(self.db,plan,'/tmp/not-written')
        # Planning must not mutate either the old task or the newly requested task.
        self.assertEqual(request,'New work')
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],'Old request')
        from dialogue_execution import persist_execution
        persist_execution(self.db,plan,request,context)
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],'New work')
        saved=self.db.execute('SELECT request,status FROM ai_task_registry WHERE project_id=? ORDER BY request',('a',)).fetchall()
        self.assertEqual(saved,[('New work','active'),('Old request','paused')])
    def test_resume_uses_immutable_request_not_resume_words(self):
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',('a','Old request','Question',None))
        c=DialogueContract('ну теперь закончи',dialogue_snapshot(self.db,'a'))
        plan=c.validate(decision('resume',artifact_kind='plans',source_id='pending',include_current_instruction=True,message_ids=['message:1']))
        req,ctx=compile_execution(self.db,plan,'/tmp/not-written')
        self.assertEqual(req,'Old request');self.assertEqual(ctx['task_mode'],'resume')
    def test_single_revision_preserves_unapproved_source(self):
        plan=self.contract.validate(decision('revise_saved',artifact_kind='plans',target_ids=['doc']))
        req,ctx=compile_execution(self.db,plan,'/tmp/not-written')
        self.assertEqual(ctx['revision_document']['content'],'Original body');self.assertEqual(ctx['task_mode'],'revision')
        self.assertEqual(self.db.execute("SELECT content FROM manager_documents WHERE id='doc'").fetchone()[0],'Original body')
    def test_clarification_of_revision_keeps_receipt_bound_original(self):
        from dialogue_runtime import digest
        self.db.execute('CREATE TABLE ai_execution_journal(id INTEGER,project_id TEXT,request TEXT,artifact_kind TEXT,artifact_sha256 TEXT,status TEXT,details TEXT)')
        self.db.execute('INSERT INTO ai_execution_journal VALUES(?,?,?,?,?,?,?)',(1,'a','Original full assignment','plans',digest('Original body'),'saved',json.dumps({'artifact_id':'doc'})))
        self.db.execute('INSERT INTO ai_dialogue_questions(project_id,request,question) VALUES(?,?,?)',('a','Можно улучшить?','Что изменить в выбранном документе?'))
        c=DialogueContract('Поясни причину проще',dialogue_snapshot(self.db,'a'))
        plan=c.validate(decision('answer_clarification',artifact_kind='plans',source_id='clarification',target_ids=['doc']))
        req,state=compile_execution(self.db,plan,'/tmp/not-written')
        for text in ('Original full assignment','Можно улучшить?','Поясни причину проще'):self.assertIn(text,req)
        self.assertIn('Что изменить',state['clarification_question'])
        self.assertEqual(state['revision_document']['id'],'doc');self.assertEqual(state['task_mode'],'revision')
    def test_receipt_document_id_disambiguates_identical_body(self):
        from dialogue_runtime import digest
        from task_artifacts import original_document_request
        self.db.execute('CREATE TABLE ai_execution_journal(id INTEGER,project_id TEXT,request TEXT,artifact_kind TEXT,artifact_sha256 TEXT,status TEXT,details TEXT)')
        for ident,request,doc in [(1,'Correct original','doc'),(2,'Other assignment','another')]:
            self.db.execute('INSERT INTO ai_execution_journal VALUES(?,?,?,?,?,?,?)',(ident,'a',request,'plans',digest('Original body'),'saved',json.dumps({'artifact_id':doc})))
        self.assertEqual(original_document_request(self.db,'a','doc'),'Correct original')
        self.db.execute("UPDATE ai_execution_journal SET details='legacy'")
        self.assertIsNone(original_document_request(self.db,'a','doc'))
    def test_semantic_schedule_overrides_language_parser_and_resets(self):
        from content_requirements import publication_scope,requested_media_assets
        from professional_content import requested_weeks
        ctx=dict(semantic_authorized=True,semantic_execution_kind='bundle',semantic_schedule=dict(weeks=2,per_week=2,total=4),semantic_requested_media=True)
        with semantic_task(ctx):
            self.assertEqual(publication_scope('whatever wording').total,4);self.assertEqual(requested_weeks('arbitrary language'),2)
            self.assertTrue(requested_media_assets('just context'))
        self.assertIsNone(current_semantics())
    def test_semantic_scope_resets_on_failure(self):
        with self.assertRaises(RuntimeError):
            with semantic_task({'semantic_authorized':True,'semantic_execution_kind':'plans'}):raise RuntimeError('stop')
        self.assertIsNone(current_semantics())
    def test_text_executor_uses_typed_kind_not_compound_words(self):
        from text_workflow import run_text_task
        context=dict(semantic_authorized=True,semantic_execution_kind='bundle',semantic_schedule=dict(weeks=1,per_week=1,total=1))
        with patch('compound_tasks.is_compound_request',side_effect=AssertionError('lexical')):
            out=run_text_task('можно уже к делу?',context,compound_runner=lambda req,state,**kw:{'action':'compound_clarify','answer':'Что уточнить?'})
        self.assertEqual(out['action'],'compound_clarify')
    def test_gui_front_door_contains_no_legacy_language_dispatch_calls(self):
        tree=ast.parse((Path(__file__).parent/'app.py').read_text())
        studio=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Studio')
        send=next(n for n in studio.body if isinstance(n,ast.FunctionDef) and n.name=='send_message')
        calls={n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else '' for n in ast.walk(send) if isinstance(n,ast.Call)}
        self.assertTrue({'DialogueWorker','snapshot','accept'}<=calls)
        self.assertNotIn('dialogue_snapshot',calls)  # SQLite snapshot belongs to ApplicationCore
        self.assertFalse(calls&{'handle_project_control','route_image_command','execution_request','explicit_creation_request','is_compound_request','update_facts','mentions_reference'})
    def test_trace_turn_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(ValueError):save_turn(home,'../escape',status='bad')
    def test_trace_turn_merges_decision_and_result(self):
        with tempfile.TemporaryDirectory() as home:
            ident='f'*32;save_turn(home,ident,status='resolved',utterance='Exact');save_turn(home,ident,status='done')
            out=json.loads((Path(home)/'drafts'/('dialogue_turn_'+ident+'.json')).read_text())
            self.assertEqual(out['utterance'],'Exact');self.assertEqual(out['status'],'done')
    def test_voice_typing_events_and_partial_failure_release_keys(self):
        from voice_input import start_windows_dictation,_Input
        calls=[]
        def send(n,events,size):calls.append((n,[(e.payload.ki.wVk,e.payload.ki.dwFlags) for e in events],size));return n
        start_windows_dictation(send_input=send,platform_name='nt')
        self.assertEqual(calls[0][1],[(0x5B,0),(0x48,0),(0x48,2),(0x5B,2)])
        self.assertEqual(calls[0][2],40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)
        calls=[]
        def partial(n,events,size):calls.append(n);return 1
        with self.assertRaises(RuntimeError):start_windows_dictation(send_input=partial,platform_name='nt')
        self.assertEqual(calls,[4,2])
    def test_voice_typing_unavailable_is_explicit(self):
        from voice_input import start_windows_dictation
        with self.assertRaises(RuntimeError):start_windows_dictation(platform_name='posix')

if __name__=='__main__':unittest.main()

class ReportTraceCoverageTests(unittest.TestCase):
    def setUp(self):
        from test_r85_support_report import SupportReport
        self.fixture=SupportReport('test_missing_evidence_is_explicit');self.fixture.setUp()
    def tearDown(self):self.fixture.tearDown()
    def test_interface_history_alone_is_not_model_trace(self):
        d=self.fixture.workspace/'drafts';d.mkdir()
        (d/'application_events.jsonl').write_text(json.dumps({'event':'interface_message','content':'Old history'})+'\n')
        _,z=self.fixture.build()
        with z:
            summary=json.loads(z.read('SUMMARY.json'))
            self.assertEqual(summary['model_trace_coverage']['model_call_started'],0)
            self.assertIn('no_model_request_trace',str(summary['warnings']))
    def test_actual_model_events_are_counted_separately(self):
        d=self.fixture.workspace/'drafts';d.mkdir()
        (d/'application_events.jsonl').write_text('\n'.join(json.dumps({'event':kind,'time':'2026'}) for kind in
            ('task_capture_started','model_call_started','model_call_completed','model_call_started','model_call_failed'))+'\n')
        _,z=self.fixture.build()
        with z:
            summary=json.loads(z.read('SUMMARY.json'));coverage=summary['model_trace_coverage']
            self.assertEqual(coverage['model_call_started'],2);self.assertEqual(coverage['model_call_completed'],1);self.assertEqual(coverage['model_call_failed'],1)
            self.assertNotIn('no_model_request_trace',str(summary['warnings']))
