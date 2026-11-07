"""Offline paid Kie dispatch: project ownership, quote, monthly cap and crash safety."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
from durable_cost_gate import DurableCostGate
from cost_authorization import PaymentDenied
from kie_media_runtime import KieMediaGateway


class PaidKieBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.db=sqlite3.connect(root/"projects.sqlite")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
        self.db.executemany("INSERT INTO projects VALUES (?)",[("alpha",),("beta",)])
        self.db.commit()
        self.ledger=DurableCostGate(root/"budget.sqlite")
        self.remote=Mock(return_value={"code":200,"data":{"taskId":"remote_task_123456789"}})
        self.quote=Mock(return_value="2.00")
        self.gateway=KieMediaGateway(
            self.db,lambda pid:root/pid,upload_file=Mock(),
            create_task=self.remote,query_task=Mock(),
            cost_gate=self.ledger,verified_price_quote=self.quote)

    def draft(self, owner="alpha"):
        return self.gateway.prepare_image(owner,prompt="Generate a local brand poster",
                                          sources=[],pro=True)

    def test_no_explicit_approval_never_calls_paid_provider(self):
        task=self.draft()
        for args in ({},{ "explicit_paid_consent":True},
                     {"explicit_paid_consent":True,"approval_token":"unknown"}):
            with self.assertRaises((RuntimeError,PaymentDenied)):
                self.gateway.start("alpha",task,**args)
        self.remote.assert_not_called()
        self.assertEqual(self.ledger.usage()["committed_usd"],0)

    def test_quote_and_one_use_grant_reserve_before_network(self):
        task=self.draft()
        token=self.ledger.approve("kie.ai","media:"+task,"2")
        self.assertEqual(self.gateway.start("alpha",task,explicit_paid_consent=True,
                                            approval_token=token),"remote_task_123456789")
        self.remote.assert_called_once()
        self.assertEqual(self.ledger.usage()["committed_usd"],2)
        stored=self.db.execute("SELECT cost_reservation_id FROM kie_media_tasks WHERE id=?",
                               (task,)).fetchone()[0]
        self.assertEqual(len(stored),32)
        self.assertEqual(DurableCostGate(self.ledger.database_path).usage()["committed_usd"],2)
        with self.assertRaises(RuntimeError):
            self.gateway.start("alpha",task,explicit_paid_consent=True,approval_token=token)
        self.remote.assert_called_once()

    def test_wrong_project_and_wrong_quote_are_denied(self):
        task=self.draft()
        token=self.ledger.approve("kie.ai","media:"+task,"1")
        with self.assertRaises(ValueError):
            self.gateway.start("beta",task,explicit_paid_consent=True,approval_token=token)
        with self.assertRaises(PaymentDenied):
            self.gateway.start("alpha",task,explicit_paid_consent=True,approval_token=token)
        self.remote.assert_not_called()
        self.assertEqual(self.ledger.usage()["committed_usd"],0)

    def test_monthly_limit_is_checked_before_transport(self):
        self.ledger.set_monthly_cap("1")
        task=self.draft()
        token=self.ledger.approve("kie.ai","media:"+task,"2")
        with self.assertRaises(PaymentDenied):
            self.gateway.start("alpha",task,explicit_paid_consent=True,approval_token=token)
        self.remote.assert_not_called()

    def test_unknown_network_result_keeps_reservation(self):
        task=self.draft()
        self.remote.side_effect=TimeoutError("unknown charge")
        token=self.ledger.approve("kie.ai","media:"+task,"2")
        with self.assertRaises(TimeoutError):
            self.gateway.start("alpha",task,explicit_paid_consent=True,approval_token=token)
        self.assertEqual(self.ledger.usage()["committed_usd"],2)
        self.assertEqual(self.gateway.tasks("alpha")[0]["state"],"submission_unknown")
        self.assertEqual(self.remote.call_count,1)

    def test_foreign_transaction_is_rejected_before_any_budget_reservation(self):
        task=self.draft()
        token=self.ledger.approve("kie.ai","media:"+task,"2")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(RuntimeError):
                self.gateway.start("alpha",task,explicit_paid_consent=True,
                                   approval_token=token)
        finally:
            self.db.rollback()
        self.remote.assert_not_called()
        self.quote.assert_not_called()
        self.assertEqual(self.ledger.usage()["committed_usd"],0)

    def test_legacy_unbound_reservation_cannot_authorize_paid_transport(self):
        task=self.draft()
        self.ledger.set_monthly_cap("20")
        with self.ledger._connect() as db:
            db.execute("""INSERT INTO cost_reservations
                (id,period,amount,state) VALUES (?,?,?,'reserved')""",
                ("legacy",self.ledger.period(),1000000))
        with self.assertRaises(PaymentDenied):
            self.ledger.assert_reservation("legacy","kie.ai","media:"+task)
        self.remote.assert_not_called()

    def test_valid_reservation_survives_reopen_but_not_settlement(self):
        task=self.draft()
        operation="media:"+task
        token=self.ledger.approve("kie.ai",operation,"2")
        reservation=self.ledger.reserve("kie.ai",operation,"2",token=token)
        reopened=DurableCostGate(self.ledger.database_path)
        self.assertTrue(reopened.assert_reservation(reservation,"kie.ai",operation))
        with self.assertRaises(PaymentDenied):
            reopened.assert_reservation(reservation,"kie.ai","media:another")
        reopened.settle(reservation,"1")
        with self.assertRaises(PaymentDenied):
            reopened.assert_reservation(reservation,"kie.ai",operation)

    def test_lowered_monthly_limit_blocks_already_reserved_dispatch(self):
        task=self.draft()
        operation="media:"+task
        token=self.ledger.approve("kie.ai",operation,"2")
        reservation=self.ledger.reserve("kie.ai",operation,"2",token=token)
        self.ledger.set_monthly_cap("1")
        with self.assertRaises(PaymentDenied):
            self.ledger.assert_reservation(reservation,"kie.ai",operation)
        self.remote.assert_not_called()
        self.assertEqual(self.ledger.usage()["pending_usd"],2)

    def test_missing_verified_quote_blocks_even_with_user_consent(self):
        task=self.draft()
        unpriced=KieMediaGateway(
            self.db,lambda pid:Path(self.temp.name)/pid,upload_file=Mock(),
            create_task=self.remote,query_task=Mock(),cost_gate=self.ledger)
        token=self.ledger.approve("kie.ai","media:"+task,"2")
        with self.assertRaises(RuntimeError):
            unpriced.start("alpha",task,explicit_paid_consent=True,approval_token=token)
        self.remote.assert_not_called()


if __name__=="__main__":
    unittest.main()
