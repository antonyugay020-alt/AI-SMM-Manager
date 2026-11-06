import io, json, os, zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import release_guard
from candidate_identity import CANDIDATE_ID, EXPECTED_FOLDER
from evidence_provenance import validate_report

class CandidateIdentityTests(unittest.TestCase):
    def test_old_v5_report_is_rejected(self):
        with TemporaryDirectory() as d:
            bundle=Path(d)/'VALIDATION_RESULTS.zip'
            with zipfile.ZipFile(bundle,'w') as z:
                z.writestr('acceptance.json',json.dumps({'build':'Stage75-R3 Windows acceptance candidate v5 (NOT a release)',
                    'checks':{'unit_tests':{'status':'PASS'}}, 'status':'BLOCKED'}))
            self.assertFalse(validate_report(bundle,'known-fingerprint')['identity_valid'])
    def test_missing_identity_does_not_count_as_current_report(self):
        with TemporaryDirectory() as d:
            bundle=Path(d)/'VALIDATION_RESULTS.zip'
            with zipfile.ZipFile(bundle,'w') as z:
                z.writestr('acceptance.json',json.dumps({'build':CANDIDATE_ID,'status':'BLOCKED',
                    'checks':{'package_integrity':{'status':'PASS','source_fingerprint':'a'}}}))
            self.assertFalse(validate_report(bundle,'a')['identity_valid'])
    def test_current_report_with_bound_fingerprint_is_recognized(self):
        with TemporaryDirectory() as d:
            bundle=Path(d)/'VALIDATION_RESULTS.zip'
            source=str(Path('C:/QA')/EXPECTED_FOLDER/'windows_acceptance.py').replace('/','\\')
            with zipfile.ZipFile(bundle,'w') as z:
                z.writestr('acceptance.json',json.dumps({'build':CANDIDATE_ID,'status':'BLOCKED',
                    'runner_path':source,'attested_source_fingerprint':'a',
                    'checks':{'package_integrity':{'status':'PASS',
                    'candidate_id':CANDIDATE_ID, 'source_fingerprint':'a'}}}))
            self.assertTrue(validate_report(bundle,'a')['identity_valid'])
    def test_extra_python_in_extracted_folder_is_detected(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'AI_SMM_VERIFY_TEST'
            root.mkdir()
            for name in release_guard.REQUIRED_FILES:
                (root/name).write_bytes(b'@echo off\r\nexit /b 0\r\n' if name.endswith('.cmd') else b'fixture')
            archive=Path(d)/'FIXTURE_NEEDS_REAL_TEST.zip'
            release_guard.package(root,archive,'acceptance')
            extract=Path(d)/'unpacked'
            with zipfile.ZipFile(archive) as z: z.extractall(extract)
            dest=extract/root.name
            self.assertEqual(release_guard.verify_tree(dest)['status'],'PASS')
            (dest/'unknown_stale_code.py').write_text('print(42)',encoding='utf-8')
            with self.assertRaises(release_guard.PackagingBlocked):
                release_guard.verify_tree(dest)
    def test_extra_runtime_cache_does_not_invalidate_source(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'AI_SMM_VERIFY_TEST'
            root.mkdir()
            for name in release_guard.REQUIRED_FILES:
                (root/name).write_bytes(b'@echo off\r\nexit /b 0\r\n' if name.endswith('.cmd') else b'fixture')
            archive=Path(d)/'FIXTURE_NEEDS_REAL_TEST.zip'
            release_guard.package(root,archive,'acceptance')
            extract=Path(d)/'unpacked'
            with zipfile.ZipFile(archive) as z: z.extractall(extract)
            dest=extract/root.name
            (dest/'__pycache__').mkdir()
            (dest/'__pycache__'/'old.pyc').write_bytes(b'temporary')
            self.assertEqual(release_guard.verify_tree(dest)['status'],'PASS')

if __name__=='__main__': unittest.main()
