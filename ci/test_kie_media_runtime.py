"""Offline integration of project-owned media -> Kie payload -> durable task."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
from kie_media_contracts import VideoMetadata
from kie_media_runtime import KieMediaGateway

class ProjectKieMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.p1="a"*32
        self.p2="b"*32
        self.db=sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
        self.db.executemany("INSERT INTO projects VALUES(?)",[(self.p1,),(self.p2,)])
        self.db.execute("""CREATE TABLE assets(
            id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,
            stored_path TEXT,sha256 TEXT,imported_at TEXT)""")
        self.db.execute("""CREATE TABLE project_files(
            id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,
            display_name TEXT,path TEXT,created_at TEXT)""")
        self.db.commit()
        self.one=self.root/self.p1/"originals"/"photo.png"
        self.one.parent.mkdir(parents=True)
        with Image.new("RGB",(768,1024)) as im:
            im.save(self.one)
        self.db.execute("INSERT INTO assets VALUES(?,?,?,?,?,?)",
                        ("c"*32,self.p1,"photo",str(self.one),"x","now"))
        self.db.commit()
        self.upload=Mock(return_value="https://files.example.org/verified.png")
        self.post=Mock(return_value={"code":200,"data":{"taskId":"task_kie_123456789"}})
        self.gateway=KieMediaGateway(self.db,lambda pid:self.root/pid,
                       upload_file=self.upload,create_task=self.post,
                       query_task=Mock(),fetch_verified_file=Mock())

    def test_pro_image_upload_and_paid_fence(self):
        ident=self.gateway.prepare_image(self.p1,prompt="Product promotion",
                                         sources=[self.one],pro=True)
        self.upload.assert_called_once_with(self.one)
        stored=json.loads(self.db.execute("SELECT payload_json FROM kie_media_tasks WHERE id=?",
                                          (ident,)).fetchone()[0])
        self.assertEqual(stored["model"],"nano-banana-pro")
        self.assertEqual(stored["input"]["image_input"],
                         ["https://files.example.org/verified.png"])
        with self.assertRaises(RuntimeError):
            self.gateway.start(self.p1,ident)
        self.post.assert_not_called()
        self.assertEqual(self.gateway.start(self.p1,ident,explicit_paid_consent=True),
                         "task_kie_123456789")
        self.post.assert_called_once()
        self.assertEqual(self.gateway.recover(self.p1),
                         [(ident,"task_kie_123456789")])
        self.assertEqual(self.gateway.recover(self.p2),[])

    def test_pro_supports_text_to_image_without_source_upload(self):
        ident=self.gateway.prepare_image(self.p1,prompt="Create a brand poster",
                                         sources=[],pro=True,resolution="2K")
        self.upload.assert_not_called()
        saved=json.loads(self.db.execute(
            "SELECT payload_json FROM kie_media_tasks WHERE id=?",(ident,)).fetchone()[0])
        self.assertEqual(saved["model"],"nano-banana-pro")
        self.assertEqual(saved["input"]["image_input"],[])
        self.assertEqual(saved["input"]["resolution"],"2K")
        with self.assertRaises(ValueError):
            self.gateway.prepare_image(self.p1,prompt="Edit",sources=[],pro=False)

    def test_gateway_status_and_unpaid_draft_are_project_scoped(self):
        ident=self.gateway.prepare_image(self.p1,prompt="Campaign cover",
                                         sources=[self.one],pro=True)
        own=self.gateway.tasks(self.p1)
        self.assertEqual(len(own),1)
        self.assertEqual(own[0]["id"],ident)
        self.assertEqual(own[0]["state"],"prepared")
        self.assertEqual(self.gateway.tasks(self.p2),[])
        with self.assertRaises(ValueError):
            self.gateway.discard_unpaid(self.p2,ident)
        self.gateway.discard_unpaid(self.p1,ident)
        self.assertEqual(self.gateway.tasks(self.p1),[])

    def test_edit_model_uses_image_urls(self):
        ident=self.gateway.prepare_image(self.p1,prompt="Fix the background",
                                         sources=[self.one],pro=False)
        stored=json.loads(self.db.execute("SELECT payload_json FROM kie_media_tasks WHERE id=?",
                                          (ident,)).fetchone()[0])
        self.assertEqual(stored["model"],"google/nano-banana-edit")
        self.assertEqual(stored["input"]["image_urls"],
                         ["https://files.example.org/verified.png"])

    def test_kling_reference_to_video_from_a_still_image(self):
        ident=self.gateway.prepare_video(
            self.p1,prompt="Animate the photo",source_images=[self.one],
            reference_mode=True,duration=5,aspect_ratio="9:16")
        stored=json.loads(self.db.execute("SELECT payload_json FROM kie_media_tasks WHERE id=?",
                                          (ident,)).fetchone()[0])
        self.assertEqual(stored["model"],"kling-3.0-omni/reference-to-video")
        self.assertNotIn("video_urls",stored["input"])
        self.assertEqual(stored["input"]["aspect_ratio"],"9:16")

    def test_registered_other_project_source_cannot_leave_its_owner(self):
        outside=self.root/self.p2/"originals"/"photo.png"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(self.one.read_bytes())
        self.db.execute("INSERT INTO assets VALUES(?,?,?,?,?,?)",
                        ("d"*32,self.p2,"photo",str(outside),"y","now"))
        self.db.commit()
        with self.assertRaises(ValueError):
            self.gateway.prepare_image(self.p1,prompt="Edit",sources=[outside])
        self.upload.assert_not_called()
        with self.assertRaises(ValueError):
            self.gateway.prepare_video(
                self.p1,prompt="Animate",source_images=[outside])
        self.upload.assert_not_called()

    def test_unregistered_file_is_rejected_even_inside_project_folder(self):
        unregistered=self.one.parent/"untracked.png"
        unregistered.write_bytes(self.one.read_bytes())
        with self.assertRaises(ValueError):
            self.gateway.prepare_image(self.p1,prompt="Edit",sources=[unregistered])
        self.upload.assert_not_called()

    def test_reference_video_uses_independent_media_validation(self):
        video=self.one.parent/"clip.mp4"
        video.write_bytes(b"placeholder stream, mocked ffprobe only")
        self.db.execute("INSERT INTO project_files VALUES(?,?,?,?,?,?)",
                        ("e"*32,self.p1,"video","clip",str(video),"now"))
        self.db.commit()
        meta=VideoMetadata(len(video.read_bytes()),5,1080,1920,30,".mp4")
        with patch("kie_media_runtime.verified_video",return_value=(video,meta)):
            ident=self.gateway.prepare_video(self.p1,prompt="Recolor the clip",
                                             source_video=video,source_images=[],
                                             reference_mode=False)
        stored=json.loads(self.db.execute("SELECT payload_json FROM kie_media_tasks WHERE id=?",
                                          (ident,)).fetchone()[0])
        self.assertEqual(stored["model"],"kling-3.0-omni/transformation")
        self.assertEqual(stored["input"]["aspect_ratio"],"auto")

if __name__=="__main__":
    unittest.main()
