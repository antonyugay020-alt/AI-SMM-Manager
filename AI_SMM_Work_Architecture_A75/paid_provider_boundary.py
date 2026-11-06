"""One mandatory pre-network authorization boundary for potentially billed AI APIs.

A GUI confirmation with an unknown provider price is NOT an authorization.
Until a trusted pricing adapter provides a conservative upper-bound quote,
unpriced OpenAI image/text and Ollama cloud-tag calls fail closed. The durable
gate and local-first dispatcher can reserve priced operations atomically.
"""
from cost_authorization import PaymentDenied


def require_priced_paid_call(provider, operation, *, cost_gate=None,
                             approval_token=None, verified_max_usd=None):
    if (cost_gate is None or not approval_token or
            verified_max_usd is None):
        raise PaymentDenied(
            'Платный вызов заблокирован: требуется проверенная максимальная '
            'стоимость, отдельное подтверждение операции и свободный '
            'остаток месячного бюджета. Используйте локальный или '
            'подтверждённый бесплатный сервис.')
    return cost_gate.reserve(provider, operation, verified_max_usd,
                             token=approval_token)
