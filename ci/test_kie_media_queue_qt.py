"""Native Qt offscreen checks for Kie project owner fencing and unpaid removal.

Offline only: no provider credentials, HTTP calls or paid generation.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AI_SMM_Work_Architecture_A75"))

from PySide6.QtWidgets import QApplication, QMessageBox
from kie_media_contracts import nano_banana_pro
from kie_media_jobs import prepare, submit
from kie_media_queue_ui import KieMediaQueuePanel


class KieMediaQueueQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(Path(self.temp.name) / "queue.sqlite")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
        self.db.executemany("INSERT INTO projects VALUES (?)", [("project-a",), ("project-b",)])
        self.db.commit()
        self.current = "project-a"
        self.view = KieMediaQueuePanel(self.db, lambda: self.current)
        self.addCleanup(self.view.close)
        self.payload = nano_banana_pro("A safe mock cover", [], aspect_ratio="1:1")

    def test_refresh_shows_only_selected_project_and_is_offline(self):
        a = prepare(self.db, "project-a", self.payload)
        b = prepare(self.db, "project-b", self.payload)
        self.view.refresh()
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 3).text(), a)
        self.assertNotEqual(self.view.table.item(0, 3).text(), b)
        self.current = "project-b"
        self.view.refresh()
        self.assertEqual(self.view.table.item(0, 3).text(), b)

    def test_create_text_image_draft_is_offline_and_project_owned(self):
        self.view.prompt.setPlainText("Design a clean product campaign image")
        self.view.aspect_ratio.setCurrentText("9:16")
        self.view.resolution.setCurrentText("2K")
        self.view.create_draft()
        rows = self.db.execute(
            "SELECT project_id,model,state,payload_json FROM kie_media_tasks"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:3], ("project-a", "nano-banana-pro", "prepared"))
        import json
        payload = json.loads(rows[0][3])
        self.assertEqual(payload["input"]["image_input"], [])
        self.assertEqual(payload["input"]["aspect_ratio"], "9:16")
        self.assertEqual(payload["input"]["resolution"], "2K")
        self.assertEqual(self.view.table.rowCount(), 1)

    def test_invalid_draft_does_not_write_or_launch(self):
        self.view.prompt.setPlainText(" ")
        self.view.create_draft()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='kie_media_tasks'"
        ).fetchone()[0], 0)

    def test_draft_uses_project_selected_at_creation(self):
        self.view.prompt.setPlainText("A visual for project B")
        self.current = "project-b"
        self.view.create_draft()
        self.assertEqual(self.db.execute(
            "SELECT project_id FROM kie_media_tasks"
        ).fetchone()[0], "project-b")

    def test_preview_draft_is_local_and_does_not_expose_provider_urls(self):
        from kie_media_jobs import preview_prepared_task
        payload = nano_banana_pro(
            "Design campaign banner",
            ["https://files.example.org/private?signature=secret-token"],
            aspect_ratio="9:16")
        task = prepare(self.db,"project-a",payload)
        metadata = preview_prepared_task(self.db,"project-a",task)
        self.assertEqual(metadata["reference_count"],1)
        self.assertEqual(metadata["model"],"nano-banana-pro")
        self.assertNotIn("signature",str(metadata))
        self.assertNotIn("secret-token",str(metadata))
        self.view.refresh()
        self.view.table.selectRow(0)
        with patch.object(QMessageBox,"information") as show:
            self.view.preview_selected()
        show.assert_called_once()
        self.assertNotIn("secret-token",str(show.call_args))
        self.assertNotIn("files.example.org",str(show.call_args))

    def test_preview_blocked_if_selected_project_changed(self):
        task=prepare(self.db,"project-a",self.payload)
        self.view.refresh()
        self.view.table.selectRow(0)
        self.current="project-b"
        with patch.object(QMessageBox,"information") as show:
            self.view.preview_selected()
        show.assert_not_called()
        self.assertEqual(self.view.table.rowCount(),0)

    def test_switching_projects_clears_unsubmitted_prompt(self):
        self.view.refresh()
        self.view.prompt.setPlainText("Confidential campaign for project A")
        self.current="project-b"
        self.view.refresh()
        self.assertEqual(self.view.prompt.toPlainText(),"")
        self.assertEqual(self.view.table.rowCount(),0)

    def test_preview_only_unpaid_drafts(self):
        task=prepare(self.db,"project-a",self.payload)
        submit(self.db,"project-a",task,
               lambda _: {"code":200,"data":{"taskId":"task_kie_1234567890"}},
               enable_paid=True,quoted_cost_cents=100)
        self.view.refresh()
        self.view.table.selectRow(0)
        with patch.object(QMessageBox,"warning") as warning:
            self.view.preview_selected()
        warning.assert_called_once()

    def test_monthly_limit_can_change_in_qt_without_paid_requests(self):
        from kie_media_budget import snapshot
        self.view.refresh()
        self.assertEqual(self.view.monthly_limit.value(),20.00)
        self.view.monthly_limit.setValue(35.50)
        with patch.object(QMessageBox,"question",return_value=QMessageBox.Yes):
            self.view.save_budget()
        self.assertEqual(snapshot(self.db)["limit_cents"],3550)
        self.assertIn("35.50",self.view.budget_status.text())

    def test_declined_budget_change_keeps_previous_limit(self):
        from kie_media_budget import snapshot
        self.view.refresh()
        self.view.monthly_limit.setValue(1000.00)
        with patch.object(QMessageBox,"question",return_value=QMessageBox.No):
            self.view.save_budget()
        self.assertEqual(snapshot(self.db)["limit_cents"],2000)
        self.assertEqual(self.view.monthly_limit.value(),20.00)

    def test_smm_route_preview_only_claims_verified_ffmpeg_capabilities(self):
        self.view.smm_task.setCurrentText("video_cut")
        self.view.smm_source_count.setValue(2)
        with patch("kie_media_queue_ui.shutil.which",return_value="/usr/bin/ffmpeg"):
            self.view.preview_route()
        self.assertIn("local-ffmpeg",self.view.route_status.text())
        self.view.smm_task.setCurrentText("image_remove_background")
        with patch("kie_media_queue_ui.shutil.which",return_value="/usr/bin/ffmpeg"):
            self.view.preview_route()
        self.assertIn("Нет подтверждённого исполнителя",self.view.route_status.text())

    def test_smm_route_missing_reference_or_ffmpeg_fails_closed(self):
        self.view.smm_task.setCurrentText("video_style_reference")
        self.view.smm_source_count.setValue(1)
        self.view.smm_reference.setChecked(False)
        with patch("kie_media_queue_ui.shutil.which",return_value=None):
            self.view.preview_route()
        self.assertIn("Маршрут недоступен",self.view.route_status.text())
        self.view.smm_reference.setChecked(True)
        self.view.smm_task.setCurrentText("video_cut")
        with patch("kie_media_queue_ui.shutil.which",return_value=None):
            self.view.preview_route()
        self.assertIn("Нет подтверждённого исполнителя",self.view.route_status.text())

    def test_stale_selection_cannot_delete_foreign_project(self):
        a = prepare(self.db, "project-a", self.payload)
        b = prepare(self.db, "project-b", self.payload)
        self.view.refresh()
        self.view.table.selectRow(0)
        self.current = "project-b"
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.view.discard_selected()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM kie_media_tasks").fetchone()[0], 2)
        self.assertEqual(self.view.table.item(0, 3).text(), b)

    def test_explicit_discard_only_prepared_without_paid_dispatch(self):
        a = prepare(self.db, "project-a", self.payload)
        self.view.refresh()
        self.view.table.selectRow(0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.view.discard_selected()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM kie_media_tasks WHERE id=?", (a,)).fetchone()[0], 0)

    def test_submitted_task_cannot_be_discarded(self):
        a = prepare(self.db, "project-a", self.payload)
        submit(self.db, "project-a", a,
               lambda _: {"code": 200, "data": {"taskId": "task_kie_1234567890"}},
               enable_paid=True,quoted_cost_cents=100)
        self.view.refresh()
        self.view.table.selectRow(0)
        with patch.object(QMessageBox, "warning") as warning:
            self.view.discard_selected()
        warning.assert_called_once()
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?", (a,)).fetchone()[0], "submitted")


if __name__ == "__main__":
    unittest.main()
