"""Offline integration of the real Qt composition ledger and Kie task recovery."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AI_SMM_Work_Architecture_A75"))
from application_paid_media import PaidMediaComposition
from durable_cost_gate import DurableCostGate


class PaidMediaCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(self.root / "studio.sqlite3")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
        self.db.executemany("INSERT INTO projects VALUES (?)", [("alpha",), ("beta",)])
        self.db.commit()
        self.ledger_path = self.root / "paid_cost_ledger.sqlite3"
        self.composition = PaidMediaComposition(
            self.db, lambda pid: self.root / pid, self.ledger_path)
        self.remote = Mock(return_value={
            "code": 200, "data": {"taskId": "task_kie_1234567890"}})
        self.gateway = self.composition.gateway(
            upload_file=Mock(), create_task=self.remote,
            query_task=Mock(), verified_price_quote=lambda *_: "1.25")

    def draft(self, project="alpha"):
        return self.gateway.prepare_image(
            project, prompt="A campaign image for the selected project", sources=[])

    def test_gui_and_provider_gateway_share_exact_durable_gate(self):
        self.assertIs(self.gateway.cost_gate, self.composition.cost_gate)
        self.composition.cost_gate.set_monthly_cap("13.50")
        restarted = PaidMediaComposition(
            self.db, lambda pid: self.root / pid, self.ledger_path)
        self.assertEqual(restarted.cost_gate.usage()["cap_usd"], 13.5)
        self.assertEqual(self.composition.cost_gate.usage()["cap_usd"], 13.5)

    def test_no_user_approval_means_no_paid_provider_call(self):
        task = self.draft()
        with self.assertRaises(RuntimeError):
            self.gateway.start("alpha", task)
        self.remote.assert_not_called()
        token = self.composition.cost_gate.approve("kie.ai", "media:" + task, "1.25")
        self.assertEqual(
            self.gateway.start("alpha", task, explicit_paid_consent=True,
                               approval_token=token), "task_kie_1234567890")
        self.remote.assert_called_once()
        self.assertEqual(
            DurableCostGate(self.ledger_path).usage()["pending_usd"], 1.25)

    def test_interrupted_paid_send_is_fenced_per_project_without_retry(self):
        a = self.draft("alpha")
        b = self.draft("beta")
        self.db.execute("UPDATE kie_media_tasks SET state='submitting' WHERE id=?", (a,))
        self.db.commit()
        self.assertEqual(self.composition.recover_project("alpha"), [])
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?", (a,)).fetchone()[0],
            "submission_unknown")
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?", (b,)).fetchone()[0],
            "prepared")
        self.remote.assert_not_called()

    def test_unverified_provider_adapters_and_unstable_ledger_are_rejected(self):
        with self.assertRaises(TypeError):
            self.composition.gateway(upload_file=None, create_task=self.remote,
                                     query_task=Mock(), verified_price_quote=lambda *_: "1")
        with self.assertRaises(TypeError):
            self.composition.gateway(upload_file=Mock(), create_task=self.remote,
                                     query_task=Mock(), verified_price_quote=None)
        with self.assertRaises(ValueError):
            PaidMediaComposition(self.db, lambda pid: pid, Path("relative.sqlite3"))
        self.remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
