import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from windows_acceptance import find_developer_test_source

class ExternalTestLoaderA12(unittest.TestCase):
    def test_adjacent_developer_zip_supplies_private_suite_and_recovery_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'AI_SMM_Work_Architecture_A17'
            base.mkdir()
            inner=io.BytesIO()
            with zipfile.ZipFile(inner,'w') as z:
                z.writestr('tests/test_smoke.py','import unittest\nclass T(unittest.TestCase): pass\n')
                z.writestr('tests/a2_recovery_fixture.json','[]')
            outer_path=base.parent/'AI_SMM_Developer_A17_NOT_RELEASE.zip'
            with zipfile.ZipFile(outer_path,'w') as outer:
                outer.writestr('AI_SMM_Developer_A17/_tests_source.zip',inner.getvalue())
            folder,temporary,origin=find_developer_test_source(base,version='A17')
            try:
                self.assertEqual(origin,'developer_zip')
                self.assertTrue((folder/'test_smoke.py').is_file())
                self.assertTrue((folder/'a2_recovery_fixture.json').is_file())
            finally:
                if temporary: temporary.cleanup()

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'AI_SMM_Work_Architecture_A17';base.mkdir()
            inner=io.BytesIO()
            with zipfile.ZipFile(inner,'w') as z:
                z.writestr('tests/../../escape.py','x=1')
                z.writestr('tests/a2_recovery_fixture.json','[]')
            with zipfile.ZipFile(base.parent/'AI_SMM_Developer_A17.zip','w') as outer:
                outer.writestr('AI_SMM_Developer_A17/_tests_source.zip',inner.getvalue())
            folder,temp,origin=find_developer_test_source(base,version='A17')
            self.assertIsNone(folder)
            self.assertIsNone(temp)
            self.assertEqual(origin,'missing')

if __name__=='__main__': unittest.main()
