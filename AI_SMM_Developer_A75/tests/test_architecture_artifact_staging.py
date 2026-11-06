"""Crash-contract tests for verified multi-file artifact projection."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

from artifact_staging import StagedArtifactBatch


class ArtifactStaging(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root/'texts').mkdir()
        (self.root/'pdf').mkdir()
        self.text = self.root/'texts'/'output.txt'
        self.pdf = self.root/'pdf'/'output.pdf'

    def _write_batch(self, content=b'new text', pdf=b'%PDF-1.7\nsome contents'):
        with StagedArtifactBatch(self.root,('texts','pdf')) as batch:
            p1=batch.stage(self.text); p1.write_bytes(content)
            p2=batch.stage(self.pdf); p2.write_bytes(pdf)
            batch.publish(lambda path,target: self.assertTrue(path.read_bytes()))

    def test_atomic_replacement_of_each_projection(self):
        self._write_batch()
        self.assertEqual(self.text.read_bytes(),b'new text')
        self.assertTrue(self.pdf.read_bytes().startswith(b'%PDF-'))
        self.assertFalse(list(self.root.rglob('*.stage-*')))

    def test_invalid_staged_artifact_never_publishes(self):
        self.text.write_bytes(b'old')
        with self.assertRaisesRegex(RuntimeError,'bad PDF'):
            with StagedArtifactBatch(self.root,('texts','pdf')) as batch:
                s1=batch.stage(self.text);s1.write_bytes(b'new')
                s2=batch.stage(self.pdf);s2.write_bytes(b'broken')
                def verify(path,target):
                    if target==self.pdf and not path.read_bytes().startswith(b'%PDF-'):
                        raise RuntimeError('bad PDF')
                batch.publish(verify)
        self.assertEqual(self.text.read_bytes(),b'old')
        self.assertFalse(self.pdf.exists())

    def test_partial_atomic_publication_is_replayable(self):
        self.text.write_bytes(b'old')
        self.pdf.write_bytes(b'%PDF-OLD')
        real_replace=os.replace
        calls=[0]
        def flaky(source,target):
            calls[0]+=1
            if calls[0]==2: raise OSError('power loss during second file')
            return real_replace(source,target)
        with patch('artifact_staging.os.replace',side_effect=flaky):
            with self.assertRaisesRegex(OSError,'power loss'):
                self._write_batch()
        self.assertEqual(self.text.read_bytes(),b'new text')
        self.assertEqual(self.pdf.read_bytes(),b'%PDF-OLD')
        # Durable outbox retries entire projection instead of claiming success
        # from the first renamed file.
        self._write_batch()
        self.assertEqual(self.text.read_bytes(),b'new text')
        self.assertTrue(self.pdf.read_bytes().startswith(b'%PDF-1.7'))

    def test_reject_outside_project(self):
        with StagedArtifactBatch(self.root,('texts','pdf')) as batch:
            with self.assertRaises(ValueError):
                batch.stage(self.root.parent/'outside.txt')

    def test_duplicate_target_rejected(self):
        with StagedArtifactBatch(self.root,('texts','pdf')) as batch:
            batch.stage(self.text)
            with self.assertRaises(ValueError):batch.stage(self.text)

    def test_symlink_destination_rejected(self):
        target=self.root/'outside.txt'
        target.write_text('sensitive')
        try:self.text.symlink_to(target)
        except (NotImplementedError,OSError) as exc:self.skipTest(str(exc))
        with StagedArtifactBatch(self.root,('texts','pdf')) as batch:
            with self.assertRaises(ValueError):batch.stage(self.text)
        self.assertEqual(target.read_text(),'sensitive')

if __name__=='__main__': unittest.main()
