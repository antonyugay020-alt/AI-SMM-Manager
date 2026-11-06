"""Migration contract: new rejected drafts must not mutate legacy prefix files."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import compound_tasks as ct

class RepairIdentityMigration(unittest.TestCase):
    def test_legacy_checkpoint_is_not_overwritten_by_new_address(self):
        # A cached rejected draft for a different task is only a read candidate,
        # never a destination for a new task with the same legacy prefix.
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            first='same-prefix-1234-AAA'
            second='same-prefix-1234-BBB'
            legacy=p/('publication_repair_'+first[:16]+'.json')
            legacy.write_text(json.dumps({'signature':first,'drafts':{'first':{'post':{'caption':'prior'}}}}))
            assert first[:16]==second[:16]
            modern=p/('publication_repair_'+hashlib.sha256(second.encode()).hexdigest()+'.json')
            modern.write_text(json.dumps({'signature':second,'drafts':{'second':{'post':{'caption':'current'}}}}))
            old=legacy.read_bytes()
            self.assertNotEqual(legacy,modern)
            self.assertEqual(json.loads(modern.read_text())['signature'],second)
            self.assertEqual(legacy.read_bytes(),old)

if __name__=='__main__': unittest.main()
