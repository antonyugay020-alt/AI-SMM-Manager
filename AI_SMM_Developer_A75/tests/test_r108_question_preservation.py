"""Unfinished clarification is independent of subsequent jobs and projects."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from dialogue_runtime import DialogueContract, dialogue_snapshot
from dialogue_execution import compile_execution,persist_execution,cancel_pending
from question_registry import replace_question,paused_questions
from test_r86_semantic_dialogue import decision


class QuestionPreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'state.sqlite3'
        self.db=sqlite3.connect(self.path)
        self.db.executescript('''PRAGMA foreign_keys=ON;
           CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,category TEXT,description TEXT);
           CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT);
           CREATE TABLE manager_documents(id TEXT,project_id TEXT,kind TEXT,title TEXT,content TEXT,updated_at TEXT);
           CREATE TABLE ai_pending_tasks(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,revision_id TEXT);
           CREATE TABLE ai_dialogue_questions(project_id TEXT PRIMARY KEY,request TEXT,question TEXT,plan_json TEXT);
           INSERT INTO projects VALUES('a','A','',''),('b','B','','');''')
        self.db.commit()

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def plan(self,project,utterance,operation,source='current'):
        return DialogueContract(utterance,dialogue_snapshot(self.db,project)).validate(
            decision(operation,artifact_kind='plans',source_id=source))

    def new_task(self,project,request):
        plan=self.plan(project,request,'create_text')
        text,ctx=compile_execution(self.db,plan,self.tmp.name)
        persist_execution(self.db,plan,text,ctx)
        return ctx['task_id']

    def test_question_is_preserved_when_another_project_task_starts(self):
        replace_question(self.db,'a','Original task','What is the budget?','{"source_id":"current"}')
        self.db.commit()
        active=self.new_task('a','Unrelated new assignment')
        questions=paused_questions(self.db,'a')
        self.assertEqual(len(questions),1)
        self.assertEqual(questions[0]['request'],'Original task')
        self.assertEqual(questions[0]['question'],'What is the budget?')
        self.assertIsNone(self.db.execute('SELECT 1 FROM ai_dialogue_questions WHERE project_id="a"').fetchone())
        self.db.close();self.db=sqlite3.connect(self.path)
        qid='question:'+questions[0]['question_id']
        snapshot=dialogue_snapshot(self.db,'a')
        self.assertIn(qid,snapshot['sources'])
        plan=self.plan('a','95 000 ₸','answer_clarification',qid)
        text,ctx=compile_execution(self.db,plan,self.tmp.name)
        self.assertIn('Original task',text);self.assertIn('95 000 ₸',text)
        self.assertEqual(ctx['clarification_question'],'What is the budget?')
        persist_execution(self.db,plan,text,ctx)
        self.assertEqual(self.db.execute('SELECT status FROM ai_suspended_questions WHERE question_id=?',(questions[0]['question_id'],)).fetchone()[0],'answered')
        self.assertEqual(self.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(active,)).fetchone()[0],'paused')

    def test_second_question_does_not_erase_first(self):
        replace_question(self.db,'a','Task A','Question A','{}')
        replace_question(self.db,'a','Task B','Question B','{}')
        self.assertEqual([q['question'] for q in paused_questions(self.db,'a')],['Question A'])
        self.assertEqual(self.db.execute('SELECT question FROM ai_dialogue_questions WHERE project_id="a"').fetchone()[0],'Question B')

    def test_same_question_does_not_make_duplicate_archive(self):
        replace_question(self.db,'a','A','Q','{}')
        replace_question(self.db,'a','A','Q','{}')
        self.assertEqual(len(paused_questions(self.db,'a')),0)

    def test_cancel_paused_question_does_not_change_active_job(self):
        replace_question(self.db,'a','Older request','Older question','{}')
        self.new_task('a','Current job')
        old=paused_questions(self.db,'a')[0]
        cancel_pending(self.db,self.plan('a','Cancel the older question','cancel_pending','question:'+old['question_id']))
        self.assertEqual(self.db.execute('SELECT status FROM ai_suspended_questions WHERE question_id=?',(old['question_id'],)).fetchone()[0],'cancelled')
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone()[0],'Current job')

    def test_cross_project_source_and_stale_question_rejected(self):
        replace_question(self.db,'a','Original','Question','{}')
        self.new_task('a','New')
        old=paused_questions(self.db,'a')[0]
        with self.assertRaises(ValueError):self.plan('b','Answer','answer_clarification','question:'+old['question_id'])
        stale=self.plan('a','Answer','answer_clarification','question:'+old['question_id'])
        self.db.execute('UPDATE ai_suspended_questions SET question=? WHERE question_id=?',('Changed',old['question_id']))
        with self.assertRaises(ValueError):compile_execution(self.db,stale,self.tmp.name)

if __name__=='__main__':unittest.main()
