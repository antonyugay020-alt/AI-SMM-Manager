"""References and image controls follow semantic fields, not prompt keywords."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from PIL import Image
from reference_policy import explicit_reference
from modern_local_editor import validate_reference_inputs


class ExplicitReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source.png';self.reference=self.root/'reference.png'
        with Image.new('RGB',(64,64),(20,40,60)) as image:image.save(self.source,'PNG')
        with Image.new('RGB',(64,64),(180,130,60)) as image:image.save(self.reference,'PNG')

    def test_non_boolean_or_missing_reference_cannot_authorize_pixel_use(self):
        for flag in (1,'yes',[True]):
            with self.assertRaises(ValueError):explicit_reference(self.reference,flag,source=self.source)
        with self.assertRaises(ValueError):explicit_reference(None,True,source=self.source)
        with self.assertRaises(ValueError):explicit_reference(self.source,True,source=self.source)

    def test_standalone_editor_requires_explicit_flag_but_no_special_wording(self):
        with self.assertRaises(ValueError):validate_reference_inputs(self.source,self.reference,self.root/'out.png')
        validate_reference_inputs(self.source,self.reference,self.root/'out.png',use_reference=True)
        with self.assertRaises(ValueError):validate_reference_inputs(self.source,self.reference,self.reference,use_reference=True)

    def test_worker_reference_authority_is_independent_of_prompt_language(self):
        from app import ModernLocalWorker,CloudEditWorker
        for worker in [ModernLocalWorker(self.source,self.reference,self.root/'a.png','Proceed with the established visual direction',use_reference=True),
                       CloudEditWorker(self.source,self.reference,self.root/'b.png','Continue the previous visual treatment','fixture',use_reference=True)]:
            self.assertEqual(worker.reference,self.reference)
        worker=CloudEditWorker(self.source,self.reference,self.root/'c.png','Используй эталон','fixture',use_reference=False)
        self.assertIsNone(worker.reference)

    def test_legacy_pixel_primitive_does_not_parse_user_language(self):
        from app import EditWorker
        first=self.root/'first.jpg';second=self.root/'second.jpg'
        errors=[]
        for prompt,target in [('ярче насыщеннее используй эталон',first),('arbitrary wording without commands',second)]:
            worker=EditWorker(self.source,self.reference,target,prompt,use_reference=False)
            worker.failed.connect(errors.append);worker.run()
        self.assertEqual(errors,[]);self.assertEqual(first.read_bytes(),second.read_bytes())
        matched=self.root/'matched.jpg'
        worker=EditWorker(self.source,self.reference,matched,'arbitrary wording',use_reference=True,adjustments={'brightness':1.12})
        worker.run();self.assertNotEqual(matched.read_bytes(),second.read_bytes())

    def test_precision_policy_avoids_bfloat16_emulation_on_turing(self):
        from types import SimpleNamespace
        from modern_local_editor import select_cuda_dtype
        def sdk(capability,native):
            return SimpleNamespace(float16='fp16',bfloat16='bf16',cuda=SimpleNamespace(
                get_device_capability=lambda:capability,
                is_bf16_supported=lambda **kwargs:native))
        self.assertEqual(select_cuda_dtype(sdk((7,5),True)),'fp16')
        self.assertEqual(select_cuda_dtype(sdk((8,0),True)),'bf16')
        self.assertEqual(select_cuda_dtype(sdk((8,0),False)),'fp16')

if __name__=='__main__':unittest.main()
