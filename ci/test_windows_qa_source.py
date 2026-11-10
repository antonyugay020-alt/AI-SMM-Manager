"""Portable QA controller contracts. Synthetic reports never count as Windows runs."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import windows_qa_session as session
from windows_qa_contract import *
from local_media_quality import LocalVision,objective_quality,compare_audio
from PIL import Image,ImageDraw

FP='a'*64

def report(role='target_2080super',seed=1):
    hardware={'cuda':True,'name':'NVIDIA GeForce RTX 2080 SUPER' if role=='target_2080super' else 'NVIDIA GeForce RTX 3080 Ti',
        'capability':[7,5] if role=='target_2080super' else [8,6],'total':8*2**30 if role=='target_2080super' else 12*2**30}
    return {'status':'PASS','source_qa':True,'windows_executed':True,'qa_role':role,'qa_hardware':hardware,
        'attested_source_fingerprint':FP,'test_profile':'full','task_seed_replay_requested':False,'task_generation_seed':seed,'release_eligible':False,
        'checks':{name:{'status':'PASS',**({'profile':'full'} if name=='unit_tests' else {})} for name in SOURCE_REQUIRED}}


class AccountingTests(unittest.TestCase):
    def test_gpu_roles_cannot_be_exchanged(self):
        for role in ROLES:
            self.assertTrue(hardware_matches(report(role)['qa_hardware'],role))
            self.assertFalse(hardware_matches(report(role)['qa_hardware'],next(r for r in ROLES if r!=role)))
        self.assertFalse(hardware_matches({'cuda':False},'target_2080super'))

    def test_preliminary_cannot_advance_target_counter(self):
        state={}
        self.assertIsNone(record_run(state,report('preliminary_3080ti'),fingerprint=FP,role='preliminary_3080ti',exit_code=0))
        self.assertEqual(state['target_clean_consecutive'],0);self.assertFalse(state['release_eligible'])

    def test_ten_complete_target_reports_required(self):
        state={}
        for i in range(1,11):
            self.assertIsNone(record_run(state,report(seed=i),fingerprint=FP,role='target_2080super',exit_code=0))
            self.assertEqual(state['clean_consecutive'],i)
            self.assertEqual(state['status'],'PASS' if i==10 else 'RUNNING')
        self.assertFalse(state['release_eligible'])

    def test_failure_drift_replay_and_missing_gates_reset(self):
        for mutation in ('process','snapshot','replay','missing','linux','partial','wrong_gpu','packaging'):
            state={};record_run(state,report(seed=1),fingerprint=FP,role='target_2080super',exit_code=0)
            candidate=report(seed=2);code=0
            if mutation=='process':code=2
            if mutation=='snapshot':candidate['attested_source_fingerprint']='b'*64
            if mutation=='replay':candidate['task_generation_seed']=1
            if mutation=='missing':candidate['checks'].pop('media_lifecycle')
            if mutation=='linux':candidate['windows_executed']=False
            if mutation=='partial':candidate['checks']['unit_tests']['profile']='fast'
            if mutation=='wrong_gpu':candidate['qa_hardware']=report('preliminary_3080ti')['qa_hardware']
            if mutation=='packaging':candidate['release_eligible']=True
            self.assertIsNotNone(record_run(state,candidate,fingerprint=FP,role='target_2080super',exit_code=code),mutation)
            self.assertEqual(state['clean_consecutive'],0,mutation)

    def test_atomic_diagnostics_and_source_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'app.py').write_text('x=1');(root/'ci').mkdir();(root/'ci/test.py').write_text('x=2')
            snapshot=source_snapshot(root);self.assertEqual(verify_snapshot(root,snapshot),snapshot['fingerprint'])
            (root/'app.py').write_text('x=3')
            with self.assertRaises(ValueError):verify_snapshot(root,snapshot)
            path=root/'output/STATUS.json';write_json(path,{'status':'BLOCKED'})
            self.assertEqual(json.loads(path.read_text())['status'],'BLOCKED');self.assertFalse(path.with_suffix('.json.pending').exists())

    def test_local_environment_scrubs_cloud_authority_and_offscreen(self):
        env=local_only_environment({'AI_SMM_CLOUD_MODELS':'openai/test','OPENAI_API_KEY':'private','OLLAMA_API_KEY':'private','QT_QPA_PLATFORM':'offscreen','PATH':'keep'},['qwen:local'])
        self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('OLLAMA_API_KEY',env);self.assertNotIn('QT_QPA_PLATFORM',env)
        self.assertEqual(env['AI_SMM_QA_LOCAL_ONLY'],'1');self.assertEqual(env['AI_SMM_CLOUD_MODELS'],'')
        self.assertEqual(env['HTTPS_PROXY'],'http://127.0.0.1:9')
        from execution_environment import isolated_contract_test_environment
        tests=isolated_contract_test_environment({**env,'AI_SMM_GENERATIVE_CONFIG':'real_models.json'})
        self.assertNotIn('AI_SMM_QA_LOCAL_ONLY',tests);self.assertNotIn('AI_SMM_GENERATIVE_CONFIG',tests)

    def test_shared_text_boundaries_block_external_calls_even_with_fake_authority(self):
        from text_providers import call_text
        from ollama_resilience import request_chat
        with patch.dict(os.environ,{'AI_SMM_QA_LOCAL_ONLY':'1','AI_SMM_QA_ALLOWED_MODELS':'["approved"]'}):
            with self.assertRaises(RuntimeError):call_text('openai/gpt',[],environ={'AI_SMM_ENABLE_OPENAI_API':'1'})
            with patch('urllib.request.urlopen') as network:
                with self.assertRaises(RuntimeError):request_chat({'model':'other','messages':[{'role':'user','content':'test'}]},stage='qa',attempts=1)
                network.assert_not_called()

    def test_preliminary_export_is_bound_to_inputs_source_and_full_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'prelim.json';proof=report('preliminary_3080ti')
            digest=__import__('hashlib').sha256(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            write_json(path,{'report':proof,'input_binding':'fixtures','report_sha256':digest})
            self.assertTrue(session.validate_preliminary(path,FP,'fixtures'))
            with self.assertRaises(ValueError):session.validate_preliminary(path,FP,'different')
            proof['qa_hardware']=report()['qa_hardware'];write_json(path,{'report':proof,'input_binding':'fixtures','report_sha256':digest})
            with self.assertRaises(ValueError):session.validate_preliminary(path,FP,'fixtures')

    def test_linux_one_click_writes_blocked_report_without_tests_or_download(self):
        if os.name=='nt':self.skipTest('Linux negative contract')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'profiles.json').write_text('{}');(root/'plan.json').write_text('{}')
            write_json(root/'settings.json',{'profiles':str(root/'profiles.json'),'plan':str(root/'plan.json'),'vision_model':'not-called'})
            result=subprocess.run([sys.executable,str(ROOT/'windows_qa_session.py'),'--role','preliminary_3080ti','--no-wizard','--config',str(root/'settings.json'),'--output',str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            latest=json.loads((root/'LATEST.json').read_text());folder=Path(latest['session'])
            status=json.loads((folder/'STATUS.json').read_text())
            self.assertEqual(status['target_clean_consecutive'],0);self.assertFalse(status['windows_executed'])
            self.assertIn('Actual Windows required',status['reason'])
            self.assertTrue((folder/'HOST.json').is_file());self.assertTrue((folder/'PREREQUISITES.json').is_file())
            self.assertTrue((folder/'REPORT_MANIFEST.json').is_file());self.assertTrue((folder/'runner_error.json').is_file())


class QualityTests(unittest.TestCase):
    def test_only_installed_loopback_vision_is_allowed(self):
        for model,url in [('qwen:cloud','http://127.0.0.1:11434'),('openai/gpt','http://127.0.0.1:11434'),('vision','https://external.example:443')]:
            with self.assertRaises(ValueError):LocalVision(model,url)
        vision=LocalVision('vision:local')
        def response(path,data=None):
            if path=='/api/tags':return {'models':[{'name':'vision:local','digest':'a'*64}]}
            return {'capabilities':['vision'],'remote_host':'cloud.example'}
        vision.call=response
        with self.assertRaises(ValueError):vision.installed()

    def test_vision_digest_cannot_change_between_qualification_and_review(self):
        vision=LocalVision('vision:local');digest=['a'*64]
        vision.call=lambda path,data=None:({'models':[{'name':'vision:local','digest':digest[0]}]} if path=='/api/tags' else {'capabilities':['vision']})
        self.assertEqual(vision.installed(),'a'*64);digest[0]='b'*64
        with self.assertRaises(ValueError):vision.installed()

    def test_pixel_controls_are_not_optional(self):
        with tempfile.TemporaryDirectory() as temporary:
            vision=LocalVision('vision:local');vision.digest='a'*64
            vision.chat=lambda *args:{'left':'red','right':'red'}
            with self.assertRaises(ValueError):vision.qualify(temporary)

    def test_semantic_uncertainty_and_unapplied_reference_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths=[]
            for i in range(3):
                path=Path(temporary)/(str(i)+'.png');Image.new('RGB',(32,32),'red').save(path);paths.append(str(path))
            case={'sources':[paths[0],paths[2]],'prompt':'Apply the reference','task':'image_reference'}
            vision=LocalVision('vision:local');vision.digest='a'*64
            base={'verdict':'PASS','instruction':'PASS','preservation':'PASS','reference_or_mask':'PASS','temporal':'NOT_APPLICABLE','observations':'Visible evidence from supplied pixels'}
            vision.chat=lambda *args:base
            self.assertEqual(vision.review(case,paths[1],temporary,{})['status'],'PASS')
            for patch_value in ({'verdict':'UNCERTAIN'},{'reference_or_mask':'NOT_APPLICABLE'},{'observations':''},{'instruction':'FAIL'},{'temporal':'FAIL'},{'temporal':'UNCERTAIN'}):
                vision.chat=lambda *args,value=patch_value:{**base,**value}
                self.assertEqual(vision.review(case,paths[1],temporary,{})['status'],'BLOCKED')

    def test_blank_unchanged_and_mask_leakage_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.png';out=root/'out.png';mask=root/'mask.png'
            image=Image.new('RGB',(64,64),'white');ImageDraw.Draw(image).rectangle((0,0,31,63),fill='blue');image.save(source);image.save(out)
            case={'sources':[str(source)],'prompt':'Change style','task':'image_instruction'}
            with self.assertRaises(ValueError):objective_quality(case,out,root)
            Image.new('RGB',(64,64),'white').save(out)
            with self.assertRaises(ValueError):objective_quality(case,out,root)
            image=Image.open(source).copy();ImageDraw.Draw(image).rectangle((0,0,31,63),fill='red');image.save(out)
            m=Image.new('L',(64,64),0);ImageDraw.Draw(m).rectangle((0,0,31,63),fill=255);m.save(mask)
            case.update(task='image_inpaint',sources=[str(source),str(mask)])
            self.assertTrue(objective_quality(case,out,root)['protected_pixels_exact'])
            image.putpixel((50,50),(0,0,0));image.save(out)
            with self.assertRaisesRegex(ValueError,'Protected mask'):objective_quality(case,out,root)

    def test_actual_audio_readback_detects_replaced_audio(self):
        if not shutil.which('ffmpeg'):self.skipTest('Native FFmpeg required')
        with tempfile.TemporaryDirectory() as temporary:
            paths=[]
            for hz in (440,880):
                path=Path(temporary)/(str(hz)+'.wav');paths.append(path)
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency='+str(hz),'-t','0.5',str(path)],capture_output=True,check=True)
            self.assertGreater(compare_audio(paths[0],paths[0],0,.5)['best_correlation'],.99)
            with self.assertRaises(ValueError):compare_audio(paths[0],paths[1],0,.5)


if __name__=='__main__':unittest.main()

class OwnedProcessTests(unittest.TestCase):
    def test_suspended_child_is_assigned_before_running_and_job_always_closed(self):
        from unittest.mock import Mock
        events=[];child=Mock();child.wait.side_effect=lambda **kwargs:events.append('wait') or 0;child.poll.return_value=0
        job=Mock();job.active.return_value=0;job.assign_resume.side_effect=lambda value:events.append('assigned')
        with tempfile.TemporaryDirectory() as temporary,patch('windows_process_group.WindowsJob',return_value=job),patch.object(session.subprocess,'Popen',return_value=child) as spawn:
            self.assertEqual(session.run_child([sys.executable,str(Path(temporary)/'probe.py')],{},temporary),0)
            self.assertEqual(events,['assigned','wait']);job.close.assert_called_once()
            self.assertEqual(spawn.call_args.kwargs['creationflags'],0x204)

    def test_descendant_leak_cannot_be_counted_as_a_clean_run(self):
        from unittest.mock import Mock
        child=Mock();child.wait.return_value=0;child.poll.return_value=0
        job=Mock();job.active.return_value=1
        with tempfile.TemporaryDirectory() as temporary,patch('windows_process_group.WindowsJob',return_value=job),patch.object(session.subprocess,'Popen',return_value=child),patch.object(session.time,'monotonic',side_effect=[0,3]):
            with self.assertRaisesRegex(RuntimeError,'live descendants'):session.run_child([sys.executable,str(Path(temporary)/'probe.py')],{},temporary)
            job.close.assert_called_once()

    def test_spawn_failure_still_closes_owned_job(self):
        from unittest.mock import Mock
        job=Mock()
        with tempfile.TemporaryDirectory() as temporary,patch('windows_process_group.WindowsJob',return_value=job),patch.object(session.subprocess,'Popen',side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):session.run_child([sys.executable,str(Path(temporary)/'probe.py')],{},temporary)
            job.close.assert_called_once()

class SetupWizardTests(unittest.TestCase):
    def test_one_time_setup_merges_profiles_maps_alias_and_reuses_selected_fixture(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle=root/'bundle';bundle.mkdir();(bundle/'manifest.json').write_text('{}')
            profiles=root/'installed.json';profiles.write_text(json.dumps({'version':1,'profiles':{'real-profile':{
                'engine':'instruct_pix2pix','device':'cuda','bundle':str(bundle),'manifest_sha256':sha(bundle/'manifest.json')}}}))
            plan=root/'plan.json';plan.write_text(json.dumps({'version':1,'cases':[{'profile':'alias','sources':['C:/missing/photo.png'],'prompt':'Edit style'},
                {'profile':'alias','sources':['C:/missing/photo.png'],'prompt':'Edit details'}]}))
            photo=root/'photo.png';Image.new('RGB',(64,64),'blue').save(photo)
            ui=Mock();files=SimpleNamespace(askopenfilenames=Mock(return_value=(str(profiles),)),
                askopenfilename=Mock(side_effect=[str(plan),str(photo)]))
            dialog=SimpleNamespace(askstring=Mock(side_effect=['vision:local','real-profile']))
            module=SimpleNamespace(Tk=lambda:ui,filedialog=files,simpledialog=dialog,messagebox=SimpleNamespace(showinfo=Mock()))
            with patch.dict(sys.modules,{'tkinter':module}):config=session.wizard(root/'settings.json')
            self.assertEqual(config['vision_model'],'vision:local')
            cases=json.loads(Path(config['plan']).read_text())['cases']
            self.assertTrue(all(c['profile']=='real-profile' and c['sources']==[str(photo)] for c in cases))
            self.assertEqual(files.askopenfilename.call_count,2);ui.destroy.assert_called_once()
            self.assertEqual(session.read_config(root/'settings.json'),config)


class NativeDiagnosticsTests(unittest.TestCase):
    def test_actual_tool_failure_retains_exit_code_and_bounded_stderr(self):
        import local_media_jobs as jobs
        with self.assertRaises(jobs.LocalToolError) as result:
            jobs._tool([sys.executable,'-c','import sys;sys.stderr.write("codec diagnostic fixture");sys.exit(3)'],lambda:None)
        self.assertEqual(result.exception.public_code,'LOCAL_MEDIA_TOOL_FAILED')
        self.assertEqual(result.exception.diagnostic['exit_code'],3)
        self.assertIn('codec diagnostic fixture',result.exception.diagnostic['stderr'])


class MemoryEvidenceTests(unittest.TestCase):
    def test_unavailable_or_impossible_vram_is_not_an_actual_sample(self):
        self.assertTrue(valid_vram_sample({'exit_code':0,'samples':['GPU-123, NVIDIA GeForce RTX 2080 SUPER, 8192, 2300, 50']}))
        for item in ({'exit_code':1,'samples':['GPU-123, GPU, 8192, 2300, 50']},
            {'exit_code':0,'samples':['GPU-123, GPU, 8192, N/A, 50']},
            {'exit_code':0,'samples':['GPU-123, GPU, 8192, 9000, 50']},
            {'exit_code':0,'samples':[]}):self.assertFalse(valid_vram_sample(item))
