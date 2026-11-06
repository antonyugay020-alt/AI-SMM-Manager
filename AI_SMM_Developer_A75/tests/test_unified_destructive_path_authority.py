"""Cross-service contract: deletion, backups and restart recovery fail closed on links."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from project_control_runtime import ProjectControlRuntime
from project_deletion import recover_deletions, delete_project_data

class DestructiveAuthorityIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.pid='a'*32
        self.outside=self.base/'outside';self.outside.mkdir()
        self.root=self.base/'store';self.root.mkdir()
        self.project=self.root/self.pid;self.project.mkdir()
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY);CREATE TABLE project_locations(project_id TEXT,folder TEXT);CREATE TABLE messages(project_id TEXT,content TEXT);')
        self.db.execute('INSERT INTO projects VALUES(?)',(self.pid,))
        self.db.execute('INSERT INTO project_locations VALUES(?,?)',(self.pid,str(self.project)))
        self.db.execute('INSERT INTO messages VALUES(?,?)',(self.pid,'unfinished task'))
        self.db.commit()

    def _link(self, link, target):
        try:link.symlink_to(target,target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest('Filesystem links unsupported')

    def test_linked_backup_ancestor_does_not_touch_database_or_external_folder(self):
        parent=self.base/'linked';self._link(parent,self.outside)
        control=ProjectControlRuntime(self.db,lambda pid:self.project,parent/'backups')
        with self.assertRaises(ValueError):control.backup_and_delete(self.pid,'chat')
        self.assertEqual(self.db.execute('SELECT content FROM messages').fetchone(),('unfinished task',))
        self.assertFalse((self.outside/'backups').exists())

    def test_backup_inside_project_rejected_before_creating_directory(self):
        target=self.project/'backups'
        control=ProjectControlRuntime(self.db,lambda pid:self.project,target)
        with self.assertRaises(ValueError):control.backup_and_delete(self.pid,'chat')
        self.assertFalse(target.exists())
        self.assertEqual(self.db.execute('SELECT content FROM messages').fetchone(),('unfinished task',))

    def test_restart_recovery_rejects_linked_project_ancestor(self):
        parent=self.base/'linked';self._link(parent,self.outside)
        with self.assertRaises(ValueError):recover_deletions(self.db,[parent/self.pid])
        self.assertEqual(self.db.execute('SELECT content FROM messages').fetchone(),('unfinished task',))

    def test_delete_runtime_rejects_linked_project_ancestor(self):
        parent=self.base/'linked';self._link(parent,self.outside)
        with self.assertRaises(ValueError):delete_project_data(self.db,self.pid,'chat',parent/self.pid)
        self.assertEqual(self.db.execute('SELECT content FROM messages').fetchone(),('unfinished task',))
