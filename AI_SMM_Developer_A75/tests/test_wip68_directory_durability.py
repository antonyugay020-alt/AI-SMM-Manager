import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from durable_json import atomic_write_json


class TestDirectoryDurability(unittest.TestCase):
    def test_atomic_json_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            atomic_write_json(path, {'version': 1})
            atomic_write_json(path, {'version': 2})
            self.assertEqual(path.read_text(encoding='utf-8').count('"version"'), 1)
            self.assertIn('2', path.read_text())
            self.assertFalse(list(Path(directory).glob('*.tmp')))

    @unittest.skipUnless(os.name == 'posix', 'Directory sync supported on POSIX')
    def test_directory_synced_after_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('durable_json.os.fsync', wraps=os.fsync) as sync:
                atomic_write_json(path, {'valid': False})
            self.assertGreaterEqual(sync.call_count, 2)
            self.assertTrue(path.exists())

    @unittest.skipUnless(os.name == 'posix', 'Directory sync supported on POSIX')
    def test_failed_directory_sync_is_reported_not_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('durable_json.os.fsync', side_effect=[None, OSError('directory sync failed')]):
                with self.assertRaisesRegex(OSError, 'directory sync failed'):
                    atomic_write_json(path, {'state': 'candidate'})
            self.assertTrue(path.exists())
