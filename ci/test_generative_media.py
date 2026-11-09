"""Offline contracts and actual Qt/SQLite/media lifecycle; model inference is a fixture."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
from PIL import Image
from generative_contract import ENGINES,validate_generation
from generative_models import profiles,verify_bundle,digest
import local_media_jobs as jobs

OWNER='a'*32


class GenerativeContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bundle=self.root/'bundle';(self.bundle/'model').mkdir(parents=True)
        (self.bundle/'model'/'model_index.json').write_text('{}')
        self.manifest={'version':1,'provenance':{'test_fixture':True},'files':{'model/model_index.json':{'sha256':digest(self.bundle/'model/model_index.json'),'bytes':2}}}
        self.write_manifest()
        self.db=sqlite3.connect(self.root/'application.sqlite');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)');self.db.execute('INSERT INTO projects VALUES(?)',(OWNER,))
        self.db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT,original_name TEXT,stored_path TEXT,sha256 TEXT,imported_at TEXT)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)');self.db.commit()
        self.sources=[]
        for index,color in enumerate(((20,40,60),(255,255,255))):
            path=self.workspace(OWNER)/'originals'/(str(index)+'.png');path.parent.mkdir(parents=True,exist_ok=True)
            with Image.new('RGB',(64,64),color) as image:image.save(path,'PNG')
            self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?)',(str(index),OWNER,path.name,str(path),digest(path),'now'));self.sources.append(str(path))
        self.db.commit()

    def workspace(self,owner):return self.root/owner

    def write_manifest(self):
        (self.bundle/'manifest.json').write_text(json.dumps(self.manifest))
        self.sha=digest(self.bundle/'manifest.json')

    def request(self,task='image_instruction'):
        return {'task':task,'sources':self.sources[:2 if task in ('image_reference','image_inpaint') else 1],
            'prompt':'Apply the requested edit','width':64,'height':64,'steps':4,'strength':.75,'seed':12,'guidance':7.5,
            'generation':{'engine':ENGINES[task],'bundle':str(self.bundle),'manifest_sha256':self.sha,'device':'cpu'}}

    def test_profiles_are_explicit_and_never_download_or_import_torch(self):
        self.assertEqual(profiles({}),{})
        path=self.root/'profiles.json';path.write_text(json.dumps({'version':1,'profiles':{'test':self.request()['generation']}}))
        self.assertEqual(profiles({'AI_SMM_GENERATIVE_CONFIG':str(path)})['test']['manifest_sha256'],self.sha)
        self.assertTrue(verify_bundle(self.request()['generation'])['provenance']['test_fixture'])

    def test_weights_and_extra_unsafe_files_are_not_silently_accepted(self):
        (self.bundle/'model/model_index.json').write_text('{"modified":true}')
        with self.assertRaisesRegex(ValueError,'weights differ'):verify_bundle(self.request()['generation'])
        (self.bundle/'model/model_index.json').write_text('{}')
        (self.bundle/'model/custom_pipeline.py').write_text('raise RuntimeError()')
        with self.assertRaisesRegex(ValueError,'file set changed'):verify_bundle(self.request()['generation'])

    def test_pickle_and_traversal_manifest_entries_rejected(self):
        for name in ('model/weights.bin','../escape','model/evil.py'):
            self.manifest['files'][name]={'sha256':'0'*64,'bytes':0};self.write_manifest()
            with self.assertRaises(ValueError):verify_bundle(self.request()['generation'])
            self.manifest['files'].pop(name)

    def test_8gb_bounds_and_engine_reference_contract(self):
        request=self.request();validate_generation(request)
        for invalid in (None,[],{'task':[]},{'task':{}},{'task':'unknown'}):
            with self.assertRaises(ValueError):validate_generation(invalid)
        for patch_value in ({'width':1024},{'height':65},{'steps':100},{'seed':True},{'prompt':''},{'sources':self.sources}):
            with self.assertRaises(ValueError):validate_generation({**request,**patch_value})
        request=self.request('video_generative');request.update(width=256,height=256,frames=16,fps=8,start=0)
        validate_generation(request)
        with self.assertRaises(ValueError):validate_generation({**request,'width':512})
        with self.assertRaises(ValueError):validate_generation({**request,'frames':32})
        request=self.request('image_reference');request['sources']=[self.sources[0],self.sources[0]]
        with self.assertRaises(ValueError):validate_generation(request)

    def test_turing_policy_never_selects_bf16_or_emulation(self):
        from generative_runner import device_policy
        torch=SimpleNamespace(float16='fp16',float32='fp32',cuda=SimpleNamespace(is_available=lambda:True,
            get_device_capability=lambda index:(7,5),mem_get_info=lambda index:(6*2**30,8*2**30)))
        self.assertEqual(device_policy(torch,'cuda'),'fp16')
        torch.cuda.is_available=lambda:False
        with self.assertRaisesRegex(RuntimeError,'CUDA_NOT_AVAILABLE'):device_policy(torch,'cuda')

    def test_durable_generation_receipt_publication_and_restart_without_model_call(self):
        request=self.request();job=jobs.create_job(self.db,OWNER,request)
        before=digest(self.sources[0])
        def fixture(request,sources,target,check):
            check()
            with Image.new('RGB',(64,64),(50,70,90)) as image:image.save(target,'PNG')
            (target.parent/'generation_receipt.json').write_text(json.dumps({**{k:request['generation'][k] for k in ('engine','manifest_sha256','device')},'seed':12,'dtype':'fixture'}))
        with patch.object(jobs,'_render',side_effect=fixture):output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertTrue(Path(output).is_file());self.assertEqual(digest(self.sources[0]),before)
        self.assertEqual(jobs.list_jobs(self.db,OWNER)[0]['model'],'local/instruct_pix2pix')
        self.assertEqual(self.db.execute('SELECT output_sha256 FROM local_generation_receipts').fetchone(),(digest(output),))
        self.db.execute("UPDATE local_media_jobs SET state='running' WHERE id=?",(job,));self.db.commit();jobs.recover_jobs(self.db,self.workspace,OWNER)
        with patch.object(jobs,'_render',side_effect=AssertionError('must adopt')):
            self.assertEqual(jobs.execute_job(self.db,self.workspace,OWNER,job),output)

    def test_missing_or_wrong_generation_receipt_prevents_publication(self):
        job=jobs.create_job(self.db,OWNER,self.request())
        def fixture(request,sources,target,check):
            with Image.new('RGB',(64,64)) as image:image.save(target,'PNG')
        with patch.object(jobs,'_render',side_effect=fixture):
            with self.assertRaisesRegex(ValueError,'Missing local generation receipt'):jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0],0)

    def test_cancel_before_generation_never_calls_model(self):
        job=jobs.create_job(self.db,OWNER,self.request());jobs.stop_job(self.db,OWNER,job)
        with patch.object(jobs,'_render') as render:
            with self.assertRaises(jobs.LocalMediaStopped):jobs.execute_job(self.db,self.workspace,OWNER,job)
        render.assert_not_called()

    def test_inpaint_compositing_preserves_unmasked_pixels_with_fixture_pipeline(self):
        import generative_runner as runner
        request=self.request('image_inpaint')
        with Image.new('L',(64,64),0) as mask:
            for x in range(32):
                for y in range(64):mask.putpixel((x,y),255)
            mask.save(self.sources[1])
        class Generator:
            def __init__(self,**kwargs):pass
            def manual_seed(self,seed):return self
        from contextlib import nullcontext
        torch=SimpleNamespace(Generator=Generator,inference_mode=nullcontext,__version__="fixture")
        def pipeline(**kwargs):return SimpleNamespace(images=[Image.new('RGB',(64,64),(200,100,50))],nsfw_content_detected=[False])
        target=self.root/'output.png'
        with patch.dict(sys.modules,{'torch':torch}):runner.run(request,[Path(p) for p in self.sources],target,pipeline_loader=lambda *a:(pipeline,'fixture'))
        with Image.open(target) as result:
            self.assertEqual(result.getpixel((50,20)),(20,40,60));self.assertEqual(result.getpixel((10,20)),(200,100,50))

    def test_semantic_generation_uses_issued_ids_and_frozen_profile_binding(self):
        from dialogue_runtime import local_media_request
        from local_media_contract import local_media_schema
        from generative_models import describe_profile
        profile=self.request()['generation'];config=self.root/'config.json'
        config.write_text(json.dumps({'version':1,'profiles':{'installed':profile}}))
        caps={'local_media_tasks':[],'generative_profiles':{'installed':describe_profile(profile)}}
        catalog={'media:source':{'kind':'image','path':self.sources[0],'sha256':digest(self.sources[0])}}
        snapshot={'media':catalog,'ui_context':{'capabilities':caps}}
        media={key:value for key,value in self.request().items() if key not in ('sources','generation')}
        media.update(profile='installed',source_ids=['media:source'])
        with patch.dict(os.environ,{'AI_SMM_GENERATIVE_CONFIG':str(config)}):
            request=local_media_request(snapshot,media)
            self.assertEqual(request['sources'],self.sources[:1]);self.assertEqual(request['generation'],profile)
            self.assertEqual(local_media_schema(catalog,caps)['anyOf'][1]['properties']['profile']['enum'],['installed'])
            wrong={**media,'source_ids':['/arbitrary/raw/path']}
            with self.assertRaises(ValueError):local_media_request(snapshot,wrong)
            profile['device']='cuda';config.write_text(json.dumps({'version':1,'profiles':{'installed':profile}}))
            with self.assertRaisesRegex(ValueError,'profile changed'):local_media_request(snapshot,media)

    def test_actual_video_container_composition_keeps_source_audio_with_model_frame_fixture(self):
        import io
        import subprocess
        import shutil
        import test_local_media_jobs as local_fixture
        import generative_media_runtime as runtime
        if not shutil.which('ffmpeg'):self.skipTest('Native FFmpeg required')
        video=local_fixture.LocalMediaJobsTests.video(self,'input.mp4',audio=True)
        request=self.request('video_generative');request.update(sources=[str(video),self.sources[0]],frames=4,fps=8,start=0)
        source_hash=digest(video);job=jobs.create_job(self.db,OWNER,request)
        class ProcessFixture:
            returncode=0
            pid=999999
            def __init__(self,command,**options):
                self.stdin=io.BytesIO();payload=json.loads(Path(command[-1]).read_text());target=Path(payload['target'])
                frames=target.parent/'output_frames';frames.mkdir()
                for index in range(4):
                    with Image.new('RGB',(64,64),(40+index,70,90)) as image:image.save(frames/(str(index).zfill(4)+'.png'),'PNG')
                (target.parent/'generation_receipt.json').write_text(json.dumps({**{key:request['generation'][key] for key in ('engine','manifest_sha256','device')},'seed':12,'frames':4,'dtype':'model-frame-fixture'}))
            def poll(self):return 0
        # Replace only generation's process factory. FFmpeg/ffprobe stay real.
        from contextlib import contextmanager
        @contextmanager
        def fixture_process(command,**options):yield ProcessFixture(command,**options)
        with patch('native_process_runtime.owned_native_process',fixture_process):
            output=jobs.execute_job(self.db,self.workspace,OWNER,job)
        duration,audio=jobs._probe(output)
        from local_media_quality import compare_audio
        self.assertGreater(compare_audio(video,output,0,.5)['best_correlation'],.9)
        self.assertTrue(audio);self.assertAlmostEqual(duration,.5,delta=.15);self.assertEqual(digest(video),source_hash)
        self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs WHERE id=?',(job,)).fetchone(),('completed',))

    def test_video_pipeline_receives_explicit_geometry_and_bounded_decode(self):
        import generative_runner as runner
        from contextlib import nullcontext
        request=self.request('video_generative');request.update(frames=4,fps=8,start=0)
        folder=self.root/'input_frames';folder.mkdir()
        for index in range(4):
            with Image.new('RGB',(64,64)) as image:image.save(folder/(str(index)+'.png'),'PNG')
        class Generator:
            def __init__(self,**kwargs):pass
            def manual_seed(self,seed):return self
        torch=SimpleNamespace(Generator=Generator,inference_mode=nullcontext,__version__='fixture')
        called=[]
        def pipeline(**kwargs):
            called.append(kwargs)
            return SimpleNamespace(frames=[[Image.new('RGB',(64,64)) for _ in range(4)]])
        with patch.dict(sys.modules,{'torch':torch}):runner.run(request,[Path(self.sources[0])],self.root/'output.mp4',pipeline_loader=lambda *a:(pipeline,'fixture'))
        self.assertEqual((called[0]['width'],called[0]['height'],called[0]['decode_chunk_size']),(64,64,1))
        self.assertEqual(len(list((self.root/'output_frames').glob('*.png'))),4)

    def test_selected_image_dialogue_uses_the_same_durable_generator(self):
        from app import Studio
        from generative_models import describe_profile
        from unittest.mock import Mock
        profile=self.request()['generation'];config=self.root/'config.json';config.write_text(json.dumps({'version':1,'profiles':{'instruction':profile}}))
        launch=Mock()
        window=SimpleNamespace(engine_mode=0,kie_queue_panel=SimpleNamespace(local_profile=SimpleNamespace(currentData=lambda:'instruction')),
            source=SimpleNamespace(path=self.sources[0]),reference=SimpleNamespace(path=None),_launch_semantic_local_request=launch)
        plan=SimpleNamespace(action={'use_reference':False},utterance='Apply this edit',snapshot={'ui_context':{'capabilities':{'generative_profiles':{'instruction':describe_profile(profile)}}},
            'media':{'issued':{'path':self.sources[0],'sha256':digest(self.sources[0])}}})
        with patch.dict(os.environ,{'AI_SMM_GENERATIVE_CONFIG':str(config)}):self.assertTrue(Studio.try_semantic_local_generation(window,plan))
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[1]['task'],'image_instruction')
        self.assertEqual(launch.call_args.args[1]['expected_sha256'],[digest(self.sources[0])])

    def test_restart_cleans_only_the_locked_tasks_owned_partial_work(self):
        job=jobs.create_job(self.db,OWNER,self.request())
        work=self.workspace(OWNER)/'.local_media_work';orphan=work/(job+'-orphan');orphan.mkdir(parents=True)
        (orphan/'partial.png').write_bytes(b'incomplete generated frame')
        other=work/('b'*32+'-other');other.mkdir();(other/'keep').write_text('other task')
        with patch.object(jobs,'_render',side_effect=RuntimeError('controlled inference failure')):
            with self.assertRaises(RuntimeError):jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertFalse(orphan.exists());self.assertTrue((other/'keep').is_file())

    def test_project_deletion_cannot_race_a_live_local_generation_claim(self):
        from project_deletion import _reject_active_project_workers
        job=jobs.create_job(self.db,OWNER,self.request())
        _reject_active_project_workers(self.db,OWNER)
        self.db.execute("UPDATE local_media_jobs SET state='running' WHERE id=?",(job,));self.db.commit()
        with self.assertRaisesRegex(RuntimeError,'active worker claim'):_reject_active_project_workers(self.db,OWNER)
        _reject_active_project_workers(self.db,'b'*32)

    def test_failed_process_diagnostics_survive_temporary_work_cleanup(self):
        from generative_media_runtime import OfflineGenerationError
        job=jobs.create_job(self.db,OWNER,self.request())
        with patch.object(jobs,'_render',side_effect=OfflineGenerationError('CUDA_NOT_AVAILABLE',{'returncode':1,'log_tail':'safe fixture diagnostic'})):
            with self.assertRaises(OfflineGenerationError):jobs.execute_job(self.db,self.workspace,OWNER,job)
        self.assertEqual(self.db.execute('SELECT error_code FROM local_media_jobs').fetchone(),('CUDA_NOT_AVAILABLE',))
        self.assertEqual(json.loads(self.db.execute('SELECT diagnostic_json FROM local_generation_diagnostics').fetchone()[0])['returncode'],1)

    def test_invalid_post_inference_receipt_retains_durable_stage_evidence(self):
        request=self.request();job=jobs.create_job(self.db,OWNER,request)
        def fixture(request,sources,target,check):
            with Image.new('RGB',(64,64),(50,70,90)) as image:image.save(target,'PNG')
            (target.parent/'generation_process.log').write_text('{"stage":"receipt_written"}\n',encoding='utf-8')
            (target.parent/'generation_receipt.json').write_text(json.dumps({
                **{k:request['generation'][k] for k in ('engine','manifest_sha256','device')},'seed':999}),encoding='utf-8')
        with patch.object(jobs,'_render',side_effect=fixture):
            with self.assertRaisesRegex(ValueError,'seed differs'):jobs.execute_job(self.db,self.workspace,OWNER,job)
        row=self.db.execute('SELECT diagnostic_json FROM local_generation_diagnostics WHERE job_id=?',(job,)).fetchone()
        self.assertIn('receipt_written',json.loads(row[0])['stage_log_tail'])
        self.assertEqual(self.db.execute('SELECT state FROM local_media_jobs WHERE id=?',(job,)).fetchone(),('failed',))
        self.assertIsNone(self.db.execute('SELECT id FROM project_files WHERE id=?',(job,)).fetchone())

if __name__=='__main__':unittest.main()

class NativeProcessOwnershipTests(unittest.TestCase):
    def test_parent_channel_eof_terminates_the_actual_runner(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'request.json';path.write_text(json.dumps({'request':{'generation':{}},'inputs':[],'target':str(Path(temporary)/'output.png')}))
            code="import sys,time,contextlib;sys.path.insert(0,sys.argv[1]);import generative_runner as runner\ndef workload(*args,**kwargs):\n print('entered',flush=True);time.sleep(30)\nrunner.run=workload;runner.validate_generation=lambda request:None;runner.verify_bundle=lambda profile:None;runner.initialize_python_torch=lambda:object();runner.device_lock=contextlib.nullcontext;sys.argv=['runner','--request',sys.argv[2]];runner.main()"
            process=subprocess.Popen([sys.executable,'-c',code,str(Path(jobs.__file__).parent),str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                for stage in ('runner_started','verifying_model_bundle','acquiring_device_lock','device_lock_acquired'):
                    event=json.loads(process.stdout.readline())
                    self.assertEqual(event['stage'],stage)
                    self.assertIsInstance(event['monotonic_s'],(int,float))
                self.assertEqual(process.stdout.readline().strip(),'entered')
                process.stdin.close();process.wait(timeout=10)
                self.assertEqual(process.returncode,75)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
                process.stdout.close();process.stderr.close()

    def test_actual_device_lock_excludes_a_second_process_and_releases_on_death(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            lock=Path(temporary)/'device.lock'
            code="import sys,time;sys.path.insert(0,sys.argv[1]);from generative_runner import device_lock\nwith device_lock(sys.argv[2]):\n print('locked',flush=True);time.sleep(30)\n"
            process=subprocess.Popen([sys.executable,'-c',code,str(Path(jobs.__file__).parent),str(lock)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(),'locked')
                from generative_runner import device_lock
                with self.assertRaises(OSError):
                    with device_lock(lock,wait=False):pass
                process.terminate();process.wait(timeout=5)
                with device_lock(lock,wait=False):pass
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
                process.stdout.close();process.stderr.close()

    def test_second_device_job_waits_and_acquires_after_owner_death(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            lock=Path(temporary)/'device.lock'
            prefix="import sys,time;sys.path.insert(0,sys.argv[1]);from generative_runner import device_lock\n"
            first=subprocess.Popen([sys.executable,'-c',prefix+"with device_lock(sys.argv[2]):\n print('locked',flush=True);time.sleep(30)\n",str(Path(jobs.__file__).parent),str(lock)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            second=None
            try:
                self.assertEqual(first.stdout.readline().strip(),'locked')
                second=subprocess.Popen([sys.executable,'-c',prefix+"with device_lock(sys.argv[2]):\n print('acquired',flush=True)\n",str(Path(jobs.__file__).parent),str(lock)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                waiting=json.loads(second.stdout.readline())
                self.assertEqual(waiting['stage'],'waiting_for_local_device')
                self.assertIsInstance(waiting['monotonic_s'],(int,float))
                first.terminate();first.wait(timeout=5)
                output,error=second.communicate(timeout=10)
                self.assertEqual(second.returncode,0,error);self.assertIn('acquired',output)
            finally:
                for process in (first,second):
                    if process is not None:
                        if process.poll() is None:process.kill();process.wait(timeout=5)
                        process.stdout.close();process.stderr.close()

    def test_linux_cannot_be_counted_as_windows_generative_acceptance(self):
        if os.name=='nt':self.skipTest('Linux refusal check')
        from generative_acceptance import run_cases
        with self.assertRaisesRegex(RuntimeError,'WINDOWS_HOST_REQUIRED'):run_cases('not-read.json','not-created')
