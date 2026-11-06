import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from manual_document_projection import save,drain,install,_paths

class DocumentProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(self.root/'data.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE manager_documents(id TEXT PRIMARY KEY, project_id TEXT,kind TEXT,title TEXT,content TEXT,created_at TEXT,updated_at TEXT)')
        self.id='a'*32
        self.pid='1'*32
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',
                        (self.id,self.pid,'strategy','Old','Initial','old','old'))
        self.db.commit()
        self.fail_export=False
        self.records=[]

    def workspace(self,pid):
        self.assertEqual(pid,self.pid)
        return self.root/'projects'/pid

    def renderer(self,pid,doc_id,kind,title,content,*,diagram_png=None):
        self.records.append((pid,doc_id,kind,title,content))
        if self.fail_export:
            raise OSError('simulated printer failure')
        txt,pdf,png=_paths(self.workspace(pid),doc_id,kind,title)
        for path in (txt,pdf):path.parent.mkdir(parents=True,exist_ok=True)
        txt.write_text(content,encoding='utf-8')
        pdf.write_bytes(b'%PDF-test-document-is-large-enough')
        if diagram_png is not None:
            png.parent.mkdir(parents=True,exist_ok=True);png.write_bytes(diagram_png)

    def edit(self,*,title='Old',content='Changed',kind='strategy',diagram_png=None):
        return save(self.db,self.workspace,self.renderer,project_id=self.pid,document_id=self.id,
                    kind=kind,title=title,content=content,updated_at='new',diagram_png=diagram_png)

    def test_success_and_readback(self):
        result=self.edit()
        self.assertTrue(result.committed)
        self.assertTrue(result.exported)
        self.assertEqual(self.db.execute('SELECT content FROM manager_documents').fetchone()[0],'Changed')
        self.assertEqual(_paths(self.workspace(self.pid),self.id,'strategy','Old')[0].read_text(),'Changed')

    def test_export_failure_keeps_user_edit_for_recovery(self):
        self.fail_export=True
        result=self.edit()
        self.assertTrue(result.committed)
        self.assertFalse(result.exported)
        self.assertEqual(self.db.execute('SELECT content FROM manager_documents').fetchone()[0],'Changed')
        self.db.close()
        self.db=sqlite3.connect(self.root/'data.sqlite')
        self.fail_export=False
        recovered=drain(self.db,self.workspace,self.renderer)
        self.assertEqual(recovered['completed'],1)
        self.assertEqual(recovered['pending'],0)

    def test_move_and_rename_clean_previous_managed_paths(self):
        self.edit(content='first')
        old=_paths(self.workspace(self.pid),self.id,'strategy','Old')
        self.assertTrue(all(path.exists() for path in old[:2]))
        result=self.edit(kind='tasks',title='New',content='second')
        self.assertTrue(result.exported)
        self.assertTrue(all(not path.exists() for path in old[:2]))
        self.assertEqual(_paths(self.workspace(self.pid),self.id,'tasks','New')[0].read_text(),'second')

    def test_competing_unowned_export_is_rejected_before_database_mutation(self):
        target=_paths(self.workspace(self.pid),self.id,'tasks','New')[0]
        target.parent.mkdir(parents=True)
        target.write_bytes(b'foreign')
        with self.assertRaises(FileExistsError):self.edit(kind='tasks',title='New')
        self.assertEqual(target.read_bytes(),b'foreign')
        self.assertEqual(self.db.execute('SELECT kind,title FROM manager_documents').fetchone(),('strategy','Old'))

    def test_failed_db_update_does_not_touch_disk(self):
        self.db.execute("CREATE TRIGGER no_edit BEFORE UPDATE ON manager_documents BEGIN SELECT RAISE(ABORT,'readonly'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.edit()
        self.assertEqual(self.db.execute('SELECT content FROM manager_documents').fetchone()[0],'Initial')
        self.assertEqual(self.records,[])

    def test_project_isolation_and_id_collision(self):
        other='a'*8+'b'*24
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',
                        (other,self.pid,'strategy','Old','foreign','old','old'))
        self.db.commit()
        with self.assertRaises(ValueError):self.edit()
        self.assertEqual(self.records,[])

    def test_replay_multiple_revisions_uses_latest(self):
        self.fail_export=True
        self.edit(title='Old',content='v1')
        self.edit(title='New',content='v2')
        self.fail_export=False
        result=drain(self.db,self.workspace,self.renderer)
        self.assertEqual(result['completed'],1)
        self.assertEqual(_paths(self.workspace(self.pid),self.id,'strategy','New')[0].read_text(),'v2')

    def test_foreign_sql_transaction_never_mutated(self):
        self.db.execute('CREATE TABLE unrelated(id TEXT)')
        self.db.execute('INSERT INTO unrelated VALUES(?)',('active',))
        with self.assertRaises(RuntimeError):self.edit()
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT id FROM unrelated').fetchone()[0],'active')

if __name__=='__main__':unittest.main()

class DocumentDeletionCoherence(unittest.TestCase):
    def test_delete_renamed_pending_document_clears_export_intent_and_owned_files(self):
        from project_deletion import delete_project_data
        with tempfile.TemporaryDirectory() as tmp:
            pid='f'*32;doc_id='e'*32
            root=Path(tmp)/pid
            root.mkdir()
            db=sqlite3.connect(Path(tmp)/'db.sqlite')
            try:
                db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,category TEXT,description TEXT)')
                db.execute('CREATE TABLE project_locations(project_id TEXT PRIMARY KEY,folder TEXT)')
                db.execute('CREATE TABLE manager_documents(id TEXT PRIMARY KEY, project_id TEXT,kind TEXT,title TEXT,content TEXT,created_at TEXT,updated_at TEXT)')
                db.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,pid,'',''))
                db.execute('INSERT INTO project_locations VALUES(?,?)',(pid,str(root)))
                db.execute('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',(doc_id,pid,'strategy','Old','old','t','t'))
                db.commit()
                old=_paths(root,doc_id,'strategy','Old')
                for path in old[:2]:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(b'%PDF-old-valid-content' if path.suffix=='.pdf' else b'old')
                register_update=lambda title,kind:__import__('manual_document_projection').register(
                    db,root,project_id=pid,document_id=doc_id,kind=kind,title=title,
                    content='edited',updated_at='now')
                db.execute('BEGIN IMMEDIATE')
                register_update('New','tasks')
                db.commit()
                self.assertTrue(delete_project_data(db,pid,'documents',root,[doc_id]))
                self.assertFalse(any(path.exists() for path in old[:2]))
                self.assertEqual(db.execute('SELECT count(*) FROM manager_document_exports').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM manager_documents').fetchone()[0],0)
            finally:
                db.close()

class PendingRevisionOwnership(unittest.TestCase):
    def test_pending_rename_reserves_old_export_name_across_documents(self):
        from manual_document_projection import register
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=sqlite3.connect(':memory:')
            try:
                pid='1'*32;a='a'*32;b='a'*8+'b'*24
                db.execute('CREATE TABLE manager_documents(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,title TEXT,content TEXT,created_at TEXT,updated_at TEXT)')
                db.executemany('INSERT INTO manager_documents VALUES(?,?,?,?,?,?,?)',[
                    (a,pid,'strategy','Old','a','t','t'),
                    (b,pid,'strategy','Other','b','t','t')])
                db.commit()
                db.execute('BEGIN IMMEDIATE')
                register(db,root,project_id=pid,document_id=a,kind='tasks',title='New',content='a2',updated_at='t')
                db.commit()
                db.execute('BEGIN IMMEDIATE')
                with self.assertRaises(ValueError):
                    register(db,root,project_id=pid,document_id=b,kind='strategy',title='Old',content='b2',updated_at='t')
                db.rollback()
                self.assertEqual(db.execute('SELECT title FROM manager_documents WHERE id=?',(b,)).fetchone()[0],'Other')
            finally:db.close()
