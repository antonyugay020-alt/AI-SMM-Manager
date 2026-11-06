"""Local-first provider selection with fail-closed paid dispatch.

Adapters expose .name, .tier ('local', 'free', 'paid'), .supports(task),
and .execute(task). Paid adapters additionally expose .estimate_usd(task)
and .actual_usd(result). No provider call occurs while deciding eligibility.
"""
from dataclasses import dataclass
from cost_authorization import PaymentDenied


@dataclass(frozen=True)
class DispatchResult:
    provider: str
    tier: str
    output: object


class NoProviderAvailable(RuntimeError):
    pass


class LocalFirstDispatcher:
    def __init__(self, providers, cost_gate):
        self.providers = tuple(providers)
        self.cost_gate = cost_gate

    def run(self, task, *, approved_provider=None, approval_token=None):
        failures = []
        for tier in ("local", "free", "paid"):
            for provider in self.providers:
                if provider.tier != tier or not provider.supports(task):
                    continue
                if tier == "paid":
                    # Paid calls are never an implicit fallback.
                    if not approval_token or provider.name != approved_provider:
                        continue
                    estimate = provider.estimate_usd(task)
                    reservation = self.cost_gate.reserve(
                        provider.name, task.operation, estimate, token=approval_token)
                    try:
                        output = provider.execute(task)
                    except Exception:
                        # Unknown charge status: leave reservation committed.
                        raise
                    self.cost_gate.settle(reservation, provider.actual_usd(output))
                    return DispatchResult(provider.name, tier, output)
                try:
                    return DispatchResult(provider.name, tier, provider.execute(task))
                except Exception as exc:
                    failures.append((provider.name, type(exc).__name__))
        raise NoProviderAvailable(
            "No eligible provider completed the task; paid providers require explicit approval. "
            + repr(failures))
