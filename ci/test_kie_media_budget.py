"""Legacy Kie ledger tests: preserve old allocations without provider calls."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
from kie_media_budget import snapshot,set_monthly_limit,reserve_in_transaction

class LegacyKieBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=sqlite3.connect(Path(self.temp.name)/"budget.sqlite")
        self.addCleanup(self.db.close)

    def reserve(self,project,job,cents):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            reserve_in_transaction(self.db,project_id=project,task_id=job,
                                   quote_cents=cents)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def test_default_cap_and_mutable_limit(self):
        self.assertEqual(snapshot(self.db)["limit_cents"],2000)
        set_monthly_limit(self.db,3500)
        self.assertEqual(snapshot(self.db)["limit_cents"],3500)
        with self.assertRaises(ValueError):set_monthly_limit(self.db,-1)
        with self.assertRaises(ValueError):set_monthly_limit(self.db,12.5)

    def test_atomic_reservation_rejects_over_limit_and_reuse(self):
        set_monthly_limit(self.db,120)
        self.reserve("one","job1",100)
        with self.assertRaisesRegex(RuntimeError,"budget exceeded"):
            self.reserve("two","job2",50)
        with self.assertRaisesRegex(RuntimeError,"already allocated"):
            self.reserve("one","job1",10)
        self.assertEqual(snapshot(self.db)["allocated_cents"],100)

    def test_reservations_survive_restart_and_reduced_cap(self):
        self.reserve("one","job1",150)
        self.db.close()
        self.db=sqlite3.connect(Path(self.temp.name)/"budget.sqlite")
        # Windows cannot delete an open SQLite file during TemporaryDirectory cleanup.
        self.addCleanup(self.db.close)
        set_monthly_limit(self.db,100)
        state=snapshot(self.db)
        self.assertEqual(state["allocated_cents"],150)
        self.assertEqual(state["remaining_cents"],0)

    def test_reject_invalid_quote_types(self):
        for cents in (0,True,1.2,"100",None,-1):
            with self.subTest(cents=cents):
                with self.assertRaises(ValueError):
                    self.reserve("one","job",cents)
        self.assertEqual(snapshot(self.db)["allocated_cents"],0)

if __name__=="__main__":
    unittest.main()
