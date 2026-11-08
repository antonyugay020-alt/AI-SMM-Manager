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


def run_cases(plan_path,output,*,review=False):
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
        if not hardware['cuda'] or 'RTX2080SUPER' not in hardware['name'].upper().replace(' ','') or hardware['capability']!=[7,5] or not 7.5*2**30<=hardware['total']<=8.5*2**30:
            raise RuntimeError('REAL_RTX_2080_SUPER_8GB_REQUIRED')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    os.environ['AI_SMM_TEST_ROOT']=str(output/'application')
    from PySide6.QtCore import QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication,QMessageBox
    from app import Studio,now
    from kie_media_queue_ui import KieMediaQueuePanel
    from qt_worker_lifecycle import drain_finished_workers
    app=QApplication.instance() or QApplication([]);window=Studio();window.show();results=[]
    try:
        for index,case in enumerate(cases):
            original={str(Path(p).resolve()):digest(p) for p in case['sources']}
            owner=window.core.projects.create(output/'projects','Generative acceptance '+str(index),'Other','Actual target hardware',now())
            window.current_project=owner
            receipts=window.core.import_media([Path(p) for p in case['sources']],project_id=owner,kind='asset').receipts
            if len(receipts)!=len(case['sources']):raise ValueError('Acceptance source import incomplete')
            panel=window.kie_queue_panel;panel.show();panel.refresh()
            try:
                panel.local_profile.setCurrentIndex(panel.local_profile.findData(case['profile']))
                for row in range(panel.sources.count()):panel.sources.item(row).setSelected(panel.sources.item(row).data(256)==str(receipts[0].final_path))
                if len(receipts)>1:panel.local_auxiliary.setCurrentIndex(panel.local_auxiliary.findData(str(receipts[1].final_path)))
                panel.prompt.setPlainText(case['prompt'])
                for field,default in [('width',256),('height',256),('steps',20),('seed',index),('strength',.65),('frames',16),('fps',8),('start',0)]:
                    getattr(panel,'local_'+field).setValue(case.get(field,default))
                panel.local_generation_button.click();deadline=time.monotonic()+7500
                if panel._result_worker is None:raise RuntimeError('Actual GUI did not dispatch generation')
                while panel._result_worker is not None and time.monotonic()<deadline:
                    app.processEvents();time.sleep(.02)
                if panel._result_worker is not None:raise TimeoutError('Windows generation deadline')
                job=window.db.execute('SELECT id,state,output_path,error_code FROM local_media_jobs WHERE project_id=?',(owner,)).fetchone()
                if not job or job[1]!='completed':raise RuntimeError('Actual generation did not complete: '+str(job))
                receipt=window.db.execute('SELECT receipt_json,output_sha256 FROM local_generation_receipts WHERE job_id=?',(job[0],)).fetchone()
                if not receipt or digest(job[2])!=receipt[1]:raise ValueError('Output execution receipt mismatch')
                proof=json.loads(receipt[0])
                if proof['device']!='cuda' or proof['dtype']!='torch.float16':raise ValueError('Target inference was not native FP16 CUDA')
                if any(digest(path)!=sha for path,sha in original.items()):raise ValueError('Protected source changed')
                reviewed=False
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
                results.append({'visual_review':reviewed,'case':index,'engine':installed[case['profile']]['engine'],'project_id':owner,
                    'job_id':job[0],'output':job[2],'sha256':receipt[1],'execution':proof,'originals':original})
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
    report={'status':'PASS' if review else 'TECHNICAL_PASS_PENDING_VISUAL_REVIEW','windows_executed':True,'target_hardware':hardware,
        'cases':results,'paid_api_calls':0,'semantic_quality':'OPERATOR_VERIFIED' if review else 'NOT_VERIFIED','windows_e2e_counter_advanced':False}
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
