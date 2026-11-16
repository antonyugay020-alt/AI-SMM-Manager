"""Real Studio/QThread/SQLite/PDF path with explicitly injected AI opinions.

This engineering smoke does not measure models, interactive Windows behavior,
or the ten real-model Windows E2E runs required by release policy.
"""
import json
import hashlib
from contextlib import nullcontext
import random
import sys
import threading
import time
import traceback
from unittest.mock import patch

from PySide6.QtCore import QThread, Signal, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
import app
from dialogue_runtime import DialogueContract
from qt_worker_lifecycle import active_workers, drain_finished_workers
from r95_text_fixture import text_review
from test_r86_semantic_dialogue import decision


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def spin(qapp, predicate, timeout=15):
    deadline=time.monotonic()+timeout
    while not predicate():
        qapp.processEvents()
        if time.monotonic()>=deadline:
            raise AssertionError('Native Qt condition timed out')
        time.sleep(.005)
    qapp.processEvents()


def switch(win,qapp,pid):
    win.refresh_projects(pid)
    qapp.processEvents()
    require(win.current_project==pid,'Project activation failed')


def assert_visible_isolation(win,pid):
    ids={row[0] for row in win.db.execute(
        "SELECT id FROM manager_documents WHERE project_id=? AND kind='plans'",(pid,))}
    require(set(win.document_ids)==ids,'Visible document identities crossed projects')
    transcript=win.chat.toPlainText()
    for (request,) in win.db.execute(
            "SELECT content FROM messages WHERE project_id<>? AND role='user'",(pid,)):
        require(request not in transcript,'Foreign request leaked into visible conversation')


def launch(win,qapp,pid,request,mode,body,*,resume=False):
    switch(win,qapp,pid)
    def synthetic_planner(message,snapshot,**kwargs):
        return DialogueContract(message,snapshot,turn_id=kwargs['turn_id']).validate(
            decision('resume' if resume else 'create_text',artifact_kind='plans',
                     source_id='pending' if resume else 'current'))
    requests=[]
    def synthetic_execution(original,context,**kwargs):
        requests.append(original)
        if mode=='error':
            raise RuntimeError('Injected provider outage')
        # The production acceptance gate must reject an apparently successful
        # verdict bound to different bytes, without a document or completion.
        review=text_review(original,body if mode in ('accepted','deferred_export') else body+' stale')
        return dict(action='write',instruction=original,answer=body,quality_review=review)
    export_failure=(patch.object(win,'_replay_ai_document_export',
        side_effect=OSError('Injected export storage outage')) if mode=='deferred_export' else nullcontext())
    with export_failure, patch('text_workflow.run_text_task',side_effect=synthetic_execution), \
         patch('dialogue_runtime.understand_dialogue',side_effect=synthetic_planner):
        win.message.setPlainText(request)
        win.send_button.click()
        require(win.task_worker is not None,'No native dialogue worker launched')
        spin(qapp,lambda:win.task_worker is None and not active_workers(win))
        require(drain_finished_workers(qapp,win,5),'Workers remain after callback')
    turn_id=win.task_dialogue_turn_id
    require(requests==[request],'Original instruction changed or execution repeated: '+repr((requests,win.db.execute('SELECT content FROM messages WHERE project_id=?',(pid,)).fetchall())))
    row=win.db.execute('SELECT task_id,status FROM ai_task_registry WHERE project_id=? AND request=?',
        (pid,request)).fetchone()
    require(row is not None,'Missing durable task')
    state=win.db.execute('SELECT state FROM execution_attempts WHERE task_id=? ORDER BY started_at DESC LIMIT 1',(row[0],)).fetchone()[0]
    if mode=='accepted':
        require(row[1]=='completed' and state=='accepted','Accepted task not completed')
        require(win.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',
            (turn_id,)).fetchone()==('resolved',),'Intake not resolved')
    else:
        require(row[1]=='active','Rejected/failed task was completed')
        if mode=='error':
            require(win.db.execute("SELECT count(*) FROM messages WHERE project_id=? AND role='assistant' AND content LIKE ?",
                (pid,'%Injected provider outage%')).fetchone()[0]>0,'Provider error was not saved in its project')
        require(state==('failed' if mode=='error' else 'accepted'),'Unexpected attempt outcome')
        require(win.db.execute('SELECT state FROM ai_task_intake WHERE dialogue_turn_id=?',
            (turn_id,)).fetchone()!=('resolved',),'Incomplete intake was resolved')
    require(not getattr(win,'_result_post_commit_errors',[]),'Post-commit effects failed')
    require(not win.db.in_transaction,'Callback leaked a transaction')
    return row[0]


class UnwindingWorker(QThread):
    done=Signal()
    def __init__(self):
        super().__init__()
        self.release=threading.Event()
    def run(self):
        self.done.emit()
        if not self.release.wait(15):
            raise RuntimeError('Smoke worker was not released')


def shutdown_fence(win,qapp):
    worker=UnwindingWorker()
    win.task_worker=worker
    worker.done.connect(lambda:setattr(win,'task_worker',None))
    worker.start()
    try:
        spin(qapp,lambda:win.task_worker is None)
        require(worker in active_workers(win),'Result callback lost native worker ownership')
        require(not win.close(),'Window closed while native worker was unwinding')
        require(win.db.execute('SELECT 1').fetchone()==(1,),'Blocked close disposed database')
    finally:
        worker.release.set()
        require(worker.wait(5000),'Unwinding worker failed to join')
        require(drain_finished_workers(qapp,win,5),'Native worker cleanup failed')


def execute(win,qapp,seed):
    rng=random.Random(seed)
    projects=[win.core.projects.create(app.storage_base(),name,'Smoke',name,app.now())
              for name in ('Private Alpha','Private Beta','Recovery','Deferred export')]
    expected={pid:[] for pid in projects}
    tasks=[]
    # Random order, sizes, Unicode and punctuation; every outcome is mandatory.
    cases=[(projects[0],'accepted'),(projects[1],'accepted'),
           (projects[0],'stale_review'),(projects[1],'error')]
    rng.shuffle(cases)
    for pid,mode in cases:
        token=f'{rng.getrandbits(64):016x}'
        quantity=rng.randint(2,8)
        request=f'Prepare a comparison of {quantity} campaign options — {token}.'
        body=f'# Campaign comparison {token}\n\nPrivate project {pid}.\n'+('\n'.join(
            f'Option {i}: evaluate the supplied brief; owner confirmation required.'
            for i in range(quantity)))
        task=launch(win,qapp,pid,request,mode,body)
        for index,(prior,status,owner) in enumerate(tasks):
            if owner==pid and status=='active':
                tasks[index]=(prior,'paused',owner)
        tasks.append((task,'completed' if mode=='accepted' else 'active',pid))
        if mode=='accepted':
            expected[pid].append(body)
        actual=[r[0] for r in win.db.execute('SELECT content FROM manager_documents WHERE project_id=?',(pid,))]
        require(sorted(actual)==sorted(expected[pid]),'Rejected result saved or accepted document lost')
    deferred=launch(win,qapp,projects[3],'Compare two export recovery options','deferred_export',
        '# Export recovery comparison\n\n1. Retry the export.\n2. Restore from the durable database.')
    expected[projects[3]]=['# Export recovery comparison\n\n1. Retry the export.\n2. Restore from the durable database.']
    require(win.db.execute("SELECT count(*) FROM ai_file_effect_outbox WHERE project_id=? AND state<>'done'",
        (projects[3],)).fetchone()[0]>0,'Export failure lost its durable replay intent')
    # Leave an owned attempt in SQLite, then reopen in a separate process.
    recovery=projects[2]
    recovery_task=launch(win,qapp,recovery,'Interrupted task','error','')
    receipt=win.core.executions.begin(recovery,recovery_task)
    win.db.commit()
    for pid in projects:
        switch(win,qapp,pid)
        win.show_section('plans')
        require(win.document_list.count()==len(expected[pid]),'Visible document isolation failed')
        assert_visible_isolation(win,pid)
    require(not list(win.db.execute('PRAGMA foreign_key_check')),'Broken foreign keys')
    # Capture exact durable bytes before closing for comparison after restart.
    files={}
    for pid in projects:
        workspace=win.core.workspace(pid)
        exports=list(workspace.rglob('*.pdf'))
        require(len(exports)==(0 if pid==projects[3] else len(expected[pid])),
            'Missing or unexpected native PDF exports')
        for path in exports:
            require(path.read_bytes().startswith(b'%PDF'),'Invalid native PDF')
        for path in workspace.rglob('*'):
            if path.is_file():
                files[str(path.relative_to(app.ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    # A later one-off task may pause an earlier rejected task in its project.
    # Assert that transition explicitly before recording restart expectations.
    for task,status,owner in tasks:
        actual=win.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',(task,)).fetchone()[0]
        require(actual==status,
            'Unfinished task lost during a later one-off task')
    state=dict(projects=projects,expected=expected,tasks=[(task,status) for task,status,owner in tasks],
               interrupted=receipt.attempt.attempt_id,recovery_task=recovery_task,deferred_task=deferred,files=files)
    (app.ROOT/'scenario.json').write_text(json.dumps(state),encoding='utf-8')
    shutdown_fence(win,qapp)


def restart(win,qapp):
    state=json.loads((app.ROOT/'scenario.json').read_text(encoding='utf-8'))
    require(win.db.execute('SELECT state FROM execution_attempts WHERE attempt_id=?',
        (state['interrupted'],)).fetchone()==('interrupted',),'Startup did not fence old worker')
    require(win.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
        (state['recovery_task'],)).fetchone()==('active',),'Restart discarded incomplete task')
    require(win.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
        (state['deferred_task'],)).fetchone()==('completed',),'Restart did not complete deferred export')
    recovered_exports=list(win.core.workspace(state['projects'][3]).rglob('*.pdf'))
    require(len(recovered_exports)==1 and recovered_exports[0].read_bytes().startswith(b'%PDF'),
        'Restart did not render the deferred native PDF')
    for task,status in state['tasks']:
        require(win.db.execute('SELECT status FROM ai_task_registry WHERE task_id=?',
            (task,)).fetchone()==(status,),'Restart changed task outcome')
    for pid in state['projects']:
        switch(win,qapp,pid)
        actual=[r[0] for r in win.db.execute('SELECT content FROM manager_documents WHERE project_id=?',(pid,))]
        require(sorted(actual)==sorted(state['expected'][pid]),'Restart changed project documents')
        win.show_section('plans')
        require(win.document_list.count()==len(actual),'Restart UI isolation failed')
        assert_visible_isolation(win,pid)
    for relative,digest in state['files'].items():
        require(hashlib.sha256((app.ROOT/relative).read_bytes()).hexdigest()==digest,
            'Restart changed an existing artifact: '+relative)
    from execution_coordinator import Attempt
    from execution_runtime import ExecutionReceipt
    pid=state['projects'][2]
    stale=ExecutionReceipt(pid,state['recovery_task'],
        Attempt(state['interrupted'],pid,state['recovery_task']))
    try:
        win.core.executions.authorize_result(stale,active_project_id=pid)
    except ValueError:
        pass
    else:
        raise AssertionError('Restart accepted a callback from a dead worker')
    resumed=launch(win,qapp,pid,'Interrupted task','accepted',
        '# Recovered comparison\n\nCompare the supplied options against the original brief.',resume=True)
    require(resumed==state['recovery_task'],'Recovery replaced the original task identity')
    require(not list(win.db.execute('PRAGMA foreign_key_check')),'Restart foreign key failure')


if __name__=='__main__':
    phase,seed=sys.argv[1],int(sys.argv[2])
    qapp=QApplication([])
    callback_errors=[]
    def exception_hook(kind,value,tb):
        callback_errors.append(f'{kind.__name__}: {value}')
        traceback.print_exception(kind,value,tb)
    sys.excepthook=exception_hook
    def qt_message(kind,context,message):
        print(message,file=sys.stderr,flush=True)
        if kind in (QtMsgType.QtCriticalMsg,QtMsgType.QtFatalMsg):
            callback_errors.append(message)
    qInstallMessageHandler(qt_message)
    win=app.Studio()
    win._recover_committed_document_exports()
    win.show()
    try:
        if phase=='execute':execute(win,qapp,seed)
        else:restart(win,qapp)
    finally:
        require(drain_finished_workers(qapp,win,10),'Leaked Qt workers')
        require(win.close(),'Final application close refused')
        qapp.processEvents()
    require(not callback_errors,'Unhandled Qt errors: '+repr(callback_errors))
    print(f'NATIVE_QT_SYNTHETIC_{phase.upper()}_OK seed={seed}',flush=True)
