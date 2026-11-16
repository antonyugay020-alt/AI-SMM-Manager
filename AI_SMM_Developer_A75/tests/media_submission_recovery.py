"""Explicit evidence-backed attachment of an uncertain paid submission ID.

Only a read-only status request is made. The remote saved model/input must
match the exact immutable local payload, not merely its prompt or model name.
No cost reservation is freed/settled and no createTask retry is performed.
"""
import json
from contextlib import closing
from durable_cost_gate import DurableCostGate
from kie_media_contracts import TASK_ID
from kie_media_jobs import _owned_row,utcnow
from cost_authorization import PaymentDenied


def recover_submission(db,owner,job,provider_id,query_task,cost_gate,*,evidence,confirmed=False):
    if confirmed is not True:raise PaymentDenied('Explicit provider-log confirmation required')
    if not isinstance(provider_id,str) or not TASK_ID.fullmatch(provider_id):raise ValueError('Invalid provider task ID')
    if not isinstance(evidence,str) or not evidence.strip() or len(evidence)>256:raise ValueError('Provider log evidence required')
    if not isinstance(cost_gate,DurableCostGate):raise PaymentDenied('Shared durable ledger required')
    if db.in_transaction:raise RuntimeError('Foreign transaction owns submission recovery')
    row=_owned_row(db,owner,job)
    if row[3]!='submission_unknown':raise RuntimeError('Only uncertain paid submissions can attach an ID')
    reservation=db.execute('SELECT cost_reservation_id FROM kie_media_tasks WHERE id=? AND project_id=?',(job,owner)).fetchone()[0]
    with closing(cost_gate._connect()) as ledger:
        authorized=ledger.execute('''SELECT g.provider,g.operation,r.dispatch_claimed FROM cost_reservations r
            JOIN cost_grants g ON r.grant_token=g.token WHERE r.id=?''',(reservation,)).fetchone()
    if authorized!=('kie.ai','media:'+job,1):
        raise PaymentDenied('No durable dispatched reservation matches this task')
    status=query_task(provider_id)
    if not isinstance(status,dict) or status.get('code')!=200:raise ValueError('Remote recovery status unavailable')
    data=status.get('data')
    if not isinstance(data,dict) or data.get('taskId')!=provider_id or data.get('model')!=row[0]:
        raise ValueError('Remote recovery identity mismatch')
    parameters=data.get('param')
    if not isinstance(parameters,str) or len(parameters)>160000:raise ValueError('Remote original request is not available')
    parameters=json.loads(parameters)
    if not isinstance(parameters,dict):raise ValueError('Remote request is invalid')
    inputs=parameters.get('input')
    if isinstance(inputs,str):inputs=json.loads(inputs)
    expected=json.loads(row[1])
    if parameters.get('model')!=expected['model'] or inputs!=expected['input']:
        raise ValueError('Remote original request differs from the local paid intent')
    try:
        db.execute('BEGIN IMMEDIATE')
        current=_owned_row(db,owner,job)
        if current[3]!='submission_unknown' or current[2]!=row[2]:
            raise RuntimeError('Task changed during recovery verification')
        changed=db.execute("UPDATE kie_media_tasks SET state='submitted',provider_task_id=?,error_code=NULL,updated_at=? WHERE id=? AND project_id=? AND state='submission_unknown'",
                           (provider_id,utcnow(),job,owner)).rowcount
        if changed!=1:raise RuntimeError('Recovery was fenced')
        db.execute('''CREATE TABLE IF NOT EXISTS media_submission_recoveries (
            job_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,provider_task_id TEXT NOT NULL,
            evidence TEXT NOT NULL,created_at TEXT NOT NULL)''')
        db.execute('INSERT INTO media_submission_recoveries VALUES(?,?,?,?,?)',(job,owner,provider_id,evidence.strip(),utcnow()))
        db.commit()
    except BaseException:db.rollback();raise
    return provider_id
