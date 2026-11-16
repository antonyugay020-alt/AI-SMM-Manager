"""Explicit real-ledger grants for OFFLINE provider fixtures, never live APIs."""
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock
from text_providers import call_text as _raw_call


def authorized_call(model,messages,**kwargs):
    import text_providers
    from durable_cost_gate import DurableCostGate
    from paid_text_runtime import reserve_text,operation_for
    if model.partition('/')[0] not in ('openai','anthropic'):
        return _raw_call(model,messages,**kwargs)
    # The caller must explicitly replace the network/transport in this fixture.
    if not isinstance(text_providers._chat_request,Mock) and not (
            isinstance(text_providers.urllib.request.urlopen,Mock)
            or getattr(text_providers.urllib.request.urlopen,'__module__','')!='urllib.request'):
        raise AssertionError('Offline text fixture requires a mocked transport')
    with TemporaryDirectory() as folder:
        gate=DurableCostGate(Path(folder)/'ledger.sqlite')
        options={k:kwargs[k] for k in ('temperature','response_format') if k in kwargs}
        operation=operation_for('fixture-project','fixture-task',model,messages,**options)
        token=gate.approve(model.partition('/')[0],operation,'1')
        payment=reserve_text(gate,'fixture-project','fixture-task',model,messages,'1',
                             approval_token=token,**options)
        return _raw_call(model,messages,payment=payment,**kwargs)
