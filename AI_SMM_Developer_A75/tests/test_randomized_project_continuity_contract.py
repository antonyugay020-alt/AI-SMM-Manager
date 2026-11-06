"""Varied multi-project crash/restart exercises, not a model quality benchmark."""
from pathlib import Path
import random
import tempfile
import unittest
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime
from project_workspace_runtime import ProjectWorkspaceResolver


class RandomizedProjectContinuity(unittest.TestCase):
    def test_twenty_distinct_multiservice_restart_scenarios(self):
        rng=random.Random(20261103)
        for case in range(20):
            with self.subTest(scenario=case),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                db=open_application_database(root/'db.sqlite3',root/'projects')
                try:
                    core=ApplicationRuntime(db,ProjectWorkspaceResolver(db))
                    core.configure_project_control(root/'backups')
                    expected={}
                    count=rng.randrange(2,6)
                    for index in range(count):
                        pid=core.projects.create(root/'projects',f'Project {index}', 'campaign', '', 'now')
                        instruction=f'{rng.choice(["Тексты","План","Аналитика","Монтаж"])} / {case} / {index} / {rng.getrandbits(64):016x}'
                        task=core.executions.activate(pid,instruction,'continue later')
                        db.commit()
                        core.record_local_command(pid,instruction,timestamp='now')
                        if rng.choice((True,False)):
                            result=core.save_text(pid,instruction[::-1],'now')
                            self.assertTrue(result.exported)
                        expected[pid]=(instruction,task)
                    victim=rng.choice(list(expected))
                    core.controls.backup_and_delete(victim,'project')
                    del expected[victim]
                    self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])
                finally:db.close()
                db=open_application_database(root/'db.sqlite3',root/'projects')
                try:
                    core=ApplicationRuntime(db,ProjectWorkspaceResolver(db))
                    self.assertEqual({p.id for p in core.projects.list_projects()},set(expected))
                    for pid,(instruction,task) in expected.items():
                        self.assertEqual(core.data.workspace(pid).messages[0][1],instruction)
                        self.assertEqual(db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',(pid,)).fetchone(),
                                         (instruction,))
                        self.assertEqual(db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(task,)).fetchone(),
                                         ('active',))
                    self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])
                finally:db.close()

if __name__=='__main__':unittest.main()
