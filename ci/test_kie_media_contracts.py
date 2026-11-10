"""Offline contract tests against user-supplied Kie OpenAPI media specs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
from kie_media_contracts import (nano_banana_pro,nano_banana_edit,
                                 kling_omni_transformation,kling_omni_reference_to_video,VideoMetadata,extract_task_id)

VIDEO="https://files.example.org/input.mp4"
IMAGE="https://files.example.org/portrait.png"


def sample(**changes):
    fields=dict(size_bytes=12_000_000,duration_seconds=5.0,width=1080,
                height=1920,fps=30.0,extension="mp4")
    fields.update(changes)
    return VideoMetadata(**fields)


class KieMediaContracts(unittest.TestCase):
    def test_pro_supports_eight_references_and_4k(self):
        v=nano_banana_pro("Product shot", [IMAGE]*8,"9:16","4K","jpg")
        self.assertEqual(v["model"],"nano-banana-pro")
        self.assertEqual(len(v["input"]["image_input"]),8)
        self.assertEqual(v["input"]["resolution"],"4K")
        with self.assertRaises(ValueError):
            nano_banana_pro("Image", [IMAGE]*9)

    def test_edit_uses_different_input_name_and_requires_photo(self):
        result=nano_banana_edit("Change background",[IMAGE])
        self.assertEqual(result["model"],"google/nano-banana-edit")
        self.assertEqual(result["input"]["image_urls"],[IMAGE])
        with self.assertRaises(ValueError):
            nano_banana_edit("Edit",[])
        with self.assertRaises(ValueError):
            nano_banana_edit("Edit",[IMAGE],output_format="jpg")

    def test_kling_video_only_and_video_with_images_are_separate_contracts(self):
        raw=kling_omni_transformation("Night style",VIDEO,sample())
        self.assertEqual(raw["model"],"kling-3.0-omni/transformation")
        self.assertEqual(raw["input"]["aspect_ratio"],"auto")
        self.assertEqual(raw["input"]["audio"],False)
        self.assertNotIn("image_urls",raw["input"])
        refs=kling_omni_transformation("Night",VIDEO,sample(),[IMAGE],"1080p","9:16")
        self.assertEqual(refs["input"]["image_urls"],[IMAGE])
        self.assertEqual(refs["input"]["aspect_ratio"],"9:16")
        with self.assertRaises(ValueError):
            kling_omni_transformation("Night",VIDEO,sample(),[IMAGE],aspect_ratio="auto")
        with self.assertRaises(ValueError):
            kling_omni_transformation("Night",VIDEO,sample(),aspect_ratio="16:9")

    def test_kling_source_limits_are_enforced_before_paid_api(self):
        wrong=[dict(duration_seconds=2.9),dict(duration_seconds=16),
               dict(size_bytes=201*1024*1024),dict(width=640),
               dict(height=5000),dict(width=4000,height=3000),
               dict(fps=20),dict(fps=61),dict(extension="avi")]
        for change in wrong:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    kling_omni_transformation("Change scene",VIDEO,sample(**change))

    def test_kling_requires_exactly_one_video_and_max_four_image_urls(self):
        for bad in [[],[VIDEO,VIDEO],"file:///tmp/source.mp4",
                    "https://localhost/file.mp4"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    kling_omni_transformation("Transform",bad,sample())
        with self.assertRaises(ValueError):
            kling_omni_transformation("Transform",VIDEO,sample(),[IMAGE]*5)

    def test_no_invented_image_to_video_without_video_source(self):
        with self.assertRaises(ValueError):
            kling_omni_transformation("Animate image",IMAGE,None,[IMAGE])

    def test_internal_ip_sources_cannot_be_sent_to_remote_provider(self):
        for value in ("https://169.254.169.254/credentials",
                      "https://192.168.1.25/test.png",
                      "https://[::1]/file.mp4",
                      "https://localhost/secret",
                      "https://host.internal/media.png"):
            with self.subTest(url=value):
                with self.assertRaises(ValueError):
                    nano_banana_pro("Edit", [value])

    def test_fail_closed_task_envelope_even_when_http_200(self):
        self.assertEqual(extract_task_id({"code":200,"data":{"taskId":"task_kling_123456789"}}),
                         "task_kling_123456789")
        for response in [{"code":402,"data":{"taskId":"task_bad_12345678"}},
                         {"code":200,"data":{}},{"code":200,"data":{"taskId":"x"}},
                         {"code":200,"data":None}]:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    extract_task_id(response)


    def test_reference_to_video_from_image_without_video(self):
        payload = kling_omni_reference_to_video(
            "Animate this photograph", [IMAGE], duration=5, resolution="720p",
            aspect_ratio="9:16", audio=True)
        self.assertEqual(payload["model"], "kling-3.0-omni/reference-to-video")
        self.assertEqual(payload["input"]["image_urls"], [IMAGE])
        self.assertNotIn("video_urls", payload["input"])
        self.assertTrue(payload["input"]["audio"])

    def test_reference_to_video_multi_shot_validation(self):
        shots=[{"prompt":"Wide product introduction", "duration":2},
               {"prompt":"Closeup of product", "duration":3}]
        payload=kling_omni_reference_to_video(
            "Product showcase", [IMAGE], duration=5, customize_multi_shots=True,
            multi_prompt=shots)
        self.assertEqual(len(payload["input"]["multi_prompt"]),2)
        with self.assertRaises(ValueError):
            kling_omni_reference_to_video(
                "Product", [IMAGE], duration=5, customize_multi_shots=True,
                prefer_multi_shots=True, multi_prompt=shots)
        with self.assertRaises(ValueError):
            kling_omni_reference_to_video(
                "Product", [IMAGE], duration=15, customize_multi_shots=True,
                multi_prompt=[{"prompt":"First", "duration":10},
                              {"prompt":"Second", "duration":6}])

    def test_reference_to_video_source_video_has_strict_audio_and_ratio(self):
        payload=kling_omni_reference_to_video(
            "Improve transitions", video_url=VIDEO, metadata=sample())
        self.assertFalse(payload["input"]["audio"])
        self.assertEqual(payload["input"]["aspect_ratio"],"auto")
        with self.assertRaises(ValueError):
            kling_omni_reference_to_video(
                "Improve", video_url=VIDEO, metadata=sample(), audio=True)
        with self.assertRaises(ValueError):
            kling_omni_reference_to_video(
                "Improve", video_url=VIDEO, metadata=sample(),
                aspect_ratio="9:16")
        with self.assertRaises(ValueError):
            kling_omni_reference_to_video(
                "Improve", video_url=VIDEO, metadata=sample(),
                customize_multi_shots=True,multi_prompt=[{"prompt":"A", "duration":5}])



if __name__=="__main__":
    unittest.main()
