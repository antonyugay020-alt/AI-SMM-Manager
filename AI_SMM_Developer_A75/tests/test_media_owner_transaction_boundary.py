"""Local media image admission: preflight, ownership, and transaction faults."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import media_result_ownership as owner


class MediaOwnershipTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,name TEXT,path TEXT,created_at TEXT)')
        for pid in ('a', 'b'):
            self.db.execute('INSERT INTO projects VALUES(?)', (pid,))
            (self.root / pid / 'processed').mkdir(parents=True)
        self.db.commit()
        self.file = self.root / 'a' / 'processed' / 'out.png'
        Image.new('RGB', (8, 8)).save(self.file)

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0]

    def publish(self, path=None, project='a', root=None):
        return owner.commit_owned_image(self.db, project, self.file,
                                        path or self.file, root or self.root / 'a')

    def test_success_duplicate_and_no_open_transaction(self):
        self.assertEqual(self.publish(), str(self.file))
        self.assertEqual(self.count(), 1)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(self.db.in_transaction)

    def test_wrong_project_path_rejected(self):
        foreign = self.root / 'b' / 'processed' / 'out.png'
        Image.new('RGB', (8, 8)).save(foreign)
        with self.assertRaises(ValueError):
            self.publish(path=foreign)
        self.assertEqual(self.count(), 0)

    def test_decode_and_hash_outside_sqlite_transaction(self):
        original = owner.capture
        def checked(*args, **kwargs):
            self.assertFalse(self.db.in_transaction)
            return original(*args, **kwargs)
        with patch.object(owner, 'capture', side_effect=checked):
            self.publish()

    def test_mutation_during_preflight_is_rejected(self):
        original = owner.capture
        def mutate(*args, **kwargs):
            evidence = original(*args, **kwargs)
            Image.new('RGB', (16, 16)).save(self.file)
            return evidence
        with patch.object(owner, 'capture', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.publish()
        self.assertEqual(self.count(), 0)

    def test_mutation_after_insert_rolls_back(self):
        real = owner.unchanged
        calls = [0]
        def mutate(evidence):
            calls[0] += 1
            if calls[0] == 3:
                Image.new('RGB', (16, 16)).save(self.file)
            return real(evidence)
        with patch.object(owner, 'unchanged', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.publish()
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.db.in_transaction)

    def test_failed_db_insert_rolls_back(self):
        self.db.execute("CREATE TRIGGER block BEFORE INSERT ON project_files BEGIN SELECT RAISE(ABORT,'blocked'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.publish()
        self.assertEqual(self.count(), 0)

    def test_ambient_transaction_preserved(self):
        self.db.execute("INSERT INTO projects VALUES('pending')")
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

    def test_linked_ancestor_rejected(self):
        link = self.root / 'alias'
        try:
            link.symlink_to(self.root / 'a', target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):
            self.publish(root=link, path=link / 'processed' / 'out.png')

    def test_corrupt_image_rejected(self):
        self.file.write_bytes(b'not a png')
        with self.assertRaises((ValueError, OSError)):
            self.publish()
        self.assertEqual(self.count(), 0)


if __name__ == '__main__':
    unittest.main()
