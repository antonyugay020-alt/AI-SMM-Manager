"""Actual Qt cancellation, owner crash, watchdog, retry and durable reopening."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from generative_models import digest


def wait_gui(app,predicate,seconds=60):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.03)
    raise TimeoutError('QA lifecycle deadline exceeded')


def wait_owned_exit(process,seconds):
    import psutil
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            if not process.is_running() or process.status() in (psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD):return
        except psutil.NoSuchProcess:return
        time.sleep(.05)
    raise RuntimeError('Owned generation process remains alive')


def exercise(window,panel,app,owner,completed_job,folder):
    import psutil
    from local_media_jobs import create_job
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    request=json.loads(window.db.execute('SELECT request_json FROM local_media_jobs WHERE id=?',(completed_job,)).fetchone()[0])
    workspace=Path(window.core.workspace(owner));database=next(r[2] for r in window.db.execute('PRAGMA database_list') if r[1]=='main')
    # Cancel an actual installed-engine subprocess, not a fake model callback.
    cancelled=window.local_media.worker(owner,request=request);panel.refresh();panel._launch_worker(cancelled)
    wait_gui(app,lambda:bool(list((workspace/'.local_media_work').glob(cancelled.job_id+'-*/generation_process.json'))))
    marker=next((workspace/'.local_media_work').glob(cancelled.job_id+'-*/generation_process.json'))
    process_id=json.loads(marker.read_text())['pid']
    handle=psutil.Process(process_id) if psutil.pid_exists(process_id) else None
    panel.table.selectRow(next(row for row in range(panel.table.rowCount()) if panel.table.item(row,3).data(256)==cancelled.job_id))
    panel.stop_button.click();wait_gui(app,lambda:panel._result_worker is None)
    row=window.db.execute('SELECT state,output_path FROM local_media_jobs WHERE id=?',(cancelled.job_id,)).fetchone()
    if row!=('cancelled',None):raise ValueError('Cancelled task published or lost stop state')
    if handle is not None:
        wait_owned_exit(handle,20)
    # Kill only the QA owner process; its real model child must exit on EOF.
    orphan=create_job(window.db,owner,request)
    payload=folder/'fault_request.json';payload.write_text(json.dumps({'database':database,'workspace':str(workspace),'owner':owner,'job':orphan}))
    with (folder/'owner_crash.log').open('wb') as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--fault-owner',str(payload)],stdout=log,stderr=log)
        try:
            wait_gui(app,lambda:bool(list((workspace/'.local_media_work').glob(orphan+'-*/generation_process.json'))))
            marker=next((workspace/'.local_media_work').glob(orphan+'-*/generation_process.json'))
            child_id=json.loads(marker.read_text())['pid'];child=psutil.Process(child_id) if psutil.pid_exists(child_id) else None
            process.terminate();process.wait(timeout=15)
            if child is not None:
                wait_owned_exit(child,25)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    window.local_media.recover(owner)
    row=window.db.execute('SELECT state FROM local_media_jobs WHERE id=?',(orphan,)).fetchone()
    if row!=('interrupted',):raise ValueError('Crashed task was not durably recovered')
    retry=window.local_media.worker(owner,job_id=orphan);panel.refresh();panel._launch_worker(retry)
    wait_gui(app,lambda:panel._result_worker is None,7500)
    if window.db.execute('SELECT state FROM local_media_jobs WHERE id=?',(orphan,)).fetchone()!=('completed',):raise ValueError('Recovered task did not complete')
    # Independent SQLite connection, not a GUI cache, validates retained results.
    with sqlite3.connect(database) as reopened:
        if reopened.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise ValueError('SQLite integrity failure')
        for job in (completed_job,orphan):
            row=reopened.execute('SELECT output_path,result_sha256 FROM local_media_jobs WHERE id=? AND project_id=?',(job,owner)).fetchone()
            proof=reopened.execute('SELECT output_sha256 FROM local_generation_receipts WHERE job_id=? AND project_id=?',(job,owner)).fetchone()
            if not row or not proof or digest(row[0])!=row[1] or proof[0]!=row[1]:raise ValueError('Reopened generated output/receipt changed')
    adopted=window.local_media.worker(owner,job_id=completed_job);panel.refresh();panel._launch_worker(adopted)
    wait_gui(app,lambda:panel._result_worker is None,60)
    if list((workspace/'.local_media_work').glob(completed_job+'-*')):raise ValueError('Completed result unexpectedly re-entered generation')
    return {'status':'PASS','cancelled_job':cancelled.job_id,'orphan_job':orphan,'retry_same_identity':True,
        'parent_death_child_exit':True,'sqlite_reopened':True,'committed_receipt_adopted':True,
        'scope':'Cancellation/owner crash during subprocess startup; not proof of interruption at every CUDA kernel phase'}


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--fault-owner' or os.environ.get('AI_SMM_QA_LOCAL_ONLY')!='1':raise SystemExit('QA-only fault subprocess')
    payload=json.loads(Path(sys.argv[2]).read_text())
    database=Path(payload['database']).resolve();root=Path(os.environ['AI_SMM_TEST_ROOT']).resolve()
    if not database.is_relative_to(root):raise SystemExit('Fault probe refuses non-QA database')
    from local_media_jobs import execute_job
    db=sqlite3.connect(database)
    try:execute_job(db,lambda owner:Path(payload['workspace']) if owner==payload['owner'] else (_ for _ in ()).throw(ValueError('owner')),payload['owner'],payload['job'])
    finally:db.close()
