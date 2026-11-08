#!/usr/bin/env python3
"""Explicit offline real-inference Qt smoke. Never a model-quality/Windows verdict.

Requires already-installed model profile and separate ML Python. No fixtures,
paid service, downloads or user data. Tiny models only prove topology/lifecycle.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profiles',required=True);parser.add_argument('--profile',required=True);parser.add_argument('--cancel',action='store_true');parser.add_argument('--corrupt-paid-ledger',action='store_true')
    args=parser.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_SMM_Work_Architecture_A75'))
    os.environ['AI_SMM_GENERATIVE_CONFIG']=str(Path(args.profiles).resolve());os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    with tempfile.TemporaryDirectory(prefix='smm-real-generative-') as temporary:
        root=Path(temporary);os.environ['AI_SMM_TEST_ROOT']=str(root/'application')
        if args.corrupt_paid_ledger:
            (root/'application').mkdir();(root/'application'/'paid_cost_ledger.sqlite3').write_bytes(b'intentionally invalid isolated paid ledger')
        from PySide6.QtCore import QCoreApplication,QEvent
        from PySide6.QtWidgets import QApplication
        from PIL import Image
        from app import Studio,now
        from kie_media_queue_ui import KieMediaQueuePanel
        from generative_models import digest,profiles,verify_bundle
        from qt_worker_lifecycle import drain_finished_workers
        profile=profiles()[args.profile];manifest=verify_bundle(profile)
        video=profile['engine']=='animatediff_v2v'
        if profile['engine'] not in ('instruct_pix2pix','animatediff_v2v'):raise ValueError('This isolated smoke exercises instruction/video topology; all engines require Windows acceptance plan')
        qapp=QApplication.instance() or QApplication([]);window=Studio();window.show()
        if args.corrupt_paid_ledger:assert window.paid_media is None
        owner=window.core.projects.create(root/'projects','Real local generation','Other','Isolated inference smoke',now());window.current_project=owner
        source=root/('source.mp4' if video else 'source.png')
        if video:
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=64x64:r=8','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1','-c:v','mpeg4','-c:a','aac',str(source)],check=True,capture_output=True,timeout=20)
        else:
            with Image.new('RGB',(64,64),(20,50,80)) as image:image.save(source,'PNG')
        imported=window.core.import_media([source],project_id=owner,kind='asset').receipts[0]
        original=digest(imported.final_path)
        panel=window.kie_queue_panel;panel.show();panel.refresh()
        try:
            panel.local_profile.setCurrentIndex(panel.local_profile.findData(args.profile));panel.sources.item(0).setSelected(True)
            panel.local_width.setValue(64);panel.local_height.setValue(64);panel.local_steps.setValue(50 if args.cancel else 4 if video else 2);panel.local_strength.setValue(.75 if video else 1)
            if video:panel.local_frames.setValue(4);panel.local_fps.setValue(8)
            panel.prompt.setPlainText('Change the source into a watercolor painting')
            started=time.monotonic();panel.local_generation_button.click()
            assert panel._result_worker is not None,'GUI did not dispatch real generation'
            deadline=time.monotonic()+600
            stop_sent=False
            while panel._result_worker is not None and time.monotonic()<deadline:
                qapp.processEvents()
                if args.cancel and not stop_sent and list((root/'projects').rglob('generation_process.log')):
                    panel.table.selectRow(0);panel.stop_button.click();stop_sent=True
                time.sleep(.01)
            assert panel._result_worker is None,'Real generation did not drain'
            row=window.db.execute('SELECT id,state,output_path,error_code FROM local_media_jobs WHERE project_id=?',(owner,)).fetchone()
            if args.cancel:
                assert stop_sent and row and row[1]=='cancelled' and row[2] is None,row
                assert window.db.execute('SELECT COUNT(*) FROM project_files WHERE project_id=?',(owner,)).fetchone()[0]==0
                execution=None
            else:
                if row and row[1]!='completed':
                    print(window.db.execute('SELECT diagnostic_json FROM local_generation_diagnostics WHERE job_id=?',(row[0],)).fetchone(),flush=True)
                assert row and row[1]=='completed',row
                receipt,sha=window.db.execute('SELECT receipt_json,output_sha256 FROM local_generation_receipts WHERE job_id=?',(row[0],)).fetchone()
                assert digest(row[2])==sha
                if video:
                    from local_media_jobs import _probe
                    duration,audio,geometry=_probe(row[2],full=True)
                    assert audio and geometry==(64,64) and abs(duration-.5)<.15
                else:
                    with Image.open(row[2]) as image:assert image.size==(64,64)
                execution=json.loads(receipt)
            assert digest(imported.final_path)==original
            print(json.dumps({'status':'PASS','scope':'actual Qt GUI, separate offline Diffusers process, SQLite, original integrity and shutdown',
                'real_neural_inference':not args.cancel,'video':video,'cancelled':args.cancel,'paid_ledger_corrupt':args.corrupt_paid_ledger,'elapsed_seconds':round(time.monotonic()-started,2),'execution':execution,
                'test_only_model':manifest['provenance'].get('test_only',False),'semantic_quality_accepted':False,'windows_e2e':False,'paid_api_calls':0}))
        finally:
            if panel._result_worker is not None:
                worker=panel._result_worker;window.local_media.stop(owner,worker.job_id);worker.requestInterruption()
                deadline=time.monotonic()+15
                stop_sent=False
            while panel._result_worker is not None and time.monotonic()<deadline:
                qapp.processEvents()
                if args.cancel and not stop_sent and list((root/'projects').rglob('generation_process.log')):
                    panel.table.selectRow(0);panel.stop_button.click();stop_sent=True
                time.sleep(.01)
            assert panel._result_worker is None
            assert drain_finished_workers(qapp,window,15)
            assert window.close();window.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);qapp.processEvents()
    return 0


if __name__=='__main__':raise SystemExit(main())
