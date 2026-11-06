"""Shared provider errors without importing a transport implementation.

Both Ollama and cloud API code must use the exact same exception classes,
while the API transport must not depend on the Ollama transport module.
"""

class OllamaStageError(RuntimeError):
    def __init__(self, stage, detail):
        self.stage = stage
        super().__init__(f'{stage}: {detail}')


class AuthorExecutionAborted(OllamaStageError):
    """Terminal case-budget event; not a content defect or revision request."""
    pass


class ProviderHTTPError(OllamaStageError):
    """Provider/transport failure; never a semantic rejection of a draft."""
    def __init__(self, stage, status, response_text=''):
        self.status_code = int(status)
        self.response_text = response_text
        self.response_diagnostics = {'http_status': self.status_code,
                                     'body_chars': len(response_text)}
        super().__init__(stage, f'HTTP {self.status_code}: ошибка провайдера модели')
