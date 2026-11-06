"""Durable multi-assignment isolation; regression on an R97-compatible base."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from dialogue_runtime import DialogueContract, dialogue_snapshot
from dialogue_execution import compile_execution, persist_execution, cancel_pending, validate_live_plan
from task_registry import ensure_registry, activate, paused_tasks, finish
from test_r86_semantic_dialogue import decision


class MultiTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.sqlite3'
        self.db=sqlite3.connect(self.path)
        self.db.executescript('''PRAGMA foreign_keys=ON;
            CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,category TEXT,description TEXT);
            CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT);
            CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT,updated_at TEXT);
            CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,question TEXT NOT NULL,revision_id TEXT);
            INSERT INTO projects VALUES('a','Project A','',''),('b','Project B','','');''')
        self.db.commit()

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def create(self,project,message):
        snap=dialogue_snapshot(self.db,project)
        plan=DialogueContract(message,snap).validate(decision('create_text',artifact_kind='plans'))
        request,context=compile_execution(self.db,plan,self.tmp.name)
        persist_execution(self.db,plan,request,context)
        return context['task_id']

    def plan(self,project,message,operation,source):
        snap=dialogue_snapshot(self.db,project)
        plan=DialogueContract(message,snap).validate(decision(operation,artifact_kind='plans',source_id=source))
        return plan

    def test_fresh_task_pauses_old_and_resumes_by_stable_id_after_restart(self):
        old=self.create('a','Task A: 39 posts')
        fresh=self.create('a','Task B: 16 posts')
        self.assertNotEqual(old,fresh)
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks WHERE project_id="a"').fetchone()[0], 'Task B: 16 posts')
        self.assertEqual([(p['task_id'],p['request']) for p in paused_tasks(self.db,'a')],[(old,'Task A: 39 posts')])
        self.db.close();self.db=sqlite3.connect(self.path)
        snap=dialogue_snapshot(self.db,'a')
        self.assertIn('paused:'+old,snap['sources'])
        self.assertEqual(snap['sources']['paused:'+old]['execution_kind'],'plans')
        plan=self.plan('a','Продолжи предыдущую задачу','resume','paused:'+old)
        request,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertEqual(request,'Task A: 39 posts')
        self.assertEqual(ctx['task_id'],old)
        persist_execution(self.db,plan,request,ctx)
        self.assertEqual(self.db.execute('SELECT task_id FROM ai_task_registry WHERE status="active"').fetchone()[0],old)
        self.assertEqual([p['task_id'] for p in paused_tasks(self.db,'a')],[fresh])
        self.db.commit()

    def test_other_projects_are_never_paused_or_resumed(self):
        first=self.create('a','Apples')
        second=self.create('b','Oranges')
        self.create('a','Bananas')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(second,)).fetchone()[0],'active')
        self.assertEqual(len(paused_tasks(self.db,'b')),0)
        with self.assertRaises(ValueError):self.plan('b','Resume apples','resume','paused:'+first)

    def test_completion_of_new_task_does_not_complete_older_task(self):
        old=self.create('a','Old')
        new=self.create('a','New')
        finish(self.db,'a','New',task_id=new)
        self.assertIsNone(self.db.execute('SELECT request FROM ai_pending_tasks WHERE project_id="a"').fetchone())
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(new,)).fetchone()[0],'completed')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(old,)).fetchone()[0],'paused')

    def test_cancel_paused_does_not_cancel_active(self):
        old=self.create('a','Old')
        new=self.create('a','New')
        result=cancel_pending(self.db,self.plan('a','Cancel old','cancel_pending','paused:'+old))
        self.assertTrue(result['archived'])
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(old,)).fetchone()[0],'cancelled')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(new,)).fetchone()[0],'active')
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks WHERE project_id="a"').fetchone()[0],'New')

    def test_stale_plan_rejected_if_another_task_started(self):
        self.create('a','Old')
        stale=self.plan('a','Late creation','create_text','current')
        self.create('a','Concurrent replacement')
        # The live state check must fail before any persistence/side effect.
        with self.assertRaises(ValueError):compile_execution(self.db,stale,self.tmp.name)

    def test_rollback_on_failure_preserves_legacy_and_registry(self):
        old=self.create('a','Old')
        plan=self.plan('a','New','create_text','current')
        request,ctx=compile_execution(self.db,plan,self.tmp.name)
        # Fail the registry insert after previous task has been paused.
        self.db.execute('''CREATE TRIGGER reject_new BEFORE INSERT ON ai_task_registry
                           WHEN NEW.request='New' BEGIN SELECT RAISE(ABORT,'blocked'); END''')
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):persist_execution(self.db,plan,request,ctx)
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],'Old')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(old,)).fetchone()[0],'active')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_task_registry').fetchone()[0],1)

    def test_identical_texts_keep_independent_task_scoped_contexts(self):
        first=self.create('a','Exactly the same request')
        self.create('a','Another request')
        third=self.create('a','Exactly the same request')
        self.assertNotEqual(first,third)
        stored=self.db.execute('SELECT task_id,context_json FROM ai_task_contexts WHERE task_id IN (?,?)',
                               (first,third)).fetchall()
        self.assertEqual(len(stored),2)
        self.assertNotEqual(json.loads(stored[0][1])['dialogue_turn_id'],
                            json.loads(stored[1][1])['dialogue_turn_id'])
        snapshot=dialogue_snapshot(self.db,'a')
        old=snapshot['sources']['paused:'+first]
        self.assertEqual(old['task_id'],first)
        plan=self.plan('a','Continue the first one','resume','paused:'+first)
        request,context=compile_execution(self.db,plan,self.tmp.name)
        self.assertEqual(context['task_id'],first)
        self.assertEqual(request,'Exactly the same request')
        self.assertEqual(context['task_mode'],'resume')

    def test_two_consecutive_new_requests_with_identical_text_have_distinct_ids(self):
        first=self.create('a','Same words')
        second=self.create('a','Same words')
        self.assertNotEqual(first,second)
        self.assertEqual([item['task_id'] for item in paused_tasks(self.db,'a')],[first])
        self.assertEqual(self.db.execute('SELECT task_id FROM ai_task_registry WHERE status="active"').fetchone()[0],second)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_task_contexts').fetchone()[0],2)

    def test_legacy_context_mutation_cannot_inject_into_paused_task(self):
        first=self.create('a','Task')
        self.create('a','Other')
        self.db.execute("UPDATE ai_semantic_tasks SET context_json='{}' WHERE request='Task'")
        self.db.commit()
        snap=dialogue_snapshot(self.db,'a')
        self.assertIsNone(snap['sources']['paused:'+first].get('execution_contract_error'))
        plan=self.plan('a','Resume old task','resume','paused:'+first)
        request,context=compile_execution(self.db,plan,self.tmp.name)
        self.assertEqual(request,'Task')
        self.assertEqual(context['task_id'],first)

    def test_explicit_task_id_cannot_be_reused_cross_project(self):
        old=self.create('a','Task A')
        with self.assertRaises(ValueError):activate(self.db,'b','Task A','Q',task_id=old)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_task_registry WHERE project_id="b"').fetchone()[0],0)

    def test_equal_worded_jobs_have_different_checkpoint_paths(self):
        from task_checkpoint import checkpoint_path
        a=self.create('a','Same request')
        b=self.create('a','Same request')
        first=checkpoint_path(self.tmp.name,'pending_text','Same request',a)
        second=checkpoint_path(self.tmp.name,'pending_text','Same request',b)
        self.assertNotEqual(first,second)
        self.assertEqual(first.parent,second.parent)
        self.assertRaises(ValueError,checkpoint_path,self.tmp.name,'pending_text','Same request','../escape')

    def test_second_same_worded_task_cannot_read_first_draft(self):
        from task_checkpoint import checkpoint_path
        first=self.create('a','Same request')
        first_path=checkpoint_path(self.tmp.name,'pending_text','Same request',first)
        first_path.parent.mkdir(parents=True,exist_ok=True)
        first_path.write_text(json.dumps({'task_id':first,'request':'Same request',
            'draft':'DRAFT FROM FIRST JOB'}),encoding='utf-8')
        second=self.create('a','Same request')
        plan=self.plan('a','Continue second job','resume','pending')
        request,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertEqual(ctx['task_id'],second)
        self.assertNotIn('pending_text_candidate',ctx)
        other_plan=self.plan('a','Continue first job','resume','paused:'+first)
        old_request,old_context=compile_execution(self.db,other_plan,self.tmp.name)
        self.assertEqual(old_context['pending_text_candidate']['draft'],'DRAFT FROM FIRST JOB')

    def test_legacy_request_keyed_draft_is_not_attached_to_new_task(self):
        from task_checkpoint import checkpoint_path
        request='Repeat'
        legacy=checkpoint_path(self.tmp.name,'pending_text',request)
        legacy.parent.mkdir(parents=True,exist_ok=True)
        legacy.write_text(json.dumps({'request':request,'draft':'AMBIGUOUS LEGACY DRAFT'}),encoding='utf-8')
        self.create('a',request)
        plan=self.plan('a','Continue','resume','pending')
        _,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertNotIn('pending_text_candidate',ctx)

    def test_migrated_legacy_task_can_recover_its_pre_registry_draft(self):
        from task_checkpoint import checkpoint_path
        request='Legacy request'
        ident=self.create('a',request)
        self.db.execute('UPDATE ai_task_registry SET legacy_origin=1 WHERE task_id=?',(ident,))
        self.db.commit()
        old_draft=checkpoint_path(self.tmp.name,'pending_text',request)
        old_draft.parent.mkdir(parents=True,exist_ok=True)
        old_draft.write_text(json.dumps({'request':request,'draft':'OLD WORK PRESERVED'}),encoding='utf-8')
        plan=self.plan('a','Continue saved draft','resume','pending')
        _,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertEqual(ctx['task_id'],ident)
        self.assertEqual(ctx['pending_text_candidate']['draft'],'OLD WORK PRESERVED')

    def test_unmarked_new_job_never_recovers_legacy_draft(self):
        from task_checkpoint import checkpoint_path
        request='Legacy request'
        old_draft=checkpoint_path(self.tmp.name,'pending_text',request)
        old_draft.parent.mkdir(parents=True,exist_ok=True)
        old_draft.write_text(json.dumps({'request':request,'draft':'OLD WORK PRESERVED'}),encoding='utf-8')
        self.create('a',request)
        plan=self.plan('a','Continue saved draft','resume','pending')
        _,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertNotIn('pending_text_candidate',ctx)

    def test_legacy_pending_migrates_once(self):
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?,?)',('a','Old legacy','Question',None))
        ensure_registry(self.db);ensure_registry(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_task_registry').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry').fetchone()[0],'active')
        self.assertEqual(self.db.execute('SELECT legacy_origin FROM ai_task_registry').fetchone()[0],1)

if __name__=='__main__':unittest.main()
