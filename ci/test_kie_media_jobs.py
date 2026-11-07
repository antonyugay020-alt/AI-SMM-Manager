"""Kie media lifecycle tests: durable paid submission, project isolation, recovery."""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
from PIL import Image
from kie_media_client import KieMediaClient
from kie_media_contracts import nano_banana_pro,kling_omni_reference_to_video
from kie_media_jobs import prepare,submit,complete,recover_pending,list_project_tasks,abandon_prepared
from durable_cost_gate import DurableCostGate
from cost_authorization import PaymentDenied

TASK="task_kie_1234567890"
URL="https://output.example.com/artifact.png"


class KieMediaDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(self.root/"state.sqlite")
        self.addCleanup(self.db.close)
        self.a="a"*32
        self.b="b"*32
        self.db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
        self.db.execute("""CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,
            original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)""")
        self.db.execute("""CREATE TABLE project_files(id TEXT PRIMARY KEY,
            project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)""")
        self.db.executemany("INSERT INTO projects VALUES(?)",[(self.a,),(self.b,)])
        self.db.commit()
        self.payload=nano_banana_pro("Make advertising cover",
                  ["https://example.org/product.png"],aspect_ratio="9:16")

    def workspace(self,project):
        return self.root/"projects"/project

    def paid_submit(self, project, job_id, transport, *, enable_paid=False):
        """Offline provider simulator still needs the real paid ledger fence."""
        if enable_paid is not True:
            return submit(self.db,project,job_id,transport,enable_paid=enable_paid)
        from kie_media_jobs import _owned_row
        row=_owned_row(self.db,project,job_id)
        if row[3]!="prepared":
            return submit(self.db,project,job_id,transport,enable_paid=True,
                          cost_gate=DurableCostGate(self.root/"paid-ledger.sqlite"))
        gate=DurableCostGate(self.root/"paid-ledger.sqlite")
        operation="media:"+job_id
        token=gate.approve("kie.ai",operation,"1")
        reservation=gate.reserve("kie.ai",operation,"1",token=token)
        return submit(self.db,project,job_id,transport,enable_paid=True,
                      payment_reservation=reservation,cost_gate=gate)

    def test_direct_submit_cannot_bypass_durable_payment_gate(self):
        job=prepare(self.db,self.a,self.payload)
        transport=Mock(return_value={"code":200,"data":{"taskId":TASK}})
        with self.assertRaises(RuntimeError):
            submit(self.db,self.a,job,transport,enable_paid=True)
        transport.assert_not_called()
        self.assertEqual(list_project_tasks(self.db,self.a)[0]["state"],"prepared")

    def test_paid_reservation_cannot_be_used_for_other_job_or_after_settlement(self):
        first=prepare(self.db,self.a,self.payload)
        second=prepare(self.db,self.a,self.payload)
        gate=DurableCostGate(self.root/"bound-ledger.sqlite")
        token=gate.approve("kie.ai","media:"+first,"1")
        reservation=gate.reserve("kie.ai","media:"+first,"1",token=token)
        transport=Mock()
        with self.assertRaises(PaymentDenied):
            submit(self.db,self.a,second,transport,enable_paid=True,
                   payment_reservation=reservation,cost_gate=gate)
        transport.assert_not_called()
        gate.settle(reservation,"0.5")
        with self.assertRaises(PaymentDenied):
            submit(self.db,self.a,first,transport,enable_paid=True,
                   payment_reservation=reservation,cost_gate=gate)
        transport.assert_not_called()
        self.assertEqual(list_project_tasks(self.db,self.a)[0]["state"],"prepared")

    def test_paid_dispatch_is_opt_in_and_exactly_once(self):
        ident=prepare(self.db,self.a,self.payload)
        create=Mock(return_value={"code":200,"data":{"taskId":TASK}})
        with self.assertRaises(RuntimeError):
            self.paid_submit(self.a,ident,create)
        create.assert_not_called()
        self.assertEqual(self.paid_submit(self.a,ident,create,enable_paid=True),TASK)
        create.assert_called_once_with(self.payload)
        with self.assertRaises(RuntimeError):
            self.paid_submit(self.a,ident,create,enable_paid=True)
        create.assert_called_once()
        self.assertEqual(recover_pending(self.db,self.a),[(ident,TASK)])
        self.assertEqual(recover_pending(self.db,self.b),[])

    def test_non_boolean_paid_consent_is_rejected_without_transport(self):
        ident=prepare(self.db,self.a,self.payload)
        create=Mock()
        for invalid in (1, "true", [True]):
            with self.subTest(value=invalid):
                with self.assertRaises(RuntimeError):
                    self.paid_submit(self.a,ident,create,enable_paid=invalid)
        create.assert_not_called()
        self.assertEqual(list_project_tasks(self.db,self.a)[0]["state"],"prepared")

    def test_task_listing_is_owner_scoped_and_unpaid_draft_discard_only(self):
        own=prepare(self.db,self.a,self.payload)
        other=prepare(self.db,self.b,self.payload)
        self.assertEqual([x["id"] for x in list_project_tasks(self.db,self.a)], [own])
        self.assertEqual([x["id"] for x in list_project_tasks(self.db,self.b)], [other])
        with self.assertRaises(ValueError):
            abandon_prepared(self.db,self.b,own)
        abandon_prepared(self.db,self.a,own)
        self.assertEqual(list_project_tasks(self.db,self.a),[])
        self.assertEqual(len(list_project_tasks(self.db,self.b)),1)

    def test_paid_task_cannot_be_discarded_after_successful_dispatch(self):
        ident=prepare(self.db,self.a,self.payload)
        self.paid_submit(self.a,ident,
               lambda _:{"code":200,"data":{"taskId":TASK}},enable_paid=True)
        with self.assertRaises(RuntimeError):
            abandon_prepared(self.db,self.a,ident)
        self.assertEqual(list_project_tasks(self.db,self.a)[0]["state"],"submitted")

    def test_unknown_submission_cannot_be_deleted_or_replayed(self):
        ident=prepare(self.db,self.a,self.payload)
        with self.assertRaises(TimeoutError):
            self.paid_submit(self.a,ident,
                   lambda _:(_ for _ in ()).throw(TimeoutError("uncertain")),
                   enable_paid=True)
        with self.assertRaises(RuntimeError):
            abandon_prepared(self.db,self.a,ident)
        self.assertEqual(recover_pending(self.db,self.a),[])
        self.assertEqual(list_project_tasks(self.db,self.a)[0]["state"],
                         "submission_unknown")

    def test_empty_fresh_task_database_can_recover_without_table(self):
        self.assertEqual(recover_pending(self.db,self.a),[])
        self.assertEqual(list_project_tasks(self.db,self.a),[])

    def test_uncertain_response_is_never_automatically_billed_twice(self):
        ident=prepare(self.db,self.a,self.payload)
        create=Mock(side_effect=TimeoutError("remote may have accepted task"))
        with self.assertRaises(TimeoutError):
            self.paid_submit(self.a,ident,create,enable_paid=True)
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0],
            "submission_unknown")
        self.assertEqual(recover_pending(self.db,self.a),[])
        with self.assertRaises(RuntimeError):
            self.paid_submit(self.a,ident,create,enable_paid=True)
        create.assert_called_once()

    def test_project_ownership_is_immutable_after_selection_changes(self):
        ident=prepare(self.db,self.a,self.payload)
        with self.assertRaises(ValueError):
            self.paid_submit(self.b,ident,Mock(),enable_paid=True)
        self.assertEqual(self.db.execute(
            "SELECT project_id FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0],self.a)

    def test_result_requires_matching_task_and_model(self):
        ident=prepare(self.db,self.a,self.payload)
        self.paid_submit(self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
               enable_paid=True)
        bad=[
            {"code":200,"data":{"taskId":"different_task_12345","model":"nano-banana-pro","state":"success"}},
            {"code":200,"data":{"taskId":TASK,"model":"other-model","state":"success"}},
            {"code":402,"data":{"taskId":TASK,"model":"nano-banana-pro","state":"success"}},
        ]
        for result in bad:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    complete(self.db,self.a,ident,lambda _:result,
                             Mock(),self.workspace)
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0],"submitted")

    def test_completed_real_png_is_published_to_original_project(self):
        ident=prepare(self.db,self.a,self.payload)
        self.paid_submit(self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
               enable_paid=True)
        status={"code":200,"data":{"taskId":TASK,"model":"nano-banana-pro",
                                  "state":"success",
                                  "resultJson":json.dumps({"resultUrls":[URL]})}}
        def save_image(url,path):
            self.assertEqual(url,URL)
            with Image.new("RGB",(128,96),color=(51,102,153)) as pic:
                pic.save(path,"PNG")
        published=complete(self.db,self.a,ident,lambda _:status,
                           save_image,self.workspace)
        self.assertTrue(Path(published).is_file())
        self.assertEqual(self.db.execute("SELECT project_id,kind FROM project_files")
                         .fetchone(),(self.a,"result"))
        self.assertEqual(complete(self.db,self.a,ident,Mock(),Mock(),self.workspace),
                         published)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM project_files").fetchone()[0],1)

    def test_completed_valid_image_tampering_fails_sha256_receipt(self):
        ident=prepare(self.db,self.a,self.payload)
        self.paid_submit(self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
               enable_paid=True)
        status={"code":200,"data":{"taskId":TASK,"model":"nano-banana-pro",
                                  "state":"success","resultJson":json.dumps({"resultUrls":[URL]})}}
        def image(_,dest):
            with Image.new("RGB",(64,64),color=(20,40,60)) as picture:
                picture.save(dest,"PNG")
        path=complete(self.db,self.a,ident,lambda _:status,image,self.workspace)
        before=self.db.execute("SELECT result_sha256 FROM kie_media_tasks WHERE id=?",
                               (ident,)).fetchone()[0]
        self.assertEqual(len(before),64)
        with Image.new("RGB",(64,64),color=(70,90,110)) as picture:
            picture.save(path,"PNG")
        with self.assertRaisesRegex(ValueError,"fingerprint mismatch"):
            complete(self.db,self.a,ident,Mock(),Mock(),self.workspace)

    def test_legacy_task_database_migrates_result_receipt_schema(self):
        from kie_media_jobs import install
        self.db.execute("""CREATE TABLE kie_media_tasks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            model TEXT NOT NULL, payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL, state TEXT NOT NULL,
            provider_task_id TEXT UNIQUE, output_path TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            error_code TEXT)""")
        self.db.commit()
        install(self.db)
        self.assertIn("result_sha256",{row[1] for row in
                          self.db.execute("PRAGMA table_info(kie_media_tasks)")})
        self.db.commit()
        ident=prepare(self.db,self.a,self.payload)
        self.assertEqual(self.db.execute("SELECT state FROM kie_media_tasks WHERE id=?",
                                         (ident,)).fetchone()[0],"prepared")

    def test_recovered_download_must_pass_media_integrity_again(self):
        ident=prepare(self.db,self.a,self.payload)
        self.paid_submit(self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
               enable_paid=True)
        status={"code":200,"data":{"taskId":TASK,"model":"nano-banana-pro",
            "state":"success","resultJson":json.dumps({"resultUrls":[URL]})}}
        def save_image(_,path):
            with Image.new("RGB",(64,64)) as im:im.save(path,"PNG")
        completed=complete(self.db,self.a,ident,lambda _:status,
                           save_image,self.workspace)
        self.db.execute("""UPDATE kie_media_tasks SET state='submitted',output_path=NULL
            WHERE id=?""",(ident,))
        self.db.commit()
        Path(completed).write_bytes(b"tampered media bytes")
        with self.assertRaises(Exception):
            complete(self.db,self.a,ident,lambda _:status,Mock(),self.workspace)
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0],
            "submitted")

    def test_video_ingest_migrates_old_result_only_schema(self):
        from media_ingest_core import ingest
        self.db.execute("""CREATE TABLE media_ingest_jobs (
            id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL
            CHECK(kind IN ('asset','reference','result')),
            source_sha256 TEXT NOT NULL,source_size INTEGER NOT NULL,
            filename TEXT NOT NULL,state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)""")
        self.db.commit()
        video=self.root/"synthetic.mp4"
        video.write_bytes(b"media ingestion transport test")
        receipt=ingest(self.db,self.workspace,video,project_id=self.a,kind="video")
        self.assertTrue(receipt.published)
        self.assertEqual(self.db.execute(
            "SELECT kind FROM project_files WHERE id=?",(receipt.item_id,)).fetchone()[0],
            "video")
        table=self.db.execute("SELECT sql FROM sqlite_master WHERE name='media_ingest_jobs'").fetchone()[0]
        self.assertIn("'video'",table)

    def test_missing_completion_does_not_publish(self):
        ident=prepare(self.db,self.a,self.payload)
        self.paid_submit(self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
               enable_paid=True)
        status={"code":200,"data":{"taskId":TASK,"model":"nano-banana-pro",
                                  "state":"processing"}}
        self.assertIsNone(complete(self.db,self.a,ident,lambda _:status,
                                    Mock(),self.workspace))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM project_files").fetchone()[0],0)

    def test_native_authenticated_transport_does_not_log_secret(self):
        sent=[]
        class Response(io.BytesIO):
            pass
        def opener(req,timeout):
            sent.append(req)
            return Response(json.dumps({"code":200,"data":{"taskId":TASK}}).encode())
        client=KieMediaClient(api_key="test-secret",enabled=True,opener=opener)
        self.assertEqual(client.create_task(self.payload)["data"]["taskId"],TASK)
        req=sent[0]
        self.assertEqual(req.full_url,"https://api.kie.ai/api/v1/jobs/createTask")
        self.assertEqual(req.get_header("Authorization"),"Bearer test-secret")
        self.assertNotIn("test-secret",req.data.decode())

    def test_reference_model_is_accepted_by_common_queue(self):
        video=kling_omni_reference_to_video("Animate this",[
            "https://example.org/photo.jpg"],duration=5)
        ident=prepare(self.db,self.a,video)
        model=self.db.execute(
            "SELECT model FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0]
        self.assertEqual(model,"kling-3.0-omni/reference-to-video")


if __name__=="__main__":
    unittest.main()
