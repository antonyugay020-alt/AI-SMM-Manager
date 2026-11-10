"""Native Qt bridge for per-request paid text approval; never approves silently.

Workers wait cooperatively; dialogs and all application SQL stay on the GUI
thread. Approval grants bind the exact request to the same media/text ledger.
"""
from dataclasses import dataclass,field
from threading import Event
from time import monotonic
import weakref
from PySide6.QtCore import QObject,Signal,Slot,Qt,QThread
from PySide6.QtWidgets import QInputDialog,QMessageBox
from cost_authorization import PaymentDenied
from durable_cost_gate import units
from paid_text_runtime import operation_for


@dataclass
class _Request:
    worker: object
    owner: str
    task: str
    model: str
    messages: list
    options: dict
    ready: Event=field(default_factory=Event)
    cancelled: bool=False
    payment: object=None
    error: str='Paid text approval denied'


class TextApprovalBroker(QObject):
    requested=Signal(object)

    def __init__(self,window,service):
        super().__init__(window)
        self.window=weakref.proxy(window);self.service=service
        self.requested.connect(self._handle,Qt.QueuedConnection)

    def for_worker(self,worker,owner,task):
        def request(model,messages,**options):
            import copy
            item=_Request(worker,owner,task,model,copy.deepcopy(messages),dict(options))
            if QThread.currentThread() is self.thread():
                self._handle(item)
            else:
                self.requested.emit(item)
                deadline=monotonic()+300
                try:
                    while not item.ready.wait(.05):
                        from cooperative_cancellation import check_cancelled
                        check_cancelled()
                        if worker.isInterruptionRequested() or monotonic()>deadline:
                            raise PaymentDenied('Paid text approval interrupted or expired')
                except BaseException:
                    item.cancelled=True;raise
            if item.cancelled or item.payment is None:raise PaymentDenied(item.error)
            return item.payment
        return request

    def _valid(self,item):
        if (item.cancelled or item.worker.isInterruptionRequested()
                or self.window.current_project!=item.owner
                or self.window.task_worker is not item.worker):return False
        attempt=getattr(item.worker,'execution_attempt',None)
        if attempt is not None:
            row=self.service.db.execute('''SELECT a.state,t.status FROM execution_attempts a
                JOIN ai_task_registry t ON a.task_id=t.task_id AND a.project_id=t.project_id
                WHERE a.attempt_id=? AND a.task_id=? AND a.project_id=?''',
                (attempt.attempt_id,item.task,item.owner)).fetchone()
            if row!=('running','active'):return False
        return True

    @Slot(object)
    def _handle(self,item):
        try:
            if not self._valid(item):return
            maximum,ok=QInputDialog.getText(self.window,'Платный текстовый запрос',
                'Модель: '+item.model+'\nПроверенная максимальная стоимость одного запроса, USD:')
            if not ok or not self._valid(item):return
            if units(maximum)<=0:raise PaymentDenied('Positive verified maximum required')
            evidence,ok=QInputDialog.getText(self.window,'Источник тарифа',
                'Источник проверки максимальной стоимости (до 256 символов):')
            if not ok or not evidence.strip() or len(evidence)>256 or not self._valid(item):return
            reply=QMessageBox.question(self.window,'Разрешить один платный запрос?',
                'Модель: '+item.model+'\nМаксимум: $'+maximum+'\nИсточник: '+evidence+
                '\nСтоимость резервируется в общем месячном бюджете. При таймауте резерв сохраняется.')
            if reply!=QMessageBox.Yes or not self._valid(item):return
            operation=operation_for(item.owner,item.task,item.model,item.messages,**item.options)
            provider=item.model.partition('/')[0]
            token=self.service.cost_gate.approve(provider,operation,maximum)
            item.payment=self.service.text_payment(item.owner,item.task,item.model,item.messages,
                maximum,approval_token=token,**item.options)
            self.service.cost_gate.record_authorization_evidence(item.payment.reservation,
                evidence=evidence,confirmed_by_user=True)
        except Exception as exc:
            item.payment=None;item.error='Paid text authorization unavailable: '+type(exc).__name__
        finally:item.ready.set()
