#!/usr/bin/env python3
"""Real Studio dialogue->media lifecycle with fixture or opt-in loopback model.

No paid API, real-model verdict, Windows E2E claim or user build. Each invocation
uses a new temporary data root; the corrupt-ledger case never touches user data.
"""
from __future__ import annotations
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--real-model',action='store_true',help='Use explicitly configured local model, without semantic fixture')
    parser.add_argument('--cancel',action='store_true')
    parser.add_argument('--corrupt-paid-ledger',action='store_true')
    args=parser.parse_args()
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise RuntimeError('Native FFmpeg/ffprobe required')
    with tempfile.TemporaryDirectory(prefix='ai-smm-studio-media-') as temporary:
        root=Path(temporary)
        os.environ['AI_SMM_TEST_ROOT']=str(root/'application')
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        if args.corrupt_paid_ledger:
            data=Path(os.environ['AI_SMM_TEST_ROOT']);data.mkdir()
            (data/'paid_cost_ledger.sqlite3').write_bytes(b'intentionally invalid test ledger')
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
        if args.real_model:
            from local_text_runtime import configured_for,endpoint_for
            model_name=os.environ.get('AI_SMM_LOCAL_LLM_MODEL','')
            if not configured_for(model_name):raise RuntimeError('Real smoke requires an explicit loopback llama.cpp model')
            endpoint_for(os.environ.get('AI_SMM_LOCAL_LLM_URL','http://127.0.0.1:8080'))
            os.environ['AI_SMM_MODEL']=model_name
            os.environ['AI_SMM_LOCAL_DIALOGUE_MODEL']=model_name
            os.environ['AI_SMM_DIALOGUE_MODE']='local'
        from PySide6.QtCore import QCoreApplication,QEvent
        from PySide6.QtWidgets import QApplication
        from PIL import Image
        from app import Studio,now
        from qt_worker_lifecycle import drain_finished_workers
        from media_artifact_validation import digest_file
        import local_media_jobs as jobs
        qapp=QApplication.instance() or QApplication([])
        window=Studio();window.show();qapp.processEvents()
        owner=window.core.projects.create(root/'projects','Media dialogue smoke','Other','Native integration fixture',now())
        window.current_project=owner
        if args.corrupt_paid_ledger:assert window.paid_media is None
        original_paths=[]
        for index,color in enumerate(((30,50,70),(70,50,30))):
            path=root/(str(index)+'.png')
            with Image.new('RGB',(160,120),color) as image:image.save(path,'PNG')
            original_paths.append(path)
        receipts=window.core.import_media(original_paths,project_id=owner,kind='asset').receipts
        source_hashes={str(receipt.final_path):digest_file(receipt.final_path) for receipt in receipts}
        source_ids=['media:asset:'+receipt.item_id for receipt in receipts]
        action={'operation':'local_media','artifact_kind':'none','target_ids':[],'source_id':'current',
            'message_ids':[],'reply':'','reason':'Сохранить выбранные материалы в коротком видео согласно поручению',
            'include_current_instruction':True,'new_title':'','control_scope':'none','use_reference':False,
            'start':None,'end':None,'offset':0,'length':1000,'schedule':None,'requested_media':True,'reuse_structure':False,
            'media':{'task':'video_from_images','source_ids':source_ids,'width':128,'height':96,'start':None,'end':None,'seconds_per_image':.5}}
        entered=threading.Event();release=threading.Event();stop_sent=False
        original_render=jobs._render
        def render(request,sources,target,check):
            if args.cancel:
                entered.set()
                if not release.wait(10):raise TimeoutError('Cancellation fixture deadline')
                check()
            return original_render(request,sources,target,check)
        request='Собери из этих двух изображений короткий ролик: 128 на 96 пикселей, каждое изображение показывать 0.5 секунды. Сохрани отдельно.'
        try:
            with (nullcontext(None) if args.real_model else patch('central_dialogue_transport.request_central_dialogue',return_value={'message':{'content':json.dumps(action)}})) as model,patch.object(jobs,'_render',side_effect=render):
                window.message.setPlainText(request);window.send_message()
                deadline=time.monotonic()+(180 if args.real_model else 25)
                while time.monotonic()<deadline:
                    qapp.processEvents()
                    if args.cancel and entered.is_set() and not stop_sent:
                        window.cancel_text_task();stop_sent=True;release.set()
                    if window.task_worker is None and window.dialogue_worker is None and window.media_worker is None:break
                    time.sleep(.005)
                if model is not None:assert model.call_count==1,'Semantic model must be an explicit one-call fixture'
                assert window.task_worker is None and window.media_worker is None,'Native workers did not drain'
            assert drain_finished_workers(qapp,window,10)
            intake=window.db.execute('SELECT original_request,state FROM ai_task_intake WHERE project_id=?',(owner,)).fetchone()
            assert intake==(request,'failed' if args.cancel else 'resolved'),intake
            task=window.db.execute('SELECT id,state,request_json FROM local_media_jobs WHERE project_id=?',(owner,)).fetchone()
            assert task and task[1]==('cancelled' if args.cancel else 'completed'),task
            executed=json.loads(task[2])
            assert executed['task']=='video_from_images',executed
            assert set(executed['sources'])==set(source_hashes),executed
            assert (executed['width'],executed['height'],executed['seconds_per_image'])==(128,96,.5),executed
            published=window.db.execute('SELECT path FROM project_files WHERE id=? AND project_id=?',(task[0],owner)).fetchone()
            if args.cancel:
                assert stop_sent and published is None
            else:
                assert published and Path(published[0]).is_file()
                assert window.db.execute('SELECT COUNT(*) FROM result_publication_events WHERE project_id=?',(owner,)).fetchone()[0]==1
            assert all(digest_file(Path(path))==fingerprint for path,fingerprint in source_hashes.items())
            assert window.projects.isEnabled()
        except BaseException:
            # Retain bounded diagnostic evidence before the isolated root is removed.
            for trace in (root/'projects').glob('*/drafts/dialogue_turn_*.json'):
                state=json.loads(trace.read_text(encoding='utf-8'))
                print(json.dumps({'diagnostic':'dialogue_state','status':state.get('status'),
                    'error':state.get('error'),'decision':state.get('decision')},ensure_ascii=False),flush=True)
            for trace in (root/'projects').glob('*/drafts/application_events.jsonl'):
                for line in trace.read_text(encoding='utf-8').splitlines():
                    event=json.loads(line)
                    if event.get('event')=='model_call_completed':
                        print(json.dumps({'diagnostic':'real_model_response','response':event.get('response')},ensure_ascii=False)[:10000],flush=True)
            raise
        finally:
            release.set()
            if window.media_worker is not None or window.task_worker is not None:window.cancel_text_task()
            assert drain_finished_workers(qapp,window,10)
            assert window.close()
            window.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);qapp.processEvents()
        print(json.dumps({'status':'PASS','scope':'actual Linux/offscreen Studio send_message, issued media IDs, local dispatcher, owned artifacts/intake and clean shutdown',
            'cancelled':args.cancel,'paid_ledger_corrupt':args.corrupt_paid_ledger,'paid_api_calls':0,'real_model_tested':args.real_model,'executed_task':executed['task'],'output_geometry':[128,96],'seconds_per_image':.5,'windows_e2e':False}))
    return 0


if __name__=='__main__':raise SystemExit(main())
