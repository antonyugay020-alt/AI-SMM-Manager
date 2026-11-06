# -*- coding: utf-8 -*-
"""Actual Windows GUI -> TaskWorker -> production backend -> GUI persistence -> reopen.

Run ONLY with AI_SMM_TEST_ROOT pointing to a newly created disposable directory.
No production data, no existing user project, no silent modification.
"""
from __future__ import annotations
import json, hashlib, os, pathlib, re, sqlite3, sys, time, traceback
from uuid import uuid4
from candidate_identity import CANDIDATE_ID
from random_acceptance_tasks import generate_acceptance_tasks
from qt_worker_lifecycle import drain_finished_workers
from content_requirements import publication_scope


_qa_app = None
_qa_qapp = None

def verified_delivery_in_gui_report(evidence, request):
    """A document pair is not proof that EVERY requested publication was made.

    Inspect the executor's durable report, not an assistant status message.
    Missing, contradictory, or partial data must fail Windows E2E acceptance.
    """
    expected=publication_scope(request)
    reports=evidence.get('delivery_reports') if isinstance(evidence,dict) else None
    if not isinstance(reports,list) or not reports:
        return False
    for item in reports:
        if not isinstance(item,dict):
            continue
        # A previous task with an identical week/post count is not evidence
        # for the current brief; verify the original full request digest.
        if item.get('request_sha256')!=hashlib.sha256(request.encode('utf-8')).hexdigest():
            continue
        signature=item.get('task_signature')
        if not isinstance(signature,str) or not re.fullmatch(r'[0-9a-f]{64}',signature):
            continue
        scope=item.get('scope')
        if not isinstance(scope,dict):
            continue
        if (type(scope.get('weeks')) is not int or type(scope.get('per_week')) is not int
                or type(scope.get('planned_total')) is not int):
            continue
        if ((scope['weeks'],scope['per_week'],scope['planned_total']) !=
                (expected.weeks,expected.per_week,expected.total)):
            continue
        if (type(item.get('checked_posts')) is int
                and item['checked_posts']==expected.total
                and item.get('unfulfilled')==[]):
            return True
    return False

def save_gui_diagnostics(workspace, app, pid, events, task_seed, task_profile, brief):
    """Only inspect the disposable project; retain a bounded failure trace."""
    report={'candidate_id':CANDIDATE_ID,'project_id':pid,'task_seed':task_seed,
            'task_profile':task_profile,'brief':brief,'worker_events':events[-90:], 'diagnostic_events':[],
            'documents':[], 'pending':None, 'assistant_replies':[], 'delivery_reports':[], 'pending_drafts':[]}
    try:
        report['documents']=[{'kind':k,'title':t,'length':len(c or ''), 'id':i}
             for k,t,c,i in app.db.execute(
                 'SELECT kind,title,content,id FROM manager_documents WHERE project_id=?',(pid,))]
        row=app.db.execute('SELECT request,question FROM ai_pending_tasks WHERE project_id=?',(pid,)).fetchone()
        if row:
            report['pending']={'request_length':len(row[0]),'question':row[1][:1700]}
        report['assistant_replies']=[c[:4000] for (c,) in app.db.execute(
            "SELECT content FROM messages WHERE project_id=? AND role='assistant' ORDER BY id DESC LIMIT 3",(pid,))]
        drafts=app.core.workspace(pid)/'drafts'
        if drafts.is_dir():
            for f in sorted(drafts.glob('publication_diagnostics_*.jsonl'))[-3:]:
                for line in f.read_text(encoding='utf-8',errors='replace').splitlines()[-110:]:
                    try:
                        event=json.loads(line)
                        fields=['stage','step','week','publication','error','reason','issues','total','completed','failed','attempt','blocked']
                        report['diagnostic_events'].append({k:event[k] for k in fields if k in event})
                    except ValueError:
                        pass
            for f in sorted(drafts.glob('pending_content_calendar_*.md'))[-2:]:
                report['pending_drafts'].append({'name':f.name,'content':f.read_text(encoding='utf-8',errors='replace')[:30000]})
            for f in sorted(drafts.glob('content_delivery_*.json'))[-3:]:
                try:
                    delivery=json.loads(f.read_text(encoding='utf-8'))
                    report['delivery_reports'].append({'name':f.name,
                       'checked_posts':delivery.get('checked_posts'),
                       'unfulfilled':delivery.get('unfulfilled'),
                       'task_signature':delivery.get('task_signature'),
                       'request_sha256':delivery.get('request_sha256'),
                       'task_id':delivery.get('task_id'),
                       'scope':delivery.get('scope'),
                       'plan_preflight':delivery.get('plan_preflight')})
                except (ValueError,TypeError):
                    pass
    except Exception as exc:
        report['trace_error']=type(exc).__name__+': '+str(exc)[:800]
    out=workspace/'gui_workflow_diagnostics.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return report


def select_two_publication_brief(seed: int):
    """Select fresh randomized live GUI work covering the two-slot failure.

    Never rewrite a generated brief or hardcode a business. The sampler's
    normal variety stays intact, and the seed remains reproducible.
    """
    for offset in range(64):
        candidate_seed=seed+offset
        task=generate_acceptance_tasks(candidate_seed,count=1)[0]
        if task['posts']==2 and publication_scope(task['request']).total==2:
            return candidate_seed,task
    raise RuntimeError('Unable to sample a valid two-publication acceptance brief')


def main():
    root=os.environ.get('AI_SMM_TEST_ROOT')
    if not root:
        raise RuntimeError('Отсутствует изолированная директория AI_SMM_TEST_ROOT')
    workspace=pathlib.Path(root).resolve()
    if not workspace.is_dir():
        raise RuntimeError('Тестовая директория не существует')
    # Always write evidence before importing Qt or starting a remote model.
    # Even a hung/native-crashed GUI must leave a durable failure checkpoint.
    (workspace/'gui_workflow_diagnostics.json').write_text(
        json.dumps({'status':'STARTED','stage':'before_qt','worker_events':[],
                    'note':'Incomplete diagnostic checkpoint, not acceptance evidence'},
                   ensure_ascii=False),encoding='utf-8')
    run_seed=int(os.environ['AI_SMM_ACCEPTANCE_SEED'])
    task_seed,task=select_two_publication_brief(run_seed ^ 0xA75A75A75)
    brief=task['request']
    profile={k:task[k] for k in ('category','channel','posts','audience','goal','scenario_id')}
    # Test files must live under the temp root handed in by the outer runner.
    from PySide6.QtWidgets import QApplication
    import app as appmod
    qapp=QApplication.instance() or QApplication([])
    app=appmod.Studio()
    # Let the QA boundary clean up on assertion failures after the worker ended.
    global _qa_app, _qa_qapp
    _qa_app, _qa_qapp = app, qapp
    app.show()
    pid=uuid4().hex
    sentinel=uuid4().hex
    app.db.executemany('INSERT INTO projects (id,name,created_at,category,description) VALUES(?,?,?,?,?)',[
        (pid,'QA '+task['category'],appmod.now(),task['category'],brief),
        (sentinel,'QA контрольный проект',appmod.now(),'контроль','Не изменять')])
    app.db.execute('INSERT INTO manager_documents VALUES (?,?,?,?,?,?,?)',
                   (uuid4().hex,sentinel,'strategy','SENTINEL','CONTROL DATA',appmod.now(),appmod.now()))
    app.db.commit()
    app.refresh_projects(pid)
    assert app.current_project==pid
    app.message.setPlainText(brief)
    app.send_message()  # Real GUI entry, TaskWorker, production route, Qt signals.
    assert app.task_worker is not None, 'GUI did not start worker'
    receipt=app.db.execute('''SELECT intake_id,original_request,message_id,state
        FROM ai_task_intake WHERE project_id=? AND dialogue_turn_id=?''',
        (pid,app.task_dialogue_turn_id)).fetchone()
    # Do not raise while QThread is running: an early assertion previously
    # destroyed the still-active worker and crashed the Windows QA process.
    intake_faults=[]
    if not (receipt and receipt[1]==brief and receipt[3]=='received'):
        intake_faults.append('Original task receipt not committed before worker launch')
    if receipt:
        message=app.db.execute('SELECT role,content FROM messages WHERE id=? AND project_id=?',
            (receipt[2],pid)).fetchone()
        if message!=('user',brief):
            intake_faults.append('Original task receipt has no matching durable user message')
    # ai_pending_tasks is a later execution pointer, never a substitute for the intake receipt.
    worker_events=[]
    app.task_worker.progress.connect(lambda item:worker_events.append(item))
    app.task_worker.failed.connect(lambda message:worker_events.append({'stage':'worker_exception','error':message[:1000]}))
    begin=time.monotonic()
    last_checkpoint=begin
    save_gui_diagnostics(workspace,app,pid,worker_events,task_seed,profile,brief)
    while app.task_worker is not None and time.monotonic()-begin < 1350:
        qapp.processEvents()
        if time.monotonic()-last_checkpoint >= 10:
            save_gui_diagnostics(workspace,app,pid,worker_events,task_seed,profile,brief)
            last_checkpoint=time.monotonic()
        time.sleep(.075)
    qapp.processEvents()
    # Qt's done signal is emitted inside QThread.run(), before the native thread
    # emits finished. Do not destruct the Studio while any worker is still alive.
    drain_finished_workers(qapp,app,timeout_seconds=10)
    if app.task_worker is not None:
        # A timeout is still a test result: capture the disposable project's
        # current artifacts and pipeline trace before reporting it. Previously
        # this branch raised first, so acceptance correctly reported missing
        # evidence but gave no way to distinguish a slow model from a stuck
        # orchestration or GUI persistence stage.
        worker_events.append({'stage':'gui_worker_timeout',
                              'elapsed_s':round(time.monotonic()-begin,2),
                              'worker_running':bool(app.task_worker.isRunning())})
        save_gui_diagnostics(workspace,app,pid,worker_events,task_seed,profile,brief)
        raise TimeoutError('GUI worker did not complete in allotted test window')
    rows=app.db.execute('SELECT kind,title,content,id FROM manager_documents WHERE project_id=?',(pid,)).fetchall()
    pending=app.db.execute('SELECT request,question FROM ai_pending_tasks WHERE project_id=?',(pid,)).fetchone()
    sentinel_rows=app.db.execute('SELECT kind,title,content FROM manager_documents WHERE project_id=?',(sentinel,)).fetchall()
    assert sentinel_rows == [('strategy','SENTINEL','CONTROL DATA')], 'Cross-project contamination'
    kinds={r[0] for r in rows}
    evidence=save_gui_diagnostics(workspace,app,pid,worker_events,task_seed,profile,brief)
    is_complete=(kinds == {'strategy','calendar'} and len(rows)==2 and pending is None
                 and not intake_faults and verified_delivery_in_gui_report(evidence,brief))
    if not is_complete:
        drain_finished_workers(qapp,app,timeout_seconds=10)
        recent=[x.get('stage') for x in evidence.get('diagnostic_events',[])[-15:]]
        raise AssertionError(f'Backend produced incomplete GUI delivery: document kinds={sorted(kinds)}, '
                             f'pending={bool(pending)}, intake_faults={intake_faults}, '
                             f'publication_scope_verified={verified_delivery_in_gui_report(evidence,brief)}; '
                             f'recent stages={recent}')
    for kind,title,content,docid in rows:
        assert isinstance(content,str) and len(content)>500, f'Empty document {kind}'
        folder=app.core.workspace(pid)/appmod.document_folder(kind)
        exports=list(folder.glob('*'+docid[:8]+'.txt'))
        pdfs=list((app.core.workspace(pid)/'pdf').glob('*'+docid[:8]+'.pdf'))
        assert len(exports)==1 and len(pdfs)==1 and pdfs[0].read_bytes().startswith(b'%PDF'),f'Failed export {kind}'
        assert exports[0].read_text(encoding='utf-8')==content, f'Export content mismatch {kind}'
    original={(r[0],r[3]):r[2] for r in rows}
    app.close(); app.db.close(); del app
    app2=appmod.Studio()
    app2.refresh_projects(pid)
    assert app2.current_project==pid
    persisted=app2.db.execute('SELECT kind,title,content,id FROM manager_documents WHERE project_id=?',(pid,)).fetchall()
    assert {(r[0],r[3]):r[2] for r in persisted}==original, 'Reopened project changed accepted documents'
    assert app2.db.execute('SELECT COUNT(*) FROM manager_documents WHERE project_id=?',(sentinel,)).fetchone()[0]==1
    assert app2.db.execute('SELECT COUNT(*) FROM ai_pending_tasks WHERE project_id=?',(pid,)).fetchone()[0]==0
    reviewer_models=[e.get('reviewer_model') for e in worker_events
                     if e.get('stage')=='independent_reviewer_selected']
    out={'status':'PASS','project_isolated':True,'task_submitted_from_gui':True,
         'task_seed':task_seed,'task_profile':profile,
         'author_model':appmod.MODEL,'reviewer_model':reviewer_models[-1] if reviewer_models else None,
         'worker_completed':True,'documents_persisted':sorted(kinds),
         'documents_reopened':True,'exports_verified':True,'pending_task_cleared':True,
         'output_lengths':{r[0]:len(r[2]) for r in rows}, 'autonomous_retry_seen':any(
              e.get('stage')=='autonomous_retry' for e in worker_events)}
    app2.close();app2.db.close()
    return out

if __name__=='__main__':
    try:
        result=main()
        status=0
    except BaseException as exc:
        result={'status':'FAIL','error':type(exc).__name__+': '+str(exc)[:500],
                'traceback':traceback.format_exc()[-2300:]}
        status=2
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    if status and _qa_app is not None and _qa_qapp is not None:
        # A failed content assertion is not evidence of a running Qt worker.
        # Prefer a normal exit after native QThread.finished; retain the hard
        # escape only when the failed QA has an uninterruptible live thread.
        from qt_worker_lifecycle import active_workers
        if not drain_finished_workers(_qa_qapp, _qa_app, timeout_seconds=10):
            os._exit(status)
        if active_workers(_qa_app):
            os._exit(status)
        try:
            _qa_app.close()
            _qa_app.db.close()
        except (RuntimeError, AttributeError, sqlite3.Error):
            pass
    sys.exit(status)
