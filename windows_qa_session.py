#!/usr/bin/env python3
"""One-launch local Windows source QA. Never downloads models or publishes a build."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from windows_qa_contract import (source_snapshot,verify_snapshot,sha,write_json,hardware_matches,
    assess_source_report,record_run,local_only_environment,valid_vram_sample,ROLES)


def output_root():
    # One user-facing report folder on the desktop; existing junctions remain valid.
    return Path(os.environ.get('USERPROFILE',str(Path.home()))) / 'Desktop' / 'AI_SMM_REPORTS'


def model_roster_evidence(inventory,allowed,requested):
    """Explain every Ollama QA selection without guessing missing local tags.

    Names and admission reasons only: no prompts, responses, credentials,
    cloud calls, or automatic installation of excluded local models.
    """
    discovered=list(dict.fromkeys(
        entry['name'] for entry in inventory
        if isinstance(entry,dict) and isinstance(entry.get('name'),str) and entry['name']))
    approved=set(allowed)
    chosen=set(requested or [])
    return {
        'explicit_local_models_selection':requested is not None,
        'discovered_tags':discovered,
        'admitted_local_completion_tags':list(allowed),
        'excluded_tags':[
            {'name':name,'reason':('not_selected_in_settings'
                if requested is not None and name not in chosen
                else 'not_eligible_for_local_completion')}
            for name in discovered if name not in approved],
    }


def read_config(path):
    data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    required={'profiles','plan','vision_model'}
    if not isinstance(data,dict) or not required<=set(data) or set(data)-required-{'preliminary_report','local_models'}:raise ValueError('QA configuration requires profiles, plan, vision_model')
    for name in ('profiles','plan'):
        value=Path(data[name])
        if not value.is_absolute() or not value.is_file():raise ValueError('QA path missing: '+name)
    if not isinstance(data['vision_model'],str) or not data['vision_model']:raise ValueError('Installed local vision model required')
    return data


def wizard(path):
    import tkinter as tk
    from tkinter import filedialog,simpledialog,messagebox
    root=tk.Tk();root.withdraw()
    try:
        messagebox.showinfo('AI SMM · Windows QA','Выберите существующие профили и план с реальными исходниками. Ничего скачиваться не будет. Затем укажите установленную локальную vision-модель Ollama. Все прогоны выполняются автоматически.',parent=root)
        profile_files=filedialog.askopenfilenames(title='Выберите JSON профилей установленных моделей (можно несколько)',filetypes=[('JSON','*.json')],parent=root)
        plan=filedialog.askopenfilename(title='JSON плана приёмки с существующими фото/масками/видео',filetypes=[('JSON','*.json')],parent=root)
        model=simpledialog.askstring('Локальный проверяющий','Точный установленный тег vision-модели Ollama:',parent=root)
        if not profile_files or not plan or not model:raise ValueError('QA setup cancelled; no tests or downloads started')
        from generative_models import profiles as read_profiles
        merged={}
        for filename in profile_files:
            for name,profile in read_profiles({'AI_SMM_GENERATIVE_CONFIG':filename}).items():
                if name in merged and merged[name]!=profile:raise ValueError('Conflicting profile name: '+name)
                merged[name]=profile
        selected=Path(path).parent/'selected_profiles.json';write_json(selected,{'version':1,'profiles':merged})
        chosen_plan=json.loads(Path(plan).read_text(encoding='utf-8-sig'));aliases={};sources={}
        for case in chosen_plan['cases']:
            name=case['profile']
            if name not in merged:
                if name not in aliases:
                    chosen=simpledialog.askstring('Профиль '+name,'Укажите точное имя установленного профиля для '+name+':\n'+'\n'.join(n+' · '+p['engine'] for n,p in merged.items()),parent=root)
                    if chosen not in merged:raise ValueError('QA profile mapping cancelled or invalid')
                    aliases[name]=chosen
                case['profile']=aliases[name]
            resolved=[]
            for filename in case['sources']:
                if not Path(filename).is_file():
                    if filename not in sources:
                        chosen=filedialog.askopenfilename(title='Укажите реальный QA-файл: '+Path(filename).name,parent=root)
                        if not chosen:raise ValueError('QA fixture selection cancelled')
                        sources[filename]=str(Path(chosen).resolve())
                    filename=sources[filename]
                resolved.append(str(Path(filename).resolve()))
            case['sources']=resolved
        selected_plan=Path(path).parent/'selected_plan.json';write_json(selected_plan,chosen_plan)
        data={'profiles':str(selected.resolve()),'plan':str(selected_plan.resolve()),'vision_model':model.strip()}
        write_json(path,data)
        return data
    finally:root.destroy()


def probe_runtime(python,env):
    code='import json,torch,diffusers,transformers,accelerate,safetensors;print(json.dumps({"cuda":torch.cuda.is_available(),"name":torch.cuda.get_device_name(0) if torch.cuda.is_available() else "","capability":list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else [],"total":torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else 0,"torch":torch.__version__,"diffusers":diffusers.__version__,"transformers":transformers.__version__,"accelerate":accelerate.__version__,"safetensors":safetensors.__version__,"device_count":torch.cuda.device_count()}))'
    result=subprocess.run([python,'-c',code],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,env=env)
    if result.returncode:raise RuntimeError('ML runtime preflight failed: '+result.stderr[-2000:])
    return json.loads(result.stdout)


def input_binding(config,profiles):
    plan=json.loads(Path(config['plan']).read_text(encoding='utf-8'))
    cases=[]
    from generative_models import verify_bundle
    from generative_contract import ENGINES
    engines=set()
    for case in plan['cases']:
        profile=profiles[case['profile']];manifest=verify_bundle(profile)
        if profile['device']!='cuda' or 'python' not in profile or manifest['provenance'].get('test_only') or manifest['provenance'].get('fixture') or manifest['provenance'].get('quality_accepted') is False:raise ValueError('Actual installed trained CUDA engine required')
        engines.add(profile['engine'])
        sources=[]
        for filename in case['sources']:
            path=Path(filename)
            if not path.is_absolute() or not path.is_file():raise ValueError('Missing absolute QA source: '+filename)
            sources.append(sha(path))
        cases.append({**{k:v for k,v in case.items() if k not in ('sources','profile')},'sources_sha256':sources,'engine':profile['engine'],'manifest_sha256':profile['manifest_sha256']})
    if set(ENGINES.values())-engines:raise ValueError('Plan must exercise all four engines')
    if not any(c['engine']=='sd15_inpaint' for c in cases) or not any(c['engine']=='animatediff_v2v' and len(c['sources_sha256'])==2 for c in cases):raise ValueError('Plan must exercise masks and video reference')
    return hashlib.sha256(json.dumps(cases,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_preliminary(path,fingerprint,binding):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if set(data)!={'report','input_binding','report_sha256'}:raise ValueError('Invalid 3080 Ti qualification export')
    report=data['report']
    actual=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if actual!=data['report_sha256'] or data['input_binding']!=binding:raise ValueError('Preliminary input/evidence mismatch')
    reason=assess_source_report(report,fingerprint,'preliminary_3080ti')
    if reason:raise ValueError('Preliminary qualification invalid: '+reason)
    return data


def monitor_vram(folder,stop):
    path=Path(folder)/'vram_samples.jsonl'
    with path.open('w',encoding='utf-8') as log:
        while not stop.is_set():
            try:
                result=subprocess.run(['nvidia-smi','--query-gpu=uuid,name,memory.total,memory.used,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=8)
                data={'utc':datetime.now(timezone.utc).isoformat(),'exit_code':result.returncode,'samples':result.stdout.strip().splitlines(),'error':result.stderr[-500:]}
            except Exception as exc:data={'error':type(exc).__name__+': '+str(exc)}
            log.write(json.dumps(data)+'\n');log.flush();stop.wait(1)


def run_child(command,env,folder,timeout=43200):
    from windows_process_group import WindowsJob
    job=WindowsJob();child=None
    try:
        with (Path(folder)/'console.log').open('w',encoding='utf-8') as log:
            child=subprocess.Popen(command,env=env,cwd=Path(command[1]).parent,stdout=log,stderr=subprocess.STDOUT,
                creationflags=0x00000200|0x00000004)
            job.assign_resume(child)
            code=child.wait(timeout=timeout)
            deadline=time.monotonic()+2
            while job.active()!=0 and time.monotonic()<deadline:time.sleep(.05)
            if job.active()!=0:raise RuntimeError('QA process exited with live descendants; owned Job Object will clean them')
            return code
    finally:
        job.close()
        if child is not None and child.poll() is None:
            try:child.terminate()
            except OSError:
                if child.poll() is None:raise
            child.wait(timeout=15)


def execute(config,role,session,state):
    if os.name!='nt':raise RuntimeError('Actual Windows required; Linux does not advance counters')
    if role not in ROLES:raise ValueError('Unknown QA role')
    for module in ('PySide6','PIL','gradio_client','pytest','pytest_subtests','psutil'):
        if importlib.util.find_spec(module) is None:raise RuntimeError('Missing QA dependency: '+module+'; install requirements-qa.txt once')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe') or not shutil.which('nvidia-smi'):raise RuntimeError('FFmpeg, ffprobe and NVIDIA driver/nvidia-smi must already be installed')
    memory=subprocess.run(['nvidia-smi','--query-gpu=uuid,name,memory.total,memory.used,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=15)
    memory_report={'exit_code':memory.returncode,'samples':memory.stdout.strip().splitlines(),'error':memory.stderr[-2000:]}
    write_json(session/'vram_preflight.json',memory_report)
    if not valid_vram_sample(memory_report):raise RuntimeError('Actual NVIDIA VRAM measurements unavailable; see vram_preflight.json')
    encoders=subprocess.run(['ffmpeg','-hide_banner','-encoders'],capture_output=True,text=True,timeout=30)
    (session/'ffmpeg_encoders.txt').write_text(encoders.stdout+'\n'+encoders.stderr,encoding='utf-8')
    if encoders.returncode or not {'libx264','aac'}<={line.split()[1] for line in encoders.stdout.splitlines() if len(line.split())>=2}:raise RuntimeError('Installed FFmpeg must support libx264 and AAC; no download attempted')
    from generative_models import profiles
    from local_media_quality import LocalVision
    reviewer=LocalVision(config['vision_model']);reviewer.installed()
    inventory=reviewer.call('/api/tags')['models']
    requested=config.get('local_models')
    if requested is not None and (not isinstance(requested,list) or not requested or any(not isinstance(m,str) for m in requested)):raise ValueError('local_models must be a nonempty local tag list')
    candidates=requested or [m['name'] for m in inventory]
    allowed=[]
    for model in candidates:
        tag=next((m for m in inventory if m.get('name')==model),None)
        if tag is None:raise ValueError('Requested QA model is not installed')
        details=reviewer.call('/api/show',{'model':model})
        remote=any(tag.get(k) or details.get(k) for k in ('remote_host','remote_model')) or ':cloud' in model.lower()
        if remote or 'completion' not in details.get('capabilities',[]):
            if requested:raise ValueError('Requested QA roster contains cloud/non-completion model: '+model)
            continue
        allowed.append(model)
    if config['vision_model'] not in allowed:raise ValueError('Vision model must be in approved local roster')
    qa_roster=model_roster_evidence(inventory,allowed,requested)
    write_json(session/'qa_model_roster.json',qa_roster)
    env=local_only_environment(os.environ,allowed);env['AI_SMM_GENERATIVE_CONFIG']=config['profiles']
    print('Verifying installed model bundles and fixture hashes (no download)...',flush=True)
    installed=profiles(env)
    if not installed:raise ValueError('No installed generation profiles')
    # Recover from photographs mistakenly selected as inpainting masks without
    # touching the originals. Hash and audit deterministic QA-only PNG masks.
    from qa_fixture_masks import prepare_qa_plan
    config={**config,'plan':prepare_qa_plan(config['plan'],installed,session.parent,session)}
    fingerprint=source_snapshot(ROOT);binding=input_binding(config,installed)
    write_json(session/'source_manifest.json',fingerprint)
    runtimes={}
    for profile in installed.values():
        python=profile.get('python')
        if python not in runtimes:runtimes[python]=probe_runtime(python,env)
        if not hardware_matches(runtimes[python],role):raise ValueError('Actual CUDA GPU does not match selected phase: '+role)
    hardware=next(iter(runtimes.values()))
    roster={m['name']:m['digest'] for m in inventory if m['name'] in allowed}
    runtime_binding=sorted({json.dumps({k:v for k,v in info.items() if k in ('torch','diffusers','transformers','accelerate','safetensors')},sort_keys=True) for info in runtimes.values()})
    def combined_binding():
        if profiles({'AI_SMM_GENERATIVE_CONFIG':config['profiles']})!=installed:raise ValueError('Installed profile configuration changed during QA')
        current_versions=[]
        for python in runtimes:
            result=subprocess.run([python,'-c','from importlib.metadata import version;import json;print(json.dumps({k:version(k) for k in ("torch","diffusers","transformers","accelerate","safetensors")}))'],env=env,capture_output=True,text=True,timeout=30,check=True)
            current_versions.append(json.dumps(json.loads(result.stdout),sort_keys=True))
        if sorted(set(current_versions))!=runtime_binding:raise ValueError('ML dependency versions changed during QA')
        main_versions={name:version(name) for name in ('PySide6','Pillow','gradio_client','pytest','pytest-subtests','psutil')}
        current_inventory=reviewer.call('/api/tags')['models']
        current_roster={m['name']:m['digest'] for m in current_inventory if m['name'] in allowed}
        if current_roster!=roster:raise ValueError('Local model roster changed during QA')
        return hashlib.sha256(json.dumps({'cases':input_binding(config,installed),'roster':roster,'runtimes':runtime_binding,'main_runtime':main_versions},sort_keys=True).encode()).hexdigest()
    binding=combined_binding()
    required_runs=1 if role=='preliminary_3080ti' else 10
    fixture_bytes=sum(Path(p).stat().st_size for case in json.loads(Path(config['plan']).read_text())['cases'] for p in case['sources'])
    estimated=required_runs*(fixture_bytes*3+sum((ROOT/p).stat().st_size for p in fingerprint['files'])*4)+2*2**30
    if shutil.disk_usage(session).free<estimated:raise ValueError('Insufficient QA disk space; estimated bytes: '+str(estimated))
    write_json(session/'preflight.json',{'status':'PASS','hardware':hardware,'runtimes':runtimes,'input_binding':binding,'vision_digest':reviewer.digest,'local_models':allowed,'windows':platform.platform(),'python':sys.version,'ffmpeg':subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,timeout=15).stdout.splitlines()[0],'nvidia_driver':subprocess.run(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'],capture_output=True,text=True,timeout=15).stdout.strip(),'model_downloads':False,'paid_apis_enabled':False})
    if role=='target_2080super':
        preliminary=config.get('preliminary_report')
        if not preliminary:
            import tkinter as tk
            from tkinter import filedialog
            root=tk.Tk();root.withdraw()
            try:preliminary=filedialog.askopenfilename(title='PRELIMINARY_ATTESTATION.json с RTX 3080 Ti',filetypes=[('JSON','*.json')],parent=root)
            finally:root.destroy()
        if not preliminary:raise ValueError('3080 Ti qualification report required before target runs')
        write_json(session/'preliminary_input.json',validate_preliminary(preliminary,fingerprint['fingerprint'],binding))
    write_json(session/'vision_preflight.json',reviewer.qualify(session/'vision_preflight'))
    state.update(fingerprint=fingerprint['fingerprint'],role=role,input_binding=binding)
    required=1 if role=='preliminary_3080ti' else 10
    for index in range(1,required+1):
        run=session/('run_'+str(index).zfill(2));run.mkdir()
        state.update(status='RUNNING',active_run=index)
        write_json(session/'STATUS.json',state);write_json(session.parent/'STATUS.json',state)
        staged=run/'source';staged.mkdir()
        for name in fingerprint['files']:
            source=ROOT/name;target=staged/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        verify_snapshot(staged,fingerprint)
        report_folder=run/'report';report_folder.mkdir()
        qa_roster_folder=report_folder/'qa_roster';qa_roster_folder.mkdir()
        write_json(qa_roster_folder/'qa_model_roster.json',qa_roster)
        from qa_fixture_masks import stage_qa_evidence
        stage_qa_evidence(session,report_folder)
        run_env={**env,'AI_SMM_QA_ROLE':role,'AI_SMM_QA_HARDWARE':json.dumps(hardware),
            'AI_SMM_QA_SNAPSHOT':str(session/'source_manifest.json'),'AI_SMM_QA_RUN_OUTPUT':str(report_folder),
            'AI_SMM_QA_ML_VERSIONS':json.dumps({name:{key:info for key,info in runtimes[profile['python']].items() if key in ('torch','diffusers','transformers','accelerate','safetensors')} for name,profile in installed.items()}),'AI_SMM_QA_VISION_MODEL':config['vision_model'],'AI_SMM_GENERATIVE_ACCEPTANCE_PLAN':config['plan'],
            'AI_SMM_TEST_PROFILE':'full','AI_SMM_TEST_SOURCE':str(staged/'AI_SMM_Developer_A75/tests')}
        # Main text aliases must be local and installed; no stale cloud env.
        text=next((m for m in allowed if m!=config['vision_model']),config['vision_model'])
        run_env.update(AI_SMM_MODEL=text,AI_SMM_LOCAL_DIALOGUE_MODEL=text)
        stop=threading.Event();monitor=threading.Thread(target=monitor_vram,args=(run,stop),daemon=True);monitor.start()
        report={};code=2
        try:
            print('Starting full Windows QA',role,index,'/',required,flush=True)
            code=run_child([sys.executable,str(staged/'AI_SMM_Work_Architecture_A75/windows_acceptance.py'),'--source-qa'],run_env,run)
            report=json.loads((report_folder/'acceptance.json').read_text(encoding='utf-8-sig'))
            verify_snapshot(staged,fingerprint);verify_snapshot(ROOT,fingerprint)
            if combined_binding()!=binding:raise ValueError('Fixtures/model inputs changed during run')
            reviewer.installed()
        except Exception as exc:
            code=2;write_json(run/'runner_error.json',{'error':type(exc).__name__,'reason':str(exc),'traceback':traceback.format_exc()})
        finally:stop.set();monitor.join(timeout=12)
        samples=[json.loads(line) for line in (run/'vram_samples.jsonl').read_text().splitlines()]
        if monitor.is_alive() or not any(valid_vram_sample(item) for item in samples):
            code=2;write_json(run/'vram_error.json',{'error':'No completed actual NVIDIA VRAM samples'})
        reason=record_run(state,report,fingerprint=fingerprint['fingerprint'],role=role,exit_code=code)
        state.setdefault('runs',[]).append({'run':index,'exit_code':code,'reason':reason,'report':str(report_folder/'acceptance.json')})
        write_json(session/'STATUS.json',state);write_json(session.parent/'STATUS.json',state)
        if reason:return 2
        if role=='preliminary_3080ti':
            export={'report':report,'input_binding':binding,'report_sha256':hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
            write_json(session/'PRELIMINARY_ATTESTATION.json',export)
    return 0


def bootstrap_inventory(folder):
    from importlib.metadata import PackageNotFoundError
    dependencies={}
    for name in ('PySide6','Pillow','gradio_client','pytest','pytest-subtests','psutil'):
        try:dependencies[name]=version(name)
        except PackageNotFoundError:dependencies[name]='NOT_INSTALLED'
    host={'platform':platform.platform(),'python':sys.version,'executable':sys.executable,
        'dependencies':dependencies,'tools':{name:shutil.which(name) for name in ('ffmpeg','ffprobe','nvidia-smi')},
        'disk_free_bytes':shutil.disk_usage(folder).free,'windows_executed':os.name=='nt'}
    if os.name=='nt' and host['tools']['nvidia-smi']:
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader'],capture_output=True,text=True,timeout=15)
            host['nvidia_inventory']={'exit_code':result.returncode,'output':result.stdout,'error':result.stderr[-2000:]}
        except Exception as exc:host['nvidia_inventory']={'error':str(exc)}
    write_json(Path(folder)/'HOST.json',host)
    write_json(Path(folder)/'PREREQUISITES.json',{'python':'3.12 x64','main_dependencies':'requirements-qa.txt',
        'existing_generation_engines':['instruct_pix2pix','sd15_ip_adapter','sd15_inpaint','animatediff_v2v'],
        'local_quality_reviewer':'Installed Ollama vision model, must pass pixel controls',
        'local_text_review':'Installed local author and independent reviewer families, must pass existing qualification',
        'fixtures':'docs/WINDOWS_GENERATIVE_PLAN.example.json with real photos, masks and audio/video',
        'automatic_downloads':False,'paid_services':False})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--role',choices=ROLES,required=True);parser.add_argument('--config',type=Path)
    parser.add_argument('--output',type=Path,default=output_root());parser.add_argument('--no-wizard',action='store_true')
    args=parser.parse_args();root=Path(os.path.abspath(args.output));root.mkdir(parents=True,exist_ok=True)
    session=root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+args.role+'_'+uuid.uuid4().hex[:8]);session.mkdir()
    state={'status':'PREPARING','role':args.role,'clean_consecutive':0,'target_clean_consecutive':0,'required':1 if args.role=='preliminary_3080ti' else 10,'release_eligible':False,'session':str(session),'windows_executed':os.name=='nt'}
    write_json(root/'LATEST.json',{'session':str(session),'role':args.role});write_json(session/'STATUS.json',state);write_json(root/'STATUS.json',state)
    config_path=args.config or root/'settings.json'
    try:
        bootstrap_inventory(session)
        if not config_path.is_file() and not args.no_wizard:
            if os.name!='nt':raise RuntimeError('Windows required; setup wizard not opened on Linux')
            wizard(config_path)
        config=read_config(config_path)
        code=execute(config,args.role,session,state)
    except BaseException as exc:
        state.update(status='BLOCKED',clean_consecutive=0,target_clean_consecutive=0,reason=str(exc),error=type(exc).__name__)
        write_json(session/'runner_error.json',{'error':type(exc).__name__,'reason':str(exc),'traceback':traceback.format_exc()});code=2
    finally:
        write_json(session/'STATUS.json',state);write_json(root/'STATUS.json',state)
        # Complete local diagnostics, not a candidate build or release archive.
        files={p.relative_to(session).as_posix():sha(p) for p in session.rglob('*') if p.is_file() and p.name!='REPORT_MANIFEST.json'}
        write_json(session/'REPORT_MANIFEST.json',{'files':files})
        print('QA diagnostics:',session,flush=True)
        print('Status:',state['status'],'target counter:',state['target_clean_consecutive'],'/10',flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
