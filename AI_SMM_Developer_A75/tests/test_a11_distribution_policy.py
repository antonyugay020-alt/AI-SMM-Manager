import unittest
from pathlib import Path
import re

def candidate_source_root():
    tests=Path(__file__).resolve().parent
    if (tests/'windows_acceptance.py').is_file(): return tests
    manager=tests.parent.parent
    for candidate in manager.glob('AI_SMM_Work_Architecture_*'):
        if (candidate/'windows_acceptance.py').is_file() and (candidate/'candidate_identity.py').is_file():
            return candidate
    return tests.parent

class InstallDistributionPolicyTests(unittest.TestCase):
    def test_windows_harness_uses_external_developer_test_folder(self):
        root=candidate_source_root()
        harness=(root/'windows_acceptance.py').read_text(encoding='utf-8')
        guard=(root/'release_guard.py').read_text(encoding='utf-8')
        identity=(root/'candidate_identity.py').read_text(encoding='utf-8')
        expected=re.search(r'EXPECTED_FOLDER\s*=\s*["\']AI_SMM_Work_Architecture_(A\d+)["\']',identity)
        self.assertIsNotNone(expected,identity)
        self.assertIn('AI_SMM_TEST_SOURCE',harness)
        version=expected.group(1)
        self.assertIn(f"version='{version}'",harness)
        self.assertIn("AI_SMM_Developer_{version}",harness)
        self.assertIn("'unit_tests',None",harness)
        self.assertIn('В установочном кандидате запрещены внутренние тесты',guard)

if __name__ == '__main__': unittest.main()
