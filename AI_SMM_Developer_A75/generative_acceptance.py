"""Actual Windows/RTX Qt generation acceptance. Missing host/plan/models is BLOCKED.

No model/API download. This supplements, never replaces, the full Windows QA
supervisor. Retains outputs and exact execution receipts for independent review.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def _parse_cuda_vram(sample):
    """Parse only the first GPU, which owns the selected CUDA device index 0."""
    from csv import reader
    rows=sample.strip().splitlines()
    if len(rows)!=1:raise RuntimeError('QA_CUDA_MULTIGPU_AMBIGUOUS')
    cells=next(reader([rows[0]]))
    if len(cells)!=2:raise RuntimeError('QA_CUDA_MEMORY_PROBE_INVALID')
    try:free,total=(int(part.strip()) for part in cells)
    except ValueError as exc:raise RuntimeError('QA_CUDA_MEMORY_PROBE_INVALID') from exc
    if total<6144 or not 0<=free<=total:raise RuntimeError('QA_CUDA_MEMORY_PROBE_INVALID')
    return {'free_mib':free,'total_mib':total,'used_mib':total-free}


def _require_cuda_headroom(output,case_index,minimum_free_mib=3072,max_wait_seconds=90):
    """Fail closed when another local process occupies native QA VRAM.

    Does not kill other GPU processes, change their Ollama residency, or
    reduce target 8GB CUDA inference limits. Record every real driver sample.
    """
    import subprocess
    from durable_json import atomic_write_json
    snapshots=[]
    limit=time.monotonic()+max_wait_seconds
    while True:
        try:
            probe=subprocess.run(
                ['nvidia-smi','--query-gpu=memory.free,memory.total',
                 '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=12)
            if probe.returncode:raise RuntimeError('QA_CUDA_MEMORY_PROBE_FAILED')
            sample=_parse_cuda_vram(probe.stdout)
            sample['observed_at_unix']=time.time()
            snapshots.append(sample)
            atomic_write_json(Path(output)/('vram_case_'+str(case_index)+'.json'),
                {'minimum_free_mib':minimum_free_mib,'samples':snapshots,
                 'status':'PASS' if sample['free_mib']>=minimum_free_mib else 'WAITING'})
            if sample['free_mib']>=minimum_free_mib:return sample
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise RuntimeError('QA_CUDA_MEMORY_PROBE_FAILED') from exc
        if time.monotonic()>=limit:
            atomic_write_json(Path(output)/('vram_case_'+str(case_index)+'.json'),
                {'minimum_free_mib':minimum_free_mib,'samples':snapshots,
                 'status':'BLOCKED','error_code':'QA_CUDA_INSUFFICIENT_FREE_VRAM'})
            raise RuntimeError('QA_CUDA_INSUFFICIENT_FREE_VRAM: only '+
                               str(snapshots[-1]['free_mib'])+' MiB free')
        time.sleep(min(5,max(0,limit-time.monotonic())))


def _observed_generation_stage(project_root):
    """Best-effort actual CUDA child stage for the live Windows QA status.

    Uses only this isolated QA project root. No fabricated progress percentage,
    timers that falsely imply work, or dependency on image quality heuristics.
    """
    from generative_media_runtime import _last_child_stage
    latest=None
    latest_mtime=-1
    for path in Path(project_root).rglob('generation_process.log'):
        try:
            mtime=path.stat().st_mtime_ns
            if mtime>latest_mtime:
                latest=path
                latest_mtime=mtime
        except OSError:
            continue
    if latest is None:return None
    try:return _last_child_stage(latest)
    except OSError:return None


def run_cases(plan_path,output,*,review=False,qa_role="target_2080super",quality_callback=None,lifecycle=False):
    if os.name!='nt':raise RuntimeError('WINDOWS_HOST_REQUIRED')
    import subprocess
    from generative_models import profiles,verify_bundle,digest
    from generative_contract import ENGINES
    installed=profiles()
    plan=json.loads(Path(plan_path).read_text(encoding='utf-8'))
    if not isinstance(plan,dict) or set(plan)!={'version','cases'} or plan['version']!=1 or not isinstance(plan['cases'],list):raise ValueError('Invalid Windows generative acceptance plan')
    cases=plan['cases']
    engines={installed.get(case.get('profile'),{}).get('engine') for case in cases}
    if set(ENGINES.values())-engines:raise ValueError('All four installed image/video engines must be exercised')
    for case in cases:
        if not isinstance(case,dict) or set(case)-{'profile','sources','prompt','width','height','steps','seed','strength','frames','fps','start'}:raise ValueError('Unsupported acceptance case')
        profile=installed[case['profile']]
        if profile['device']!='cuda' or 'python' not in profile:raise ValueError('Separate CUDA runtime required')
        manifest=verify_bundle(profile)
        if manifest['provenance'].get('test_only') or manifest['provenance'].get('fixture') or manifest['provenance'].get('quality_accepted') is False:raise ValueError('Tiny/fixture models cannot count as acceptance')
        probe=subprocess.run([profile['python'],'-c',
            'import torch,json;print(json.dumps({"cuda":torch.cuda.is_available(),"name":torch.cuda.get_device_name(0) if torch.cuda.is_available() else "","capability":torch.cuda.get_device_capability(0) if torch.cuda.is_available() else [],"total":torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else 0}))'],capture_output=True,text=True,check=True,timeout=60)
        hardware=json.loads(probe.stdout)
        from windows_qa_contract import hardware_matches
        if not hardware_matches(hardware,qa_role):raise RuntimeError('ACTUAL_QA_GPU_ROLE_MISMATCH')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    os.environ['AI_SMM_TEST_ROOT']=str(output/'application')
    from PySide6.QtCore import QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication,QMessageBox
    from app import Studio,now
    from kie_media_queue_ui import KieMediaQueuePanel
    from qt_worker_lifecycle import drain_finished_workers
    app=QApplication.instance() or QApplication([]);window=Studio();window.show();results=[];lifecycle_proof=None
    try:
        for index,case in enumerate(cases):
            from durable_json import atomic_write_json
            atomic_write_json(output/'active_case.json',{'case':index,'request':case,'stage':'starting'})
            try:
                _require_cuda_headroom(output,index)
            except BaseException as exc:
                atomic_write_json(output/'active_case.json',
                    {'case':index,'request':case,'stage':'blocked_vram',
                     'error':type(exc).__name__,'reason':str(exc)[:300]})
                raise
            original={str(Path(p).resolve()):digest(p) for p in case['sources']}
            owner=window.core.projects.create(output/'projects','Generative acceptance '+str(index),'Other','Actual target hardware',now())
            window.current_project=owner
            receipts=window.core.import_media([Path(p) for p in case['sources']],project_id=owner,kind='asset').receipts
            if len(receipts)!=len(case['sources']):raise ValueError('Acceptance source import incomplete')
            panel=window.kie_queue_panel;window.show_section('publishing');window.pub_tabs.setCurrentWidget(panel);panel.show();panel.refresh()
            try:
                panel.local_profile.setCurrentIndex(panel.local_profile.findData(case['profile']))
                for row in range(panel.sources.count()):panel.sources.item(row).setSelected(panel.sources.item(row).data(256)==str(receipts[0].final_path))
                if len(receipts)>1:panel.local_auxiliary.setCurrentIndex(panel.local_auxiliary.findData(str(receipts[1].final_path)))
                panel.prompt.setPlainText(case['prompt'])
                for field,default in [('width',256),('height',256),('steps',20),('seed',index),('strength',.65),('frames',16),('fps',8),('start',0)]:
                    getattr(panel,'local_'+field).setValue(case.get(field,default))
                panel.local_generation_button.click();deadline=time.monotonic()+7500
                if panel._result_worker is None:raise RuntimeError('Actual GUI did not dispatch generation')
                observed_stage='worker_dispatched'
                atomic_write_json(output/'active_case.json',
                    {'case':index,'request':case,'stage':observed_stage})
                next_poll=0
                while panel._result_worker is not None and time.monotonic()<deadline:
                    app.processEvents()
                    current=time.monotonic()
                    if current>=next_poll:
                        next_poll=current+2
                        actual=_observed_generation_stage(output/'projects')
                        if actual is not None and actual!=observed_stage:
                            observed_stage=actual
                            atomic_write_json(output/'active_case.json',
                                {'case':index,'request':case,'stage':observed_stage,
                                 'evidence':'actual_child_stdout'})
                    time.sleep(.02)
                if panel._result_worker is not None:raise TimeoutError('Windows generation deadline')
                job=window.db.execute('SELECT id,state,output_path,error_code FROM local_media_jobs WHERE project_id=?',(owner,)).fetchone()
                if not job or job[1]!='completed':raise RuntimeError('Actual generation did not complete: '+str(job))
                receipt=window.db.execute('SELECT receipt_json,output_sha256 FROM local_generation_receipts WHERE job_id=?',(job[0],)).fetchone()
                if not receipt or digest(job[2])!=receipt[1]:raise ValueError('Output execution receipt mismatch')
                proof=json.loads(receipt[0])
                atomic_write_json(output/'active_case.json',{'case':index,'request':case,'stage':'generated','output':job[2],'execution':proof})
                expected_versions=json.loads(os.environ.get('AI_SMM_QA_ML_VERSIONS','{}')).get(case['profile'])
                if os.environ.get('AI_SMM_QA_LOCAL_ONLY')=='1' and proof.get('runtime_versions')!=expected_versions:raise ValueError('ML runtime changed during QA')
                if type(proof.get('peak_cuda_bytes')) is not int or not 0<proof['peak_cuda_bytes']<=hardware['total']:raise ValueError('Missing/invalid actual CUDA allocation peak')
                if type(proof.get('peak_cuda_reserved_bytes')) is int and proof['peak_cuda_reserved_bytes']>8*2**30:raise ValueError('Generation exceeded the target 8 GB CUDA reservation envelope')
                if type(proof.get('peak_cuda_reserved_bytes')) is not int or not proof['peak_cuda_bytes']<=proof['peak_cuda_reserved_bytes']<=hardware['total']:raise ValueError('Invalid actual CUDA reservation peak')
                if proof['device']!='cuda' or proof['dtype']!='torch.float16':raise ValueError('Target inference was not native FP16 CUDA')
                if any(digest(path)!=sha for path,sha in original.items()):raise ValueError('Protected source changed')
                reviewed=False;automatic=None
                if quality_callback is not None:
                    callback_case={**case,'task':json.loads(window.db.execute('SELECT request_json FROM local_media_jobs WHERE id=?',(job[0],)).fetchone()[0])['task']}
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future=pool.submit(quality_callback,callback_case,job[2],output/('quality_'+str(index)))
                        while not future.done():app.processEvents();time.sleep(.03)
                        automatic=future.result()
                    if automatic.get('status')!='PASS':raise ValueError('Automated local quality review did not pass')
                if review:
                    question=QMessageBox(window)
                    question.setWindowTitle('Фактическая визуальная приёмка генерации')
                    question.setText('Сопоставьте исходник, эталон/маску, инструкцию и результат. Проверьте стиль, фон, объекты, детали; для видео — связность кадров и исходный звук. Не подтверждайте по одному сообщению модели.\n\nИнструкция: '+case['prompt']+'\nРезультат: '+job[2])
                    open_result=question.addButton('Открыть результат',QMessageBox.ActionRole)
                    open_sources=question.addButton('Открыть исходники',QMessageBox.ActionRole)
                    question.addButton(QMessageBox.Yes);question.addButton(QMessageBox.No);question.setDefaultButton(QMessageBox.No)
                    while True:
                        question.exec()
                        clicked=question.clickedButton()
                        if clicked is open_result:os.startfile(job[2]);continue
                        if clicked is open_sources:
                            for path in original:os.startfile(path)
                            continue
                        reviewed=clicked is not None and question.standardButton(clicked)==QMessageBox.Yes
                        break
                    if not reviewed:raise RuntimeError('OPERATOR_REJECTED_VISUAL_RESULT')
                window.grab().save(str(output/('qt_case_'+str(index)+'.png')))
                if lifecycle and lifecycle_proof is None:
                    from media_lifecycle_acceptance import exercise
                    lifecycle_proof=exercise(window,panel,app,owner,job[0],output/'lifecycle')
                results.append({'automatic_quality':automatic,'visual_review':reviewed,'case':index,'engine':installed[case['profile']]['engine'],'project_id':owner,
                    'job_id':job[0],'output':job[2],'sha256':receipt[1],'execution':proof,'originals':original})
                (output/'cases_progress.json').write_text(json.dumps({'cases':results,'lifecycle':lifecycle_proof},indent=2),encoding='utf-8')
            except BaseException as exc:
                import traceback
                evidence={}
                try:
                    row=window.db.execute(
                        'SELECT id,state,error_code FROM local_media_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1',
                        (owner,)).fetchone()
                    if row:
                        evidence['job']={'id':row[0],'state':row[1],'error_code':row[2]}
                        detail=window.db.execute(
                            'SELECT diagnostic_json FROM local_generation_diagnostics WHERE job_id=? AND project_id=?',
                            (row[0],owner)).fetchone()
                        if detail:evidence['generation_diagnostic']=json.loads(detail[0])
                except Exception as diagnostic_exc:
                    evidence['capture_error']=type(diagnostic_exc).__name__+': '+str(diagnostic_exc)[:200]
                atomic_write_json(output/'active_case.json',
                    {'case':index,'request':case,'stage':'failed','error':type(exc).__name__})
                window.grab().save(str(output/('qt_case_'+str(index)+'_FAILED.png')))
                atomic_write_json(output/('case_'+str(index)+'_error.json'),
                    {'case':index,'error':type(exc).__name__,'reason':str(exc),
                     'traceback':traceback.format_exc(),'job_evidence':evidence})
                raise
            finally:
                if panel._result_worker is not None:
                    worker=panel._result_worker;window.local_media.stop(worker.project_id,worker.job_id);worker.requestInterruption()
                    deadline=time.monotonic()+15
                    while panel._result_worker is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
                if panel._result_worker is not None:raise RuntimeError('Generation worker did not stop')
                QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
    finally:
        if not drain_finished_workers(app,window,15):raise RuntimeError('Native workers remain alive')
        if not window.close():raise RuntimeError('Actual Studio close refused')
        window.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
    report={'status':'PASS' if review or quality_callback is not None else 'TECHNICAL_PASS_PENDING_VISUAL_REVIEW','windows_executed':True,'target_hardware':hardware,
        'cases':results,'paid_api_calls':0,'qa_role':qa_role,'lifecycle':lifecycle_proof,'semantic_quality':'LOCAL_MODEL_VERIFIED' if quality_callback is not None else 'OPERATOR_VERIFIED' if review else 'NOT_VERIFIED','windows_e2e_counter_advanced':False}
    (output/'generative_results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True);parser.add_argument('--output',required=True);parser.add_argument('--review',action='store_true')
    args=parser.parse_args()
    try:report=run_cases(args.plan,args.output,review=args.review)
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc),'windows_e2e_counter_advanced':False}));return 2
    print(json.dumps(report));return 0


if __name__=='__main__':raise SystemExit(main())
