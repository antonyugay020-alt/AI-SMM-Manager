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
        client = KieMediaClient()
        if row[3] == "submitted":
            client._credential()
        return KieResultWorker(path, self.workspace(project_id), project_id,
                               job_id, client.query_task)

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
