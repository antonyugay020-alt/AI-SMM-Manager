"""Integration faults in real GUI publication methods, no Qt dependency.

SQLite must never remain mid-transaction after an application-owned failure.
A foreign transaction must never be committed or rolled back by these paths.
"""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app.py'

def method(name, bindings):
    tree = ast.parse(APP.read_text('utf8'))
    defs = [n for item in tree.body if isinstance(item, ast.ClassDef)
            for n in item.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(defs) == 1, name
    namespace = dict(bindings)
    exec(compile(ast.fix_missing_locations(ast.Module(body=defs, type_ignores=[])), str(APP), 'exec'), namespace)
    return namespace[name]

class AtomicPublicationContract(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.base = Path(t.name) / 'p'; (self.base / 'ready').mkdir(parents=True)
        self.first = self.base / 'ready' / 'first.png'; self.first.write_bytes(b'photo1')
        self.second = self.base / 'ready' / 'second.png'; self.second.write_bytes(b'photo2')
        self.db=open_application_database(self.base.parent/'manager.sqlite',self.base.parent)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE other(flag TEXT)')
        self.db.execute("INSERT INTO projects(id,name,created_at) VALUES('p','P','now')")
        for f in (self.first,self.second):
            self.db.execute('INSERT INTO publication_media_review VALUES(?,?,?,?)',('p',str(f),'Одобрен','now'))
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.base)
        self.events = []
        box = SimpleNamespace(warning=lambda *args:self.events.append(('warn',args)),
            information=lambda *args:self.events.append(('info',args)),
            question=lambda *args:1, Yes=1, No=2)
        self.view = SimpleNamespace(current_project='p', db=self.db,core=self.core,
            media_review_status=lambda _: 'Одобрен',
            ready_media=SimpleNamespace(selectedItems=lambda:[SimpleNamespace(data=lambda *_:str(f)) for f in (self.first,self.second)]),
            pub_channel=SimpleNamespace(currentText=lambda:'Posts'),
            pub_block=SimpleNamespace(currentText=lambda:'Posts'),
            pub_frequency=SimpleNamespace(currentText=lambda:'Daily'),
            pub_time=SimpleNamespace(text=lambda:'12:30'),
            selected_publication_id=lambda:'j1',
            refresh_publications=lambda:self.events.append(('refresh',None)),
            statusBar=lambda:SimpleNamespace(showMessage=lambda *a:None))
        self.env = {'QMessageBox':box, 'QInputDialog':SimpleNamespace(getText=lambda *a,**kw:('2026-11-10 13:00', True)),
            'Qt':SimpleNamespace(UserRole=32), 'ensure_project_folders':lambda _:self.base,
            'uuid4':uuid4, 'now':lambda:'now', 'datetime':__import__('datetime').datetime,
            'sqlite3':sqlite3, 'Path':Path}

    def test_two_file_queue_insert_is_all_or_nothing_when_second_fails(self):
        self.db.execute('''CREATE TRIGGER fault_second BEFORE INSERT ON publication_jobs
            WHEN NEW.media_path LIKE '%second.png' BEGIN SELECT RAISE(ABORT,'injected second insert failure'); END''')
        run = method('add_publication_jobs',self.env)
        run(self.view)
        self.assertFalse(self.db.in_transaction, 'GUI left SQLite locked after partial failure')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)
        self.assertTrue(any(row[0]=='warn' for row in self.events))

    def test_channel_queue_insert_is_all_or_nothing_when_second_fails(self):
        self.db.execute('''CREATE TRIGGER fault_second BEFORE INSERT ON publication_jobs
            WHEN NEW.media_path LIKE '%second.png' BEGIN SELECT RAISE(ABORT,'injected second insert failure'); END''')
        run = method('add_media_paths_to_channel',self.env)
        result = run(self.view,[str(self.first),str(self.second)],'Posts')
        self.assertFalse(result)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)

    def test_rule_write_failure_does_not_leave_db_locked(self):
        self.db.execute('''CREATE TRIGGER rule_fault BEFORE INSERT ON publication_block_rules
            BEGIN SELECT RAISE(ABORT,'injected rule failure'); END''')
        method('save_publication_rule',self.env)(self.view)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_block_rules').fetchone()[0],0)
        self.assertTrue(any(row[0]=='warn' for row in self.events))

    def test_schedule_failure_rolls_back_and_keeps_previous_job(self):
        self.db.execute('INSERT INTO publication_jobs(id,project_id,media_path,channel,schedule_mode,created_at) VALUES(?,?,?,?,?,?)',
                        ('j1','p',str(self.first),'Posts','Manual','now'))
        self.db.commit()
        self.db.execute('''CREATE TRIGGER update_fault BEFORE UPDATE ON publication_jobs
            BEGIN SELECT RAISE(ABORT,'injected update failure'); END''')
        method('set_block_publication',self.env)(self.view)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT schedule_mode FROM publication_jobs WHERE id=?',('j1',)).fetchone()[0],'Manual')

    def test_failed_delete_does_not_leave_db_locked(self):
        self.db.execute('INSERT INTO publication_jobs(id,project_id,media_path,channel,schedule_mode,created_at) VALUES(?,?,?,?,?,?)',
                        ('j1','p',str(self.first),'Posts','Manual','now'))
        self.db.commit()
        self.db.execute('''CREATE TRIGGER delete_fault BEFORE DELETE ON publication_jobs
            BEGIN SELECT RAISE(ABORT,'injected delete failure'); END''')
        method('remove_publication_job',self.env)(self.view)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],1)

    def test_foreign_transaction_is_untouched_for_all_simple_writers(self):
        self.db.execute('INSERT INTO other VALUES (?)',('foreign',))
        self.assertTrue(self.db.in_transaction)
        for name in ('add_media_paths_to_channel','add_publication_jobs','set_individual_publication',
                     'set_block_publication','remove_publication_job','save_publication_rule'):
            with self.subTest(operation=name):
                fn=method(name,self.env)
                if name=='add_media_paths_to_channel':
                    self.assertFalse(fn(self.view,[str(self.first)],'Posts'))
                else:fn(self.view)
                self.assertTrue(self.db.in_transaction)
                self.assertEqual(self.db.execute('SELECT flag FROM other').fetchone()[0],'foreign')
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM other').fetchone()[0],0)

if __name__ == '__main__':unittest.main()
