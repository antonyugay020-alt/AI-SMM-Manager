"""Cross-service acceptance of multi-project isolation and restart semantics.

Exercises the actual database bootstrap, project registry, workspace, tasks,
worker recovery, results, project switch, deletion and a second process-like
connection. This is a headless contract, not a substitute for Windows Qt E2E.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
from project_workspace_runtime import ProjectWorkspaceResolver


class EndToEndProjectBoundaryTests(unittest.TestCase):
    def test_independent_projects_survive_restart_and_deletion(self):
        with TemporaryDirectory() as home:
            base=Path(home)
            path=base/'application.sqlite3'
            projects=base/'projects'
            db=open_application_database(path,projects)
            try:
                core=ApplicationRuntime(db,ProjectWorkspaceResolver(db))
                core.configure_project_control(base/'backups')
                a=core.projects.create(projects,'Alpha','marketing','A','now')
                b=core.projects.create(projects,'Beta','publishing','B','now')
                assert a!=b
                task=core.executions.activate(a,'Finish the campaign','Pending review')
                core.executions.begin(a,task)
                db.commit()
                core.record_local_command(a,'Alpha private request',timestamp='now')
                core.record_local_command(b,'Beta private request',timestamp='now')
                receipt=core.save_text(b,'Durable Beta result','now')
                self.assertTrue(receipt.committed)
                self.assertTrue(receipt.exported)
                self.assertEqual(receipt.path.read_text(), 'Durable Beta result')
                core.recover_inflight()
                self.assertEqual(db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
                                            (task,)).fetchone(),('active',))
                self.assertEqual(db.execute('SELECT state FROM execution_attempts WHERE task_id=?',
                                            (task,)).fetchone(),('interrupted',))
                view=core.prepare_project_switch(b)
                self.assertEqual(view.project_id,b)
                self.assertEqual([item[1] for item in view.messages], ['Beta private request'])
                self.assertEqual(db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',
                                            (a,)).fetchone(),('Finish the campaign',))
                deleted_folder=core.workspace(a)
                backup=core.controls.backup_and_delete(a,'project')
                self.assertTrue(backup.backup.exists())
                self.assertFalse(deleted_folder.exists())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],1)
                self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])
            finally: db.close()
            # Reopen through the same entrypoint the Windows app uses.
            db=open_application_database(path,projects)
            try:
                core=ApplicationRuntime(db,ProjectWorkspaceResolver(db))
                self.assertEqual([p.id for p in core.projects.list_projects()],[b])
                self.assertEqual(core.data.workspace(b).project_id,b)
                self.assertEqual(core.data.workspace(b).messages[0][1],'Beta private request')
                self.assertTrue(receipt.path.is_file())
                self.assertEqual(receipt.path.read_text(),'Durable Beta result')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_pending_tasks WHERE project_id=?',
                                            (a,)).fetchone()[0],0)
                self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])
            finally:db.close()

if __name__=='__main__':unittest.main()
