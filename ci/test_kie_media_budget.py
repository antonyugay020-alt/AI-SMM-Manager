"""Offline monthly USD budget and paid submission fencing tests."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
from kie_media_budget import snapshot,set_monthly_limit
from kie_media_jobs import prepare,submit
from kie_media_contracts import nano_banana_pro


class KieMonthlyBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=sqlite3.connect(Path(self.temp.name)/"budget.sqlite")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
        self.db.executemany("INSERT INTO projects VALUES (?)",[("one",),("two",)])
        self.db.commit()
        self.payload=nano_banana_pro("Product cover",[])
        self.response={"code":200,"data":{"taskId":"remote_task_1234567890"}}

    def task(self,owner="one"):
        return prepare(self.db,owner,self.payload)

    def test_default_budget_twenty_dollars_and_mutable_limit(self):
        self.assertEqual(snapshot(self.db)["limit_cents"],2000)
        set_monthly_limit(self.db,3500)
        self.assertEqual(snapshot(self.db)["limit_cents"],3500)
        with self.assertRaises(ValueError):
            set_monthly_limit(self.db,-1)
        with self.assertRaises(ValueError):
            set_monthly_limit(self.db,12.5)

    def test_missing_quote_is_rejected_before_paid_network(self):
        task=self.task()
        transport=Mock()
        with self.assertRaises(ValueError):
            submit(self.db,"one",task,transport,enable_paid=True)
        transport.assert_not_called()
        self.assertEqual(snapshot(self.db)["allocated_cents"],0)
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?",(task,)).fetchone()[0],
            "prepared")

    def test_limit_blocks_dispatch_and_preserves_prepared_job(self):
        set_monthly_limit(self.db,120)
        first=self.task()
        submit(self.db,"one",first,lambda _:self.response,
               enable_paid=True,quoted_cost_cents=100)
        second=self.task("two")
        transport=Mock()
        with self.assertRaisesRegex(RuntimeError,"budget exceeded"):
            submit(self.db,"two",second,transport,
                   enable_paid=True,quoted_cost_cents=50)
        transport.assert_not_called()
        self.assertEqual(snapshot(self.db)["allocated_cents"],100)
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?",(second,)).fetchone()[0],
            "prepared")

    def test_timeout_keeps_allocation_and_prevents_repeat(self):
        task=self.task()
        transport=Mock(side_effect=TimeoutError("provider accepted?"))
        with self.assertRaises(TimeoutError):
            submit(self.db,"one",task,transport,
                   enable_paid=True,quoted_cost_cents=125)
        self.assertEqual(snapshot(self.db)["allocated_cents"],125)
        with self.assertRaises(RuntimeError):
            submit(self.db,"one",task,transport,
                   enable_paid=True,quoted_cost_cents=125)
        transport.assert_called_once()
        self.db.close()
        self.db=sqlite3.connect(Path(self.temp.name)/"budget.sqlite")
        self.assertEqual(snapshot(self.db)["allocated_cents"],125)

    def test_reduced_limit_does_not_erase_existing_allocations(self):
        task=self.task()
        submit(self.db,"one",task,lambda _:self.response,
               enable_paid=True,quoted_cost_cents=150)
        set_monthly_limit(self.db,100)
        data=snapshot(self.db)
        self.assertEqual(data["allocated_cents"],150)
        self.assertEqual(data["remaining_cents"],0)

    def test_only_exact_integer_cents_accepted(self):
        task=self.task()
        transport=Mock()
        for amount in (0,True,1.2,"100",None,-1):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    submit(self.db,"one",task,transport,
                           enable_paid=True,quoted_cost_cents=amount)
        transport.assert_not_called()

if __name__=="__main__":
    unittest.main()
