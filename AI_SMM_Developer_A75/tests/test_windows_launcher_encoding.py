from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile
import unittest
import release_guard

class WindowsBatchEncodingTests(unittest.TestCase):
    def make_source(self, where):
        folder=Path(where)/'AI_SMM_TEST_BATCH'
        folder.mkdir()
        for name in release_guard.REQUIRED_FILES:
            (folder/name).write_bytes(b'@echo off\r\nexit /b 0\r\n' if name.endswith('.cmd') else b'test')
        return folder

    def test_unix_newlines_are_blocked(self):
        with TemporaryDirectory() as tmp:
            folder=self.make_source(tmp)
            (folder/'RUN.cmd').write_bytes(b'@echo off\necho test\n')
            with self.assertRaises(release_guard.PackagingBlocked):
                release_guard.package(folder,Path(tmp)/'RUN_NEEDS_REAL_TEST.zip','acceptance')

    def test_utf8_russian_batch_is_blocked(self):
        with TemporaryDirectory() as tmp:
            folder=self.make_source(tmp)
            (folder/'RUN.bat').write_bytes('@echo off\r\necho Проверка\r\n'.encode('utf-8'))
            with self.assertRaises(release_guard.PackagingBlocked):
                release_guard.package(folder,Path(tmp)/'RUN_NEEDS_REAL_TEST.zip','acceptance')

    def test_ascii_crlf_passes_and_survives_roundtrip(self):
        with TemporaryDirectory() as tmp:
            folder=self.make_source(tmp)
            good=b'@echo off\r\necho RUNNING\r\n'
            (folder/'RUN.cmd').write_bytes(good)
            archive=Path(tmp)/'RUN_NEEDS_REAL_TEST.zip'
            release_guard.package(folder,archive,'acceptance')
            release_guard.verify(archive)
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(z.read(folder.name+'/RUN.cmd'),good)
                z.extractall(Path(tmp)/'extracted')
            self.assertEqual(release_guard.verify_tree(Path(tmp)/'extracted'/folder.name)['status'],'PASS')

    def test_check_real_launchers(self):
        # Resolve the shipped source independently of the shell working
        # directory; the checkpoint keeps program and developer tests adjacent.
        test_dir = Path(__file__).resolve().parent
        name='RUN_ARCHITECTURE_CHECK.cmd'
        candidates=(test_dir.parent/'application'/name,
                    test_dir.parent.parent/'application'/name,
                    test_dir.parent.parent/'AI_SMM_Work_Architecture_A75'/name,
                    test_dir/name)
        launcher=next((p for p in candidates if p.is_file()),None)
        self.assertIsNotNone(launcher,'candidate launcher must be included in source or developer reference')
        data=launcher.read_bytes()
        self.assertTrue(data.isascii())
        self.assertEqual(data.count(b'\n'),data.count(b'\r\n'))
        self.assertTrue(data.endswith(b'\r\n'))

if __name__=='__main__': unittest.main()
