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
from kie_media_jobs import prepare,submit,complete,recover_pending

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

    def test_paid_dispatch_is_opt_in_and_exactly_once(self):
        ident=prepare(self.db,self.a,self.payload)
        create=Mock(return_value={"code":200,"data":{"taskId":TASK}})
        with self.assertRaises(RuntimeError):
            submit(self.db,self.a,ident,create)
        create.assert_not_called()
        self.assertEqual(submit(self.db,self.a,ident,create,enable_paid=True),TASK)
        create.assert_called_once_with(self.payload)
        with self.assertRaises(RuntimeError):
            submit(self.db,self.a,ident,create,enable_paid=True)
        create.assert_called_once()
        self.assertEqual(recover_pending(self.db,self.a),[(ident,TASK)])
        self.assertEqual(recover_pending(self.db,self.b),[])

    def test_uncertain_response_is_never_automatically_billed_twice(self):
        ident=prepare(self.db,self.a,self.payload)
        create=Mock(side_effect=TimeoutError("remote may have accepted task"))
        with self.assertRaises(TimeoutError):
            submit(self.db,self.a,ident,create,enable_paid=True)
        self.assertEqual(self.db.execute(
            "SELECT state FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0],
            "submission_unknown")
        self.assertEqual(recover_pending(self.db,self.a),[])
        with self.assertRaises(RuntimeError):
            submit(self.db,self.a,ident,create,enable_paid=True)
        create.assert_called_once()

    def test_project_ownership_is_immutable_after_selection_changes(self):
        ident=prepare(self.db,self.a,self.payload)
        with self.assertRaises(ValueError):
            submit(self.db,self.b,ident,Mock(),enable_paid=True)
        self.assertEqual(self.db.execute(
            "SELECT project_id FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0],self.a)

    def test_result_requires_matching_task_and_model(self):
        ident=prepare(self.db,self.a,self.payload)
        submit(self.db,self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
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
        submit(self.db,self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
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

    def test_missing_completion_does_not_publish(self):
        ident=prepare(self.db,self.a,self.payload)
        submit(self.db,self.a,ident,lambda _:{"code":200,"data":{"taskId":TASK}},
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
