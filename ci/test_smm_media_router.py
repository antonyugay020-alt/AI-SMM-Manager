"""Offline regression for SMM-first media capability selection."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
from smm_media_router import (
    MediaRequest,Executor,select_executor,default_executors,
    TASKS,IMAGE_STYLE,IMAGE_REMOVE_BACKGROUND,IMAGE_STYLE_REFERENCE,
    VIDEO_STYLE,VIDEO_CUT,VIDEO_FROM_IMAGES,VIDEO_MONTAGE,
)

class MediaRouterTests(unittest.TestCase):
    def test_all_supported_smm_operations_are_explicit(self):
        self.assertGreaterEqual(len(TASKS),12)
        for task in TASKS:
            with self.subTest(task=task):
                ref=task.endswith("_reference")
                self.assertIsInstance(MediaRequest(task,1,"Test campaign",
                                                   has_style_reference=ref),MediaRequest)

    def test_local_is_preferred_even_if_free_and_paid_available(self):
        req=MediaRequest(VIDEO_CUT,2)
        providers=[
            Executor("paid","paid_cloud",frozenset({VIDEO_CUT}),True,99),
            Executor("free","free_cloud",frozenset({VIDEO_CUT}),True),
            Executor("ffmpeg","local",frozenset({VIDEO_CUT}),True),
        ]
        d=select_executor(req,providers)
        self.assertEqual((d.state,d.executor,d.tier),("ready","ffmpeg","local"))

    def test_free_cloud_used_only_when_verified(self):
        req=MediaRequest(IMAGE_REMOVE_BACKGROUND,1)
        providers=[
            Executor("demo-website","free_cloud",frozenset({IMAGE_REMOVE_BACKGROUND}),False),
            Executor("adapter","free_cloud",frozenset({IMAGE_REMOVE_BACKGROUND}),True),
        ]
        self.assertEqual(select_executor(req,providers).executor,"adapter")
        self.assertEqual(select_executor(req,providers[:1]).state,"unavailable")

    def test_paid_never_runs_automatically(self):
        req=MediaRequest(VIDEO_STYLE,1,"Change cinematic look")
        paid=Executor("kie","paid_cloud",frozenset({VIDEO_STYLE}),True,120)
        self.assertEqual(select_executor(req,[paid]).state,"needs_paid_approval")
        self.assertEqual(select_executor(req,[paid],paid_approved=True).state,
                         "ready_for_paid_boundary")

    def test_missing_paid_quote_is_never_a_valid_candidate(self):
        paid=Executor("unpriced","paid_cloud",frozenset({VIDEO_STYLE}),True)
        self.assertEqual(select_executor(MediaRequest(VIDEO_STYLE,1),[paid]).state,
                         "unavailable")

    def test_incomplete_media_rejected_before_routing(self):
        for item in (
            lambda:MediaRequest(VIDEO_CUT,0),
            lambda:MediaRequest(IMAGE_STYLE_REFERENCE,1,has_style_reference=False),
            lambda:MediaRequest("unlisted_external_service",1),
            lambda:MediaRequest(IMAGE_STYLE,True),
        ):
            with self.assertRaises(ValueError):
                item()

    def test_default_ffmpeg_does_not_claim_ai_generation(self):
        providers=default_executors(ffmpeg_ready=True)
        self.assertEqual(select_executor(MediaRequest(VIDEO_MONTAGE,3),providers).state,
                         "ready")
        self.assertEqual(select_executor(MediaRequest(VIDEO_FROM_IMAGES,2),providers).state,
                         "ready")
        self.assertEqual(select_executor(MediaRequest(VIDEO_STYLE,1),providers).state,
                         "unavailable")
        self.assertEqual(select_executor(MediaRequest(IMAGE_REMOVE_BACKGROUND,1),providers).state,
                         "unavailable")
        self.assertEqual(select_executor(MediaRequest(VIDEO_CUT,1),
                                         default_executors()).state,"unavailable")

    def test_truthy_values_cannot_bypass_consent_and_identity(self):
        paid=Executor("paid","paid_cloud",frozenset({VIDEO_STYLE}),True,45)
        with self.assertRaises(ValueError):
            select_executor(MediaRequest(VIDEO_STYLE,1),[paid],paid_approved=1)
        with self.assertRaises(ValueError):
            Executor("bad","free_cloud",frozenset({VIDEO_STYLE}),True,45)
        with self.assertRaises(ValueError):
            Executor("bad","paid_cloud",frozenset({VIDEO_STYLE}),True,True)
        with self.assertRaises(ValueError):
            select_executor(MediaRequest(VIDEO_STYLE,1),[paid,paid])

if __name__=="__main__":
    unittest.main()
