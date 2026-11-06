"""Fault-injection matrix: no link or forged job may cross a project's media boundary.

Execute real methods extracted from the shipped app.py, without Qt or a fake
substitute for the production boundary checker.
"""
import ast
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

APP = Path(__file__).resolve().parents[1] / 'app.py'


def method(name, bindings):
    code = ast.parse(APP.read_text(encoding='utf8'))
    selected = [f for klass in code.body if isinstance(klass, ast.ClassDef)
                for f in klass.body if isinstance(f, ast.FunctionDef) and f.name == name]
    if len(selected) != 1:
        raise AssertionError('Expected exactly one method ' + name)
    env = dict(bindings)
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(APP), 'exec'), env)
    return env[name]


class PublicationOwnershipMatrix(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'p'; self.root.mkdir()
        self.outside = Path(tmp.name) / 'outside'; self.outside.mkdir()
        self.secret = self.outside / 'secret.png'; self.secret.write_bytes(b'DO NOT TOUCH')
        self.db=open_application_database(Path(tmp.name)/'manager.sqlite',Path(tmp.name))
        self.addCleanup(self.db.close)
        self.db.executemany('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',
                            [('project','P','now'),('other','O','now')])
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.root)
        self.warnings = []
        self.box = SimpleNamespace(warning=lambda *x:self.warnings.append(x),
                                   information=lambda *x:self.warnings.append(x))
        self.path = self.root / 'ready' / 'secret.png'
        try:
            (self.root / 'ready').symlink_to(self.outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Directory symbolic links unavailable')
        self.item = SimpleNamespace(data=lambda *_:str(self.path))
        self.gallery = SimpleNamespace(selectedItems=lambda:[self.item])
        self.view = SimpleNamespace(current_project='project', db=self.db,core=self.core,
             ready_media=self.gallery, refresh_publications=lambda:None,
             media_review_status=lambda _: 'Одобрен',
             pub_channel=SimpleNamespace(currentText=lambda:'Лента'))
        self.env = {'Path':Path, 'QMessageBox':self.box,
             'ensure_project_folders':lambda *a:self.root,
             'Qt':SimpleNamespace(UserRole=32), 'uuid4':lambda:UUID(int=1),
             'now':lambda:'now', 'os':os, 'sqlite3':sqlite3}

    def test_add_to_channel_rejects_linked_folder(self):
        self.assertFalse(method('add_media_paths_to_channel',self.env)(self.view,[str(self.path)],'Лента'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)
        self.assertEqual(self.secret.read_bytes(),b'DO NOT TOUCH')

    def test_review_rejects_linked_folder(self):
        self.assertFalse(method('set_media_review',self.env)(self.view,'Одобрен'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_media_review').fetchone()[0],0)
        self.assertEqual(self.secret.read_bytes(),b'DO NOT TOUCH')

    def test_create_publication_job_rejects_linked_folder(self):
        run=method('add_publication_jobs',self.env)
        run(self.view)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)

    def test_stored_job_with_linked_folder_not_returned(self):
        self.db.execute('INSERT INTO publication_jobs (id,project_id,media_path,channel,schedule_mode,created_at) VALUES(?,?,?,?,?,?)',
                        ('j1','project',str(self.path),'Лента','Manual','now'))
        self.db.commit()
        run=method('publication_paths_for_job_ids',self.env)
        self.assertEqual(run(self.view,['j1']),[])

    def test_stored_job_in_other_project_not_returned(self):
        self.db.execute('INSERT INTO publication_jobs (id,project_id,media_path,channel,schedule_mode,created_at) VALUES(?,?,?,?,?,?)',
                        ('j1','other',str(self.path),'Лента','Manual','now'))
        self.db.commit()
        self.assertEqual(method('publication_paths_for_job_ids',self.env)(self.view,['j1']),[])

    def test_preview_selection_rejects_linked_folder(self):
        display=[]
        self.view.pub_preview=SimpleNamespace(clear=lambda:display.append('clear'))
        self.view.pub_preview_info=SimpleNamespace(setText=lambda v:display.append(v))
        method('show_publication_selection',self.env)(self.view,self.item)
        self.assertIn('clear',display)
        self.assertTrue(any('вне проекта' in str(x) for x in display))

    def test_open_location_rejects_linked_folder(self):
        self.view.ready_media.currentItem=lambda:self.item
        method('open_media_location',self.env)(self.view)
        self.assertTrue(self.warnings)

    def test_normal_owned_media_approved_and_queued(self):
        (self.root/'ready').unlink()
        (self.root/'ready').mkdir()
        self.path.write_bytes(b'OWNED')
        self.assertTrue(method('set_media_review',self.env)(self.view,'Одобрен'))
        self.assertTrue(method('add_media_paths_to_channel',self.env)(self.view,[str(self.path)],'Лента'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],1)
        job_id=self.db.execute('SELECT id FROM publication_jobs').fetchone()[0]
        self.assertEqual(method('publication_paths_for_job_ids',self.env)(self.view,[job_id]),[str(self.path)])

    def test_corrupt_non_path_selection_rejected_without_crash(self):
        for payload in (None, 3, object()):
            with self.subTest(payload=repr(payload)):
                self.view.ready_media.selectedItems=lambda p=payload:[SimpleNamespace(data=lambda *_:p)]
                self.assertFalse(method('set_media_review',self.env)(self.view,'Одобрен'))
                self.assertFalse(method('add_media_paths_to_channel',self.env)(self.view,[payload],'Лента'))
                self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)
                self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_media_review').fetchone()[0],0)
        self.assertEqual(self.secret.read_bytes(),b'DO NOT TOUCH')

    def test_review_does_not_commit_foreign_transaction(self):
        self.db.execute('CREATE TABLE unrelated(value TEXT)')
        self.db.execute('INSERT INTO unrelated VALUES(?)',('keep',))
        self.assertFalse(method('set_media_review',self.env)(self.view,'Одобрен'))
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT * FROM unrelated').fetchall(),[('keep',)])

    def test_add_channel_does_not_commit_foreign_transaction(self):
        self.db.execute('CREATE TABLE unrelated(value TEXT)')
        self.db.execute('INSERT INTO unrelated VALUES(?)',('keep',))
        self.assertFalse(method('add_media_paths_to_channel',self.env)(self.view,[str(self.path)],'Лента'))
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT * FROM unrelated').fetchall(),[('keep',)])

if __name__ == '__main__':
    unittest.main()
