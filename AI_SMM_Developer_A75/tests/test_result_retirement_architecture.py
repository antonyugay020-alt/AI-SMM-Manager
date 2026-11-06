"""Adversarial full-lifecycle result retirement without Qt."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from application_bootstrap import open_application_database
from application_core import ApplicationRuntime


class ResultRetirementArchitecture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.database=self.root/'state.sqlite'
        self.db=open_application_database(self.database,self.root)
        self.addCleanup(self._close)
        self.a='a'*32;self.b='b'*32
        for pid in (self.a,self.b):
            self.db.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',(pid,pid,'now'))
            (self.root/pid/'processed').mkdir(parents=True)
        self.db.commit()
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)

    def _close(self):
        try:self.db.close()
        except Exception:pass

    def add(self,pid,name,body=b'result'):
        path=self.root/pid/'processed'/name;path.write_bytes(body)
        self.db.execute('INSERT INTO project_files VALUES(?,?,?,?,?,?)',
                        (pid+name,pid,'result',name,str(path),'now'));self.db.commit()
        return path

    def test_several_results_deleted_durably_and_neighbor_untouched(self):
        a=self.add(self.a,'one.png');b=self.add(self.a,'two.png');foreign=self.add(self.b,'other.png')
        r=self.core.result_retirements.request(self.a,[a,b]);self.assertEqual(len(r.committed),2)
        self.assertEqual(r.failed,())
        self.assertFalse(a.exists());self.assertFalse(b.exists());self.assertEqual(foreign.read_bytes(),b'result')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE project_id=?',(self.a,)).fetchone()[0],0)
        self.assertEqual(len(list((self.root/self.a/'.trash'/'results').iterdir())),2)

    def test_foreign_paths_and_unregistered_file_fail_before_move(self):
        foreign=self.add(self.b,'other.png')
        with self.assertRaises(ValueError):self.core.result_retirements.request(self.a,[foreign])
        self.assertTrue(foreign.exists())
        unregistered=self.root/self.a/'processed'/'extra.png';unregistered.write_bytes(b'extra')
        with self.assertRaises(ValueError):self.core.result_retirements.request(self.a,[unregistered])
        self.assertTrue(unregistered.exists())

    def test_existing_foreign_transaction_never_committed(self):
        own=self.add(self.a,'one.png')
        self.db.execute('UPDATE projects SET name=? WHERE id=?',('uncommitted',self.a))
        with self.assertRaises(RuntimeError):self.core.result_retirements.request(self.a,[own])
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT name FROM projects WHERE id=?',(self.a,)).fetchone()[0],self.a)
        self.assertEqual(own.read_bytes(),b'result')

    def test_restart_recovers_move_without_second_mutation(self):
        own=self.add(self.a,'one.png',b'original')
        self.db.execute('''CREATE TRIGGER reject_delete BEFORE DELETE ON project_files
            BEGIN SELECT RAISE(ABORT,'not now'); END''');self.db.commit()
        r=self.core.result_retirements.request(self.a,[own])
        self.assertEqual(len(r.failed),1)
        dest=self.db.execute('SELECT destination_path FROM project_result_retirements').fetchone()[0]
        self.assertFalse(own.exists());self.assertEqual(Path(dest).read_bytes(),b'original')
        self.db.execute('DROP TRIGGER reject_delete');self.db.commit();self.db.close()
        self.db=open_application_database(self.database,self.root)
        self.core=ApplicationRuntime(self.db,lambda pid:self.root/pid)
        repaired=self.core.result_retirements.recover(project_id=self.a)
        self.assertEqual(len(repaired.committed),1)
        self.assertEqual(self.core.result_retirements.recover(project_id=self.a).committed,())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE project_id=?',(self.a,)).fetchone()[0],0)

    def test_checksum_change_refuses_deletion_and_keeps_unrelated(self):
        own=self.add(self.a,'one.png',b'original');foreign=self.add(self.b,'other.png')
        self.db.execute('''CREATE TRIGGER reject_delete BEFORE DELETE ON project_files
            BEGIN SELECT RAISE(ABORT,'not now'); END''');self.db.commit()
        self.core.result_retirements.request(self.a,[own]);self.db.execute('DROP TRIGGER reject_delete');self.db.commit()
        dest=Path(self.db.execute('SELECT destination_path FROM project_result_retirements').fetchone()[0]);dest.write_bytes(b'changed')
        status=self.core.result_retirements.recover(project_id=self.a)
        self.assertEqual(len(status.failed),1)
        self.assertEqual(foreign.read_bytes(),b'result')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files WHERE project_id=?',(self.a,)).fetchone()[0],1)

    def test_file_rename_and_sqlite_receipt_are_separately_recoverable(self):
        own=self.add(self.a,'one.png',b'original')
        self.db.execute('''CREATE TRIGGER reject_delete BEFORE DELETE ON project_files
            BEGIN SELECT RAISE(ABORT,'not now'); END''');self.db.commit()
        self.core.result_retirements.request(self.a,[own])
        dest=Path(self.db.execute('SELECT destination_path FROM project_result_retirements').fetchone()[0]);self.assertTrue(dest.exists())
        self.assertFalse(own.exists())
        self.db.execute('DROP TRIGGER reject_delete');self.db.commit()
        status=self.core.result_retirements.request(self.a,[own])
        self.assertEqual(len(status.committed),1)

if __name__=='__main__':unittest.main()
