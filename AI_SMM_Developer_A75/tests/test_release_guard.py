"""Policy tests (synthetic fixtures test the gate, not product release readiness)."""
from __future__ import annotations
from contextlib import contextmanager
import importlib.util
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile
import re

import release_guard as g

class ReleasePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.folder=self.base/'AI_SMM_TEST_V6'
        self.folder.mkdir()
        for rel in sorted(g.REQUIRED_FILES):
            (self.folder/rel).write_bytes(b'@echo off\r\nexit /b 0\r\n' if rel.endswith('.cmd') else b'valid fixture')
        self.a=self.base/'AI_SMM_TEST_NEEDS_REAL_TEST.zip'

    def valid(self):
        verdict=g.package(self.folder,self.a,'acceptance')
        self.assertEqual(verdict['root'],self.folder.name)
        self.assertEqual(verdict['release_status'],'NOT_RELEASE')
        return verdict

    def test_archive_must_have_exactly_one_root(self):
        self.valid()
        with zipfile.ZipFile(self.a) as z:
            ns=z.namelist()
            self.assertTrue(ns)
            self.assertEqual({x.split('/')[0] for x in ns},{self.folder.name})
            self.assertTrue(all(x.startswith(self.folder.name+'/') for x in ns))

    def test_real_zip_corruption_is_rejected(self):
        self.valid()
        archive=self.a.read_bytes()
        self.a.write_bytes(archive[:-40])
        with self.assertRaises(g.PackagingBlocked):
            g.verify(self.a)

    def test_no_mixed_roots(self):
        self.valid()
        with zipfile.ZipFile(self.a,'a') as z:
            z.writestr('READ_ME_FIRST.txt','stray flat file')
        with self.assertRaises(g.PackagingBlocked):
            g.verify(self.a)

    def test_root_level_file_instead_of_folder_is_rejected(self):
        with zipfile.ZipFile(self.a,'w') as z:
            z.writestr('one_file.txt','x')
        with self.assertRaises(g.PackagingBlocked):
            g.verify(self.a)

    def test_traversal_is_rejected(self):
        self.valid()
        with zipfile.ZipFile(self.a,'a') as z:
            z.writestr(f'{self.folder.name}/../outside.txt','no')
        with self.assertRaises(g.PackagingBlocked):
            g.verify(self.a)

    def test_windows_case_collision_rejected(self):
        # Windows cannot store app.py and App.py as two separate directory
        # entries.  Test source collisions only when the filesystem supports
        # them; always test malicious ZIP names independently.
        twin=self.folder/'App.py'
        if not twin.exists():
            twin.write_text('bad on Windows',encoding='utf-8')
            with self.assertRaises(g.PackagingBlocked):
                g.package(self.folder,self.a,'acceptance')
            self.assertFalse(self.a.exists())
            twin.unlink()
        self.valid()
        with zipfile.ZipFile(self.a,'a') as z:
            z.writestr(self.folder.name+'/App.py','case collision')
        with self.assertRaisesRegex(g.PackagingBlocked, 'Windows'):
            g.verify(self.a)

    def test_symlink_in_source_rejected(self):
        try:
            (self.folder/'shortcut').symlink_to(self.folder/'app.py')
        except (OSError, NotImplementedError):
            self.skipTest('symlink privilege not available')
        with self.assertRaises(g.PackagingBlocked):
            g.package(self.folder,self.a,'acceptance')

    def test_secrets_not_packaged(self):
        (self.folder/'.env').write_text('private')
        with self.assertRaises(g.PackagingBlocked):
            g.package(self.folder,self.a,'acceptance')

    def test_no_missing_required_file(self):
        (self.folder/'app.py').unlink()
        with self.assertRaises(g.PackagingBlocked):
            g.package(self.folder,self.a,'acceptance')

    def test_mode_cannot_be_mislabelled(self):
        with self.assertRaises(g.PackagingBlocked):
            g.package(self.folder,self.base/'AI_SMM_FAKE_RELEASE.zip','acceptance')

    def test_release_requires_evidence(self):
        with self.assertRaises(g.PackagingBlocked):
            g.package(self.folder,self.base/'AI_SMM_RELEASE.zip','release')
        self.assertFalse((self.base/'AI_SMM_RELEASE.zip').exists())

    def test_acceptance_does_not_approve_release(self):
        self.valid()
        with zipfile.ZipFile(self.a) as z:
            manifest=__import__('json').loads(z.read(f'{self.folder.name}/PACKAGE_MANIFEST.json'))
        self.assertEqual(manifest['release_status'],'NOT_RELEASE')

    def test_post_build_tampering_is_rejected(self):
        self.valid()
        with zipfile.ZipFile(self.a,'a') as z:
            z.writestr(f'{self.folder.name}/unauthorized.py','print("hi")')
        with self.assertRaises(g.PackagingBlocked):
            g.verify(self.a)

    def test_extracted_program_tree_integrity(self):
        self.valid()
        target=self.base/'extracted'
        with zipfile.ZipFile(self.a) as z:
            z.extractall(target)
        root=target/self.folder.name
        self.assertEqual(g.verify_tree(root)['status'],'PASS')
        (root/'app.py').write_text('tampered',encoding='utf-8')
        with self.assertRaises(g.PackagingBlocked):
            g.verify_tree(root)

    def test_explicit_directory_is_allowed(self):
        self.valid()
        with zipfile.ZipFile(self.a,'a') as z:
            z.writestr(f'{self.folder.name}/','')
        self.assertEqual(g.verify(self.a)['status'],'PASS')

    def test_identical_content_identical_bytes(self):
        self.valid()
        target=self.base/'SECOND_NEEDS_REAL_TEST.zip'
        g.package(self.folder,target,'acceptance')
        self.assertEqual(self.a.read_bytes(),target.read_bytes())

class WindowsTestBundlePolicyTests(unittest.TestCase):
    def test_windows_candidate_keeps_internal_tests_out_of_install_bundle(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/'AI_SMM_Work_Architecture_GUARD_TEST'
            root.mkdir()
            for name in g.REQUIRED_FILES:
                (root/name).write_bytes(b'@echo off\r\nexit /b 0\r\n' if name.endswith('.cmd') else b'fixture')
            (root/'windows_acceptance.py').write_bytes(b'@echo off\r\nexit /b 0\r\n' if name.endswith('.cmd') else b'fixture')
            (root/'RUN_ARCHITECTURE_CHECK.cmd').write_bytes(b'@echo off\r\necho RUN\r\n')
            target=Path(tmp)/'AI_SMM_Work_Architecture_GUARD_TEST_NOT_RELEASE.zip'
            self.assertEqual(g.package(root,target,'acceptance')['status'],'PASS')
            (root/'_tests_source.zip').write_bytes(b'private tests')
            with self.assertRaisesRegex(g.PackagingBlocked,'внутренние тесты'):
                g.package(root,target,'acceptance')

    def test_windows_runner_uses_external_developer_test_folder(self):
        tests=Path(__file__).resolve().parent
        if (tests/'windows_acceptance.py').is_file():
            root=tests
        else:
            manager=tests.parent.parent
            candidates=[p for p in manager.glob('AI_SMM_Work_Architecture_*')
                        if (p/'windows_acceptance.py').is_file()]
            root=candidates[0] if candidates else tests.parent
        source=(root/'windows_acceptance.py').read_text(encoding='utf-8')
        identity=(root/'candidate_identity.py').read_text(encoding='utf-8')
        expected=re.search(r'EXPECTED_FOLDER\s*=\s*["\']AI_SMM_Work_Architecture_(A\d+)["\']',identity)
        self.assertIsNotNone(expected,identity)
        self.assertIn('AI_SMM_TEST_SOURCE',source)
        version=expected.group(1)
        self.assertIn(f"version='{version}'",source)
        self.assertIn("AI_SMM_Developer_{version}",source)


if __name__=='__main__':
    unittest.main()
