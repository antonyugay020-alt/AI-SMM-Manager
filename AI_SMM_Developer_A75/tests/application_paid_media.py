"""Single application composition for Kie media, recovery and paid authorization.

The Qt queue supports explicit result refresh. Paid provider adapters are constructed
through this composition so it shares the exact durable ledger shown by Qt.
No quote or user approval is inferred here and no network call occurs at boot.
"""
from __future__ import annotations

from pathlib import Path

from durable_cost_gate import DurableCostGate
from kie_media_jobs import recover_pending
from kie_media_runtime import KieMediaGateway


class PaidMediaComposition:
    def __init__(self, db, workspace, ledger_path):
        if not callable(workspace):
            raise TypeError("project workspace resolver must be callable")
        path = Path(ledger_path)
        if not path.is_absolute():
            raise ValueError("paid ledger must have an absolute, stable path")
        self.db = db
        self.workspace = workspace
        self.ledger_path = path
        self.cost_gate = DurableCostGate(path)

    def gateway(self, *, upload_file, create_task, query_task,
                verified_price_quote, fetch_verified_file=None):
        """Bind paid provider transport and the GUI to one ledger instance.

        Provider routes and quotes must be externally verified and explicitly
        injected. This factory never enables paid generation by itself.
        """
        for name, adapter in (("upload_file", upload_file),
                              ("create_task", create_task),
                              ("query_task", query_task),
                              ("verified_price_quote", verified_price_quote)):
            if not callable(adapter):
                raise TypeError(name + " must be an explicit callable adapter")
        if fetch_verified_file is not None and not callable(fetch_verified_file):
            raise TypeError("fetch_verified_file must be callable")
        return KieMediaGateway(
            self.db, self.workspace, upload_file=upload_file,
            create_task=create_task, query_task=query_task,
            verified_price_quote=verified_price_quote,
            fetch_verified_file=fetch_verified_file, cost_gate=self.cost_gate)

    def recover_project(self, project_id):
        """Fence interrupted paid sends; never resubmit or poll remotely."""
        return recover_pending(self.db, project_id)

    def result_worker(self, project_id, job_id):
        """Create an idle read-only provider worker; no request occurs here."""
        from kie_media_client import KieMediaClient
        from kie_media_worker import KieResultWorker
        from kie_media_jobs import _owned_row
        row = _owned_row(self.db, project_id, job_id)
        if row[3] not in ("submitted", "completed"):
            raise RuntimeError("only confirmed tasks can fetch results")
        path = next((item[2] for item in self.db.execute("PRAGMA database_list")
                     if item[1] == "main"), "")
        if not path:
            raise RuntimeError("result worker requires a durable project database")
        factory=getattr(self,'media_client_factory',None)
        client = factory() if factory is not None else KieMediaClient()
        if row[3] == "submitted":
            client._credential()
        return KieResultWorker(path, self.workspace(project_id), project_id,
                               job_id, client.query_task,
                               fetch_verified_file=getattr(self,'media_result_fetcher',None))

    def native_gateway(self, *, verified_price_quote, api_key=None,
                       enable_paid=False, opener=None, fetch_verified_file=None):
        """Compose verified native HTTP adapters with the GUI's exact ledger.

        Upload consent remains enforced by prepare_image/prepare_video before
        their upload adapter runs. Paid generation stays off by default.
        """
        from kie_media_client import KieMediaClient
        client = KieMediaClient(api_key=api_key, enabled=enable_paid, opener=opener)
        return self.gateway(
            upload_file=lambda path: client.upload_file(path, explicit_upload_consent=True),
            create_task=client.create_task, query_task=client.query_task,
            verified_price_quote=verified_price_quote,
            fetch_verified_file=fetch_verified_file)

    def text_payment(self, project_id, task_id, model, messages, maximum_usd, *,
                     approval_token, temperature=.2, response_format=None):
        """Authorize one exact paid text request against the GUI's shared cap."""
        from kie_media_jobs import _project_exists
        from paid_text_runtime import reserve_text
        _project_exists(self.db,project_id)
        return reserve_text(self.cost_gate,project_id,task_id,model,messages,
                            maximum_usd,approval_token=approval_token,
                            temperature=temperature,response_format=response_format)

    def generation_intent(self, project_id, request):
        from media_generation_runtime import create_intent
        return create_intent(self.db,self.workspace,project_id,request)

    def _database_path(self):
        path=next((row[2] for row in self.db.execute('PRAGMA database_list') if row[1]=='main'),'')
        if not path:raise RuntimeError('Durable application database required')
        return path

    def _generation_factory(self, owner, job):
        gate=self.cost_gate
        client_factory=getattr(self,'media_client_factory',None)
        def factory(db,workspace):
            from kie_media_client import KieMediaClient
            from media_generation_runtime import quote_for_job
            client=client_factory() if client_factory is not None else KieMediaClient(enabled=True)
            return KieMediaGateway(db,workspace,
                upload_file=lambda path:client.upload_file(path,explicit_upload_consent=True),
                create_task=client.create_task,query_task=client.query_task,cost_gate=gate,
                verified_price_quote=lambda *_:quote_for_job(db,owner,job))
        return factory

    def preparation_worker(self, owner, job):
        from kie_media_worker import KieGenerationWorker
        from media_generation_runtime import intent,check_intent
        intent(self.db,owner,job);check_intent(self.db,owner,job)
        return KieGenerationWorker(self._database_path(),self.workspace(owner),owner,job,
                                   self._generation_factory(owner,job),operation='prepare')

    def approve_generation(self,owner,job,maximum_usd,evidence,*,confirmed=False):
        from datetime import datetime,timezone,timedelta
        from media_generation_runtime import confirm_quote
        from media_task_control import check_active
        check_active(self.db,owner,job)
        maximum=confirm_quote(self.db,owner,job,maximum_usd,evidence,confirmed=confirmed,
            expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat())
        return self.cost_gate.approve('kie.ai','media:'+job,maximum)

    def submission_worker(self,owner,job,approval_token):
        from kie_media_worker import KieGenerationWorker
        from media_generation_runtime import quote_for_job
        from kie_media_jobs import _owned_row
        row=_owned_row(self.db,owner,job)
        if row[3]!='prepared':raise RuntimeError('Only unpaid drafts may be submitted')
        quote_for_job(self.db,owner,job)
        return KieGenerationWorker(self._database_path(),self.workspace(owner),owner,job,
            self._generation_factory(owner,job),operation='submit',approval_token=approval_token)

    def stop_generation(self,owner,job):
        from media_generation_runtime import install,intent
        from media_task_control import request_stop
        if self.db.in_transaction:raise RuntimeError('Foreign transaction owns cancellation')
        install(self.db)
        found=self.db.execute('SELECT 1 FROM media_generation_intents WHERE id=? AND project_id=?',(job,owner)).fetchone()
        if found:
            intent(self.db,owner,job)
            self.db.execute('UPDATE media_generation_intents SET stop_requested=1 WHERE id=? AND project_id=?',(job,owner))
            self.db.commit()
        table=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kie_media_tasks'").fetchone()
        if table and self.db.execute('SELECT 1 FROM kie_media_tasks WHERE id=? AND project_id=?',(job,owner)).fetchone():
            return request_stop(self.db,owner,job)
        if not found:raise ValueError('Unknown owned media task')
        return True

    def intent_worker(self,owner,request):
        from uuid import uuid4
        from kie_media_jobs import _project_exists
        from kie_media_worker import KieGenerationWorker
        _project_exists(self.db,owner)
        return KieGenerationWorker(self._database_path(),self.workspace(owner),owner,uuid4().hex,
                                   None,operation='draft',request=request)

    def recovery_worker(self,owner,job,provider_id,evidence,*,confirmed=False):
        if confirmed is not True:raise PermissionError('Explicit provider-log confirmation required')
        from kie_media_jobs import _owned_row
        from kie_media_worker import KieGenerationWorker
        if _owned_row(self.db,owner,job)[3]!='submission_unknown':
            raise RuntimeError('Only uncertain paid submissions can attach a provider ID')
        return KieGenerationWorker(self._database_path(),self.workspace(owner),owner,job,
            self._generation_factory(owner,job),operation='reconcile',
            recovery={'provider_id':provider_id,'evidence':evidence})
