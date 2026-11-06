"""Multi-model, request-grounded review for standalone text deliverables.

Four review dimensions use qualified independent voices. The source/arbiter
voice is separate from all editorial origins; editorial roles may reuse a voice.
The author never reviews its own result. Insufficient capacity fails closed.
"""
from __future__ import annotations

import json
import copy
import hashlib
import os
import re
import secrets
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from urllib.request import urlopen

from ollama_resilience import request_chat, model_call_role
from acceptance_policy import is_first_pass_acceptance, final_repair_limit, REVIEW_PROBE_OUTPUT_TOKENS
from text_providers import configured_cloud_models
from claim_support import PUBLIC_COPY_FACT_POLICY
from artifact_review import (ArtifactReviewContract, validated_review_call,
                             resolve_editorial_approval, validate_reason_reference)
from deliverable_contract import (review_requirement_bindings, bind_requirement_findings,
                                  REQUIREMENT_FAILURE_TYPES, editorial_form_bindings)

CHECKS = ('requirements', 'completeness', 'factual_grounding', 'format_and_tone')
MODEL_POOL_FILE = Path(__file__).with_name('quality_review_model_pool.json')
CHECK_LABELS = {
    'requirements': 'все явные требования исходного запроса',
    'completeness': 'полноту, внутреннюю непротиворечивость и содержательную корректность именно запрошенного результата',
    'factual_grounding': 'подтверждённость фактических утверждений только исходным запросом и проверенными источниками',
    'format_and_tone': 'соответствие запрошенным формату, аудитории и тону',
}
DIMENSION_REVIEW_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['pass', 'issues'],
    'properties': {
        'pass': {'type': 'boolean'},
        'issues': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['evidence_id', 'reason', 'severity'], 'properties': {
                'evidence_id': {'type': 'string', 'minLength': 1},
                'reason': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                'severity': {'type': 'string', 'enum': ['major']},
            },
        }},
    },
}
_ACCEPTANCE_MODEL_POOL = ContextVar('a75_acceptance_model_pool', default=None)
_JSON_CAPABILITY_CACHE: dict[str, bool] = {}
_JSON_CAPABILITY_LOCK = RLock()


@contextmanager
def acceptance_model_pool(models):
    """Scope optional cloud candidates to the synthetic acceptance runner."""
    token = _ACCEPTANCE_MODEL_POOL.set(tuple(str(x).strip() for x in models if str(x).strip()))
    try:
        yield
    finally:
        _ACCEPTANCE_MODEL_POOL.reset(token)


from model_roles import model_family as _model_family, allocate_revision_roles, MIN_REVIEWER_FAMILIES


def _reviewer_pool_is_prevalidated():
    # A legacy JSON-probe allowlist is selection input, not competence evidence.
    from review_qualification import cached_qualification
    scoped=_ACCEPTANCE_MODEL_POOL.get()
    raw=os.environ.get('AI_SMM_ACCEPTANCE_REVIEWER_MODELS','').strip()
    try:pool=list(scoped) if scoped is not None else json.loads(raw) if raw else []
    except ValueError:return False
    return (isinstance(pool,list) and bool(pool) and all(isinstance(name,str)
        and (cached_qualification(name) or {}).get('pass') is True for name in pool))



def qualification_records(models):
    from review_qualification import cached_qualification
    records=[]
    for name in dict.fromkeys(models):
        record=cached_qualification(name)
        if record is not None:records.append(record)
    return records


def _has_structured_json_contract(model):
    """Historical name: admission now requires randomized semantic controls too."""
    from review_qualification import probe_qualification
    result=probe_qualification(model,transport=request_chat)
    with _JSON_CAPABILITY_LOCK:
        _JSON_CAPABILITY_CACHE[str(model).strip().casefold()]=result['pass']
    return result['pass']


def _calibrate_random_pool(models, required, randomizer):
    """Check only until enough distinct families pass, bounding first-use cost."""
    candidates=list(models)
    randomizer.shuffle(candidates)
    eligible=[]
    seen=set()
    for name in candidates:
        family=_model_family(name)
        if family in seen:
            continue
        seen.add(family)
        if _has_structured_json_contract(name):
            eligible.append(name)
            if len(eligible)>=required:
                break
    return eligible


def configured_model_pool():
    """Read an optional allowlist; an empty list means all installed chat models."""
    if not MODEL_POOL_FILE.exists():
        return {'allowlist': [], 'exclude': []}
    try:
        configured = json.loads(MODEL_POOL_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'Не удалось прочитать quality_review_model_pool.json: {exc}') from exc
    if not isinstance(configured, dict):
        raise ValueError('quality_review_model_pool.json должен содержать JSON-объект')
    allowlist = configured.get('allowlist', [])
    exclude = configured.get('exclude', [])
    if not isinstance(allowlist, list) or not all(isinstance(x, str) for x in allowlist):
        raise ValueError('allowlist должен быть списком имён моделей')
    if not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude):
        raise ValueError('exclude должен быть списком имён моделей')
    return {'allowlist': allowlist, 'exclude': exclude}


def list_installed_ollama_models(endpoint='http://127.0.0.1:11434/api/tags'):
    """Read Ollama's installed model tags without logging prompts or user data."""
    with urlopen(endpoint, timeout=12) as response:
        result = json.load(response)
    if not isinstance(result, dict) or not isinstance(result.get('models'), list):
        raise ValueError('Ollama вернула некорректный список моделей')
    names = []
    for item in result['models']:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        lowered = name.casefold()
        if name and not any(token in lowered for token in ('embed', 'embedding', 'rerank', 'whisper', 'clip', 'tts')):
            names.append(name)
    return list(dict.fromkeys(names))


def list_acceptance_models(endpoint='http://127.0.0.1:11434/api/tags', *, include_cloud=None):
    """Scoped validation candidates or explicitly enabled application models."""
    scoped = _ACCEPTANCE_MODEL_POOL.get()
    if scoped is not None:
        return list(scoped)
    # The validation runner sets this after randomized reviewer qualification. It
    # limits reviewer selection only; author model coverage remains unchanged.
    reviewer_pool = os.environ.get('AI_SMM_ACCEPTANCE_REVIEWER_MODELS', '').strip()
    if reviewer_pool:
        try:
            approved = json.loads(reviewer_pool)
        except (TypeError, ValueError) as exc:
            raise ValueError('AI_SMM_ACCEPTANCE_REVIEWER_MODELS должен быть JSON-списком') from exc
        if not isinstance(approved, list) or not all(isinstance(x, str) and x.strip() for x in approved):
            raise ValueError('AI_SMM_ACCEPTANCE_REVIEWER_MODELS должен быть JSON-списком имён')
        return list(dict.fromkeys(x.strip() for x in approved))
    # Scoped validation pools above remain authoritative. Otherwise honor the
    # user's explicit provider permissions in the ordinary application too.
    cloud=[]
    if include_cloud is not False:
        from text_providers import parse_cloud_model
        allow_free=str(os.environ.get('AI_SMM_ENABLE_FREE_CLOUD_ACCEPTANCE','')).strip()=='1'
        cloud=[name for name in configured_cloud_models()
               if parse_cloud_model(name)[0]=='openai' or allow_free]
    inventory_error=None
    try:
        names=list_installed_ollama_models(endpoint)
    except (OSError,ValueError) as exc:
        if not cloud:raise
        names=[];inventory_error=type(exc).__name__
    candidates=list(dict.fromkeys([*names,*cloud]))
    from diagnostic_report import record_model_event
    record_model_event('model_pool_resolved',scope='application',local_models=names,
                       configured_cloud_models=cloud,candidate_models=candidates,
                       local_inventory_error=inventory_error)
    return candidates


def known_quality_model_pool(author, assignments, *, eligible_models=None):
    """Role allocation can use only eligible models and the user's allow/exclude policy."""
    available = (list_acceptance_models() if _reviewer_pool_is_prevalidated()
                 else list(dict.fromkeys([author, *(eligible_models or assignments.values())])))
    config = configured_model_pool()
    allow = {m.casefold() for m in config['allowlist']}
    blocked = {m.casefold() for m in config['exclude']}
    return [m for m in available if m.casefold() not in blocked and
            (not allow or m.casefold() in allow)]


def select_reviewer_models(author_model, *, candidates=None, previous_reviewers=None,
                           chooser=None, reserve_reviser=False, return_pool=False):
    """Assign all four checks to independent qualified voices, reusing editorial roles when needed.

    Roles are randomized each pass. Source review preserves eligible cloud
    capacity: its only independent cloud family may review a corrected draft
    again. The writer is always excluded, and other roles still rotate.
    """
    config = configured_model_pool()
    available = list_acceptance_models() if candidates is None else [str(x).strip() for x in candidates]
    allow = {x.casefold() for x in config['allowlist']}
    blocked = {x.casefold() for x in config['exclude']}
    author = str(author_model).strip().casefold()
    author_family = _model_family(author_model)
    unique = {}
    for name in available:
        lowered = name.casefold()
        if not name or lowered == author or _model_family(name) == author_family or lowered in blocked:
            continue
        if allow and lowered not in allow:
            continue
        # Model-size tags from one family are not independent reviewer voices.
        unique.setdefault(_model_family(name), name)
    pool = list(unique.values())
    requested_pool=list(pool)
    randomizer = chooser or secrets.SystemRandom()
    if candidates is None and not _reviewer_pool_is_prevalidated():
        pool = _calibrate_random_pool(pool, len(CHECKS)+(1 if reserve_reviser else 0), randomizer)
    if len(pool) < MIN_REVIEWER_FAMILIES:
        from review_qualification import ReviewerQualificationUnavailable
        raise ReviewerQualificationUnavailable(f'Нужно минимум {MIN_REVIEWER_FAMILIES} независимых моделей-рецензентов, прошедших проверку компетенции; доступно {len(pool)}.',
            qualification_records(requested_pool))
    previous = previous_reviewers if isinstance(previous_reviewers, dict) else {}
    assignment = {}

    # Place the source role first so an eligible cloud judge is not consumed
    # by a stylistic role. Authors/tasks and the other roles remain randomized;
    # a cloud tag alone is not treated as semantic quality certification.
    from text_providers import is_ollama_cloud_tag, parse_cloud_model
    role_order=('factual_grounding',*(key for key in CHECKS if key!='factual_grounding'))
    if len(pool)<len(CHECKS):
        # Keep source/applicability independent from every editorial origin.
        # Reuse a qualified editorial voice across criteria, never the author.
        sources=[name for name in pool if is_ollama_cloud_tag(name) or parse_cloud_model(name) is not None] or list(pool)
        prior=_model_family(previous.get('factual_grounding') or '')
        fresh=[name for name in sources if _model_family(name)!=prior]
        source=randomizer.choice(fresh or sources)
        assignment={'factual_grounding':source}
        peers=[name for name in pool if _model_family(name)!=_model_family(source)]
        used={name:0 for name in peers}
        for check in CHECKS:
            if check=='factual_grounding':continue
            prior=_model_family(previous.get(check) or '')
            fresh=[name for name in peers if _model_family(name)!=prior]
            choices=fresh or peers
            least=min(used[name] for name in choices)
            selected=randomizer.choice([name for name in choices if used[name]==least])
            assignment[check]=selected;used[selected]+=1
        return (assignment,pool) if return_pool else assignment
    def assign(index, used):
        if index == len(role_order):
            return True
        check = role_order[index]
        prior = _model_family(previous.get(check) or '')
        choices = [name for name in pool if _model_family(name) not in used
                   and _model_family(name) != prior]
        if check=='factual_grounding':
            cloud=[name for name in pool if _model_family(name) not in used and
                   (is_ollama_cloud_tag(name) or parse_cloud_model(name) is not None)]
            if cloud:
                fresh=[name for name in cloud if _model_family(name)!=prior]
                choices=fresh or cloud
        randomizer.shuffle(choices)
        if check=='factual_grounding':
            choices.sort(key=lambda name:not (is_ollama_cloud_tag(name) or parse_cloud_model(name) is not None))
        for name in choices:
            assignment[check] = name
            if assign(index + 1, used | {_model_family(name)}):
                return True
            assignment.pop(check, None)
        return False

    if not assign(0, set()):
        raise ValueError('Не нашлось четырёх новых моделей для независимой перепроверки')
    return (assignment,pool) if return_pool else assignment


def select_independent_model(author_model, *, candidates=None, exclude_models=None,
                             chooser=None):
    """Choose one random installed reviewer family distinct from the author.

    Used by compound strategy/calendar work, which needs one independent
    reviewer for the whole bundle. Standalone text review still uses the
    multi-criterion panel above (at least two independent reviewer families).
    """
    config=configured_model_pool()
    available=list_acceptance_models() if candidates is None else [str(x).strip() for x in candidates]
    allow={x.casefold() for x in config['allowlist']}
    blocked={x.casefold() for x in config['exclude']}
    excluded_families={_model_family(author_model)}
    excluded_families.update(_model_family(x) for x in (exclude_models or []))
    families={}
    for name in available:
        lowered=name.casefold()
        family=_model_family(name)
        if not name or family in excluded_families or lowered in blocked:
            continue
        if allow and lowered not in allow:
            continue
        families.setdefault(family,[]).append(name)
    if not families:
        raise ValueError('Не найдена установленная модель-редактор из другого семейства')
    randomizer=chooser or secrets.SystemRandom()
    family_order=list(families)
    randomizer.shuffle(family_order)
    from text_providers import is_ollama_cloud_tag, parse_cloud_model
    cloud_families=[family for family in family_order if any(
        is_ollama_cloud_tag(name) or parse_cloud_model(name) is not None for name in families[family])]
    family_order.sort(key=lambda family:family not in cloud_families)
    if candidates is None and not _reviewer_pool_is_prevalidated():
        for family in family_order:
            choices=list(families[family]);randomizer.shuffle(choices)
            for name in choices:
                if _has_structured_json_contract(name):
                    return name
        from review_qualification import ReviewerQualificationUnavailable, cached_qualification
        raise ReviewerQualificationUnavailable('Не найдена независимая модель-рецензент с подтверждёнными контрольными решениями',
            [cached_qualification(name) for names in families.values() for name in names])
    family=randomizer.choice(cloud_families or family_order)
    return randomizer.choice(families[family])


def select_independent_reviewers(author_models, *, candidates=None, chooser=None):
    """Randomly assign distinct reviewer families across a model comparison.

    Eligible cloud families are preferred for the compound source/editor role.
    A full matching is used within that preferred pool when possible; otherwise
    reuse is balanced. Author families remain excluded. A cloud tag is a routing
    preference, not certification of semantic quality.
    """
    config=configured_model_pool()
    available=list_acceptance_models() if candidates is None else [str(x).strip() for x in candidates]
    allow={x.casefold() for x in config['allowlist']}
    blocked={x.casefold() for x in config['exclude']}
    families={}
    for raw in available:
        name=raw.strip(); lowered=name.casefold(); family=_model_family(name)
        if not name or lowered in blocked or (allow and lowered not in allow):
            continue
        families.setdefault(family,[]).append(name)
    randomizer=chooser or secrets.SystemRandom()
    authors=[str(x).strip() for x in author_models or []]
    if any(not author for author in authors):
        raise ValueError('Модель-автор не может быть пустой')
    choices={}
    for author in authors:
        pool=[family for family in families if family!=_model_family(author)]
        from text_providers import is_ollama_cloud_tag, parse_cloud_model
        cloud=[family for family in pool if any(is_ollama_cloud_tag(name)
            or parse_cloud_model(name) is not None for name in families[family])]
        pool=cloud or pool
        randomizer.shuffle(pool)
        if not pool:
            raise ValueError(f'Для автора {author} не найдена независимая модель-рецензент')
        choices[author]=pool
    order=list(authors);randomizer.shuffle(order)
    assigned={}
    def match(index,used):
        if index==len(order):return True
        author=order[index]
        for family in choices[author]:
            if family in used:continue
            assigned[author]=family
            if match(index+1,used|{family}):return True
            assigned.pop(author,None)
        return False
    if not match(0,set()):
        assigned={}
        counts={family:0 for family in families}
        for author in order:
            minimum=min(counts[family] for family in choices[author])
            fresh=[family for family in choices[author] if counts[family]==minimum]
            family=randomizer.choice(fresh)
            assigned[author]=family;counts[family]+=1
    return {author:randomizer.choice(families[assigned[author]]) for author in authors}


def _independent_panel(assignments):
    if (not isinstance(assignments,dict) or set(assignments)!=set(CHECKS)
            or any(not isinstance(value,str) or not value.strip() for value in assignments.values())):return False
    families={key:_model_family(value) for key,value in assignments.items()}
    source=families['factual_grounding']
    return (bool(source) and len(set(families.values()))>=MIN_REVIEWER_FAMILIES
            and all(value and value!=source for key,value in families.items() if key!='factual_grounding'))


def validate_model_assignments(author_model, reviewer_models, previous_reviewers=None):
    """Validate an explicit assignment used by controlled tests or integrations."""
    assignments = dict(reviewer_models) if isinstance(reviewer_models, dict) else {}
    if set(assignments) != set(CHECKS):
        raise ValueError('Для каждого редакторского критерия нужно назначение модели')
    if any(not isinstance(value,str) for value in assignments.values()):
        raise ValueError('Названия моделей редакторов должны быть строками')
    normalized = {key: assignments[key].strip() for key in CHECKS}
    if any(not value for value in normalized.values()):
        raise ValueError('Название модели редактора не может быть пустым')
    lowered = [value.casefold() for value in normalized.values()]
    if not _independent_panel(normalized):
        raise ValueError('Нужны разные семьи для source/арбитра и редакторских критериев')
    if str(author_model).strip().casefold() in lowered:
        raise ValueError('Модель автора не может проверять собственный текст')
    author_family = _model_family(author_model)
    if any(_model_family(value) == author_family for value in normalized.values()):
        raise ValueError('Модель семейства автора не может проверять его текст')
    previous = previous_reviewers if isinstance(previous_reviewers, dict) else {}
    for check in CHECKS:
        if normalized[check].casefold() == str(previous.get(check) or '').strip().casefold():
            raise ValueError(f'Для повторной проверки {check} нужна новая модель')
    return normalized


def _validated_issues(raw_issues, draft, check):
    if not isinstance(raw_issues, list):
        raise ValueError(f'Модель проверки {check} не вернула список замечаний')
    issues = []
    for issue in raw_issues:
        if not isinstance(issue, dict):
            raise ValueError(f'Некорректное замечание проверки {check}')
        quote = str(issue.get('quote') or '')
        reason = str(issue.get('reason') or '').strip()
        if not quote.strip() or quote not in draft:
            raise ValueError(f'Замечание проверки {check} не привязано к точной цитате из черновика')
        if not reason:
            raise ValueError(f'Замечание проверки {check} не содержит причины')
        issues.append({'quote': quote, 'reason': reason[:500],
                       'severity': str(issue.get('severity') or 'major')[:30],
                       'check': check})
        for field in ('evidence_id','field','field_role','field_type','review_signature','criterion_id'):
            if isinstance(issue.get(field),str):
                issues[-1][field]=issue[field]
        for field in ('related_evidence_ids','related_quotes'):
            if isinstance(issue.get(field),list) and all(isinstance(v,str) for v in issue[field]):
                issues[-1][field]=list(issue[field])
        if isinstance(issue.get('review_scope'),dict):
            issues[-1]['review_scope']=copy.deepcopy(issue['review_scope'])
        for field in ('adjudication','statement_class'):
            if isinstance(issue.get(field),str):issues[-1][field]=issue[field]
        for field in ('finding_validation','adjudication_trace','quality_criterion'):
            if isinstance(issue.get(field),dict):issues[-1][field]=copy.deepcopy(issue[field])
    return issues


def validate_verdict(verdict, draft):
    """Validate the final aggregate verdict and exact-quote citations."""
    if not isinstance(verdict, dict) or type(verdict.get('pass')) is not bool:
        raise ValueError('Нет однозначного решения редакторской панели')
    checks = verdict.get('checks')
    if not isinstance(checks, dict) or any(type(checks.get(key)) is not bool for key in CHECKS):
        raise ValueError('Редакторская панель не проверила все измерения')
    errors = verdict.get('review_errors') or {}
    if (not isinstance(errors, dict) or any(key not in CHECKS or
            not isinstance(value, str) or not value.strip() for key, value in errors.items())
            or any(checks[key] for key in errors)):
        raise ValueError('Некорректная диагностика незавершённых редакторских проверок')
    issues = _validated_issues(verdict.get('issues'), str(draft), 'aggregate')
    # Preserve the originating dimension on each issue where supplied.
    for source, target in zip(verdict.get('issues', []), issues):
        if source.get('check') in CHECKS:
            target['check'] = source['check']
        for field in ('requirement_id', 'requirement_quote', 'requirement_mode', 'failure_type'):
            if isinstance(source.get(field), str):
                target[field] = source[field]
    passed = verdict['pass'] and all(checks[key] for key in CHECKS) and not issues and not errors
    if verdict['pass'] and not passed:
        raise ValueError('Редакторская панель выдала противоречивое решение')
    if not verdict['pass'] and not issues and not errors:
        issues.append({'quote': str(draft)[:min(120, len(str(draft)))],
                       'reason': 'Редакторская панель заблокировала результат без конкретного замечания',
                       'severity': 'major', 'check': 'aggregate'})
    return {'status': 'PASS' if passed else 'BLOCKED', 'pass': passed,
            'checks': {key: checks[key] for key in CHECKS}, 'issues': issues,
            'review_state': ('complete' if not errors else
                             'unavailable' if len(errors) == len(CHECKS) else 'partial'),
            'review_errors': dict(errors),
            'completed_dimensions': [key for key in CHECKS if key not in errors],
            'reason': ('Не завершены редакторские проверки: ' + '; '.join(
                       f'{key}: {message}' for key, message in errors.items()) if errors else ''),
            'review_role': 'random_independent_editorial_panel',
            'independent_model': _independent_panel(verdict.get('reviewer_models')),
            'reviewer_family_count':len({_model_family(name) for name in (verdict.get('reviewer_models') or {}).values()}),
            'reviewer_models': dict(verdict.get('reviewer_models') or {})}


def review_dimension(request, draft, context, check, model, *, report=None,
                     artifact_fields=None, artifact_roles=None, endpoint=None, review_scope=None):
    """Ask one assigned model to judge exactly one review dimension."""
    if check not in CHECKS:
        raise ValueError(f'Неизвестное измерение редакторской проверки: {check}')
    if check == 'requirements' and not str(request or '').strip():
        raise ValueError('Нет исходного задания для проверки требований')
    if check == 'factual_grounding':
        return review_public_copy_sources(request, draft, context, model, report=report)
    fields={'standalone':str(draft)} if artifact_fields is None else artifact_fields
    roles={'standalone':'caption'} if artifact_fields is None else artifact_roles
    if review_scope is not None and not isinstance(review_scope,dict):
        raise ValueError('Область проверки должна быть объектом')
    scope={**(review_scope or {}),'dimension':check}
    contract=ArtifactReviewContract.create(str(request),fields,roles,scope=scope)
    review_body='\n\n'.join(fields.values())
    evidence = context.get('verified_project_materials') or context.get('evidence_context') or []
    if isinstance(evidence, str):
        evidence = evidence[:12000]
    else:
        evidence = json.dumps(evidence, ensure_ascii=False)[:12000]
    system = (
        'Ты независимый редактор. Проведи только указанную проверку, не выставляя оценки по другим критериям. '
        f'Критерий: {CHECK_LABELS[check]}. Не добавляй собственные требования и не требуй лишнего объёма. '
        'Если запрос не требует другого языка, проверь соответствие языка результата языку исходного запроса; '
        'отмечай только явное несоответствие и выбери ID фрагмента. Не навязывай один язык всем задачам. '
        'Определи формат результата только из исходного запроса. Не назначай ему другой формат. '
        'Начало с вопроса, сведения об услуге и CTA допустимы, если исходный запрос их не запрещает; '
        'само наличие этих элементов не является нарушением стиля или формата. '
        'Сведения о названии проекта, категории бизнеса, аудитории и услуге, прямо указанные в запросе, разрешены; '
        'не объявляй их запрещёнными или неподтверждёнными, если запрос явно этого не говорит. '
        'Сохраняй лексическую идентичность имён собственных: грамматическое склонение и согласование, '
        'необходимые языку результата, сами по себе не являются ошибкой написания. '
        'Отмечай изменение названия только при явной подмене или искажении его основы, а не из-за падежной формы. '
        'Оценивай выполнение запроса с учётом доступных подтверждённых данных. Не требуй точных имён, позиций, цифр, этапов или условий, которых нет в запросе и источниках; '
        'если без них нельзя безопасно конкретизировать результат, считай приемлемым точное описание на уровне имеющихся фактов и уместный вопрос об уточнении. '
        'Для каждого существенного дефекта выбери evidence_id из review_evidence и конкретную причину. '
        'Не копируй цитату: программа подставляет точный неизменённый текст выбранного фрагмента. '
        'Если причина сравнивает фрагменты, перечисли остальные в related_evidence_ids; '
        'основной evidence_id указывает место дефекта, а связанные ID — проверяемый контекст. '
        'Если дефектов по этому критерию нет, верни pass=true и пустой issues. '
        'Верни только JSON по выданной схеме: pass и issues с evidence_id, reason и severity=major. '
        'Дополнительные поля замечания, если они есть в схеме, также обязательны.'
    )
    if check=='completeness':
        system += ('\nВ проверке полноты требуй конкретное полезное содержание, соответствующее цели и теме; '
            'не принимай один хук, общие похвалы и CTA за завершённый материал. '
            'Проверь также смысл и корректность общих рекомендаций: отнесение фразы к общим знаниям '
            'освобождает её от подтверждения бизнес-источником, но не делает истинной. '
            'Отмечай конкретный логический дефект, опасную безусловную рекомендацию или абсолютную '
            'гарантию, которая не следует из описанных действий. Не требуй бизнес-источник для обычного общего объяснения.')
    if check!='format_and_tone':
        system += '\nПри отсутствии требуемого содержания выбери ID всего материала, затем исходное требование.'
    bindings = review_requirement_bindings(request) if check in ('requirements','completeness') else []
    if check=='format_and_tone':
        system += ('\nЭта проверка оценивает только язык, запрошенную форму, тон и читаемость доступных полей. '
            'review_scope.artifact_kinds связывает ID полей с видом артефакта: стратегия является '
            'внутренним документом, а не готовым постом. Описание аудитории и целей в стратегии '
            'не нарушает правило о служебных инструкциях в публичной речи; caption и CTA '
            'оценивай как речь для читателя. Не переноси критерии разных ролей друг на друга. '
            'При partial_fields проверяются выбранные поля с сохранёнными ролями; '
            'не объявляй всю стратегию или публикацию неполной по этой выборке. '
            'Она не оценивает полноту содержания, перечень результатов и наличие ещё не созданных артефактов. '
            'review_scope задаёт текущую область. Ожидаемые позже результаты не обязаны содержаться внутри '
            'проверяемого документа. Нельзя переименовать замечание о полноте в замечание о тоне или форме.')
        bindings = editorial_form_bindings(scope)
    proposal = context.get('proposed_content_contract') if check == 'requirements' else None
    schema = copy.deepcopy(DIMENSION_REVIEW_SCHEMA)
    item=schema['properties']['issues']['items']
    item['properties']['evidence_id']={'type':'string','enum':[s.ident for s in contract.spans]}
    item['properties']['related_evidence_ids']={'type':'array','maxItems':4,'uniqueItems':True,
        'items':{'type':'string','enum':[s.ident for s in contract.spans]}}
    if bindings:
        item = schema['properties']['issues']['items']
        item['required'] += ['requirement_id', 'failure_type']
        item['properties'].update({
            'requirement_id': {'type': 'string', 'enum': [b['id'] for b in bindings]},
            'failure_type': {'type': 'string', 'enum': ['style_mismatch'] if check=='format_and_tone' else list(REQUIREMENT_FAILURE_TYPES)},
        })
        system += '\nКаждое замечание свяжи с requirement_id из requirement_bindings и укажи failure_type из схемы.'
        if check in ('requirements','completeness'):
            system += (' Поля audience_fit, objective_fit, style_fit и fact_context задают '
            'контекст и соответствие текста, а не обязательные слова для вставки. Не требуй дословного '
            'упоминания аудитории, цели или всех исходных фактов. Verbatim_missing означает только '
            'отсутствие явно требуемой формулировки. Если текст не подходит аудитории, используй '
            'audience_mismatch и объясни конкретное несоответствие содержания/языка; отсутствие названия '
            'группы само по себе таким несоответствием не является. Явная просьба упомянуть или '
            'процитировать формулировку обязательна и остаётся instruction.')
        if check=='format_and_tone':
            system += ('\nФормат и тон определены только запросом. Для замечания выбери '
                'один G_ критерий и объясни конкретный дефект. Эти критерии не запрещают '
                'CTA, вопросы или сведения об услуге и не задают жанр/объём. Не подменяй ими запрос. '
                'Допустим только failure_type=style_mismatch; содержательные пропуски относятся к другим критериям.')
    if proposal:
        schema=copy.deepcopy(schema)
        schema['required'].append('creative_permission_authorized')
        schema['properties']['creative_permission_authorized']={'type':'boolean'}
        system += ('\nПроверь также proposed_content_contract. Разрешает ли процитированное исходное '
            'задание вымышленные/условные элементы именно в этом материале? Верни '
            'creative_permission_authorized=true только при явном разрешении пользователя '
            'и корректной области применения. Цель рекламного текста, неизвестность и запрет '
            'выдумывать факты не являются таким разрешением. Реальные утверждения о бизнесе '
            'сохраняют фактические ограничения даже внутри творческого материала.')
    substance=None
    if check=='completeness' and context.get('substance_review_required'):
        from artifact_review import SubstanceReviewContract
        catalog={span.ident:span.text for span in contract.spans}
        substance=SubstanceReviewContract.create(str(request),catalog,[{'id':'standalone',
            'criteria':{name:list(catalog) for name in ('topic_delivery','reader_value','clarity')}}])
        if artifact_fields is None and review_scope is None:
            from artifact_review import standalone_substance_contract
            substance=standalone_substance_contract(str(request),str(draft))
        schema['required'].append('assessments')
        schema['properties']['assessments']=substance.schema()
        schema['required'].append('quality_controls')
        schema['properties']['quality_controls']=substance.control_schema()
        item=schema['properties']['issues']['items']
        item['required'].append('criterion_id')
        item['properties']['criterion_id']={'type':'string','enum':['none',*substance.checks]}
        system+=('\nВыполни каждый quality_criteria отдельно: satisfied требует конкретного '
            'объяснения и исходного evidence_id; defect требует замечания с criterion_id. '
            'unknown означает незавершённую проверку. Не удаляй содержательную пользу ради '
            'общей оговорки. Не требуй бизнес-фактов, которых нет в источниках. '
            'quality_controls — отдельные вымышленные задания, не источники для текста. '
            'Для каждого верни boolean: выполняет ли candidate task по source_points. '
            'Призыв уточнить не выполняет содержательную задачу при достаточных исходных данных.')
    user = json.dumps({'original_request': str(request), 'verified_sources': evidence,
                       'draft_to_review': review_body, 'review_dimension': check,
                       'review_evidence':contract.evidence_payload(),
                       'review_signature':contract.signature,
                       'review_scope':scope,
                       'requirement_bindings': bindings,
                       'proposed_content_contract':proposal,
                       'quality_criteria':substance.model_manifest()['checks'] if substance is not None else {},
                       'quality_definitions':substance.model_manifest()['definitions'] if substance is not None else {},
                       'quality_controls':substance.control_payload() if substance is not None else {}}, ensure_ascii=False)
    payload = {'model': model, 'stream': False, 'think': False, 'format': schema,
               'keep_alive': 0,
               'messages': [{'role': 'system', 'content': system},
                            {'role': 'user', 'content': user}],
               'options': {'num_ctx': 16384 if artifact_fields is not None else 8192, 'temperature': 0.1}}
    def ask(reviewer_model, instruction, content, *, json_schema):
        body={**payload,'model':reviewer_model,'format':json_schema,
              'messages':[{'role':'system','content':instruction},{'role':'user','content':content}]}
        extra={'endpoint':endpoint} if endpoint is not None else {}
        data=request_chat(body,stage=f'редакторская проверка: {check} ({model})',
                          timeout=300,attempts=1,report=report,**extra)
        return data.get('message',{}).get('content') if isinstance(data,dict) else ''

    def validate(verdict):
        if not isinstance(verdict,dict) or set(verdict)!=set(schema['required']):
            raise ValueError(f'Поля вердикта {check} не соответствуют схеме')
        if type(verdict.get('pass')) is not bool:
            raise ValueError(f'Модель проверки {check} не вернула однозначное решение')
        if proposal and type(verdict.get('creative_permission_authorized')) is not bool:
            raise ValueError('Не проверено разрешение пользователя на вымысел')
        if not isinstance(verdict.get('issues'),list):
            raise ValueError('Замечания редактора должны быть списком')
        item_schema=schema['properties']['issues']['items']
        grounded=[]
        for item in verdict['issues']:
            if (not isinstance(item,dict) or not set(item_schema['required'])<=set(item)
                    or set(item)-set(item_schema['properties'])
                    or not all(isinstance(value,str) for key,value in item.items() if key!='related_evidence_ids')
                    or item.get('severity')!='major'):
                raise ValueError('Поля замечания редактора не соответствуют схеме')
            if not 1<=len(item['reason'].strip())<=500:
                raise ValueError('Замечание редактора не содержит допустимой причины')
            for key,property_schema in item_schema['properties'].items():
                if key in item and 'enum' in property_schema and item[key] not in property_schema['enum']:
                    raise ValueError('Поле '+key+' не относится к критерию и области проверки '+check)
            span=contract.resolve_evidence(item['evidence_id'])
            related=contract.related_evidence(item)
            validate_reason_reference(item['reason'],'\n'.join([span.text,*[s.text for s in related]]),
                                      (s.text for s in contract.spans),str(request))
            grounded.append({**item,'quote':span.text,'field':span.field,
                'field_role':span.field,'field_type':span.role,'review_signature':contract.signature,
                'review_scope':copy.deepcopy(scope)})
            if related:grounded[-1].update(related_quotes=[s.text for s in related])
        issues = _validated_issues(grounded, review_body, check)
        host_pass=resolve_editorial_approval(verdict['pass'],issues)
        if verdict['pass'] and issues and report:
            report('editorial_approval_overridden',dimension=check,model=model,
                   issue_count=len(issues),reason='Concrete validated findings prevent approval')
        dismissed = []
        if bindings and issues:
            for source, target in zip(verdict['issues'], issues):
                target.update(requirement_id=source.get('requirement_id'),
                              failure_type=source.get('failure_type'))
            issues, dismissed = bind_requirement_findings(issues, bindings)
        substance_proof=({'substance_review':substance.validate(verdict.get('assessments'),issues,verdict.get('quality_controls'))}
                         if substance is not None else {})
        return {'pass': not issues if dismissed else host_pass,
                'issues': issues, 'dismissed_issues': dismissed, 'model_pass': verdict['pass'],
                'creative_permission_authorized':bool(proposal and verdict.get('creative_permission_authorized')),
                **substance_proof}
    return validated_review_call(ask,model,system,user,schema,validate,report=report)



def review_public_copy_sources(request, draft, context, model, *, report=None):
    """Replace a boolean fact opinion with the shared, source-bound claim audit."""
    from claim_support import public_copy_findings, adjudicate_factual_findings
    from claim_interpretation import INCOMPLETE_ADJUDICATIONS
    from evidence_context import build_evidence_context
    findings = public_copy_findings(str(draft))
    ledger = build_evidence_context(str(request), context)
    model_calls = 0

    def ask(reviewer_model, system, user, *, json_schema):
        nonlocal model_calls
        model_calls += 1
        system += ('\nСохраняй лексическую идентичность имён собственных: '
                   'грамматическое склонение само по себе не искажает название. '
                   'Не отклоняй название только из-за падежной формы. '
                   'Для публичного текста связь с компанией из предыдущей фразы или списка '
                   'сохраняется: описание её программы/процесса требует источника.')
        payload = {'model': reviewer_model, 'stream': False, 'think': False,
                   'format': json_schema, 'keep_alive': 0,
                   'messages': [{'role': 'system', 'content': system},
                                {'role': 'user', 'content': user}],
                   'options': {'num_ctx': 16384, 'num_predict': max(1200,min(2800,320*len(json_schema['properties']['decisions']['properties']))), 'temperature': 0}}
        data = request_chat(payload, stage=f'редакторская проверка источников ({reviewer_model})',
                            timeout=240, attempts=1, report=report)
        return data.get('message', {}).get('content') if isinstance(data, dict) else ''

    permission=context.get('authorized_creative_permission')
    if not (isinstance(permission,dict) and permission.get('draft_sha256')==
            hashlib.sha256(str(draft).encode()).hexdigest()):
        permission=None
    audit = adjudicate_factual_findings(str(request), findings, model=model,
        evidence_context=ledger, ask=ask, report=report,creative_permission=permission,
        require_semantic_contract=True)
    audit['coverage'] = {'public_segments': len(findings), 'model_calls': model_calls,
                         'source_count': len(ledger.sources),
                         'review_mode': 'source_model' if model_calls else 'host_sources'}
    unavailable = [item for item in audit['blocking'] if item.get('adjudication') in INCOMPLETE_ADJUDICATIONS]
    issues = [{**copy.deepcopy(item),'quote': item['quote'], 'reason': str(item.get('reason') or '')[:500],
               'severity': 'major', 'check': 'factual_grounding',
               'adjudication': item.get('adjudication')}
              for item in audit['blocking'] if item.get('adjudication') not in INCOMPLETE_ADJUDICATIONS]
    return {'pass': not audit['blocking'], 'issues': issues,
            'review_error': ('Не завершена сверка источников для ' +
                             str(len(unavailable)) + ' фрагментов: ' +
                             str(unavailable[0].get('reason') or '')[:220]) if unavailable else '',
            'grounding_audit': audit}


def review_text(request, draft, context, *, model='qwen3:8b', reviewer_models=None,
                previous_reviewers=None, report=None):
    """Run all criteria on qualified voices with source/editorial and author independence."""
    assignments = (select_reviewer_models(model, previous_reviewers=previous_reviewers)
                   if reviewer_models is None else
                   validate_model_assignments(model, reviewer_models, previous_reviewers))
    checks, issues, errors, dismissed = {}, [], {}, []
    review_context=dict(context)
    review_context.pop('authorized_creative_permission',None)
    grounding_audit = None
    substance_proof = None
    timed_out_families=set()
    for check in CHECKS:
        family=_model_family(assignments[check])
        if (is_first_pass_acceptance(context) and context.get('acceptance_profile')=='fast'
                and family in timed_out_families):
            checks[check]=False
            errors[check]='NOT_RUN: это семейство модели уже дало транспортный таймаут в текущей проверке'
            if report:
                report('text_review_dimension_skipped_after_timeout',dimension=check,
                       model=assignments[check],reason='same_review_family_timed_out')
            continue
        try:
            with model_call_role('reviewer'):
                result = review_dimension(request, draft, review_context, check, assignments[check], report=report)
            if (check=='requirements' and result.get('pass') and result.get('creative_permission_authorized')):
                review_context['authorized_creative_permission']={
                    **review_context['proposed_content_contract'],
                    'draft_sha256':hashlib.sha256(str(draft).encode()).hexdigest()}
            checks[check] = bool(result['pass'])
            if check=='completeness':substance_proof=result.get('substance_review')
            issues.extend(result['issues'])
            dismissed.extend(result.get('dismissed_issues', []))
            if result.get('review_error'):
                checks[check] = False
                errors[check] = result['review_error']
            if 'grounding_audit' in result:
                grounding_audit = result['grounding_audit']
            if report:
                extra = ({'grounding_coverage': grounding_audit['coverage']}
                         if check == 'factual_grounding' and grounding_audit else {})
                report('text_review_dimension_completed', dimension=check,
                       model=assignments[check], passed=result['pass'], issue_count=len(result['issues']), **extra)
        except Exception as exc:
            # A bounded synthetic check must not repeatedly wait on the same
            # transport outage. Other reviewer families still run; skipped
            # criteria remain incomplete and never become approval.
            cause=exc;seen=set()
            while isinstance(cause,BaseException) and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause,TimeoutError):
                    timed_out_families.add(family);break
                cause=cause.__cause__ or cause.__context__ or getattr(cause,'reason',None)
            checks[check] = False
            errors[check] = f'{type(exc).__name__}: {str(exc)[:300]}'
            if report:
                report('text_review_dimension_unavailable', dimension=check,
                       model=assignments[check], error_type=type(exc).__name__)
    final = validate_verdict({'pass': all(checks.values()), 'checks': checks,
        'issues': issues, 'reviewer_models': assignments, 'review_errors': errors}, str(draft))
    final['independent_model'] = True
    final['dismissed_issues'] = dismissed
    final['quality_model_pool'] = known_quality_model_pool(model, assignments)
    final['content_contract'] = review_context.get('authorized_creative_permission') or {'mode':'factual'}
    if grounding_audit is not None:
        final['grounding_audit'] = grounding_audit
    if substance_proof is not None:final['substance_review']=substance_proof
    from artifact_review import review_source_signature
    final['source_signature']=review_source_signature(str(request),{'standalone':str(draft)})
    final['author_model']=model
    return final


def repair_text(request, draft, issues, context, *, model='qwen3:8b', report=None):
    from text_task_contract import TextTaskContract, TextRevisionContract
    task = TextTaskContract.create(str(request), context)
    revision = TextRevisionContract.create(str(draft), issues)
    system = (
        'Ты редактор исправления готового материала. Выполни каждую операцию revision_findings. '
        'Это адресная правка одного отклонённого текста. Сохрани назначение, язык, полезное содержание '
        'и явно запрошенные элементы. Не сохраняй отклонённые обещания другими словами. '
        'Объясняй пользу через общие знания без приписывания компании неподтверждённых процессов. '
        'Не принимай отсутствие фактов за разрешение выдумать их. '
        'Верни только revised_text — полный исправленный материал. Не составляй отчёт о правках '
        'и не копируй новые цитаты отдельно: программа вычисляет изменения, затем другой редактор '
        'проверяет выполнение исходного задания и все прежние замечания. '
        'Не заключай весь материал во внешние кавычки и не добавляй пояснение вне результата. '
        + PUBLIC_COPY_FACT_POLICY)
    user = json.dumps({'original_request': task.request,
                       'verified_sources': task.evidence.to_dict(),
                       'rejected_draft': revision.draft,
                       'revision_findings': list(revision.findings)}, ensure_ascii=False)
    payload = {'model': model, 'stream': False, 'think': False, 'format': revision.schema(),
               'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
               'options': {'num_ctx': 16384, 'temperature': 0.2}}
    data = request_chat(payload, stage='адресная доработка текста',
                        timeout=300, attempts=2, report=report)
    raw = data.get('message', {}).get('content') if isinstance(data, dict) else ''
    answer = revision.validate(json.loads(raw))
    observed=revision.evidence(answer)
    if context.get('project_workspace'):
        from durable_json import atomic_write_json
        atomic_write_json(Path(context['project_workspace'])/'drafts'/
            ('text_revision_'+task.signature[:16]+'.json'),
            {'task_signature':task.signature,'revised_text':answer,'evidence':observed,
             'author_model':model,'status':'awaiting_independent_review'})
    if report:
        report('text_revision_contract_validated', findings=len(revision.findings),
               task_signature=task.signature, revision_author_model=model,
               input_sha256=observed['input_sha256'],output_sha256=observed['output_sha256'])
    return answer


def _validate_text_applicability(request,draft,context,verdict,author,*,full_review,report=None):
    """Use the same repair-authority boundary before and after a correction."""
    from review_arbitration import validate_editorial_verdict
    from evidence_context import build_evidence_context
    editorial=[item for item in verdict.get('issues',[]) if item.get('check')!='factual_grounding']
    if not editorial or verdict.get('review_errors') or verdict.get('pass'):
        return verdict
    arbiter=(verdict.get('reviewer_models') or {}).get('factual_grounding')
    origins={(verdict.get('reviewer_models') or {}).get(item.get('check')) for item in editorial}
    if (not arbiter or _model_family(arbiter)==_model_family(author)
            or any(_model_family(name)==_model_family(arbiter) for name in origins)):
        raise ValueError('Нет независимого арбитра применимости редакторских замечаний')
    def ask_judge(reviewer_model,system,user,*,json_schema):
        response=request_chat({'model':reviewer_model,'stream':False,'think':False,
            'format':json_schema,'messages':[{'role':'system','content':system},
            {'role':'user','content':user}],'options':{'num_ctx':16384,'temperature':0}},
            stage='проверка применимости замечаний отдельного текста',attempts=1,report=report)
        return response.get('message',{}).get('content','')
    out=validate_editorial_verdict(ask_judge,arbiter,str(request),{'standalone':draft},verdict,
        full_review=full_review,source_context=build_evidence_context(str(request),context).to_dict(),report=report)
    proof=out.get('finding_adjudication') or {}
    from artifact_review import deduplicate_findings
    out['dismissed_issues']=deduplicate_findings([*verdict.get('dismissed_issues',[]),
        *proof.get('dismissed',[]),*out.get('dismissed_issues',[])])
    checks=dict(out.get('checks') or {});errors=dict(out.get('review_errors') or {})
    for issue in out.get('issues',[]):
        if issue.get('adjudication')=='unavailable' and issue.get('check') in CHECKS:
            check=issue['check'];checks[check]=False
            errors[check]='Применимость замечания не установлена; авторская правка по нему недопустима'
    for issue in proof.get('dismissed',[]):
        check=issue.get('check')
        if check in CHECKS and check not in errors and not any(
                item.get('check')==check for item in out.get('issues',[])):
            checks[check]=True
    checked=validate_verdict({'pass':out.get('pass') is True,'checks':checks,
        'issues':out.get('issues',[]),'review_errors':errors,
        'reviewer_models':out.get('reviewer_models',{})},str(draft))
    return {**out,**checked}


def run_quality_cycle(request, result, context, *, reviewer=review_text,
                      repairer=repair_text, model='qwen3:8b', reviewer_models=None, report=None):
    """Review generic drafts; bounded workflows repair once and retain first-pass evidence."""
    if not isinstance(result, dict) or result.get('action') != 'write':
        return result
    draft = str(result.get('answer') or '').strip()
    if not draft:
        return {**result, 'action': 'write_blocked', 'quality_review': {
            'status': 'BLOCKED', 'pass': False, 'issues': [],
            'reason': 'Пустой результат нельзя принять или сохранить.'}}
    reviews = []
    working_draft=draft
    try:
        verdict = reviewer(request, draft, context, model=model,
                           reviewer_models=reviewer_models, report=report)
        reviews.append(verdict)
        if report:
            report('text_first_pass_review_result', passed=verdict.get('pass') is True,
                   issue_count=len(verdict.get('issues') or []),
                   review_state=verdict.get('review_state'))
        if verdict.get('pass'):
            return {**result, 'answer': draft, 'quality_review': {
                **verdict, 'attempts': 1, 'repair_applied': False}}
        if verdict.get('review_errors'):
            # An unavailable criterion does not certify a content defect.
            # Production may try one different independent panel on IDENTICAL
            # bytes. Never repair from incomplete observations, and never
            # promote the original partial verdict to PASS.
            if (reviewer is review_text and not verdict.get('issues')
                    and not is_first_pass_acceptance(context)):
                try:
                    if report:
                        report('text_unavailable_review_retry',
                               failed_dimensions=sorted(verdict['review_errors']))
                    followup=reviewer(request,draft,context,model=model,
                        previous_reviewers=verdict.get('reviewer_models'),report=report)
                    reviews.append(followup)
                    if (followup.get('pass') is True and
                            not followup.get('review_errors')):
                        return {**result,'answer':draft,'quality_review':{
                            **followup,'attempts':2,'repair_applied':False,
                            'previous_review_errors':dict(verdict['review_errors'])}}
                    verdict=followup
                except Exception as exc:
                    if report:
                        report('text_unavailable_review_retry_failed',
                               error_type=type(exc).__name__)
            return {**result, 'action': 'write_blocked', 'answer': draft,
                    'quality_review': {**verdict, 'status':'BLOCKED', 'pass':False,
                                       'attempts': len(reviews),
                                       'repair_applied': False}}
        if is_first_pass_acceptance(context) and final_repair_limit(context,1)==0:
            # Fast acceptance measures the author's first result. Production
            # may repair after review, but replaying a failed synthetic sample
            # hides first-pass quality and spends several extra model calls.
            return {**result, 'action': 'write_blocked', 'answer': draft,
                    'quality_review': {**verdict, 'status': 'BLOCKED', 'pass': False,
                        'attempts': 1, 'repair_applied': False,
                        'first_pass_only': True}}
        if reviewer is review_text and repairer is repair_text:
            verdict=_validate_text_applicability(request,draft,context,verdict,model,
                full_review=lambda:reviewer(request,draft,context,model=model,
                    previous_reviewers=verdict.get('reviewer_models'),report=report),report=report)
            reviews[-1]=verdict
            if verdict.get('full_recheck_after_finding_dismissal') or verdict.get('review_errors'):
                return {**result,'action':'write' if verdict.get('pass') else 'write_blocked',
                    'answer':draft,'quality_review':{**verdict,'attempts':2 if
                    verdict.get('full_recheck_after_finding_dismissal') else 1,'repair_applied':False}}
        role_pool=verdict.get('quality_model_pool') or []
        if (reviewer is review_text and repairer is repair_text and reviewer_models is None
                and not _reviewer_pool_is_prevalidated()):
            # Calibrate only the extra capacity needed for a different writer.
            # Previously checked reviewers are process-cached; no content is
            # regenerated to discover a model's response capability.
            _, eligible = select_reviewer_models(model,reserve_reviser=True,return_pool=True)
            role_pool=known_quality_model_pool(model,verdict.get('reviewer_models') or {},
                                               eligible_models=eligible)
        roles = allocate_revision_roles(model, role_pool,
            required_reviewers=MIN_REVIEWER_FAMILIES if reviewer is review_text and repairer is repair_text else 4,
            fixed_reviewers=reviewer_models)
        if report:
            report('text_revision_roles_selected', initial_author_model=model,
                   revision_author_model=roles.revision_author,
                   reviewer_pool=list(roles.reviewer_pool), reason=roles.reason)
        revised = repairer(request, draft, verdict['issues'], context,
                           model=roles.revision_author, report=report)
        working_draft=revised
        previous_reviewers = verdict.get('reviewer_models') if isinstance(verdict, dict) else None
        if roles.reviewer_pool:
            with acceptance_model_pool(roles.reviewer_pool):
                verdict2 = reviewer(request, revised, context, model=roles.revision_author,
                                    reviewer_models=reviewer_models,
                                    previous_reviewers=previous_reviewers, report=report)
        else:
            verdict2 = reviewer(request, revised, context, model=roles.revision_author,
                                reviewer_models=reviewer_models,
                                previous_reviewers=previous_reviewers, report=report)
        if reviewer is review_text and repairer is repair_text:
            with acceptance_model_pool(roles.reviewer_pool or role_pool):
                verdict2=_validate_text_applicability(request,revised,context,verdict2,roles.revision_author,
                    full_review=lambda:reviewer(request,revised,context,model=roles.revision_author,
                        previous_reviewers=verdict2.get('reviewer_models'),report=report),report=report)
        verdict2['initial_author_model'] = model
        if verdict.get('finding_adjudication'):
            if verdict2.get('finding_adjudication'):
                verdict2['finding_adjudication_history']=[verdict['finding_adjudication'],verdict2['finding_adjudication']]
            else:
                verdict2['finding_adjudication']=verdict['finding_adjudication']
        verdict2['revision_author_model'] = roles.revision_author
        verdict2['revision_role_policy'] = roles.reason
        reviews.append(verdict2)
        if verdict2.get('pass'):
            return {**result, 'answer': revised, 'quality_review': {
                **verdict2, 'attempts': 2, 'repair_applied': True,
                'previous_issues': verdict['issues']}}
        return {**result, 'action': 'write_blocked', 'answer': revised,
                'quality_review': {**verdict2, 'attempts': 2,
                                   'repair_applied': True,
                                   'previous_issues': verdict['issues']}}
    except Exception as exc:
        latest=reviews[-1] if reviews else {}
        if working_draft!=draft:
            # Retain a completed correction if its subsequent review failed.
            # No verdict on the old draft can certify these new bytes.
            latest={**latest,'checks':dict.fromkeys(CHECKS,False),'review_state':'unavailable',
                'completed_dimensions':[],'issues':[],'repair_applied':True,
                'previous_issues':list(latest.get('issues') or []),'original_draft':draft}
        return {**result, 'action': 'write_blocked', 'answer': working_draft,
                'quality_review': {**latest,'status': 'BLOCKED', 'pass': False,
                                   'attempts': len(reviews),
                                   'review_role': 'random_independent_editorial_panel',
                                   'independent_model': latest.get('independent_model',False),
                                   'reviewer_models': dict(latest.get('reviewer_models') or {}),
                                   'issues': list(latest.get('issues') or []),
                                   'original_draft_preserved':True,
                                   'review_state':'unavailable' if getattr(exc,'review_unavailable',False) else latest.get('review_state','unavailable'),
                                   'execution_error':f'{type(exc).__name__}: {str(exc)[:300]}',
                                   'reason': f'Цикл подготовки материала не завершён: {type(exc).__name__}: {str(exc)[:300]}'}}
