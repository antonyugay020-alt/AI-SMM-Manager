"""Deterministic task lifecycle boundary; no industry-specific rules."""
import re

_RESUME = re.compile(r'^\s*(?:пожалуйста[, ]*)?(?:продолж(?:и|ить|ай)(?:\s+(?:выполнение|работу|задание|предыдущее\s+задание|незаверш[её]нное\s+задание|с\s+места\s+остановки))?|возобнови(?:\s+задание)?|повтори\s+недостающий\s+этап|доделай\s+задание|доработай\s+незаверш[её]нную\s+задачу|заверши(?:ть)?\s+проект)[.!\s]*$', re.I)
_RESUME_LEADING = re.compile(r'^\s*(?:пожалуйста[, ]*)?(?:продолж(?:и|ай|ить)|возобнови|доделай|доработай\s+незаверш[её]нную\s+задачу|заверши(?:ть)?\s+проект)\b',re.I)
_NEW_INTENT = re.compile(r'\b(?:создай|разработай|подготовь|сделай)\s+нов\w*|\b(?:новый\s+проект|измени\s+готовый|отредактируй\s+готовый)\b', re.I)


def resolve_request(message, pending_request=None, pending_question=None):
    """Only explicit resume commands inherit the pending request.

    A full new request always wins, even in the same project. Clarification
    answers are passed separately to the general agent, not mistaken for resume.
    """
    message = str(message or '').strip()
    pending_request=(pending_request if isinstance(pending_request,str) and pending_request.strip() else None)
    resume=(_RESUME.fullmatch(message) or
            (_RESUME_LEADING.match(message) and not _NEW_INTENT.search(message)))
    if resume:
        return (pending_request,'resume') if pending_request else (message,'resume_missing')
    # A short answer is a clarification only when a genuine question is pending.
    # Never classify a new business assignment as an answer.
    question = str(pending_question or '').strip()
    if (pending_request and '?' in question and len(message) <= 90
            and '?' not in message
            and not re.search(r'\b(?:нов(?:ый|ое|ая)|бизнес|разработай|создай|подготовь|стратеги|контент.план)\b', message, re.I)
            and not re.search(r'\b(?:часть документов|не завершен|не создан|не прош[её]л)\b', question, re.I)):
        return message, 'clarification'
    return message, 'new'


def execution_request(message, pending_request=None, pending_question=None):
    """Resolve once at the UI boundary; clarification becomes a scoped amendment."""
    original, mode = resolve_request(message, pending_request, pending_question)
    if mode == 'clarification':
        original = (str(pending_request).strip() + '\n\n'
                    + 'Уточнение пользователя в ответ на вопрос «'
                    + str(pending_question).strip() + '»: '
                    + str(message).strip())
    return original, mode


def creation_checkpoint_required(request):
    """A known creation request remains pending until the host verifies storage."""
    from agent_runtime import explicit_creation_request
    from compound_tasks import is_compound_request
    return is_compound_request(request) or explicit_creation_request(request)
