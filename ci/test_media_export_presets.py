"""Output-size selection uses the existing immutable job contract and GPU limits."""
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from media_export_presets import EXPORT_SIZES,export_size
from local_media_contract import validate_local_request


class ExportSizes(unittest.TestCase):
    def test_explicit_sizes_fit_local_video_contract_without_changing_sources(self):
        for key,label,width,height in EXPORT_SIZES:
            with self.subTest(key=key):
                request={'task':'video_format','sources':['source.mp4'],'width':width,'height':height}
                before=dict(request);validate_local_request(request)
                self.assertEqual(export_size(key),(width,height));self.assertEqual(request,before)
                self.assertEqual(width%2+height%2,0)
        with self.assertRaises(ValueError):export_size('automatic-provider-fallback')

    def test_export_sizes_cannot_bypass_generative_memory_envelope(self):
        from generative_contract import validate_generation
        request={'task':'image_instruction','sources':['source.png'],'prompt':'Keep the subject',
            'generation':{'engine':'instruct_pix2pix','bundle':'/installed',
                'manifest_sha256':'a'*64,'device':'cuda'}}
        for key,label,width,height in EXPORT_SIZES:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Generative limit'):
                validate_generation({**request,'width':width,'height':height})
