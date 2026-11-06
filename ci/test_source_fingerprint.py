"""Cross-platform source identity and strict local byte-integrity contract.

These are offline tests; they never call external model providers.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import verify_tree


class PortableFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prod = self.root / "AI_SMM_Work_Architecture_A75"
        self.dev = self.root / "AI_SMM_Developer_A75"
        self.prod.mkdir()
        self.dev.mkdir()
        self.patch = mock.patch.multiple(
            verify_tree, ROOT=self.root, PROD=self.prod, DEV=self.dev
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.write("AI_SMM_10_FULL_WINDOWS_QA.py", b"print('ok')\n")
        self.write("RUN_10_FULL_WINDOWS_QA.cmd", b"@echo off\r\n")

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_mixed_case_paths_have_explicit_posix_string_order(self):
        self.write("AI_SMM_Work_Architecture_A75/a.py", b"a=1\n")
        self.write("AI_SMM_Work_Architecture_A75/Z.py", b"z=1\n")
        self.write("AI_SMM_Developer_A75/a.py", b"a=1\n")
        self.write("AI_SMM_Developer_A75/Z.py", b"z=1\n")
        fingerprint, hashes = verify_tree.files_snapshot()
        self.assertEqual(list(hashes), sorted(hashes))
        expected = hashlib.sha256()
        for relative in sorted(hashes):
            data = (self.root / relative).read_bytes().replace(b"\r\n", b"\n")
            content_hash = hashlib.sha256(data).hexdigest()
            expected.update(
                relative.encode("utf-8") + b"\0" +
                content_hash.encode("ascii") + b"\n"
            )
        self.assertEqual(fingerprint, expected.hexdigest())

    def test_checkout_newlines_are_portable_but_raw_mutation_is_detected(self):
        relative = "AI_SMM_Work_Architecture_A75/example.py"
        self.write(relative, b"line=1\nline=2\n")
        portable_lf, _ = verify_tree.files_snapshot()
        raw_lf, _ = verify_tree.files_snapshot(normalize_line_endings=False)
        self.write(relative, b"line=1\r\nline=2\r\n")
        portable_crlf, _ = verify_tree.files_snapshot()
        raw_crlf, _ = verify_tree.files_snapshot(normalize_line_endings=False)
        self.assertEqual(portable_lf, portable_crlf)
        self.assertNotEqual(raw_lf, raw_crlf)
        self.write(relative, b"line=1\r\nline=3\r\n")
        changed, _ = verify_tree.files_snapshot()
        self.assertNotEqual(changed, portable_lf)


if __name__ == "__main__":
    unittest.main()
