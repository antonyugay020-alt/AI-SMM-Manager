"""Cross-resource deletion acceptance without Qt or external network."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from project_deletion import delete_project_data, recover_deletions


class RecoverableDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = sqlite3.connect(self.path / 'projects.sqlite3')
        self.addCleanup(lambda: self.db.close())
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,category TEXT,description TEXT);
            CREATE TABLE project_locations(project_id TEXT PRIMARY KEY,folder TEXT);
            CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,stored_path TEXT);
            CREATE TABLE messages(id TEXT PRIMARY KEY,project_id TEXT);
            CREATE TABLE manager_documents(id TEXT PRIMARY KEY,project_id TEXT);
        ''')
        self.pids = ['a'*32,'b'*32]
        self.folders = {}
        for pid in self.pids:
            folder = self.path / pid
            (folder / 'originals').mkdir(parents=True)
            (folder / 'ready').mkdir()
            (folder / 'originals' / 'source.txt').write_text(pid, encoding='utf-8')
            (folder / 'ready' / 'saved.txt').write_text(pid,encoding='utf-8')
            self.folders[pid] = folder
            self.db.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,pid,'cat','desc'))
            self.db.execute('INSERT INTO project_locations VALUES(?,?)',(pid,str(folder)))
            self.db.execute('INSERT INTO assets VALUES(?,?,?)',(pid,pid,str(folder/'originals'/'source.txt')))
            self.db.execute('INSERT INTO messages VALUES(?,?)',(pid,pid))
            self.db.execute('INSERT INTO manager_documents VALUES(?,?)',(pid,pid))
        self.db.commit()

    def test_full_project_clears_owned_files_and_rows_only(self):
        a,b=self.pids
        self.assertTrue(delete_project_data(self.db,a,'project',self.folders[a]))
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages WHERE project_id=?',(a,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages WHERE project_id=?',(b,)).fetchone()[0],1)
        self.assertFalse((self.folders[a]/'ready'/'saved.txt').exists())
        self.assertTrue((self.folders[b]/'ready'/'saved.txt').is_file())
        self.assertIsNone(self.db.execute('SELECT id FROM projects WHERE id=?',(a,)).fetchone())
        self.assertIsNone(self.db.execute('SELECT folder FROM project_locations WHERE project_id=?',(a,)).fetchone())

    def test_assets_only_leaves_finished_artifacts(self):
        a=self.pids[0]
        delete_project_data(self.db,a,'assets',self.folders[a])
        self.assertFalse((self.folders[a]/'originals'/'source.txt').exists())
        self.assertTrue((self.folders[a]/'ready'/'saved.txt').is_file())
        self.assertEqual(self.db.execute('SELECT count(*) FROM manager_documents WHERE project_id=?',(a,)).fetchone()[0],1)

    def test_single_document_does_not_touch_other_project_or_files(self):
        a,b=self.pids
        delete_project_data(self.db,a,'documents',self.folders[a],[a])
        self.assertEqual(self.db.execute('SELECT count(*) FROM manager_documents').fetchone()[0],1)
        self.assertTrue((self.folders[b]/'originals'/'source.txt').is_file())

    def test_fail_before_commit_restores_files_and_rows(self):
        a=self.pids[0]
        self.db.execute('''CREATE TRIGGER abort_remove BEFORE DELETE ON assets
                          BEGIN SELECT RAISE(ABORT,'injected failure'); END''')
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            delete_project_data(self.db,a,'project',self.folders[a])
        self.assertTrue((self.folders[a]/'originals'/'source.txt').is_file())
        self.assertTrue((self.folders[a]/'ready'/'saved.txt').is_file())
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets WHERE project_id=?',(a,)).fetchone()[0],1)
        self.assertTrue((self.folders[self.pids[1]]/'ready'/'saved.txt').is_file())

    def test_cleanup_after_commit_can_be_retried(self):
        a=self.pids[0]
        original_rmtree=__import__('shutil').rmtree
        def fail_stage_once(path,*args,**kwargs):
            if '.ai_smm_delete_staging' in str(path):
                raise OSError('injected cleanup outage')
            return original_rmtree(path,*args,**kwargs)
        with patch('project_deletion.shutil.rmtree',side_effect=fail_stage_once):
            delete_project_data(self.db,a,'project',self.folders[a])
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_delete_commit_marks').fetchone()[0],1)
        recover_deletions(self.db,[str(self.folders[a]),str(self.folders[self.pids[1]])])
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_delete_commit_marks').fetchone()[0],0)
        self.assertFalse((self.folders[a]/'ready'/'saved.txt').exists())

    def _crash_child(self, committed):
        pid=self.pids[0]
        snippet = """
import os, sqlite3, sys
from pathlib import Path
from project_deletion import delete_project_data
class CrashConnection(sqlite3.Connection):
    def commit(self):
        if sys.argv[4]=='before' and self.execute('SELECT count(*) FROM ai_delete_commit_marks').fetchone()[0]:
            os._exit(67)
        super().commit()
con=sqlite3.connect(sys.argv[1],factory=CrashConnection)
con.execute('PRAGMA foreign_keys=ON')
if sys.argv[4]=='after':
    import project_deletion
    project_deletion.shutil.rmtree=lambda *a,**kw:os._exit(68)
delete_project_data(con,sys.argv[2],'project',Path(sys.argv[3]))
"""
        code=subprocess.run([sys.executable,'-c',snippet,
                   str(self.path/'projects.sqlite3'),pid,str(self.folders[pid]),
                   'after' if committed else 'before'],
                   capture_output=True,text=True,timeout=15)
        self.assertEqual(code.returncode,68 if committed else 67,code.stderr)
        self.db.close()
        self.db=sqlite3.connect(self.path/'projects.sqlite3')
        self.db.execute('PRAGMA foreign_keys=ON')
        recover_deletions(self.db,[str(folder) for folder in self.folders.values()])
        return pid

    def test_process_termination_before_commit_restores_files(self):
        pid=self._crash_child(False)
        self.assertTrue((self.folders[pid]/'originals'/'source.txt').exists())
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets WHERE project_id=?',(pid,)).fetchone()[0],1)

    def test_process_termination_after_commit_finishes_cleanup(self):
        pid=self._crash_child(True)
        self.assertFalse((self.folders[pid]/'originals'/'source.txt').exists())
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets WHERE project_id=?',(pid,)).fetchone()[0],0)
        self.assertTrue((self.folders[self.pids[1]]/'ready'/'saved.txt').exists())

    def test_assets_cleanup_failure_can_recover_nested_originals_path(self):
        """A real asset manifest contains originals/name, not just one segment."""
        a = self.pids[0]
        original_rmtree = __import__('shutil').rmtree
        def fail_stage_only(path, *args, **kwargs):
            if '.ai_smm_delete_staging' in str(path):
                raise OSError('injected cleanup failure')
            return original_rmtree(path, *args, **kwargs)
        with patch('project_deletion.shutil.rmtree', side_effect=fail_stage_only):
            delete_project_data(self.db, a, 'assets', self.folders[a])
        self.assertFalse((self.folders[a] / 'originals' / 'source.txt').exists())
        recover_deletions(self.db, [str(f) for f in self.folders.values()])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_delete_commit_marks').fetchone()[0], 0)
        self.assertFalse((self.folders[a] / 'originals' / 'source.txt').exists())
        self.assertTrue((self.folders[self.pids[1]] / 'originals' / 'source.txt').exists())

    def test_assets_crash_before_commit_restores_nested_originals_path(self):
        a = self.pids[0]
        stage = self.path / '.ai_smm_delete_staging' / ('d' * 32)
        (stage / 'content' / 'originals').mkdir(parents=True)
        import json
        manifest = {'op_id': stage.name, 'project_ref': a, 'project_root': str(self.folders[a]),
                    'scope': 'assets', 'entries': ['originals/source.txt']}
        (stage / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        os.replace(self.folders[a] / 'originals' / 'source.txt',
                   stage / 'content' / 'originals' / 'source.txt')
        recover_deletions(self.db, [str(f) for f in self.folders.values()])
        self.assertEqual((self.folders[a] / 'originals' / 'source.txt').read_text(encoding='utf-8'), a)
        self.assertFalse(stage.exists())

    def test_malicious_manifest_traversal_is_rejected(self):
        a = self.pids[0]
        stage = self.path / '.ai_smm_delete_staging' / ('e' * 32)
        stage.mkdir(parents=True)
        import json
        (stage / 'manifest.json').write_text(json.dumps({
            'op_id': stage.name, 'project_ref': a, 'project_root': str(self.folders[a]),
            'scope': 'assets', 'entries': ['originals/../ready/saved.txt']}), encoding='utf-8')
        with self.assertRaises(RuntimeError):
            recover_deletions(self.db, [str(f) for f in self.folders.values()])
        self.assertTrue((self.folders[a] / 'ready' / 'saved.txt').exists())
        self.assertTrue((self.folders[self.pids[1]] / 'ready' / 'saved.txt').exists())

    def test_wrong_folder_is_rejected_before_any_change(self):
        a,b=self.pids
        with self.assertRaises(ValueError):
            delete_project_data(self.db,a,'project',self.folders[b])
        self.assertTrue((self.folders[b]/'ready'/'saved.txt').exists())
        self.assertTrue((self.folders[a]/'ready'/'saved.txt').exists())

    def test_recovery_without_commit_marker_restores_abandoned_files(self):
        a=self.pids[0]
        folder=self.folders[a]
        stage=folder.parent/'.ai_smm_delete_staging'/('c'*32)
        (stage/'content'/'originals').mkdir(parents=True)
        import json
        (stage/'manifest.json').write_text(json.dumps({'op_id':stage.name,'project_ref':a,
            'project_root':str(folder),'scope':'assets','entries':['originals/source.txt']}),encoding='utf-8')
        os.replace(folder/'originals'/'source.txt',stage/'content'/'originals'/'source.txt')
        recover_deletions(self.db,[str(folder)])
        self.assertTrue((folder/'originals'/'source.txt').exists())
        self.assertTrue((folder/'ready'/'saved.txt').exists())
        self.assertFalse(stage.exists())

if __name__=='__main__': unittest.main()
