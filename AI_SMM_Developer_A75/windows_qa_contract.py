"""Source QA identity, GPU roles and fail-closed run accounting; no Qt/network."""
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime,timezone

ROLES=('preliminary_3080ti','target_2080super')
SOURCE_REQUIRED=('source_snapshot_integrity','source_ci_contracts','windows_platform','developer_test_source','unit_tests','live_qt_gui',
 'ollama_roundtrip','ollama_structured_json','ollama_constrained_schema','ollama_inventory_map_schema','reviewer_models',
 'live_backend_content_pipeline','independent_semantic_acceptance','live_gui_full_workflow','local_generative_media',
 'media_lifecycle','full_real_task_end_to_end','evidence_integrity')


def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.pending')
    with temp.open('w',encoding='utf-8') as out:
        json.dump(data,out,ensure_ascii=False,indent=2,allow_nan=False);out.flush();os.fsync(out.fileno())
    os.replace(temp,path)


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as source:
        while block:=source.read(1024*1024):value.update(block)
    return value.hexdigest()


def source_snapshot(root):
    root=Path(root);files={}
    paths=list(root.glob('*.py'))+list(root.glob('*.cmd'))+list(root.glob('*.ps1'))
    if (root/'requirements-qa.txt').is_file():paths.append(root/'requirements-qa.txt')
    for name in ('AI_SMM_Work_Architecture_A75','AI_SMM_Developer_A75','ci','tools'):
        paths.extend((root/name).rglob('*'))
    for path in sorted(set(paths)):
        if not path.is_file() or any(p in ('__pycache__','.pytest_cache','VALIDATION_RESULTS','.venv') for p in path.parts):continue
        if path.suffix.lower() not in ('.py','.txt','.json','.cmd','.ini'):continue
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('Source links are not accepted')
        files[path.relative_to(root).as_posix()]=sha(path)
    if not files:raise ValueError('Empty source snapshot')
    fingerprint=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'fingerprint':fingerprint,'files':files}


def verify_snapshot(root,expected):
    actual=source_snapshot(root)
    if actual!=expected:raise ValueError('Source snapshot drift')
    return actual['fingerprint']


def hardware_matches(hardware,role):
    if role not in ROLES or hardware.get('cuda') is not True:return False
    name=re.sub(r'[^A-Z0-9]','',str(hardware.get('name','')).upper())
    capability=hardware.get('capability');total=hardware.get('total')
    if type(total) is not int:return False
    if role=='preliminary_3080ti':return 'RTX3080TI' in name and capability==[8,6] and total>=11*2**30
    return 'RTX2080SUPER' in name and capability==[7,5] and 7.5*2**30<=total<=8.5*2**30


def assess_source_report(report,fingerprint,role):
    if not isinstance(report,dict) or report.get('status')!='PASS':return 'Full source QA did not pass'
    if report.get('qa_role')!=role or report.get('source_qa') is not True or report.get('windows_executed') is not True:return 'Wrong host/role/scope'
    if report.get('attested_source_fingerprint')!=fingerprint:return 'Source fingerprint mismatch'
    if report.get('test_profile')!='full' or report.get('task_seed_replay_requested'):return 'Incomplete or replayed run'
    if type(report.get('task_generation_seed')) is not int:return 'Missing fresh task seed'
    checks=report.get('checks',{})
    for key in SOURCE_REQUIRED:
        if not isinstance(checks.get(key),dict) or checks[key].get('status')!='PASS':return 'Required check not PASS: '+key
    if checks['unit_tests'].get('profile')!='full':return 'Partial developer regression'
    if not hardware_matches(report.get('qa_hardware',{}),role):return 'Actual GPU mismatch'
    if report.get('release_eligible') is not False:return 'Source QA must not certify packaging/release'
    return None


def record_run(state,report,*,fingerprint,role,exit_code):
    """A failure/drift resets the counter; 3080 passes never advance target."""
    if state.get('role')!=role or state.get('fingerprint')!=fingerprint:state.update(clean_consecutive=0,seen_seeds=[])
    state.update(role=role,fingerprint=fingerprint)
    reason=assess_source_report(report,fingerprint,role) if exit_code==0 else 'QA child process failed'
    seed=report.get('task_generation_seed') if isinstance(report,dict) else None
    seen=state.setdefault('seen_seeds',[])
    if reason is None and seed in seen:reason='Repeated task seed'
    if reason is not None:state.update(clean_consecutive=0,status='BLOCKED',reason=reason)
    else:
        seen.append(seed);state['clean_consecutive']=state.get('clean_consecutive',0)+1
        state.update(status='PASS' if role=='preliminary_3080ti' or state['clean_consecutive']>=10 else 'RUNNING',reason=None)
    state['target_clean_consecutive']=state['clean_consecutive'] if role=='target_2080super' else 0
    state['release_eligible']=False
    return reason


def local_only_environment(source,allowed_models):
    result={k:v for k,v in source.items() if not k.upper().startswith(('AI_SMM_','OPENAI_','ANTHROPIC_','GROQ_','KIE_','HF_','OLLAMA_'))
            and not any(token in k.upper() for token in ('API_KEY','PASSWORD','SECRET','ACCESS_TOKEN'))}
    result.update(AI_SMM_QA_LOCAL_ONLY='1',AI_SMM_QA_ALLOWED_MODELS=json.dumps(allowed_models),
       AI_SMM_DIALOGUE_MODE='local',AI_SMM_ENABLE_OPENAI_API='0',AI_SMM_ENABLE_OPENAI_ACCEPTANCE='0',
       AI_SMM_CLOUD_MODELS='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
       HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',ALL_PROXY='http://127.0.0.1:9',
       http_proxy='http://127.0.0.1:9',https_proxy='http://127.0.0.1:9',all_proxy='http://127.0.0.1:9',
       NO_PROXY='localhost,127.0.0.1,::1',no_proxy='localhost,127.0.0.1,::1',PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONFAULTHANDLER='1')
    result.pop('QT_QPA_PLATFORM',None)
    return result


def valid_vram_sample(item):
    import csv
    if not isinstance(item,dict) or item.get('exit_code')!=0:return False
    for line in item.get('samples',[]):
        try:
            fields=next(csv.reader([line]));total=int(fields[2]);used=int(fields[3])
            if len(fields)==5 and fields[0].strip().startswith('GPU-') and total>=6*1024 and 0<=used<=total:return True
        except (ValueError,TypeError,IndexError,StopIteration):continue
    return False
