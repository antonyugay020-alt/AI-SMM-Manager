"""End-to-end durable Kie intent provenance, no paid network I/O."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from kie_media_jobs import prepare
from media_intent_binding import require_bound
from media_generation_runtime import install as install_intents,prepare_intent


def _db():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
    db.execute("INSERT INTO projects VALUES ('owner')")
    db.commit()
    return db


def test_prepared_payload_is_atomically_bound_to_immutable_intent():
    db=_db();job='b'*32
    payload={'model':'nano-banana-pro','input':{'prompt':'forest'}}
    prepare(db,'owner',payload,job_id=job,intent_sha256='a'*64)
    digest=db.execute('SELECT payload_sha256 FROM kie_media_tasks WHERE id=?',(job,)).fetchone()[0]
    assert require_bound(db,'owner',job,'a'*64,digest)
    for owner,identity,original,content in [
        ('wrong',job,'a'*64,digest),('owner','c'*32,'a'*64,digest),
        ('owner',job,'c'*64,digest),('owner',job,'a'*64,'d'*64)]:
        with pytest.raises(ValueError,match='(not bound|Unbound)'):
            require_bound(db,owner,identity,original,content)
    db.close()


def test_legacy_orphan_is_not_adopted_by_prompt_only():
    db=_db();job='c'*32
    prepare(db,'owner',{'model':'nano-banana-pro','input':{'prompt':'forest'}},job_id=job)
    install_intents(db)
    request={'mode':'image_pro','prompt':'forest','sources':[]}
    text=json.dumps(request,separators=(',',':'))
    db.execute("""INSERT INTO media_generation_intents
        (id,project_id,model,request_json,request_sha256,state,stop_requested,
         claim,error_code,created_at,updated_at) VALUES (?,?,?,?,?,'draft',0,NULL,NULL,'t','t')""",
        (job,'owner','nano-banana-pro',text,hashlib.sha256(text.encode()).hexdigest()))
    db.commit()
    with pytest.raises(ValueError,match='manual reconciliation'):
        prepare_intent(db,lambda _:Path('/tmp'),'owner',job,object(),
                       explicit_upload_consent=True)
    assert db.execute('SELECT state FROM media_generation_intents WHERE id=?',(job,)).fetchone()[0]=='draft'
    db.close()


def test_failed_intent_binding_rolls_back_entire_prepared_task():
    db=_db();job='d'*32
    with pytest.raises(ValueError,match='digest'):
        prepare(db,'owner',{'model':'nano-banana-pro','input':{}},
                job_id=job,intent_sha256='invalid')
    table=db.execute("SELECT 1 FROM sqlite_master WHERE name='kie_media_tasks'").fetchone()
    assert table is None or db.execute('SELECT 1 FROM kie_media_tasks WHERE id=?',(job,)).fetchone() is None
    db.close()
