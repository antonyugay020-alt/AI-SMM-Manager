"""No-network transfer acceptance: safe output files, no overwrite, no leaks."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"AI_SMM_Work_Architecture_A75"))
import kie_media_download as dl


class FakeResponse(io.BytesIO):
    status=200
    def __init__(self, data, headers=None, status=200):
        super().__init__(data)
        self.status=status
        self.headers=headers or {}


class KieDownloaderTests(unittest.TestCase):
    def test_valid_result_written_to_exact_target(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"image.png"
            calls=[]
            def opener(req,timeout):
                calls.append(req)
                return FakeResponse(b"good-picture-bytes",{"Content-Length":"18"})
            self.assertEqual(dl.download_result("https://cdn.example.org/image.png",
                                                 target,opener=opener),target)
            self.assertEqual(target.read_bytes(),b"good-picture-bytes")
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0].get_method(),"GET")
            self.assertFalse(list(Path(temp).glob(".downloading-*")))

    def test_rejects_private_literal_host_and_non_https(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"clip.mp4"
            for url in ("http://cdn.example.org/video.mp4",
                        "https://127.0.0.1/video.mp4",
                        "https://169.254.169.254/metadata",
                        "file:///etc/hosts",
                        "https://user:password@cdn.example.org/video.mp4"):
                with self.subTest(url=url):
                    with self.assertRaises(ValueError):
                        dl.download_result(url,target,opener=lambda *a,**k:None)
            self.assertFalse(target.exists())

    def test_missing_and_corrupt_content_length_rejected_without_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"image.png"
            for size in ("NaN",str(dl.MAX_IMAGE+1)):
                with self.subTest(size=size):
                    with self.assertRaises(ValueError):
                        dl.download_result("https://cdn.example.org/image.png",target,
                            opener=lambda *a,**k:FakeResponse(b"data",{"Content-Length":size}))
                    self.assertFalse(target.exists())

    def test_streaming_limit_checked_even_without_content_length(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"image.png"
            with patch.object(dl,"MAX_IMAGE",8):
                with self.assertRaises(ValueError):
                    dl.download_result("https://cdn.example.org/image.png",target,
                        opener=lambda *a,**k:FakeResponse(b"0123456789"))
            self.assertFalse(target.exists())
            self.assertFalse(list(Path(temp).glob(".downloading-*")))

    def test_existing_result_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"image.png"
            target.write_bytes(b"existing")
            with self.assertRaises(ValueError):
                dl.download_result("https://cdn.example.org/image.png",target,
                    opener=lambda *a,**k:FakeResponse(b"replacement"))
            self.assertEqual(target.read_bytes(),b"existing")

    def test_failed_stream_cleans_partial_download(self):
        class Broken(FakeResponse):
            def read(self,n=-1):
                raise OSError("synthetic stream crash")
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"clip.mp4"
            with self.assertRaises(OSError):
                dl.download_result("https://cdn.example.org/clip.mp4",target,
                    opener=lambda *a,**k:Broken(b"start"))
            self.assertFalse(target.exists())
            self.assertFalse(list(Path(temp).glob(".downloading-*")))

    def test_no_redirect_handler_allows_no_follow(self):
        with self.assertRaises(ValueError):
            dl._NoRedirect().redirect_request(None,None,302,"redirect",{},
                                              "https://redirect.example.com/file.png")

if __name__=="__main__":
    unittest.main()
