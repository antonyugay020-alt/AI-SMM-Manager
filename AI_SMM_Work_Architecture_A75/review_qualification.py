"""Bounded, randomized competence controls for independent reviewer admission.

Six known-answer contrasts establish elementary scope/source/requirement skills,
not arbitrary-text quality. No user text, credentials or paid-provider discovery.
"""
from __future__ import annotations
import copy
import hashlib
import json
import random
import secrets
import time
from threading import RLock
from review_arbitration import adjudicate_findings, FindingReviewUnavailable
from ollama_resilience import model_call_role, request_chat, capture_utf8_transport

PROTOCOL = 'review_qualification_host_scope_r77_v2'
CERT_ENV = 'AI_SMM_REVIEW_QUALIFICATIONS'
_CACHE = {}
_LOCK = RLock()


def qualification_summary(record):
    """Safe admission evidence; never include raw prompts, replies or keys."""
    controls=record.get('controls') if isinstance(record,dict) else None
    rows=[row for row in controls if isinstance(row,dict)] if isinstance(controls,list) else []
    return {'model':record.get('model'),'passed':record.get('pass') is True,
            'contract_pass':record.get('contract_pass') is True,
            'error':str(record.get('error') or '')[:300] or None,
            'controls_passed':sum(row.get('pass') is True for row in rows),
            'controls_total':len(rows),
            'failed_controls':[{'kind':row.get('kind'),'expected':row.get('expected'),
                                'actual':row.get('actual')} for row in rows if row.get('pass') is not True]}


class ReviewerQualificationUnavailable(ValueError):
    review_unavailable=True
    retryable=False
    def __init__(self,message,results=()):
        self.results=copy.deepcopy([row for row in results if isinstance(row,dict)])
        self.summaries=[qualification_summary(row) for row in self.results]
        if self.summaries:
            statuses=[]
            for row in self.summaries:
                label=('допущен' if row['passed'] else 'не допущен')
                score=str(row['controls_passed'])+'/'+str(row['controls_total'])
                note=('; '+row['error']) if row.get('error') and not row['contract_pass'] else ''
                statuses.append(str(row['model'])+': '+label+' ('+score+note+')')
            message+='\nКвалификация редакторов: '+'; '.join(statuses)
        super().__init__(message)


def build_controls(seed):
    """Create a new balanced test with an oracle kept outside the model input."""
    if type(seed) is not int or not 0<=seed<2**64:
        raise ValueError('Нужен целочисленный seed от 0 до 2**64-1')
    rng=random.Random(seed)
    words=('кедр','янтарь','липа','агат','ирис','сапфир','топаз','клён','берилл','ольха')
    selected=rng.sample(words,6)
    values=rng.sample(range(10,95),3)
    suffix=''.join(rng.choice('abcdef0123456789') for _ in range(10))
    source_label='Источник_'+suffix;current='Текущий_'+suffix;pending='Следующий_'+suffix
    literal=selected[4]+' '+selected[5]
    request=(f'Проверь только существующие поля текущего документа «{current}». '
        f'Отдельный документ «{pending}» должен создаваться позже; его отсутствие сейчас допустимо. '
        f'Подтверждённый источник «{source_label}»: параметр «{selected[0]}» имеет значение {values[0]}. '
        f'В полях о параметре нужен именно этот подтверждённый факт без изменённых значений. '
        f'В обоих полях с названием «требование» должна дословно присутствовать фраза «{literal}». '
        'Для поля «структура» нужна строка с меткой «Решение» и описанием действия. '
        f'Документ «{pending}» нельзя вставлять в текущий документ.')
    data=[
        ('scope_pending',f'План документа «{current}». Решение: выполнить описанное действие.',
         f'В текущем документе отсутствует отдельный ожидаемый документ «{pending}»; добавьте его целиком.',False),
        ('scope_local',f'Вводная заметка о документе «{current}».',
         'В поле «структура» нет обязательной строки «Решение» и описания действия.',True),
        ('source_supported',f'Параметр «{selected[0]}» имеет значение {values[0]}.',
         f'Значение параметра не подтверждено источником «{source_label}».',False),
        ('source_changed',f'Параметр «{selected[0]}» имеет значение {values[1]}.',
         'Значение параметра отличается от подтверждённого источника.',True),
        ('literal_present',f'Требование выполнено: {literal}.',
         'Обязательная дословная фраза отсутствует.',False),
        ('literal_missing',f'Требование выполнено: {selected[2]} {selected[3]}.',
         'Обязательная дословная фраза отсутствует.',True),
    ]
    rng.shuffle(data)
    fields={};findings=[];oracle={}
    for index,(kind,text,reason,needs_change) in enumerate(data,1):
        # Opaque, randomly assigned IDs reveal neither the oracle nor its polarity.
        label=('требование' if kind.startswith('literal') else 'структура' if kind=='scope_local' else 'поле')
        key=f'{label}_{index}_{suffix}'
        fields[key]=text;oracle[key]={'kind':kind,'needs_change':needs_change}
        findings.append({'field':key,'quote':text,'reason':reason,
            'review_scope':{'stage':'qualification_current_artifact',
                'available_artifacts':[current],'pending_artifacts':[pending]}})
    for key,expected in oracle.items():
        if expected['kind']=='scope_local':
            request+=f' Поле «{key}»: обязательна строка с меткой «Решение» и описанием действия.'
        elif expected['kind'].startswith('literal'):
            request+=f' Поле «{key}»: дословно включи «{literal}».'
        elif expected['kind'].startswith('source'):
            request+=f' Поле «{key}»: значение параметра должно соответствовать источнику «{source_label}».'
    return request,fields,findings,oracle


def _valid_record(record,model):
    if (not isinstance(record,dict) or record.get('protocol')!=PROTOCOL
            or record.get('model')!=model or record.get('pass') is not True
            or record.get('contract_pass') is not True
            or not isinstance(record.get('controls'),list) or len(record['controls'])!=6):
        return False
    kinds={'scope_pending','scope_local','source_supported','source_changed','literal_present','literal_missing'}
    if {row.get('kind') for row in record['controls'] if isinstance(row,dict)}!=kinds:
        return False
    if not all(isinstance(row,dict) and row.get('pass') is True for row in record['controls']):
        return False
    seed=record.get('seed')
    if type(seed) is not int or not 0<=seed<2**64 or record.get('model_calls')!=1:return False
    _,_,_,oracle=build_controls(seed)
    rows=record['controls']
    if {row.get('field') for row in rows}!=set(oracle):return False
    for row in rows:
        expected=oracle[row['field']]
        decision='confirmed' if expected['needs_change'] else 'dismissed'
        if (row.get('kind')!=expected['kind'] or row.get('expected')!=decision
                or row.get('actual')!=decision):return False
    timestamp=record.get('checked_at')
    return type(timestamp) in (int,float) and 0<=time.time()-timestamp<8*3600


def cached_qualification(model):
    import os
    name=str(model).strip()
    with _LOCK:
        cached=_CACHE.get(name)
        if (isinstance(cached,dict) and cached.get('protocol')==PROTOCOL
                and type(cached.get('checked_at')) in (int,float)
                and 0<=time.time()-cached['checked_at']<(8*3600 if cached.get('contract_pass') is True or cached.get('contract_errors') else 120)
                and (cached.get('pass') is False or _valid_record(cached,name))):
            return copy.deepcopy(cached)
    raw=os.environ.get(CERT_ENV,'')
    if raw:
        try:
            envelope=json.loads(raw)
            record=envelope.get('results',{}).get(name) if isinstance(envelope,dict) and envelope.get('protocol')==PROTOCOL else None
            if _valid_record(record,name):return copy.deepcopy(record)
        except (ValueError,AttributeError,TypeError):
            pass
    return None


def probe_qualification(model,*,seed=None,transport=None,force=False,report=None):
    """At most one model request; malformed results never trigger more traffic."""
    if not isinstance(model,str) or not model.strip():raise ValueError('Пустая модель проверки')
    name=model.strip()
    if not force:
        cached=cached_qualification(name)
        if cached is not None:
            from diagnostic_report import record_model_event
            record_model_event('reviewer_qualification_result',cached=True,**qualification_summary(cached))
            return cached
    seed=secrets.randbits(64) if seed is None else seed
    request,fields,findings,oracle=build_controls(seed)
    send=transport or request_chat
    calls=0;contract=False;resolved=[];error='';response_diagnostics={}
    contract_errors=[];raw_reply='';raw_reply_truncated=False;author_response_compatible=False
    utf8_evidence={}
    started=time.monotonic()
    def ask(reviewer,system,user,*,json_schema):
        nonlocal calls,response_diagnostics,raw_reply,raw_reply_truncated,author_response_compatible
        if calls:raise RuntimeError('Квалификация ограничена одним запросом; неверный контракт не повторяется')
        calls+=1
        body={'model':reviewer,'stream':False,'think':False,'format':json_schema,'keep_alive':0,
            'messages':[{'role':'system','content':system},{'role':'user','content':user}],
            'options':{'temperature':0,'num_ctx':8192,'num_predict':1800}}
        encoded=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode('utf-8',errors='strict')
        utf8_evidence.update(input_sha256=hashlib.sha256(encoded).hexdigest(),
            input_bytes=len(encoded),input_payload=body,encoding='utf-8',strict_roundtrip=json.loads(encoded.decode('utf-8'))==body)
        with capture_utf8_transport() as wire_evidence:
            try:
                # Allow local 14B cold-start and 8K-context generation; admission oracle remains unchanged.
                response=send(body,stage='проверка компетенции модели-рецензента',timeout=180,attempts=1,report=report)
            finally:
                utf8_evidence['wire']=wire_evidence
        from ollama_resilience import response_diagnostics as diagnostics
        response_diagnostics=diagnostics(response)
        content=response.get('message',{}).get('content','') if isinstance(response,dict) else ''
        raw_reply=content[:18000] if isinstance(content,str) else ''
        raw_reply_truncated=isinstance(content,str) and len(content)>18000
        if isinstance(content,str):
            encoded_reply=content.encode('utf-8',errors='strict')
            utf8_evidence.update(reply_sha256=hashlib.sha256(encoded_reply).hexdigest(),
                reply_bytes=len(encoded_reply),replacement_character_present='\ufffd' in content)
        try:
            author_response_compatible=(isinstance(content,str) and isinstance(json.loads(content),dict)
                and response.get('done_reason')!='length')
        except ValueError:
            author_response_compatible=False
        return content
    def calibration_report(stage,**details):
        if stage=='review_contract_invalid':contract_errors.append(copy.deepcopy(details))
        if report:report(stage,**details)
    try:
        with model_call_role('reviewer'):
            confirmed,dismissed=adjudicate_findings(ask,name,request,fields,findings,report=calibration_report)
        resolved=confirmed+dismissed;contract=True
    except FindingReviewUnavailable as exc:
        resolved=getattr(exc,'assessed_findings',exc.findings)
        contract=getattr(exc,'contract_valid',False) is True
        error=str(exc)[:500]
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc)[:400]
    if contract_errors and not contract:
        error=str(contract_errors[0].get('reason') or error)[:500]
    by_field={item.get('field'):item for item in resolved if isinstance(item,dict)}
    outcomes=[]
    for field,expected in oracle.items():
        actual=(by_field.get(field,{}).get('finding_validation') or {}).get('decision')
        wanted='confirmed' if expected['needs_change'] else 'dismissed'
        outcomes.append({'kind':expected['kind'],'field':field,'expected':wanted,'actual':actual,
            'pass':actual==wanted})
    result={'protocol':PROTOCOL,'model':name,'seed':seed,'checked_at':time.time(),
        'contract_pass':contract,'author_response_compatible':author_response_compatible,
        'pass':contract and all(row['pass'] for row in outcomes),
        'controls':outcomes,'model_calls':calls,'duration_s':round(time.monotonic()-started,2),
        'error':error or None,'response_diagnostics':response_diagnostics,
        'utf8_evidence':utf8_evidence,'contract_errors':contract_errors,'raw_reply':raw_reply,'raw_reply_truncated':raw_reply_truncated,
        'semantic_controls_evaluated':len(outcomes) if contract else 0,
        'semantic_controls_correct':sum(row['pass'] for row in outcomes) if contract else None,
        'scope':'Elementary known-answer controls only; not certification of arbitrary-text quality'}
    with _LOCK:_CACHE[name]=copy.deepcopy(result)
    from diagnostic_report import record_model_event
    record_model_event('reviewer_qualification_result',cached=False,**qualification_summary(result))
    if report:report('reviewer_qualification_completed',model=name,passed=result['pass'],
        controls_passed=sum(row['pass'] for row in outcomes),controls_total=len(outcomes),
        duration_s=result['duration_s'])
    return copy.deepcopy(result)


def qualification_envelope(records):
    # Keep bounded proofs in the child environment; raw diagnostic replies stay
    # in the report (Windows environment variables have a finite size).
    keys=('protocol','model','seed','checked_at','contract_pass','pass','controls','model_calls')
    return {'protocol':PROTOCOL,'results':{row['model']:{key:copy.deepcopy(row[key])
        for key in keys if key in row} for row in records if row.get('pass') is True}}
