"""Regression/fault-injection for GUI project/publication transaction boundaries."""
import ast, os, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime

APP=Path(__file__).resolve().parents[1]/'app.py'

def method(name, bindings):
    module=ast.parse(APP.read_text(encoding='utf-8'))
    nodes=[n for cls in module.body if isinstance(cls,ast.ClassDef) for n in cls.body
           if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(nodes)==1, name
    scope=dict(bindings)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(APP),'exec'),scope)
    return scope[name]

class PublicationAndProjectGuards(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.base=Path(t.name)/'project';self.base.mkdir()
        self.db=open_application_database(self.base.parent/'manager.sqlite',self.base.parent)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE unrelated(value TEXT)')
        self.db.execute("INSERT INTO projects(id,name,created_at,category,description) VALUES('p','Existing','now','','')")
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.base)
        self.events=[];self.refresh=[]
        self.msg=SimpleNamespace(warning=lambda *a:self.events.append(('warning',a)),
              information=lambda *a:self.events.append(('info',a)),
              Yes=1,No=2,question=lambda *a:1)
        self.entry=self.base/'ready'/'image.png';self.entry.parent.mkdir()
        self.entry.write_bytes(b'actual image data')
        self.item=SimpleNamespace(data=lambda *_:str(self.entry))
        self.view=SimpleNamespace(db=self.db,core=self.core,current_project='p',
              ready_media=SimpleNamespace(selectedItems=lambda:[self.item]),
              refresh_publications=lambda:self.refresh.append('refreshed'),
              statusBar=lambda:SimpleNamespace(showMessage=lambda *a:self.events.append(('status',a))),
              pub_channel=SimpleNamespace(currentText=lambda:'Posts'),
              pub_block=SimpleNamespace(currentText=lambda:'Posts'),
              pub_frequency=SimpleNamespace(currentText=lambda:'Daily'),
              pub_time=SimpleNamespace(text=lambda:'12:00'),
              selected_publication_id=lambda:'job',
              document_ids=[], load_manager_documents=lambda: self.refresh.append('doc'))
        self.bind={'QMessageBox':self.msg,'Qt':SimpleNamespace(UserRole=1),
                   'project_dir':lambda _p:self.base,'ensure_project_folders':lambda _p:self.base,
                   'uuid4':lambda:UUID('12345678123412341234123412341234'),
                   'now':lambda:'now','Path':Path,'os':os,'sqlite3':sqlite3,
                   'QInputDialog':SimpleNamespace(getText=lambda *a,**kw:('2026-11-05 09:00',True)),
                   'datetime':__import__('datetime').datetime}

    def foreign(self):
        self.db.execute('INSERT INTO unrelated VALUES(?)',('must survive',))
        self.assertTrue(self.db.in_transaction)
    def assert_foreign(self):
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT * FROM unrelated').fetchall(),[('must survive',)])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_media_review').fetchone()[0],0)

    def test_media_review_does_not_commit_foreign_transaction(self):
        self.foreign()
        self.assertFalse(method('set_media_review',self.bind)(self.view,'Одобрен'))
        self.assert_foreign()

    def test_media_approval_dialog_does_not_commit_foreign_transaction(self):
        # The guarded approval callback is tested structurally without a Qt event loop.
        body=ast.get_source_segment(APP.read_text(encoding='utf-8'), next(n for cls in ast.parse(APP.read_text(encoding='utf-8')).body if isinstance(cls,ast.ClassDef) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='preview_publication_media'))
        self.assertIn('if self.db.in_transaction:',body)
        self.assertIn('self.core.publication_queue.review_media(',body)
        self.assertNotIn('self.db.rollback()',body)

    def test_publication_commands_do_not_commit_foreign_transaction(self):
        for name in ('add_publication_jobs','set_individual_publication','set_block_publication',
                     'remove_publication_job','save_publication_rule'):
            with self.subTest(name=name):
                self.foreign()
                method(name,self.bind)(self.view)
                self.assert_foreign()
                self.db.rollback()

    def test_remove_ready_media_does_not_commit_foreign_transaction(self):
        self.foreign()
        self.assertFalse(method('remove_ready_media',self.bind)(self.view))
        self.assertTrue(self.entry.read_bytes()==b'actual image data')
        self.assert_foreign()

    def test_ready_media_removal_rolls_back_disk_on_sql_error(self):
        self.db.execute('INSERT INTO publication_jobs(id,project_id,media_path,channel,schedule_mode,created_at) VALUES (?,?,?,?,?,?)',('job','p',str(self.entry),'Posts','Manual','now'))
        self.db.commit()
        self.db.execute('CREATE TRIGGER deny_delete BEFORE DELETE ON publication_jobs BEGIN SELECT RAISE(ABORT, "fault injected"); END')
        self.assertFalse(method('remove_ready_media',self.bind)(self.view))
        # The new protocol persists the move intent before touching the disk.
        # A failed SQL acknowledgment leaves a replayable pending effect.
        pending=self.db.execute("SELECT state,destination_path FROM publication_media_rejections").fetchone()
        self.assertEqual(pending[0],'pending')
        self.assertTrue(Path(pending[1]).is_file())
        self.assertEqual(Path(pending[1]).read_bytes(),b'actual image data')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],1)
        self.db.execute('DROP TRIGGER deny_delete');self.db.commit()
        recovered=self.core.publication_queue.recover_rejections(project_id='p')
        self.assertEqual(len(recovered['done']),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)

    def test_ready_media_success_moves_files_and_commits(self):
        self.db.execute('INSERT INTO publication_jobs(id,project_id,media_path,channel,schedule_mode,created_at) VALUES (?,?,?,?,?,?)',('job','p',str(self.entry),'Posts','Manual','now'))
        self.db.execute('INSERT INTO publication_media_review VALUES (?,?,?,?)',('p',str(self.entry),'Одобрен','now'))
        self.db.commit()
        self.assertTrue(method('remove_ready_media',self.bind)(self.view))
        self.assertFalse(self.entry.exists())
        items=list((self.base/'rejected').iterdir())
        self.assertEqual(len(items),1)
        self.assertEqual(items[0].read_bytes(),b'actual image data')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_jobs').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_media_review').fetchone()[0],0)
        self.assertFalse(self.db.in_transaction)

    def test_ready_media_rejects_symlink_without_touching_target(self):
        external=self.base/'other.png'; external.write_bytes(b'keep')
        self.entry.unlink()
        try:self.entry.symlink_to(external)
        except OSError:self.skipTest('symlink not available')
        self.assertFalse(method('remove_ready_media',self.bind)(self.view))
        self.assertEqual(external.read_bytes(),b'keep')
        self.assertTrue(self.entry.is_symlink())

    def _result_setup(self):
        result=self.base/'processed'/'result.png'
        result.parent.mkdir(exist_ok=True)
        result.write_bytes(b'result contents')
        self.view.result_versions=[str(result)]
        self.view.result_list=SimpleNamespace(
            selectedItems=lambda:[object()], row=lambda *_:0,
            takeItem=lambda *_:self.refresh.append('removed'))
        self.view.result=SimpleNamespace(show_file=lambda *_:self.refresh.append('shown'))
        self.db.execute('INSERT INTO project_files VALUES(?,?,?,?,?,?)',
                        ('result1','p','result','result.png',str(result),'now'))
        self.db.commit()
        return result

    def test_result_deletion_blocks_foreign_transaction(self):
        result=self._result_setup()
        self.foreign()
        self.assertFalse(method('remove_workspace_results',self.bind)(self.view))
        self.assert_foreign()
        self.assertEqual(result.read_bytes(),b'result contents')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)

    def test_result_deletion_restores_on_sql_failure(self):
        result=self._result_setup()
        self.db.execute('CREATE TRIGGER deny_result_delete BEFORE DELETE ON project_files '
                        'BEGIN SELECT RAISE(ABORT, "injected failure"); END')
        self.assertFalse(method('remove_workspace_results',self.bind)(self.view))
        # The intent is durable, and the moved bytes remain recoverable after
        # a crash between filesystem rename and SQLite acknowledgement.
        intent=self.db.execute('SELECT status,destination_path FROM project_result_retirements').fetchone()
        self.assertEqual(intent[0],'pending')
        self.assertEqual(Path(intent[1]).read_bytes(),b'result contents')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],1)
        self.assertEqual(self.view.result_versions,[str(result)])
        self.db.execute('DROP TRIGGER deny_result_delete');self.db.commit()
        repaired=self.core.result_retirements.recover(project_id='p')
        self.assertEqual(len(repaired.committed),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)

    def test_result_deletion_moves_file_and_deletes_row(self):
        result=self._result_setup()
        self.assertTrue(method('remove_workspace_results',self.bind)(self.view))
        self.assertFalse(result.exists())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)
        self.assertEqual(len(list((self.base/'.trash'/'results').iterdir())),1)
        self.assertEqual(self.view.result_versions,[])

    def test_new_project_does_not_commit_foreign_transaction(self):
        from project_operation_guard import project_operation_busy
        class Dialog:
            def __init__(self, *a):pass
            def exec(self):return 1
        self.view.refresh_projects=lambda *_:self.refresh.append('project')
        self.foreign()
        new_path=self.base.parent/'new'
        bindings=dict(self.bind,ProjectDialog=Dialog,QDialog=SimpleNamespace(Accepted=1),
                      project_operation_busy=lambda *_:False,project_dir=lambda _p:new_path,
                      STORAGE_SUBFOLDERS=['originals'],shutil=__import__('shutil'))
        method('add_project',bindings)(self.view)
        self.assertFalse(new_path.exists())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],1)
        self.assert_foreign()

    def test_manual_document_create_does_not_commit_foreign_transaction(self):
        self.foreign()
        self.view.active_section='plans';self.view.current_project='p'
        binding=dict(self.bind,QInputDialog=SimpleNamespace(getText=lambda *a:('Title',True)))
        method('create_manager_document',binding)(self.view)
        self.assert_foreign()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM manager_documents').fetchone()[0],0)

    def test_batch_start_does_not_commit_foreign_transaction(self):
        self.foreign()
        self.view.batch_active=False
        self.view.modern_worker=None
        self.view.cloud_worker=None
        self.view.append_chat=lambda *args:self.events.append(('chat',args))
        method('start_batch',self.bind)(self.view)
        self.assert_foreign()
        self.assertTrue(any(label=='chat' for label,_ in self.events))

    def test_cloud_done_and_batch_checkpoint_fail_closed(self):
        self.foreign()
        self.view.batch_run_id='queue1'
        self.view.batch_inflight=None;self.view.batch_queue=[]
        bindings=dict(self.bind,json=__import__('json'))
        with self.assertRaisesRegex(RuntimeError,'SQLite'):
            method('cloud_done',bindings)(self.view,str(self.entry),project_id='p')
        # Batch ownership is also rejected inside another SQLite transaction.
        with self.assertRaises(RuntimeError):
            self.core.batches.create('p',[str(self.entry)],'edit',0)
        self.assert_foreign()

if __name__=='__main__':unittest.main()
