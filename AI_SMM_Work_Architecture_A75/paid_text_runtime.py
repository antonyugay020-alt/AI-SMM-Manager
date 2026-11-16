"""Request-bound, single-dispatch text authority using the shared paid ledger.

No context means no paid request. A timed-out/incomplete response retains its
reservation and cannot reuse its dispatch right after restart. Only verified
billing reconciliation can release/settle a reservation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from durable_cost_gate import DurableCostGate
from cost_authorization import PaymentDenied

_CURRENT = ContextVar('ai_smm_text_payment', default=None)


def operation_for(project_id, task_id, model, messages, *, temperature=.2,
                  response_format=None):
    if not all(isinstance(x,str) and x.strip() for x in (project_id,task_id,model)):
        raise PaymentDenied('Project, task and model identity required')
    encoded=json.dumps([model,messages,temperature,response_format],sort_keys=True,
                       ensure_ascii=False,separators=(',',':'),allow_nan=False)
    digest=hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    return 'text:'+project_id+':'+task_id+':'+digest


@dataclass(frozen=True)
class TextPayment:
    gate: DurableCostGate
    provider: str
    operation: str
    reservation: str
    project_id: str
    task_id: str

    def claim(self, model, messages, *, temperature=.2, response_format=None):
        expected=operation_for(self.project_id,self.task_id,model,messages,
                               temperature=temperature,response_format=response_format)
        if expected!=self.operation or model.partition('/')[0]!=self.provider:
            raise PaymentDenied('Text approval does not match this exact request')
        self.gate.claim_dispatch(self.reservation,self.provider,self.operation)


def reserve_text(gate, project_id, task_id, model, messages, maximum_usd, *,
                 approval_token, temperature=.2, response_format=None):
    if not isinstance(gate,DurableCostGate):
        raise PaymentDenied('Shared durable cost ledger required')
    operation=operation_for(project_id,task_id,model,messages,temperature=temperature,
                            response_format=response_format)
    provider=model.partition('/')[0]
    reservation=gate.reserve(provider,operation,maximum_usd,token=approval_token,unique_operation=True)
    return TextPayment(gate,provider,operation,reservation,project_id,task_id)


@contextmanager
def text_payment_scope(payment):
    if not isinstance(payment,TextPayment):
        raise PaymentDenied('Explicit text payment authority required')
    token=_CURRENT.set(payment)
    try:
        yield payment
    finally:
        _CURRENT.reset(token)


def claim_text(model, messages, *, payment=None, temperature=.2, response_format=None):
    authority=payment if payment is not None else _CURRENT.get()
    if authority is None and _REQUESTER.get() is not None:
        authority=_REQUESTER.get()(model,messages,temperature=temperature,response_format=response_format)
    from cooperative_cancellation import check_cancelled
    check_cancelled()
    if not isinstance(authority,TextPayment):
        raise PaymentDenied('Paid text requires request-bound approval and durable budget reservation')
    authority.claim(model,messages,temperature=temperature,response_format=response_format)

_REQUESTER=ContextVar('ai_smm_text_approval_requester',default=None)


@contextmanager
def text_authorization_scope(requester):
    if requester is None:
        yield
        return
    if not callable(requester):raise PaymentDenied('Text approval requester must be callable')
    token=_REQUESTER.set(requester)
    try:yield
    finally:_REQUESTER.reset(token)
