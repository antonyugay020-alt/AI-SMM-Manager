"""Canonical, provenance-preserving factual context for every task stage.

Only the user's current request and explicitly verified project materials are
factual authorities. Prior AI drafts and generated documents are never silently
promoted to evidence. The same immutable context can be supplied to generation,
repair, deterministic audits, and independent review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable


FACT_CONTEXT_CHARACTER_BUDGET=20000


@dataclass(frozen=True)
class EvidenceSource:
    source_id: str
    source_type: str
    label: str
    content: str


@dataclass(frozen=True)
class EvidenceContext:
    sources: tuple[EvidenceSource, ...]
    unknowns: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    references: tuple[EvidenceSource, ...] = ()

    def source_index(self) -> dict[str, dict[str, str]]:
        return {s.source_id: {'type': s.source_type, 'label': s.label,
                              'content': s.content} for s in self.sources}

    def to_dict(self) -> dict[str, Any]:
        result = {'schema_version': 1,
                'sources': [{'source_id': s.source_id, 'type': s.source_type,
                             'label': s.label, 'content': s.content}
                            for s in self.sources],
                'unknowns': list(self.unknowns), 'conflicts': list(self.conflicts)}
        if self.references:
            result['nonfactual_references']=[{'source_id':s.source_id,'type':s.source_type,'label':s.label,'content':s.content} for s in self.references]
        return result

    def render(self, *, max_chars: int = FACT_CONTEXT_CHARACTER_BUDGET, include_request: bool = True) -> str:
        """Render all authorities within a stable budget; never silently clip a source."""
        blocks = []
        for src in self.sources:
            if not include_request and src.source_type == 'user_request':
                continue
            blocks.append(f"[{src.source_id}] {src.label}\n{src.content}")
        if self.references:
            blocks.append('ПРОИЗВОДНЫЙ КОНТЕКСТ ДИАЛОГА — НЕ ИСТОЧНИК БИЗНЕС-ФАКТОВ. '
                'Помогает понять, к чему относятся указания и ответы пользователя; само утверждение AI ничего не подтверждает:\n'+
                '\n\n'.join('['+s.source_id+'] '+s.label+'\n'+s.content for s in self.references))
        if self.unknowns:
            blocks.append('ЯВНО НЕИЗВЕСТНО (это пробелы, не факты):\n- ' +
                          '\n- '.join(self.unknowns))
        if self.conflicts:
            blocks.append('КОНФЛИКТУЮЩИЕ ИСТОЧНИКИ (не выбирай значение самостоятельно):\n- ' +
                          '\n- '.join(self.conflicts))
        rendered = '\n\n'.join(blocks)
        if len(rendered) > max_chars:
            raise ValueError('Проверенный контекст фактов превышает бюджет; нельзя молча обрезать источник')
        return rendered


def _clean(value: Any, limit: int | None = None) -> str:
    return value.strip()[:limit] if isinstance(value,str) and limit is not None else value.strip() if isinstance(value,str) else ''


def _materials(context: dict[str, Any]) -> Iterable[tuple[str, str, str]]:
    """Yield only materials carrying an explicit verification/approval marker."""
    values = context.get('verified_project_materials', context.get('verified_materials', []))
    if isinstance(values, dict):
        values = list(values.values())
    if not isinstance(values, (list, tuple)):
        return
    for index, item in enumerate(values):
        if isinstance(item, str):
            # Bare text has no auditable provenance and is intentionally ignored.
            continue
        if not isinstance(item, dict):
            continue
        verified = (item.get('verified') is True or item.get('user_confirmed') is True
                    or item.get('approval_status') in ('verified', 'user_confirmed', 'approved'))
        if not verified:
            continue
        content = _clean(item.get('content') or item.get('text') or item.get('extracted_text'))
        if not content:
            continue
        label = _clean(item.get('title') or item.get('name') or item.get('path') or f'Материал {index + 1}', 300)
        source_type = _clean(item.get('source_type') or 'verified_project_material', 80)
        if source_type in ('user_request','assistant_context'):
            raise ValueError('Зарезервированная роль в материале фактического контекста')
        yield source_type, label or f'Материал {index + 1}', content


def build_evidence_context(request: str, project_context: dict[str, Any] | None = None,
                           task_analysis: dict[str, Any] | None = None) -> EvidenceContext:
    if not isinstance(request, str) or not request.strip():
        raise ValueError('Нужен исходный пользовательский запрос для фактического контекста')
    context = project_context if isinstance(project_context, dict) else {}
    analysis = task_analysis if isinstance(task_analysis, dict) else {}
    sources: list[EvidenceSource] = []
    digest = hashlib.sha256(request.encode('utf-8')).hexdigest()[:16]
    sources.append(EvidenceSource(f'user_request:{digest}', 'user_request',
                                  'Исходный запрос пользователя', request.strip()))
    prior = context.get('evidence_context')
    if prior is not None:
        inherited = coerce_evidence_context(prior, request)
        sources.extend(s for s in inherited.sources if s.source_type != 'user_request')
    for index, (kind, label, content) in enumerate(_materials(context), 1):
        key = hashlib.sha256((kind + '\0' + label + '\0' + content).encode('utf-8')).hexdigest()[:16]
        candidate = EvidenceSource(f'project_material:{key}', kind, label, content)
        if not any(s.source_id == candidate.source_id for s in sources):
            sources.append(candidate)

    inherited_unknowns = list(inherited.unknowns) if prior is not None else []
    unknowns = analysis.get('unknowns', context.get('unknowns', inherited_unknowns))
    if isinstance(unknowns, str):
        unknowns = [unknowns] if unknowns.strip() else []
    if not isinstance(unknowns, (list, tuple)):
        unknowns = []
    unknowns = tuple(dict.fromkeys(_clean(x) for x in
                                   [*inherited_unknowns, *unknowns] if _clean(x)))

    inherited_conflicts = list(inherited.conflicts) if prior is not None else []
    conflicts = analysis.get('conflicts', context.get('fact_conflicts', inherited_conflicts))
    if isinstance(conflicts, str):
        conflicts = [conflicts] if conflicts.strip() else []
    if not isinstance(conflicts, (list, tuple)):
        conflicts = []
    conflicts = tuple(dict.fromkeys(_clean(x) for x in
                                    [*inherited_conflicts, *conflicts] if _clean(x)))
    references=list(inherited.references) if prior is not None else []
    for row in context.get('dialogue_references',[]):
        if (not isinstance(row,dict) or row.get('role')!='assistant'
                or row.get('authority')!='derived_dialogue_context_not_factual_evidence'
                or not isinstance(row.get('content'),str) or not row['content'].strip()):continue
        text=row['content'];key=hashlib.sha256((str(row.get('id'))+'\0'+text).encode()).hexdigest()[:16]
        ref=EvidenceSource('dialogue_context:'+key,'assistant_context','Нефактический контекст '+str(row.get('id','')),text)
        if ref.source_id not in {r.source_id for r in references}:references.append(ref)
    return EvidenceContext(tuple(sources), unknowns, conflicts,tuple(references))


def coerce_evidence_context(value: Any, request: str = '') -> EvidenceContext:
    """Validate a serialized context at component boundaries; fail closed."""
    if isinstance(value, EvidenceContext):
        value=value.to_dict()
    if isinstance(value, dict) and value.get('schema_version') == 1:
        sources = [];seen_ids=set()
        for row in value.get('sources', []):
            if not isinstance(row, dict):
                raise ValueError('Некорректный источник в фактическом контексте')
            source_id, content = row.get('source_id'), row.get('content')
            if not isinstance(source_id, str) or not source_id or not isinstance(content, str) or not content:
                raise ValueError('Источник фактов не имеет ID или текста')
            if source_id in seen_ids:raise ValueError('Повторяющийся ID источника фактов')
            seen_ids.add(source_id)
            if source_id.startswith('dialogue_context:') or row.get('type')=='assistant_context':
                raise ValueError('Производный контекст нельзя включать в источники фактов')
            sources.append(EvidenceSource(source_id, _clean(row.get('type'), 80),
                _clean(row.get('label'), 300), content))
        if sum(s.source_type=='user_request' for s in sources)!=1:
            raise ValueError('В фактическом контексте отсутствует исходный запрос пользователя')
        if request and not any(s.source_type == 'user_request' and s.content == request.strip()
                               for s in sources):
            raise ValueError('Фактический контекст относится к другому запросу')
        unknowns = value.get('unknowns', [])
        conflicts = value.get('conflicts', [])
        if not isinstance(unknowns, list) or not isinstance(conflicts, list):
            raise ValueError('Некорректные неизвестные или конфликты фактов')
        references=[];reference_ids=set()
        rows=value.get('nonfactual_references',[])
        if not isinstance(rows,list):raise ValueError('Некорректный производный контекст')
        for row in rows:
            if (not isinstance(row,dict) or row.get('type')!='assistant_context'
                    or not isinstance(row.get('source_id'),str) or not row['source_id'].startswith('dialogue_context:')
                    or not isinstance(row.get('content'),str) or not row['content'].strip()):
                raise ValueError('Некорректная ссылка на производный контекст')
            if row['source_id'] in reference_ids:raise ValueError('Повторяющийся ID производного контекста')
            reference_ids.add(row['source_id'])
            if row['source_id'] in {s.source_id for s in sources}:raise ValueError('Производный контекст совпадает с источником фактов')
            references.append(EvidenceSource(row['source_id'],'assistant_context',str(row.get('label','')),row['content']))
        return EvidenceContext(tuple(sources), tuple(map(str, unknowns)), tuple(map(str, conflicts)),tuple(references))
    if value is not None:raise ValueError('Неподдерживаемый или повреждённый фактический контекст')
    return build_evidence_context(request)
