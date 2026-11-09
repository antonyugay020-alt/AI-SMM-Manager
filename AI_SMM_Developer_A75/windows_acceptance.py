# -*- coding: utf-8 -*-
"""Read-only live Windows acceptance probe. NEVER run against production data."""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error, zipfile, platform, traceback, pathlib, uuid, io, stat, secrets
from datetime import datetime, timezone

from candidate_identity import CANDIDATE_ID, EXPECTED_FOLDER, SOURCE_STAGE
from acceptance_policy import REVIEW_PROBE_OUTPUT_TOKENS
BASE=pathlib.Path(__file__).resolve().parent
SOURCE_QA='--source-qa' in sys.argv
OUT=pathlib.Path(os.environ['AI_SMM_QA_RUN_OUTPUT']).resolve() if SOURCE_QA else BASE/'VALIDATION_RESULTS'
if '--reviewers-only' in sys.argv:OUT=OUT/'REVIEWER_CHECK'
if __name__=='__main__':OUT.mkdir(parents=True,exist_ok=True)

# The fast live profile already performs randomized model workflows and a real
# Qt smoke. Run the regressions for those boundaries here instead of replaying
# the entire historical suite on every iteration. The full profile still runs
# every developer test before its longer all-model/GUI acceptance.
FAST_UNIT_TEST_MODULES=(
    'test_r97_ai_content_authority','test_r96_live_requirement_evidence','test_r95_standalone_binding','test_r94_artifact_binding','test_r93_bound_completion','test_r92_runtime_model_pool','test_r91_result_ownership','test_r90_state_integrity','test_r89_workflow_state','test_r88_contract_storage','test_r87_action_contract','test_r86_semantic_dialogue','test_assertion_scope', 'test_a11_unknowns', 'test_a58_shared_publication_schema',
    'test_acceptance_policy',
    'test_reviewer_probe_recovery', 'test_a60_typed_assertion_inventory',
    'test_random_acceptance_tasks',
    'test_generic_text_review', 'test_claim_support', 'test_production_pipeline',
    'test_a35_model_gateway', 'test_semantic_editor_independence',
    'test_task_scope_binding', 'test_candidate_identity',
    'test_windows_acceptance_v2', 'test_windows_acceptance_v4',
    'test_windows_acceptance_v5', 'test_windows_acceptance_v6',
    'test_r40_universal_planning_contract', 'test_a67_architecture_regressions',
    'test_r52_response_contracts',
    'test_r53_source_bound_review',
    'test_r54_source_audit_and_planning',
    'test_r55_bounded_delivery',
    'test_text_workflow_architecture', 'test_r57_evidence_delivery', 'test_r58_shared_recovery', 'test_r59_field_review_protocol', 'test_r60_revision_capacity', 'test_r61_review_chain', 'test_r62_review_boundaries', 'test_r63_review_scope', 'test_r65_review_evidence', 'test_r65_distribution_guard', 'test_r66_editorial_authority', 'test_r67_review_scope', 'test_r68_repair_authority', 'test_r69_reviewer_qualification', 'test_r70_independent_roles', 'test_r71_evidence_repair', 'test_r72_finding_transport', 'test_r73_bounded_acceptance', 'test_r74_communication_scope', 'test_r75_public_scope', 'test_r76_document_contracts', 'test_r77_host_scope', 'test_r78_artifact_boundaries', 'test_r79_audit_workflows', 'test_r80_task_lifecycle', 'test_r81_typed_windows', 'test_r82_substance_delivery', 'test_r83_revision_flow', 'test_r85_support_report', 'test_strategy_draft_schema',
    'test_acceptance_evidence_v3', 'test_v9_document_repair',
    'test_stage69_scope', 'test_stage75_orchestration',
)
EDITORIAL_DIMENSION_COUNT=4
# Standalone authors are selected from this same pool. Their own family is
# excluded; reviewer capacity is checked separately from author compatibility.
from model_roles import MIN_REVIEWER_FAMILIES, authors_with_review_capacity
MIN_ACCEPTANCE_REVIEWER_POOL=MIN_REVIEWER_FAMILIES


def unit_test_selection(profile):
    """Return the explicit regression slice for fast, or None for full discovery."""
    return FAST_UNIT_TEST_MODULES if str(profile).strip().casefold()=='fast' else None


def workflow_coverage_errors(report):
    """Validate task and author counts independently; one author may do two tasks."""
    if not isinstance(report,dict):return ['invalid workflow report']
    cases=report.get('cases');meta=report.get('task_generation');comparison=report.get('model_comparison')
    if (not isinstance(cases,list) or not isinstance(meta,dict)
            or not isinstance(comparison,list)):return ['invalid workflow report shape']
    authors=meta.get('author_models_tested');declared=meta.get('case_count')
    author_count=meta.get('selected_author_model_count')
    errors=[]
    if type(declared) is not int or declared<1 or len(cases)!=declared:
        errors.append('case count does not match declared task count')
    if (not isinstance(authors,list) or not authors
            or any(not isinstance(a,str) or not a.strip() for a in authors)
            or len(set(authors))!=len(authors)
            or type(author_count) is not int or author_count!=len(authors)):
        return errors+['selected author count does not match distinct tested authors']
    if (any(not isinstance(c,dict) or not isinstance(c.get('author_model'),str) for c in cases)
            or {c.get('author_model') for c in cases}!=set(authors)):
        errors.append('case authors do not match tested models')
    names=[c.get('name') if isinstance(c,dict) else None for c in cases]
    if (any(not isinstance(n,str) or not n.strip() for n in names)
            or len(set(names))!=len(names)):
        errors.append('case identities are missing or duplicated')
    if (len(comparison)!=author_count or any(not isinstance(c,dict) or not isinstance(c.get('model'),str) for c in comparison)
            or {c.get('model') for c in comparison}!=set(authors)):
        errors.append('model comparison does not match tested models')
    return errors


def _test_source_ignore(profile):
    selected=unit_test_selection(profile)
    if selected is None:
        return None
    keep={name+'.py' for name in selected}
    def ignore(_directory,names):
        return {name for name in names if name.startswith('test_') and name.endswith('.py')
                and name not in keep}
    return ignore


def find_developer_test_source(base_dir=BASE, version='A75', override=None):
    """Find tests in an extracted developer kit or safely unpack its adjacent ZIP into TEMP."""
    def usable(folder):
        return folder.is_dir() and (folder/'a2_recovery_fixture.json').is_file() and any(folder.glob('test_*.py'))
    candidates=[]
    if override:
        candidates.append(pathlib.Path(override).expanduser().resolve())
    candidates.append(base_dir.parent/f'AI_SMM_Developer_{version}'/'tests')
    for parent in sorted(base_dir.parent.glob(f'AI_SMM_Developer_{version}*')):
        if parent.is_dir():
            candidates.extend(sorted(parent.rglob('tests')))
    for folder in candidates:
        if usable(folder): return folder.resolve(),None,'developer_folder'
    for archive_path in sorted(base_dir.parent.glob(f'AI_SMM_Developer_{version}*.zip')):
        temp=None
        try:
            with zipfile.ZipFile(archive_path) as outer:
                nested=next((n for n in outer.namelist() if n.endswith(f'AI_SMM_Developer_{version}/_tests_source.zip')),None)
                if nested is None: continue
                temp=tempfile.TemporaryDirectory(prefix='AI_SMM_EXTERNAL_TESTS_')
                test_root=pathlib.Path(temp.name)
                with zipfile.ZipFile(io.BytesIO(outer.read(nested))) as inner:
                    for info in inner.infolist():
                        path=pathlib.PurePosixPath(info.filename)
                        mode=(info.external_attr>>16)&0xFFFF
                        if (path.is_absolute() or '..' in path.parts or (mode and stat.S_ISLNK(mode))
                                or not path.parts or path.parts[0]!='tests'
                                or pathlib.Path(info.filename).suffix.lower() not in ('.py','.json')):
                            raise ValueError('unsafe developer test archive path')
                        target=test_root.joinpath(*path.parts)
                        if info.is_dir(): target.mkdir(parents=True,exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True,exist_ok=True)
                            target.write_bytes(inner.read(info.filename))
                extracted=test_root/'tests'
                if usable(extracted): return extracted,temp,'developer_zip'
                temp.cleanup()
        except (OSError,zipfile.BadZipFile,ValueError):
            if temp is not None: temp.cleanup()
            continue
    return None,None,'missing'
# Only the supervisor clears prior evidence. The GUI child imports this module
# and must NEVER erase unit test logs already written by its parent.
if __name__=='__main__' and '--gui-child' not in sys.argv:
    for stale in OUT.glob('*.log'):
        stale.unlink()
    for stale in OUT.glob('*.json'):
        stale.unlink()

def record(name, ok, **details):
    value={'status':'PASS' if ok is True else ('FAIL' if ok is False else 'NOT_TESTED'),**details}
    RESULT['checks'][name]=value
    print(f'{name}: {value["status"]}',flush=True)


def acceptance_status(checks, required):
    """Report absence of evidence as blocked, never as a test failure or pass."""
    return 'PASS' if all(isinstance(checks.get(key),dict)
                         and checks[key].get('status')=='PASS' for key in required) else 'BLOCKED'

def live_acceptance_ready(checks):
    """Keep expensive generation out of runs with an unfit reviewer pool."""
    required=('ollama_roundtrip','ollama_structured_json','ollama_constrained_schema',
              'ollama_inventory_map_schema','reviewer_models')
    cloud_models=os.environ.get('AI_SMM_CLOUD_MODELS','').replace(';',',').split(',')
    openai_requested=(os.environ.get('AI_SMM_ENABLE_OPENAI_API','').strip()=='1' and
                      os.environ.get('AI_SMM_ENABLE_OPENAI_ACCEPTANCE','').strip()=='1' and
                      any(item.strip().casefold().startswith('openai/') for item in cloud_models))
    if openai_requested:
        required=required+('openai_api_preflight',)
    return all(isinstance(checks.get(name),dict) and checks[name].get('status')=='PASS'
               for name in required)


def openai_api_preflight():
    """Make one tiny, explicit provider smoke call before costly generation."""
    from text_providers import configured_cloud_models, call_text
    models=[name for name in configured_cloud_models() if name.casefold().startswith('openai/')]
    if not models:
        raw=[item.strip() for item in os.environ.get('AI_SMM_CLOUD_MODELS','').replace(';',',').split(',')]
        requested=(os.environ.get('AI_SMM_ENABLE_OPENAI_API','').strip()=='1' and
                   os.environ.get('AI_SMM_ENABLE_OPENAI_ACCEPTANCE','').strip()=='1' and
                   any(item.casefold().startswith('openai/') for item in raw))
        if requested:
            record('openai_api_preflight',False,provider='openai',
                   error='OpenAI модель не допущена к тесту: проверьте настройки и доступность ключа')
        return
    model=models[0]
    started=time.monotonic()
    try:
        result=call_text(model,[{'role':'user','content':'Ответь одним словом: ГОТОВО'}],
                         timeout=35,temperature=0.0)
        message=result.get('message',{})
        answer=str(message.get('content') or '').strip() if isinstance(message,dict) else ''
        usage=result.get('usage',{})
        record('openai_api_preflight',bool(answer),provider='openai',model=model,
               duration_s=round(time.monotonic()-started,2),response_received=bool(answer),
               usage=usage if isinstance(usage,dict) else {},
               billing_note='one short API generation request')
    except Exception as exc:
        record('openai_api_preflight',False,provider='openai',model=model,
               duration_s=round(time.monotonic()-started,2),
               error=type(exc).__name__+': '+str(exc)[:240],
               billing_note='failed before generated output; provider may still record a rejected request')

def format_process_log(returncode, stdout, stderr):
    """Keep complete child output so failures remain diagnosable after upload."""
    return (f'returncode: {returncode}\n'
            f'stdout_bytes: {len(stdout.encode("utf-8",errors="replace"))}\n'
            f'stderr_bytes: {len(stderr.encode("utf-8",errors="replace"))}\n'
            '----- stdout -----\n'+stdout+'\n----- stderr -----\n'+stderr)

def parse_pytest_completion(stdout, returncode):
    """Read a completed pytest summary, including Windows skips and long durations.

    Never infer PASS from progress percentages, a partial summary, or exit code
    alone. The summary can include skips between passes and subtests, e.g.
    '512 passed, 2 skipped, 240 subtests passed in 29.48s'.
    """
    result = {'success': False, 'passed': 0, 'skipped': 0, 'subtests': 0}
    # Capture only a terminal summary, not intermediate progress or chatter.
    # Parenthesized HH:MM:SS timings occur with pytest on long Windows runs.
    for raw_line in reversed(str(stdout).splitlines()):
        line = re.sub(r'\x1b\[[0-9;]*m', '', raw_line).strip()
        if not re.search(r'\bin\s+\d+(?:\.\d+)?s(?:\s+\(\d+(?::\d+){1,2}\))?$', line):
            continue
        counts = {}
        for match in re.finditer(
            r'(?<!\w)(\d+)\s+(subtests passed|passed|failed|errors?|skipped|xfailed|xpassed)\b', line
        ):
            counts[match.group(2)] = int(match.group(1))
        if 'passed' not in counts:
            return result
        result['passed'] = counts['passed']
        result['skipped'] = counts.get('skipped', 0)
        result['subtests'] = counts.get('subtests passed', 0)
        result['success'] = (
            type(returncode) is int and returncode == 0
            and result['passed'] > 0
            and counts.get('failed', 0) == 0
            and counts.get('error', 0) == 0
            and counts.get('errors', 0) == 0
            and counts.get('xpassed', 0) == 0
        )
        return result
    return result


def run_isolated_python_regression(test_dir, environment, profile, shard_count=4):
    """Run every discovered test module once, in bounded isolated groups.

    A monolithic unittest discover process can hang even after many tests pass.
    A group must produce a real pytest summary and exit zero to count as PASS.
    """
    import concurrent.futures
    import re
    modules=sorted(pathlib.Path(test_dir).glob('test_*.py'))
    if len(modules) < (263 if profile == 'full' else 1):
        return {'status':'FAIL','reason':'Incomplete Python test module inventory',
                'module_count':len(modules),'shards':[]}
    count=min(shard_count,len(modules))
    shards=[modules[i::count] for i in range(count)]
    coverage=[str(p) for group in shards for p in group]
    if len(coverage)!=len(set(coverage)) or set(coverage)!={str(p) for p in modules}:
        return {'status':'FAIL','reason':'Test shard inventory incomplete or duplicated',
                'module_count':len(modules),'shards':[]}
    isolated_root=pathlib.Path(test_dir).parent.parent
    isolated_app=isolated_root/'AI_SMM_Work_Architecture_A75'
    if not (isolated_app/'app.py').is_file():
        return {'status':'FAIL','reason':'Isolated application source is missing',
                'module_count':len(modules),'shards':[]}
    pythonpath=os.pathsep.join([str(pathlib.Path(test_dir).parent/'application'),str(test_dir),str(isolated_app)])
    test_env=dict(environment,PYTHONPATH=pythonpath)
    def execute(index, group):
        log_name=f'unit_tests_shard_{index:02d}.log'
        command=[sys.executable,'-X','utf8','-m','pytest','-q','--import-mode=importlib',
                 '-p','no:cacheprovider','--disable-warnings','--tb=short',
                 *[str(p) for p in group]]
        try:
            proc=subprocess.run(command,cwd=isolated_root,env=test_env,capture_output=True,
                                text=True,encoding='utf-8',errors='replace',timeout=600,check=False)
            body=format_process_log(proc.returncode,proc.stdout,proc.stderr)
            (OUT/log_name).write_text(body,encoding='utf-8')
            parsed=parse_pytest_completion(proc.stdout, proc.returncode)
            return {'shard':index,'modules':len(group),
                    'status':'PASS' if parsed['success'] else 'FAIL',
                    'exit_code':proc.returncode,'passed':parsed['passed'],
                    'skipped':parsed['skipped'],'subtests':parsed['subtests'],
                    'log':log_name}
        except BaseException as exc:
            (OUT/log_name).write_text(traceback.format_exc(),encoding='utf-8')
            return {'shard':index,'modules':len(group),'status':'FAIL',
                    'reason':type(exc).__name__+': '+str(exc),'log':log_name}
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        futures=[pool.submit(execute,i+1,group) for i,group in enumerate(shards)]
        results=sorted((f.result() for f in futures),key=lambda r:r['shard'])
    valid=(len(results)==count and sum(r['modules'] for r in results)==len(modules)
           and all(r['status']=='PASS' for r in results))
    return {'status':'PASS' if valid else 'FAIL','module_count':len(modules),
            'passed':sum(r.get('passed',0) for r in results),
            'subtests':sum(r.get('subtests',0) for r in results),'shards':results}


def child_run(name, args, timeout, root):
    env=os.environ.copy();env['AI_SMM_TEST_ROOT']=str(root)
    env['PYTHONIOENCODING']='utf-8'
    started=time.monotonic()
    try:
        p=subprocess.run(args,cwd=BASE,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        (OUT/(name+'.log')).write_text(format_process_log(p.returncode,p.stdout,p.stderr),encoding='utf-8')
        if name=='live_gui_full_workflow':
            evidence=pathlib.Path(root)/'gui_workflow_diagnostics.json'
            if evidence.is_file():
                shutil.copy2(evidence,OUT/evidence.name)
        record(name,p.returncode==0,returncode=p.returncode,log=f'{name}.log')
    except subprocess.TimeoutExpired as e:
        def decoded(value):
            return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else str(value or '')
        details=format_process_log('TIMEOUT',decoded(e.stdout),decoded(e.stderr))+'\n'+str(e)
        (OUT/(name+'.log')).write_text(details,encoding='utf-8')
        if name=='live_gui_full_workflow':
            evidence=pathlib.Path(root)/'gui_workflow_diagnostics.json'
            if evidence.is_file():
                shutil.copy2(evidence,OUT/evidence.name)
        if name=='independent_semantic_acceptance':
            from durable_json import atomic_write_json
            destination=OUT/'independent_editorial_results.json'
            try:partial=json.loads(destination.read_text(encoding='utf-8'))
            except (OSError,ValueError):partial={'cases':[],'active_stage':'before_first_checkpoint'}
            if not isinstance(partial,dict):partial={'cases':[],'active_stage':'invalid_checkpoint'}
            partial.update(status='BLOCKED',review_state='timed_out',finished=False,
                           timeout_s=timeout,error=type(e).__name__+': '+str(e))
            atomic_write_json(destination,partial)
        record(name,False,error=str(e),log=f'{name}.log',timed_out=True,
               timeout_s=timeout,duration_s=round(time.monotonic()-started,2))
    except Exception as e:
        (OUT/(name+'.log')).write_text(str(e),encoding='utf-8')
        record(name,False,error=str(e),log=f'{name}.log')
    finally:
        if SOURCE_QA and name in ('live_qt_gui','live_backend_content_pipeline','live_gui_full_workflow'):
            shutil.copytree(root,OUT/'retained_artifacts'/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.pytest_cache'))

def ollama_probe():
    """Two real model calls, no user data. Record safe metadata, never prompts."""
    import time
    model='qwen3:8b'
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=12) as response:
            version=json.load(response).get('version','unknown')
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=12) as response:
            tags=json.load(response)
        names=[str(m.get('name','')) for m in tags.get('models',[]) if isinstance(m,dict)]
        record('ollama_server',True,version=str(version),model_count=len(names))
        if model not in names:
            record('ollama_roundtrip',False,reason='Не установлена модель qwen3:8b',models=names[:20]);return
        def call(messages, *, json_format=False, predict=160):
            # qwen3 may spend all of a 25-token budget on internal thinking; explicitly disable it.
            body={'model':model,'stream':False,'think':False,
                  'messages':messages,'options':{'num_predict':predict,'temperature':0}}
            if json_format:body['format']='json'
            req=urllib.request.Request('http://127.0.0.1:11434/api/chat',
                  data=json.dumps(body,ensure_ascii=False).encode('utf-8'),
                  headers={'Content-Type':'application/json'},method='POST')
            started=time.monotonic()
            with urllib.request.urlopen(req,timeout=240) as response:
                answer=json.load(response)
            msg=answer.get('message') if isinstance(answer,dict) else None
            content=str(msg.get('content') or '').strip() if isinstance(msg,dict) else ''
            meta={'duration_s':round(time.monotonic()-started,1),
                  'done_reason':str(answer.get('done_reason','')) if isinstance(answer,dict) else 'invalid',
                  'content_length':len(content),
                  'thinking_length':len(str(msg.get('thinking') or '')) if isinstance(msg,dict) else 0,
                  'eval_count':answer.get('eval_count') if isinstance(answer,dict) else None}
            return content,meta
        answer,meta=call([{'role':'user','content':'Ответь одним словом: ГОТОВО'}])
        record('ollama_roundtrip',bool(answer),model=model,response_received=bool(answer),**meta)
        if not answer:return
        structured,meta=call([{'role':'user','content':'Верни JSON-объект c ключами action и answer. Значения: respond и готово. Без дополнительного текста.'}],json_format=True,predict=160)
        try:
            parsed=json.loads(structured)
            valid=isinstance(parsed,dict) and parsed.get('action')=='respond' and bool(parsed.get('answer'))
        except (ValueError,TypeError):valid=False
        record('ollama_structured_json',valid,model=model,**meta)
        # V6's independent editor relies on constrained JSON Schema with IDs
        # supplied by the host. Check actual Ollama schema support separately.
        schema={'type':'object','additionalProperties':False,
                'properties':{'pass':{'type':'boolean'},
                              'issues':{'type':'array','items':{'type':'object',
                                  'properties':{'evidence_id':{'type':'string','enum':['strategy:0001']}},
                                  'required':['evidence_id']}}},
                'required':['pass','issues']}
        req=urllib.request.Request('http://127.0.0.1:11434/api/chat',
            data=json.dumps({'model':model,'stream':False,'think':False,'format':schema,
                 'messages':[{'role':'user','content':
                     'Верни pass=true и пустой список issues. Ответ только JSON.'}],
                 'options':{'temperature':0,'num_predict':150}},ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=240) as reply: sample=json.load(reply)
            obj=json.loads(sample['message']['content'])
            schema_ok=isinstance(obj,dict) and type(obj.get('pass')) is bool and isinstance(obj.get('issues'),list)
            record('ollama_constrained_schema',schema_ok,model=model)
        except Exception as schema_exc:
            record('ollama_constrained_schema',False,model=model,
                   error=type(schema_exc).__name__+': '+str(schema_exc)[:320])
        # A41's claim-inventory contract uses required object properties rather
        # than an array of IDs, whose JSON Schema cannot enforce uniqueness.
        inventory_ids=['strategy:0001','strategy:0002','calendar:0001']
        inventory_schema={'type':'object','additionalProperties':False,
            'properties':{'segment_decisions':{'type':'object','additionalProperties':False,
                'properties':{ident:{'type':'string','enum':['business_claim','not_business_claim']}
                              for ident in inventory_ids},
                'required':inventory_ids}},
            'required':['segment_decisions']}
        req=urllib.request.Request('http://127.0.0.1:11434/api/chat',
            data=json.dumps({'model':model,'stream':False,'think':False,'format':inventory_schema,
                 'messages':[{'role':'user','content':
                     'Заполни каждый ключ segment_decisions значением not_business_claim. Только JSON.'}],
                 'options':{'temperature':0,'num_predict':180}},ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=240) as reply: sample=json.load(reply)
            obj=json.loads(sample['message']['content'])
            decisions=obj.get('segment_decisions') if isinstance(obj,dict) else None
            map_ok=(isinstance(decisions,dict) and set(decisions)==set(inventory_ids)
                    and all(value=='not_business_claim' for value in decisions.values()))
            record('ollama_inventory_map_schema',map_ok,model=model,
                   expected_keys=len(inventory_ids),received_keys=len(decisions) if isinstance(decisions,dict) else 0)
        except Exception as schema_exc:
            record('ollama_inventory_map_schema',False,model=model,
                   error=type(schema_exc).__name__+': '+str(schema_exc)[:320])
    except urllib.error.HTTPError as e:
        record('ollama_roundtrip',False,reason='HTTP-ошибка Ollama',http_status=e.code,model=model)
    except Exception as e:
        record('ollama_roundtrip',False,error=type(e).__name__+': '+str(e),model=model)

def reviewer_model_probe():
    """One randomized six-control batch per family, before generated workflows.

    Author JSON compatibility and reviewer reasoning are recorded separately.
    A model failing a known-answer control cannot judge user content.
    """
    from generic_text_review import _model_family, list_installed_ollama_models
    from review_qualification import probe_qualification, qualification_envelope, CERT_ENV
    try:
        installed=list_installed_ollama_models()
    except Exception as exc:
        record('reviewer_models',False,error=type(exc).__name__+': '+str(exc)[:320])
        return False
    # Qualify every installed model, not just the first representative of a family.
    # Independence is still enforced by counting distinct passing families below.
    family_models={}
    for name in installed:
        family_models.setdefault(_model_family(name),[]).append(name)
    eligible=[];details=[];author_models=[]
    for family,models in family_models.items():
        family_results=[]
        for model in models:
            try:
                result=probe_qualification(model,force=True)
            except Exception as exc:
                # A failing candidate must not prevent probing other candidates.
                result={'model':model,'pass':False,'author_response_compatible':False,
                        'error':type(exc).__name__+': '+str(exc)[:400]}
            details.append({**result,'family':family})
            family_results.append(result)
            if result.get('author_response_compatible') is True:author_models.append(model)
            if result['pass']:eligible.append(model)
        record('reviewer_capability_'+re.sub(r'[^a-z0-9_-]+','_',family.casefold()),
            any(result['pass'] for result in family_results),
            models=family_results,model_count=len(models))
    # API models require the user's explicit provider preflight; they undergo
    # the same qualification instead of being admitted on a short echo alone.
    if (RESULT['checks'].get('openai_api_preflight',{}).get('status')=='PASS'
            and os.environ.get('AI_SMM_ENABLE_OPENAI_ACCEPTANCE','').strip()=='1'):
        from text_providers import configured_cloud_models
        try:
            cloud_candidates=configured_cloud_models()
        except Exception as exc:
            record('reviewer_cloud_inventory',False,
                   error=type(exc).__name__+': '+str(exc)[:400])
            cloud_candidates=[]
        for model in cloud_candidates:
            if '/' not in model or any(model in names for names in family_models.values()):continue
            try:
                result=probe_qualification(model,force=True)
            except Exception as exc:
                result={'model':model,'pass':False,'author_response_compatible':False,
                        'error':type(exc).__name__+': '+str(exc)[:400]}
            details.append(result)
            if result.get('author_response_compatible') is True:author_models.append(model)
            if result.get('pass') is True:eligible.append(model)
    os.environ[CERT_ENV]=json.dumps(qualification_envelope(details),ensure_ascii=False)
    os.environ['AI_SMM_ACCEPTANCE_REVIEWER_MODELS']=json.dumps(eligible,ensure_ascii=False)
    os.environ['AI_SMM_ACCEPTANCE_AUTHOR_MODELS']=json.dumps(author_models,ensure_ascii=False)
    viable_authors,deferred=authors_with_review_capacity(author_models,eligible)
    passed=(len({_model_family(name) for name in eligible})>=MIN_ACCEPTANCE_REVIEWER_POOL and bool(viable_authors))
    record('reviewer_models',passed,calibrated=len(details),eligible=len(eligible),
        reviewer_models=eligible,details=details,author_models=author_models,
        workflow_author_models=viable_authors,author_models_deferred_for_review_capacity=deferred,
        excluded_author_models=[name for name in installed if name not in author_models],
        minimum_reviewer_pool=MIN_ACCEPTANCE_REVIEWER_POOL,
        qualification='Six randomized known-answer scope/source/literal controls per model; distinct passing families required',
        reason=None if passed else (
            f'Нужны {MIN_ACCEPTANCE_REVIEWER_POOL} квалифицированных семейств редакторов помимо хотя бы одного автора; генерация пропущена, '
            'чтобы не оценивать тексты неподготовленным редактором'))
    return passed

def reviewer_check_only():
    """Fast competence report; no unit campaign, GUI, or authored content."""
    from release_guard import verify_tree
    from durable_json import atomic_write_json
    RESULT.update(scope='reviewer_qualification_only',content_generation='NOT_RUN',
                  unit_tests='NOT_RUN',full_gui='NOT_RUN')
    try:
        verified=verify_tree(BASE)
        record('package_integrity',True,source_fingerprint=verified['source_fingerprint'])
        RESULT['status']='PASS' if reviewer_model_probe() else 'BLOCKED'
    except Exception as exc:
        RESULT.update(status='BLOCKED',error=type(exc).__name__+': '+str(exc)[:500])
    OUT.mkdir(parents=True,exist_ok=True)
    report=OUT/'reviewer_qualification.json'
    atomic_write_json(report,RESULT)
    archive=OUT.parent/'REVIEWER_CHECK.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.write(report,'REVIEWER_CHECK/reviewer_qualification.json')
    print('SEND THIS FILE: '+str(archive),flush=True)
    return 0 if RESULT['status']=='PASS' else 2


RESULT={'build':CANDIDATE_ID, 'source_stage':SOURCE_STAGE, 'runner_path':str(pathlib.Path(__file__).resolve()),
        'timestamp':datetime.now(timezone.utc).isoformat(),
        'os':platform.platform(),'python':platform.python_version(),
        'checks':{},'status':'BLOCKED'}

if __name__ == '__main__' and BASE.name != EXPECTED_FOLDER:
    raise SystemExit('BLOCKED: неправильная папка, ожидалась '+EXPECTED_FOLDER+'; текущая '+str(BASE))

if '--gui-child' in sys.argv:
    try:
        from PySide6.QtWidgets import QApplication
        import app as appmod
        from uuid import uuid4
        qapp=QApplication.instance() or QApplication([])
        win=appmod.Studio()
        win.show()
        for _ in range(8): qapp.processEvents()
        assert win.windowTitle() and win.nav_buttons
        assert all(k in win.nav_buttons for k in ('overview','strategy','calendar','content','publishing','analytics'))
        a,b=uuid4().hex,uuid4().hex
        # The E2E harness must use the exact application project registration
        # contract, including durable project_locations and folder preflight.
        win.core.projects.create(appmod.storage_base(),'QA Project A','Test','A',appmod.now(),project_id=a)
        win.core.projects.create(appmod.storage_base(),'QA Project B','Test','B',appmod.now(),project_id=b)
        win.db.execute('INSERT INTO manager_documents VALUES (?,?,?,?,?,?,?)',
                       (uuid4().hex,a,'strategy','QA Doc A','A private content',appmod.now(),appmod.now()))
        win.db.execute('INSERT INTO manager_documents VALUES (?,?,?,?,?,?,?)',
                       (uuid4().hex,b,'strategy','QA Doc B','B private content',appmod.now(),appmod.now()))
        win.db.commit()
        win.refresh_projects(a)
        win.show_section('strategy');qapp.processEvents()
        assert win.current_project==a and win.document_list.count()==1
        assert win.document_list.item(0).text()=='QA Doc A'
        win.refresh_projects(b)
        win.show_section('strategy');qapp.processEvents()
        assert win.current_project==b and win.document_list.count()==1
        assert win.document_list.item(0).text()=='QA Doc B'
        assert not appmod.mentions_reference('Редактируй только выбранное фото')
        assert appmod.mentions_reference('Используй эталон')
        # Real PDF generation by Qt in a disposable, separate project.
        pdf_paths=appmod.export_document_files(b,uuid4().hex,'strategy','Qt PDF Smoke','PDF export smoke test',workspace_root=win.core.workspace(b))
        assert pdf_paths[1].exists() and pdf_paths[1].read_bytes().startswith(b'%PDF')
        win.close(); win.db.close()
        qapp.processEvents()
        print('GUI_SMOKE_PASS: project isolation, Qt launch, navigation, PDF, reference opt-in',flush=True)
    except BaseException:
        traceback.print_exc();sys.exit(2)
    sys.exit(0)

if __name__=='__main__':
    if '--reviewers-only' in sys.argv:
        raise SystemExit(reviewer_check_only())
    try:
        seed_text=os.environ.get('AI_SMM_REPLAY_SEED','').strip()
        run_seed=int(seed_text) if seed_text else secrets.randbits(128)
        os.environ['AI_SMM_ACCEPTANCE_SEED']=str(run_seed)
        RESULT['task_generation_seed']=run_seed
        RESULT['task_seed_replay_requested']=bool(seed_text)
        # A replay seed is opt-in. An inherited seed from an earlier QA run
        # must never silently force the next run to repeat the same tasks.
        profile=os.environ.get('AI_SMM_TEST_PROFILE','full').strip().casefold()
        if profile not in ('fast','full'):
            profile='full'
        RESULT['test_profile']=profile
        # Run package integrity before importing the application or invoking Ollama.
        # This check is tied to the exact files extracted from the shipped ZIP.
        if SOURCE_QA:
            from windows_qa_contract import verify_snapshot
            snapshot=json.loads(pathlib.Path(os.environ['AI_SMM_QA_SNAPSHOT']).read_text(encoding='utf-8'))
            fingerprint=verify_snapshot(BASE.parent,snapshot)
            record('source_snapshot_integrity',True,source_fingerprint=fingerprint)
            RESULT.update(source_qa=True,release_eligible=False,windows_executed=os.name=='nt',qa_role=os.environ['AI_SMM_QA_ROLE'],qa_hardware=json.loads(os.environ['AI_SMM_QA_HARDWARE']))
        else:
            from release_guard import verify_tree, PackagingBlocked
            try:
                manifest_result=verify_tree(BASE)
                record('package_integrity',True,source_fingerprint=manifest_result['source_fingerprint'],candidate_id=CANDIDATE_ID,actual_root=str(BASE),verified_files=manifest_result['verified_files'])
            except PackagingBlocked as e:
                record('package_integrity',False,reason=str(e));raise
        if os.name!='nt':
            record('windows_platform',False,reason='Нужен запуск на Windows')
        else:
            record('windows_platform',True)
        test_source,test_source_temp,test_source_origin=find_developer_test_source(
            BASE,version='A75',override=os.environ.get('AI_SMM_TEST_SOURCE'))
        if test_source:
            os.environ['AI_SMM_TEST_SOURCE']=str(test_source)
            record('developer_test_source',True,source=test_source_origin)
        else:
            record('developer_test_source',None,error='Комплект разработчика не найден рядом с кандидатом')
        with tempfile.TemporaryDirectory(prefix='AI_SMM_ISOLATED_TEST_') as t:
            root=pathlib.Path(t)
            envroot=pathlib.Path(t)/'user_data';envroot.mkdir(exist_ok=True)
            if not test_source:
                record('unit_tests',None,error='Юнит-тесты не запускались: отсутствует внешний комплект разработчика')
            else:
                selected_tests=unit_test_selection(profile)
                missing_tests=([name for name in selected_tests
                                if not (test_source/(name+'.py')).is_file()]
                               if selected_tests is not None else [])
                isolated_tests=root/'AI_SMM_Developer_A75'/'tests'
                isolated_app=root/'AI_SMM_Work_Architecture_A75'
                shutil.copytree(test_source,isolated_tests,dirs_exist_ok=True,
                                ignore=_test_source_ignore(profile))
                shutil.copytree(BASE,isolated_app,ignore=shutil.ignore_patterns(
                    '__pycache__','.pytest_cache','VALIDATION_RESULTS','VALIDATION_RESULTS.zip',
                    '*.pyc'))
                # Tests import the real checkout CI protocol by repository-relative
                # path. Copy its complete module tree, not selected entry points.
                from windows_qa_contract import copy_contract_ci
                copy_contract_ci(BASE.parent,root,required=SOURCE_QA)
                isolated_dev_application=root/'AI_SMM_Developer_A75'/'application'
                shutil.copytree(isolated_app,isolated_dev_application)
                # Legacy AST contracts address AI_SMM_Developer_A75/app.py, while
                # runtime modules are imported from the isolated candidate. Both
                # must be the same *verified* source snapshot, never a stale
                # developer tree or a hand-maintained test double.
                developer_root=isolated_tests.parent
                for production_module in isolated_app.glob('*.py'):
                    if production_module.name.startswith('test_'):
                        continue
                    shutil.copy2(production_module,developer_root/production_module.name)
                # Contract tests run isolated from the live provider roster.
                # Qualification certificates and credentials remain in the
                # supervisor for later real-model acceptance, never in tests.
                from execution_environment import isolated_contract_test_environment
                env=isolated_contract_test_environment(os.environ); env['PYTHONPATH']=str(isolated_app)
                env['AI_SMM_TEST_ROOT']=str(envroot)
                env['PYTHONUTF8']='1';env['PYTHONIOENCODING']='utf-8'
                if missing_tests:
                    record('unit_tests',False,profile=profile,scope='core_regressions' if selected_tests else 'full',
                           missing_modules=missing_tests)
                else:
                    test_started=time.monotonic()
                    try:
                        regression=run_isolated_python_regression(isolated_tests,env,profile)
                        (OUT/'unit_tests.log').write_text(json.dumps(regression,ensure_ascii=False,indent=2),encoding='utf-8')
                        record('unit_tests',regression['status']=='PASS',log='unit_tests.log',
                               profile=profile,scope='core_regressions' if selected_tests else 'full',
                               selected_modules=list(selected_tests) if selected_tests else 'all',
                               module_count=regression['module_count'],
                               passed=regression.get('passed',0),subtests=regression.get('subtests',0),
                               shards=regression.get('shards',[]),
                               duration_s=round(time.monotonic()-test_started,2))
                    except BaseException as exc:
                        (OUT/'unit_tests.log').write_text(traceback.format_exc(),encoding='utf-8')
                        record('unit_tests',False,profile=profile,error=type(exc).__name__+': '+str(exc),
                               log='unit_tests.log',duration_s=round(time.monotonic()-test_started,2))
            child_run('live_qt_gui', [sys.executable,str(pathlib.Path(__file__).resolve()),'--gui-child'], 150,envroot)
            # Empty sandbox is deleted at exit; production project data never opened.
        if os.name=='nt':
            ollama_probe()
            openai_api_preflight()
            reviewer_model_probe()
            if live_acceptance_ready(RESULT['checks']):
                backend_timeout=900 if profile=='fast' else 2400
                semantic_timeout=420 if profile=='fast' else 1200
                with tempfile.TemporaryDirectory(prefix='AI_SMM_LIVE_WORKFLOW_') as live:
                    child_run('live_backend_content_pipeline',
                        [sys.executable,str(BASE/'real_workflow_acceptance.py')], backend_timeout,live)
                    # Preserve reproducible diagnostic evidence from synthetic-only cases.
                    for candidate in pathlib.Path(live).glob('*synthetic_documents.json'):
                        shutil.copy2(candidate,OUT/candidate.name)
                    for diagnostic in pathlib.Path(live).glob('*_workflow_evidence.json'):
                        shutil.copy2(diagnostic,OUT/diagnostic.name)
                    for diagnostic_name in ('real_workflow_progress.jsonl','real_workflow_checkpoint.json'):
                        diagnostic=pathlib.Path(live)/diagnostic_name
                        if diagnostic.is_file():shutil.copy2(diagnostic,OUT/diagnostic.name)
                    recoveryfile=pathlib.Path(live)/'strategy_recovery_results.json'
                    if recoveryfile.is_file():shutil.copy2(recoveryfile,OUT/recoveryfile.name)
                    resultfile=pathlib.Path(live)/'real_workflow_results.json'
                    if resultfile.is_file():shutil.copy2(resultfile,OUT/resultfile.name)
                    # A numeric 2/2 is not independent evidence of content quality.
                    if any(OUT.glob('*_synthetic_documents.json')):
                        child_run('independent_semantic_acceptance',
                            [sys.executable,str(BASE/'independent_acceptance_editor.py'),
                             str(OUT),str(OUT/'independent_editorial_results.json'),
                             *(['--ready-only'] if profile=='fast' else [])],
                            semantic_timeout,live)
                    else:
                        record('independent_semantic_acceptance',None,
                               reason='Backend pipeline did not complete')
                # The fast profile checks the real backend on randomized tasks,
                # but leaves the 20+ minute full Qt workflow for explicit full runs.
                if profile=='full':
                    with tempfile.TemporaryDirectory(prefix='AI_SMM_GUI_FULL_WORKFLOW_') as gui_live:
                        child_run('live_gui_full_workflow',
                            [sys.executable,str(BASE/'live_gui_workflow_acceptance.py')],
                            1500,gui_live)
                else:
                    record('live_gui_full_workflow',None,reason='Пропущено профилем fast; полный GUI-сценарий доступен в профиле full')
            else:
                if 'ollama_constrained_schema' not in RESULT['checks']:
                    record('ollama_constrained_schema',None,reason='Ollama did not complete the initial probe')
                if 'ollama_inventory_map_schema' not in RESULT['checks']:
                    record('ollama_inventory_map_schema',None,reason='Ollama did not complete the inventory schema probe')
                record('live_backend_content_pipeline',None,
                       reason='Кампания пропущена: не пройдены контрольные решения или нет автора с двумя независимыми квалифицированными редакторами; подробности в reviewer_models')
                record('independent_semantic_acceptance',None,reason='Нет результата генерации')
                record('live_gui_full_workflow',None,
                       reason='Пропущен: не пройдены необходимые предварительные проверки',
                       blocking_checks=[name for name in (
                           'ollama_roundtrip','ollama_structured_json','ollama_constrained_schema',
                           'ollama_inventory_map_schema','reviewer_models',
                           *(['openai_api_preflight'] if (
                               os.environ.get('AI_SMM_ENABLE_OPENAI_API','').strip()=='1' and
                               os.environ.get('AI_SMM_ENABLE_OPENAI_ACCEPTANCE','').strip()=='1' and
                               any(item.strip().casefold().startswith('openai/') for item in
                                   os.environ.get('AI_SMM_CLOUD_MODELS','').replace(';',',').split(','))) else ()),
                       ) if RESULT['checks'].get(name,{}).get('status')!='PASS'])
        else:
            record('ollama_roundtrip',None,reason='Only checked on Windows host')
            record('ollama_constrained_schema',None,reason='Only checked on Windows host')
            record('ollama_inventory_map_schema',None,reason='Only checked on Windows host')
            record('reviewer_models',None,reason='Only checked on Windows host')
            record('live_backend_content_pipeline',None,reason='Only checked on Windows host')
            record('independent_semantic_acceptance',None,reason='Only checked on Windows host')
            record('live_gui_full_workflow',None,reason='Only checked on Windows host')
    except BaseException as e:
        record('verifier_exception',False,error=repr(e))
    if SOURCE_QA:
        from execution_environment import isolated_contract_test_environment
        ci_env=isolated_contract_test_environment(os.environ)
        ci_env.update(AI_SMM_TEST_ROOT=str(OUT/'ci_application'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1')
        try:
            with (OUT/'source_ci.log').open('w',encoding='utf-8') as log:
                ci_process=subprocess.run([sys.executable,'-m','pytest','-q',str(BASE.parent/'ci'),'--junitxml='+str(OUT/'source_ci.xml')],
                    cwd=BASE.parent,env=ci_env,stdout=log,stderr=subprocess.STDOUT,timeout=2400)
            record('source_ci_contracts',ci_process.returncode==0,log='source_ci.log',scope='Complete offline contracts; these are not real-model/visible-GUI evidence')
        except Exception as exc:record('source_ci_contracts',False,error=str(exc),log='source_ci.log')
    # Only actual target Windows/CUDA/Qt execution plus interactive visual
    # review can satisfy local generative media acceptance. Never fixtures.
    if os.environ.get('AI_SMM_TEST_PROFILE','full').strip().casefold()=='full':
        plan=os.environ.get('AI_SMM_GENERATIVE_ACCEPTANCE_PLAN')
        if os.name!='nt' or not plan:
            record('local_generative_media',None,reason='Requires Windows RTX 2080 Super, installed profiles, explicit fixture plan and actual visual review')
        else:
            try:
                from generative_acceptance import run_cases
                callback=None
                if SOURCE_QA:
                    from local_media_quality import LocalVision,objective_quality
                    from durable_json import atomic_write_json
                    vision=LocalVision(os.environ['AI_SMM_QA_VISION_MODEL'])
                    # Text qualification already requests keep_alive=0.
                    # Loading every 14B/8B candidate again just to unload it
                    # consumes GPU memory and can falsely timeout the media QA.
                    # Do not touch unrelated local Ollama workloads; the real
                    # per-case nvidia-smi gate handles insufficient headroom.
                    qualification=vision.qualify(OUT/'vision_qualification')
                    atomic_write_json(OUT/'vision_qualification.json',qualification)
                    def callback(case,path,folder):
                        pathlib.Path(folder).mkdir(parents=True,exist_ok=True)
                        try:
                            metrics=objective_quality(case,path,folder)
                            verdict=vision.review(case,path,folder,metrics)
                            atomic_write_json(pathlib.Path(folder)/'quality.json',{'metrics':metrics,**verdict})
                            return verdict
                        except Exception as exc:
                            atomic_write_json(pathlib.Path(folder)/'quality.json',{'status':'BLOCKED','reason':str(exc),'traceback':traceback.format_exc()})
                            raise
                proof=run_cases(plan,OUT/'generative_media',review=not SOURCE_QA,
                    qa_role=os.environ.get('AI_SMM_QA_ROLE','target_2080super'),quality_callback=callback,lifecycle=SOURCE_QA)
                record('local_generative_media',proof.get('status')=='PASS' and proof.get('windows_executed') is True and proof.get('semantic_quality') in ('OPERATOR_VERIFIED','LOCAL_MODEL_VERIFIED'),
                       log='generative_media/generative_results.json',scope='Actual CUDA/Qt generation, objective readback and qualified local quality review' if SOURCE_QA else 'Actual target CUDA/Qt generation plus operator visual review')
                if SOURCE_QA:record('media_lifecycle',proof.get('lifecycle',{}).get('status')=='PASS',log='generative_media/generative_results.json')
            except Exception as exc:
                record('local_generative_media',False,error=type(exc).__name__+': '+str(exc))
    # A technical end-to-end PASS is not proof of actual factual correctness.
    profile=os.environ.get('AI_SMM_TEST_PROFILE','full').strip().casefold()
    task_checks=[('source_snapshot_integrity' if SOURCE_QA else 'package_integrity'),'windows_platform','unit_tests','live_qt_gui','ollama_roundtrip',
                 'ollama_structured_json','ollama_constrained_schema','ollama_inventory_map_schema','reviewer_models','live_backend_content_pipeline',
                 'independent_semantic_acceptance']
    if os.environ.get('AI_SMM_ENABLE_OPENAI_API','').strip()=='1' and os.environ.get('AI_SMM_ENABLE_OPENAI_ACCEPTANCE','').strip()=='1':
        task_checks.append('openai_api_preflight')
    if profile=='full':
        task_checks.extend(('live_gui_full_workflow','local_generative_media'))
        if SOURCE_QA:task_checks.extend(('media_lifecycle','source_ci_contracts'))
    full_checks=tuple(task_checks)
    RESULT['checks']['full_real_task_end_to_end']={
      'status':acceptance_status(RESULT['checks'],full_checks),
      'evidence':{k:RESULT['checks'].get(k,{}).get('status','NOT_TESTED') for k in full_checks},
      'profile':profile,
      'scope':('fast profile: one newly randomized synthetic brief per selected model; when configured, pair an API or Ollama Cloud author with one random local Ollama author. Cases cover both strategy/calendar and standalone writing with independent review; one bounded targeted final correction; no whole-task restart or full GUI workflow. First-pass and final verdicts are separate. Partial model coverage, not release evidence.' if profile=='fast' else 'full profile: newly randomized synthetic briefs cover both the strategy/calendar and standalone writing workflows across installed author model tags, with independent editorial review and real GUI roundtrip; real-world correctness needs further review')}
    # Release-gate evidence must actually be present, not just named.
    missing=[]
    for check_name, proof in RESULT['checks'].items():
        name=proof.get('log') if isinstance(proof,dict) else None
        if name and not (OUT/name).is_file():
            missing.append(str(check_name)+': '+str(name))
    if RESULT['checks'].get('live_backend_content_pipeline',{}).get('status')=='PASS':
        results_path=OUT/'real_workflow_results.json'
        if not results_path.is_file():
            missing.append('live_backend_content_pipeline: real_workflow_results.json')
        else:
            try:
                live_results=json.loads(results_path.read_text(encoding='utf-8-sig'))
                expected_cases=live_results.get('cases',[])
                missing.extend('live_backend_content_pipeline: '+error for error in workflow_coverage_errors(live_results))
                for case in expected_cases:
                    name=str(case.get('name',''))+'_synthetic_documents.json'
                    if not case.get('name') or not (OUT/name).is_file():
                        missing.append('live_backend_content_pipeline: '+name)
            except Exception as exc:
                missing.append('live_backend_content_pipeline: invalid real_workflow_results.json ('+type(exc).__name__+')')
    if RESULT['checks'].get('independent_semantic_acceptance',{}).get('status') in ('PASS','FAIL'):
        if not (OUT/'independent_editorial_results.json').is_file():
            missing.append('independent_semantic_acceptance: independent_editorial_results.json')
    if RESULT['checks'].get('live_gui_full_workflow',{}).get('status') in ('PASS','FAIL'):
        if not (OUT/'gui_workflow_diagnostics.json').exists():
            missing.append('live_gui_full_workflow: gui_workflow_diagnostics.json')
    record('evidence_integrity',not missing,missing=missing)
    RESULT['status']=acceptance_status(RESULT['checks'],
                                      ('full_real_task_end_to_end','evidence_integrity'))
    RESULT['attested_source_fingerprint']=RESULT['checks'].get('source_snapshot_integrity' if SOURCE_QA else 'package_integrity',{}).get('source_fingerprint')
    (OUT/'acceptance.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile((OUT/'DIAGNOSTICS.zip') if SOURCE_QA else BASE/'VALIDATION_RESULTS.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in sorted(OUT.rglob('*')):
            if f.is_file() and f.name!='DIAGNOSTICS.zip': z.write(f,arcname='VALIDATION_RESULTS/'+f.relative_to(OUT).as_posix())
    if 'test_source_temp' in locals() and test_source_temp is not None: test_source_temp.cleanup()
    print('Проверка завершена. Диагностика: '+str(OUT/'DIAGNOSTICS.zip' if SOURCE_QA else BASE/'VALIDATION_RESULTS.zip'),flush=True)
    # A blocked acceptance is a failed process, even when diagnostics were saved.
    raise SystemExit(0 if RESULT['status']=='PASS' else 2)
