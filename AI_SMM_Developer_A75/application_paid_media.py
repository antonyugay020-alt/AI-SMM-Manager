"""Single application composition for Kie media, recovery and paid authorization.

The Qt queue remains offline. A future provider adapter must be constructed
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
