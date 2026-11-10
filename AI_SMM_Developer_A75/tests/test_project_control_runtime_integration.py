"""End-to-end destructive project operation safety without Qt."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from project_control_runtime import ProjectControlRuntime

class ProjectControlIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.pid='a'*32
        self.project=self.root/self.pid
        self.project.mkdir()
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,category TEXT,description TEXT)')
        self.db.execute('CREATE TABLE project_locations(project_id TEXT PRIMARY KEY,folder TEXT)')
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT)')
        self.db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT)')
        self.db.execute('CREATE TABLE manager_documents(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,title TEXT)')
        self.db.execute('INSERT INTO projects VALUES(?,?,?)',(self.pid,'active','keep'))
        self.db.execute('INSERT INTO project_locations VALUES(?,?)',(self.pid,str(self.project)))
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?)',(1,self.pid,'user','history'))
        self.db.commit()
        self.control=ProjectControlRuntime(self.db,lambda pid:self.root/pid,self.root/'backups')

    def test_chat_deletion_backup_and_scoped_verification(self):
        (self.project/'note.txt').write_text('never delete this', encoding='utf-8')
        receipt=self.control.backup_and_delete(self.pid,'chat')
        self.assertTrue(receipt.backup.is_file())
        with zipfile.ZipFile(receipt.backup) as z:
            rows=json.loads(z.read('database_rows.json'))
            self.assertEqual(rows['tables']['messages']['rows'][0][-1],'history')
            self.assertEqual(z.read('project_files/note.txt'),b'never delete this')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(0,))
        self.assertEqual((self.project/'note.txt').read_text(encoding='utf-8'),'never delete this')

    def test_foreign_document_identity_fails_without_backup(self):
        self.db.execute('INSERT INTO manager_documents VALUES(?,?,?,?)',('b'*32,'foreign','strategy','private'))
        self.db.commit()
        with self.assertRaises(ValueError):
            self.control.backup_and_delete(self.pid,'documents',selected_ids=('b'*32,))
        self.assertFalse((self.root/'backups').exists())

    def test_unowned_transaction_is_preserved(self):
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?)',(2,self.pid,'user','uncommitted'))
        with self.assertRaises(RuntimeError):self.control.backup_and_delete(self.pid,'chat')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(2,))

    def test_symlink_inside_workspace_aborts_before_mutation(self):
        outside=self.root/'private.txt'; outside.write_text('untouchable', encoding='utf-8')
        try:(self.project/'linked.txt').symlink_to(outside)
        except (OSError,NotImplementedError):self.skipTest('symlink unsupported')
        with self.assertRaises(ValueError):self.control.backup_and_delete(self.pid,'chat')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(1,))
        self.assertEqual(outside.read_text(encoding='utf-8'),'untouchable')
        self.assertEqual(list((self.root/'backups').glob('*.zip')),[])

    def test_project_deletion_removes_owned_state_but_preserves_other_project(self):
        foreign='b'*32
        (self.root/foreign).mkdir()
        self.db.execute('INSERT INTO projects VALUES(?,?,?)',(foreign,'foreign','unrelated'))
        self.db.execute('INSERT INTO project_locations VALUES(?,?)',(foreign,str(self.root/foreign)))
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?)',(3,foreign,'user','retain'))
        self.db.commit()
        (self.project/'personal.txt').write_text('delete only owned data', encoding='utf-8')
        receipt=self.control.backup_and_delete(self.pid,'project')
        self.assertTrue(receipt.backup.is_file())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages WHERE project_id=?',(self.pid,)).fetchone(),(0,))
        self.assertEqual(self.db.execute('SELECT content FROM messages WHERE project_id=?',(foreign,)).fetchone(),('retain',))
        self.assertFalse((self.project/'personal.txt').exists())
        self.assertEqual(self.db.execute('SELECT description FROM projects WHERE id=?',(foreign,)).fetchone(),('unrelated',))

    def test_document_deletion_only_selected_identity(self):
        selected='c'*32; another='d'*32
        self.db.executemany('INSERT INTO manager_documents VALUES(?,?,?,?)',[(selected,self.pid,'strategy','A'),(another,self.pid,'strategy','B')])
        self.db.commit()
        (self.project/'strategies').mkdir()
        (self.project/'strategies'/('A_'+selected[:8]+'.txt')).write_text('chosen', encoding='utf-8')
        (self.project/'strategies'/('B_'+another[:8]+'.txt')).write_text('unchosen', encoding='utf-8')
        self.control.backup_and_delete(self.pid,'documents',selected_ids=(selected,))
        self.assertFalse((self.project/'strategies'/('A_'+selected[:8]+'.txt')).exists())
        self.assertEqual((self.project/'strategies'/('B_'+another[:8]+'.txt')).read_text(encoding='utf-8'),'unchosen')
        self.assertEqual(self.db.execute('SELECT id FROM manager_documents').fetchone(),(another,))

    def test_asset_deletion_preserves_texts_and_documents(self):
        self.db.execute('INSERT INTO assets VALUES(?,?)',('e'*32,self.pid))
        self.db.commit()
        (self.project/'originals').mkdir()
        (self.project/'originals'/'source.png').write_bytes(b'img')
        (self.project/'texts').mkdir()
        (self.project/'texts'/'draft.txt').write_text('keep', encoding='utf-8')
        self.control.backup_and_delete(self.pid,'assets')
        self.assertFalse((self.project/'originals'/'source.png').exists())
        self.assertEqual((self.project/'texts'/'draft.txt').read_text(encoding='utf-8'),'keep')
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets').fetchone(),(0,))

    def test_failed_database_delete_restores_staged_files_and_keeps_backup(self):
        (self.project/'originals').mkdir()
        source=self.project/'originals'/'photo.png';source.write_bytes(b'original')
        self.db.execute('INSERT INTO assets VALUES(?,?)',('f'*32,self.pid))
        self.db.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON assets BEGIN SELECT RAISE(ABORT,'blocked'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            self.control.backup_and_delete(self.pid,'assets')
        self.assertEqual(source.read_bytes(),b'original')
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets').fetchone(),(1,))
        self.assertEqual(len(list((self.root/'backups').glob('*.zip'))),1)

    def test_backup_binary_sqlite_values_are_recoverable(self):
        import base64
        blob=b'\x00\xff\x10original'
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?)',(7,self.pid,'assistant',blob))
        self.db.commit()
        receipt=self.control.backup_and_delete(self.pid,'chat')
        with zipfile.ZipFile(receipt.backup) as archive:
            payload=json.loads(archive.read('database_rows.json'))
            records=payload['tables']['messages']['rows']
            encoded=next(row[-1]['__sqlite_blob_b64__'] for row in records if row[0]==7)
            self.assertEqual(base64.b64decode(encoded),blob)
            hashes=json.loads(archive.read('files_sha256.json'))
            self.assertEqual(hashes,{})

    def test_preflight_runs_under_sqlite_write_transaction(self):
        from project_deletion import delete_project_data
        observed=[]
        def refuse():
            observed.append(self.db.in_transaction)
            raise RuntimeError('backup device unavailable')
        with self.assertRaisesRegex(RuntimeError,'backup device unavailable'):
            delete_project_data(self.db,self.pid,'chat',self.project,preflight=refuse)
        self.assertEqual(observed,[True])
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(1,))

    def test_missing_workspace_never_deletes_database(self):
        self.project.rmdir()
        with self.assertRaises(ValueError):self.control.backup_and_delete(self.pid,'chat')
        self.assertEqual(self.db.execute('SELECT count(*) FROM messages').fetchone(),(1,))

if __name__=='__main__':unittest.main()
