"""Final distribution checks include nested source and developer mirror bytes."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import release_guard as guard


class DistributionGuard(unittest.TestCase):
    def prepare(self, base):
        kit=base/'AI_SMM_R65_NOT_RELEASE';source=kit/'AI_SMM_Work_Architecture_A75'
        source.mkdir(parents=True)
        for name in guard.REQUIRED_FILES:(source/name).write_bytes(b'@echo off\r\nexit /b 0\r\n' if name.endswith('.cmd') else b'fixture')
        for name in ('RUN_AI_SMM_MANAGER.cmd','RUN_ARCHITECTURE_CHECK.cmd'):
            (source/name).write_bytes(b'@echo off\r\nexit /b 0\r\n')
        archive=base/'source_NOT_RELEASE.zip'
        guard.package(source,archive,'acceptance')
        with zipfile.ZipFile(archive) as z:z.extractall(kit)
        tests=kit/'AI_SMM_Developer_A75/tests';tests.mkdir(parents=True)
        for p in source.glob('*.py'):(tests/p.name).write_bytes(p.read_bytes())
        (tests/'a2_recovery_fixture.json').write_text('{}')
        (tests/'test_sample.py').write_text('pass')
        return kit

    def test_full_kit_roundtrip_has_one_root_and_guard_generated_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);kit=self.prepare(base);target=base/'KIT_NOT_RELEASE.zip'
            result=guard.package_kit(kit,target)
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(result['profile'],'acceptance-kit')
            with zipfile.ZipFile(target) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual({n.split('/')[0] for n in z.namelist()},{kit.name})
                z.extractall(base/'fresh')
            self.assertEqual(guard.verify_tree(base/'fresh'/kit.name/'AI_SMM_Work_Architecture_A75')['source_fingerprint'],result['source_fingerprint'])

    def test_stale_source_or_test_mirror_blocks_packaging_and_preserves_previous_output(self):
        for kind in ('source','mirror','missing_tests'):
            with tempfile.TemporaryDirectory() as temp:
                base=Path(temp);kit=self.prepare(base);target=base/'KIT_NOT_RELEASE.zip'
                target.write_bytes(b'previous artifact')
                if kind=='source':(kit/'AI_SMM_Work_Architecture_A75/generic_text_review.py').write_text('changed')
                elif kind=='mirror':(kit/'AI_SMM_Developer_A75/tests/generic_text_review.py').write_text('changed')
                else:(kit/'AI_SMM_Developer_A75/tests/test_sample.py').unlink()
                with self.assertRaises(guard.PackagingBlocked):guard.package_kit(kit,target)
                self.assertEqual(target.read_bytes(),b'previous artifact')

    def test_even_rehashed_outer_manifest_cannot_hide_changed_program(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);kit=self.prepare(base);target=base/'KIT_NOT_RELEASE.zip'
            guard.package_kit(kit,target)
            with zipfile.ZipFile(target) as z:files={n:z.read(n) for n in z.namelist()}
            root=kit.name+'/'
            for name in ('AI_SMM_Work_Architecture_A75/generic_text_review.py',
                         'AI_SMM_Developer_A75/tests/generic_text_review.py'):files[root+name]=b'changed'
            manifest=json.loads(files[root+'DISTRIBUTION_MANIFEST.json'])
            for name in manifest['files']:
                data=files[root+name];manifest['files'][name]={'sha256':guard._sha(data),'size':len(data)}
            files[root+'DISTRIBUTION_MANIFEST.json']=json.dumps(manifest).encode()
            with zipfile.ZipFile(target,'w') as z:
                for name,data in files.items():z.writestr(name,data)
            with self.assertRaises(guard.PackagingBlocked):guard.verify(target)

    def test_presupplied_manifest_and_release_labels_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);kit=self.prepare(base)
            with self.assertRaises(guard.PackagingBlocked):guard.package_kit(kit,base/'RELEASE.zip')
            (kit/'DISTRIBUTION_MANIFEST.json').write_text('{}')
            with self.assertRaises(guard.PackagingBlocked):guard.package_kit(kit,base/'KIT_NOT_RELEASE.zip')

    def test_missing_launcher_and_nested_symlink_are_rejected(self):
        for kind in ('launcher','symlink'):
            with tempfile.TemporaryDirectory() as temp:
                base=Path(temp);kit=self.prepare(base)
                if kind=='launcher':(kit/'AI_SMM_Work_Architecture_A75/RUN_AI_SMM_MANAGER.cmd').unlink()
                else:
                    try:(kit/'link').symlink_to(base/'source_NOT_RELEASE.zip')
                    except OSError:self.skipTest('Symlink creation is unavailable')
                with self.assertRaises(guard.PackagingBlocked):guard.package_kit(kit,base/'KIT_NOT_RELEASE.zip')


if __name__=='__main__':unittest.main()
