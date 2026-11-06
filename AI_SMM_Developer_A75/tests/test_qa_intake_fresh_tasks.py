"""New application/acceptance contract tests: durable intake and fresh briefs."""
import sqlite3
import unittest
import ast
from pathlib import Path
from unittest.mock import patch

from task_intake import ensure_intake, record_intake, update_intake, recoverable_intakes
from random_acceptance_tasks import generate_acceptance_tasks, build_randomized_workflow_cases


class DurableIntakeTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
        self.db.execute('''CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,role TEXT,content TEXT,created_at TEXT)''')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('a',),('b',)])
        ensure_intake(self.db)
        self.db.commit()

    def submit(self,project,request,turn):
        cur=self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
                            (project,'user',request,'2026-10-31'))
        receipt=record_intake(self.db,project,cur.lastrowid,turn,request)
        self.db.commit()
        return receipt

    def test_same_text_is_two_durable_independent_assignments(self):
        id1=self.submit('a','Одна и та же задача','turn1')
        id2=self.submit('a','Одна и та же задача','turn2')
        self.assertNotEqual(id1,id2)
        self.assertEqual(len(recoverable_intakes(self.db,'a')),2)
        update_intake(self.db,'a','turn1','resolving')
        update_intake(self.db,'a','turn1','resolved')
        self.db.commit()
        recovered=recoverable_intakes(self.db,'a')
        self.assertEqual(len(recovered),2)
        self.assertIn((id1,'Одна и та же задача','resolved'),recovered)
        self.assertIn((id2,'Одна и та же задача','received'),recovered)

    def test_other_project_cannot_complete_this_turn(self):
        self.submit('a','Текст','turnA')
        with self.assertRaises(ValueError):update_intake(self.db,'b','turnA','resolved')
        self.db.rollback()
        self.assertEqual(len(recoverable_intakes(self.db,'a')),1)

    def test_atomic_rollback_leaves_no_orphan_receipt_or_message(self):
        cur=self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
                            ('a','user','Задание','2026-10-31'))
        record_intake(self.db,'a',cur.lastrowid,'turnA','Задание')
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_task_intake').fetchone()[0],0)

    def test_receipt_rejects_mismatched_message(self):
        cur=self.db.execute('INSERT INTO messages(project_id,role,content,created_at) VALUES(?,?,?,?)',
                            ('a','user','Правильный текст','2026-10-31'))
        with self.assertRaises(ValueError):record_intake(self.db,'a',cur.lastrowid,'turnA','Другой текст')
        self.db.rollback()

    def test_unfinished_receipt_survives_reopen(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'db.sqlite3'
            target=sqlite3.connect(path)
            self.addCleanup(target.close)
            target.execute('PRAGMA foreign_keys=ON')
            target.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
            target.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,role TEXT,content TEXT,created_at TEXT)')
            target.execute("INSERT INTO projects VALUES('a')")
            ensure_intake(target)
            target.execute("INSERT INTO messages(project_id,role,content,created_at) VALUES('a','user','Уникальная задача','now')")
            record_intake(target,'a',1,'turnA','Уникальная задача')
            target.commit();target.close()
            check=sqlite3.connect(path)
            try:self.assertEqual(recoverable_intakes(check,'a')[0][1],'Уникальная задача')
            finally:check.close()

    def test_gui_worker_entrypoint_uses_application_core_before_worker(self):
        code=(Path(__file__).resolve().parents[1]/'app.py').read_text('utf-8')
        tree=ast.parse(code)
        studio=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='Studio')
        method=next(x for x in studio.body if isinstance(x,ast.FunctionDef) and x.name=='send_message')
        source=ast.unparse(method)
        self.assertIn('self.core.conversations.accept(',source)
        self.assertIn('self.core.conversations.snapshot(',source)
        self.assertNotIn("INSERT INTO messages",source)
        self.assertNotIn('self.db.commit()',source)
        self.assertNotIn('self.db.rollback()',source)
        self.assertLess(source.index('self.core.conversations.accept('),source.index('worker.start()'))
        self.assertLess(source.index('self.core.conversations.snapshot('),source.index('worker.start()'))


class FreshAcceptanceTasks(unittest.TestCase):
    def test_distinct_randomized_assignment_per_model(self):
        models=['local-a:1','local-b:2','local-c:3','local-d:4']
        cases=build_randomized_workflow_cases(830001,models,ensure_workflow_coverage=True)
        self.assertEqual(len(cases),4)
        self.assertEqual(len({c['scenario_id'] for c in cases}),4)
        self.assertEqual(len({c['request'] for c in cases}),4)
        self.assertEqual({c['author_model'] for c in cases},set(models))
        self.assertEqual({c['workflow_type'] for c in cases},{'standalone_post','compound_strategy_calendar'})

    def test_new_seed_introduces_new_constraints_not_only_identifier(self):
        one=generate_acceptance_tasks(930001,8)
        two=generate_acceptance_tasks(930002,8)
        self.assertNotEqual([c['request'] for c in one],[c['request'] for c in two])
        for row in one+two:
            self.assertIn(row['format_requirement'],row['request'])
            self.assertEqual(len(row['scenario_id']),16)

    def test_seed_replays_exact_task_set_for_defect_reproduction(self):
        self.assertEqual(generate_acceptance_tasks(20261031,4),generate_acceptance_tasks(20261031,4))

if __name__=='__main__':unittest.main()
