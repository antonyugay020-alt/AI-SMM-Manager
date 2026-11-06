"""Multi-project state machine verification with actual SQLite close/reopen.

Models are not mocked because this suite checks the durable host contract only.
It is NOT an actual AI-model or Windows GUI acceptance test.
"""
import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from dialogue_runtime import DialogueContract,dialogue_snapshot
from dialogue_execution import compile_execution,persist_execution,cancel_pending,validate_live_plan
from task_registry import paused_tasks, finish
from question_registry import replace_question,paused_questions
from project_operation_guard import project_operation_busy
from test_r86_semantic_dialogue import decision


class Continuity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'db.sqlite3'
        self.connect()
        self.db.executescript('''PRAGMA foreign_keys=ON;
            CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,category TEXT,description TEXT);
            CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT);
            CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT,updated_at TEXT);
            CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,revision_id TEXT);
            CREATE TABLE ai_dialogue_questions(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,plan_json TEXT);
            INSERT INTO projects VALUES ('a','Workshop','',''),('b','Games','',''),('c','Photo','','');''')
        self.db.commit()
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def connect(self):
        self.db=sqlite3.connect(self.path)
        self.db.execute('PRAGMA foreign_keys=ON')
    def restart(self):
        self.db.commit();self.db.close();self.connect()
    def plan(self,pid,text,op,source='current'):
        return DialogueContract(text,dialogue_snapshot(self.db,pid)).validate(
            decision(op,artifact_kind='plans',source_id=source))
    def submit(self,pid,text):
        plan=self.plan(pid,text,'create_text')
        request,ctx=compile_execution(self.db,plan,self.tmp.name)
        persist_execution(self.db,plan,request,ctx)
        return ctx['task_id']

    def test_three_projects_two_tasks_and_restart(self):
        a=self.submit('a','Plan 16 posts')
        b=self.submit('b','Plan 39 posts')
        c=self.submit('c','Plan 24 posts')
        newer=self.submit('a','Independent plan 2')
        self.restart()
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_task_registry').fetchone()[0],4)
        self.assertEqual(len(paused_tasks(self.db,'a')),1)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(b,)).fetchone()[0],'active')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(c,)).fetchone()[0],'active')
        plan=self.plan('a','Resume first','resume','paused:'+a)
        request,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertEqual(ctx['task_id'],a)
        persist_execution(self.db,plan,request,ctx)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(newer,)).fetchone()[0],'paused')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(a,)).fetchone()[0],'active')
        finish(self.db,'a','Plan 16 posts')
        self.restart()
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(a,)).fetchone()[0],'completed')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(b,)).fetchone()[0],'active')
        self.assertEqual(len(paused_tasks(self.db,'a')),1)

    def test_two_equal_jobs_cannot_use_other_context_after_loss(self):
        older=self.submit('a','Same instruction')
        newer=self.submit('a','Same instruction')
        self.db.execute('DELETE FROM ai_task_contexts WHERE task_id=?',(newer,))
        self.db.commit();self.restart()
        snap=dialogue_snapshot(self.db,'a')
        self.assertIn('execution_contract_error',snap['sources']['pending'])
        self.assertNotIn('execution_contract_error',snap['sources']['paused:'+older])
        # Explicit attempt to restore the broken newer task is rejected, not
        # silently redirected to the request-keyed compat table.
        with self.assertRaises(ValueError):self.plan('a','Continue newer','resume','pending')
        other=self.plan('a','Continue older','resume','paused:'+older)
        _,ctx=compile_execution(self.db,other,self.tmp.name)
        self.assertEqual(ctx['task_id'],older)

    def test_resumed_project_context_not_affected_by_another_project_compat_table(self):
        first=self.submit('a','Exact words')
        self.submit('b','Exact words')
        self.db.execute('UPDATE ai_semantic_tasks SET context_json=? WHERE project_id=?',
            (json.dumps({'semantic_authorized':True,'semantic_execution_kind':'analytics','project':'Wrong'}),'b'))
        self.restart()
        plan=self.plan('a','Continue A','resume','pending')
        _,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertEqual(ctx['task_id'],first)
        self.assertEqual(ctx['project'],'Workshop')

    def test_paused_question_and_current_task_survive_crash_and_cancel(self):
        replace_question(self.db,'a','Original campaign','Budget?','{}')
        self.db.commit()
        first=self.submit('a','Active new task')
        saved=paused_questions(self.db,'a')[0]
        self.restart()
        self.assertEqual(paused_questions(self.db,'a')[0]['question'],'Budget?')
        plan=self.plan('a','Cancel old question','cancel_pending','question:'+saved['question_id'])
        cancel_pending(self.db,plan)
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(first,)).fetchone()[0],'active')
        self.assertEqual(len(paused_questions(self.db,'a')),0)

    def test_stale_resume_rejected_after_task_switched(self):
        a=self.submit('a','First')
        self.submit('a','Second')
        stale=self.plan('a','Resume first','resume','paused:'+a)
        self.submit('a','Third')
        with self.assertRaises(ValueError):validate_live_plan(self.db,'a',stale)


class ProjectOperationGate(unittest.TestCase):
    class Worker:
        def __init__(self,running):self.running=running
        def isRunning(self):return self.running

    class View:pass

    def test_paused_tasks_do_not_block_navigation(self):
        view=self.View();view._background_workers=[];view.task_worker=None
        self.assertFalse(project_operation_busy(view))

    def test_any_active_worker_blocks_navigation(self):
        for key in ('task_worker','dialogue_worker','video_worker','cloud_worker','modern_worker',
                    'edit_worker','download_worker','report_worker'):
            view=self.View();view._background_workers=[];setattr(view,key,object())
            with self.subTest(key=key):self.assertTrue(project_operation_busy(view))
        view=self.View();view._background_workers=[self.Worker(True)]
        self.assertTrue(project_operation_busy(view))

    def test_result_application_blocks_navigation(self):
        view=self.View();view._background_workers=[];view._dialogue_applying=True
        self.assertTrue(project_operation_busy(view))
        view._dialogue_applying=False;view._result_effects=[]
        self.assertTrue(project_operation_busy(view))

    def test_new_chat_message_not_consumed_while_another_worker_runs(self):
        app=Path(__file__).with_name('app.py')
        root=ast.parse(app.read_text(encoding='utf-8'))
        cls=next(node for node in root.body if isinstance(node,ast.ClassDef) and node.name=='Studio')
        node=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='send_message')
        ns={}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(app),'exec'),ns)
        class Composer:
            def __init__(self):self.text='Independent second request';self.clears=0
            def toPlainText(self):return self.text
            def clear(self):self.clears+=1;self.text=''
        class Label:
            def __init__(self):self.value=None
            def setText(self,value):self.value=value
        view=self.View();view.current_project='a';view.task_worker=object()
        view.message=Composer();view.ai_status=Label();view._background_workers=[]
        ns['send_message'](view)
        self.assertEqual(view.message.text,'Independent second request')
        self.assertEqual(view.message.clears,0)

    def test_blocked_qt_combo_selection_reverted(self):
        # Execute only the selected method's AST, without importing the Qt app.
        app=Path(__file__).with_name('app.py')
        root=ast.parse(app.read_text(encoding='utf-8'))
        cls=next(node for node in root.body if isinstance(node,ast.ClassDef) and node.name=='Studio')
        node=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='select_project')
        ns={}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(app),'exec'),ns)
        class Combo:
            def __init__(self):self.index=1;self.blocked=False
            def currentIndex(self):return self.index
            def blockSignals(self,v):old=self.blocked;self.blocked=v;return old
            def setCurrentIndex(self,v):self.index=v
        class Status:
            def showMessage(self,*args):pass
        view=self.View();view._switching_project=False;view._background_workers=[]
        view.project_ids=['a','b'];view.current_project='a';view.projects=Combo();view.task_worker=object()
        view.statusBar=lambda:Status()
        ns['select_project'](view,1)
        self.assertEqual(view.projects.index,0)
        self.assertEqual(view.current_project,'a')
        self.assertFalse(view.projects.blocked)

if __name__=='__main__':unittest.main()
