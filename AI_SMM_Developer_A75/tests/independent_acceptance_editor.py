# -*- coding: utf-8 -*-
"""Independent, fail-closed, task-grounded editorial acceptance of actual Ollama output.

Shared by production and acceptance. A completed independent review is required
before persistence; unavailable opinions cannot certify documents. Model replies
are grounded in host-owned evidence, not self-reported completion.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import urllib.request  # shared transport monkeypatch seam used by existing adapters/tests
from editorial_evidence_matrix import (structural_digest, disproved_missing_field_claim,
    disproved_document_role_claim, disproved_unrequested_contact_link,
    disproved_user_stated_fact, disproved_calendar_format_strategy_claim, publication_rows, _cell)
from editorial_claim_policy import REVIEW_FACT_POLICY
from publication_schema import publication_field_type
from evidence_segments import sentence_spans
from assertion_scope import classify_obvious_non_business, requires_business_fact_review
from ollama_resilience import request_chat, OllamaStageError
from claim_interpretation import INCOMPLETE_ADJUDICATIONS
from artifact_review import (resolve_editorial_approval, validate_reason_reference,
    document_substance_contract, SubstanceReviewContract, SUBSTANCE_POLICY)
from artifact_review import SubstanceControlFailure

MODEL = 'qwen3:8b'
ENDPOINT = 'http://127.0.0.1:11434/api/chat'
CRITICAL = {'invented_fact', 'contradiction', 'unsafe_claim', 'missing_required_content'}


class ValidatedEditorialVerdict(dict):
    """Host-validated review result; downstream gates must not parse it again."""


class EditorialContextError(ValueError):
    review_unavailable=True
    retryable=False


class IndependentReviewUnavailable(RuntimeError):
    """Typed validator failure; never reinterpret it as a content defect."""
    review_unavailable = True
    def __init__(self,message,findings=()):
        import copy
        self.findings=copy.deepcopy(list(findings))
        # A completed source rejection cannot be erased by a backup PASS.
        self.retryable=not any(isinstance(item,dict)
            and item.get('type') in ('invented_fact','unsafe_claim')
            and item.get('adjudication') not in (*INCOMPLETE_ADJUDICATIONS,None)
            for item in self.findings)
        super().__init__(message)


def _ground_quote(target: str, quote: str) -> str | None:
    """Map a claim to an exact source span or its exact Markdown table row.

    Markdown pipes separate displayed cells. If the reviewer joins adjacent
    cell values while quoting, preserve and return the original row as the
    evidence rather than reconstructing a quotation that never existed.
    """
    snippet = quote.strip() if isinstance(quote, str) else ''
    if not snippet:
        return None
    if snippet in target:
        return snippet
    pieces = re.findall(r'\S+', snippet)
    if not pieces or len(pieces) > 60:
        return None
    pattern = r'\s+'.join(re.escape(x) for x in pieces)
    match = re.search(pattern, target, flags=re.IGNORECASE)
    if match:
        return match.group(0)
    def normalize_markdown(value):
        value=value.replace('\\|',' ').replace('|',' ')
        value=re.sub(r'[`*_~]','',value)
        value=re.sub(r'^\s*>\s?','',value,flags=re.M)
        return re.sub(r'\s+',' ',value).strip().casefold()
    normalized=normalize_markdown(snippet)
    if normalized:
        for line in target.splitlines():
            if normalized in normalize_markdown(line):
                return line.strip()
    return None


def _recover_unique_claim_evidence(evidence: dict[str, str], document: str,
                                  claim: str) -> list[tuple[str, str]]:
    """Resolve a bad reviewer locator only from one exact claim in its document.

    Evidence IDs are routing metadata, not evidence. If a model returns a
    stale or wrong ID, the host may canonicalize it only when the exact claim
    has one location in the document the reviewer named. Cross-document and
    fuzzy recovery are deliberately forbidden; duplicates remain ambiguous.
    """
    if document not in ('strategy', 'calendar') or not isinstance(claim, str) or not claim.strip():
        return []
    matches = []
    prefix = document + ':'
    for evidence_id, segment in evidence.items():
        if not evidence_id.startswith(prefix):
            continue
        # Recovery has a stricter contract than ordinary citation validation:
        # byte-exact text or whitespace-only normalization, never fuzzy token
        # matching and never a reconstructed table-row citation.
        grounded = claim.strip()
        if grounded in segment:
            matches.append((evidence_id, grounded))
            continue
        pieces = re.findall(r'\S+', grounded)
        if pieces:
            match = re.search(r'\s+'.join(re.escape(piece) for piece in pieces), segment)
            if match:
                matches.append((evidence_id, match.group(0)))
    return matches


def _evidence_chunks(strategy: str, calendar: str, *, chunk_size: int = 1800,
                     table_cells: bool = False) -> dict[str, str]:
    """Immutable host-generated citations, never quotations invented by a model.

    Blank lines have no evidence. A Markdown table row is the indivisible
    evidence unit because fields in one post are interdependent. IDs are local
    to one verdict, not durable business facts.
    """
    catalog = {}
    for name, document in (('strategy', strategy), ('calendar', calendar)):
        index = 0
        for line in document.splitlines():
            if not line.strip():
                continue
            if line.lstrip().startswith('|'):
                index += 1
                catalog[f'{name}:{index:04d}'] = line.strip()
                if table_cells:
                    for cell in (part.strip() for part in line.strip().strip('|').split('|')):
                        if not cell or re.fullmatch(r'[-: ]+',cell):
                            continue
                        for fragment in re.split(r'(?<=[.!?])\s+',cell):
                            fragment=fragment.strip()
                            if fragment:
                                index += 1
                                catalog[f'{name}:{index:04d}']=fragment
                continue
            # Table cells and sentence-sized fragments give the reviewer
            # useful evidence without asking a model to reproduce quotations.
            cells = [cell.strip() for cell in line.split('|') if cell.strip()] if '|' in line else [line]
            fragments = []
            for cell in cells:
                fragments.extend(re.split(r'(?<=[.!?])\s+', cell))
            for fragment in fragments:
                for offset in range(0, len(fragment), chunk_size):
                    segment = fragment[offset:offset + chunk_size]
                    if not segment.strip():
                        continue
                    index += 1
                    catalog[f'{name}:{index:04d}'] = segment
    return catalog


def _numbered_document(name: str, strategy: str, calendar: str) -> str:
    catalog = _evidence_chunks(strategy, calendar, table_cells=True)
    return '\n'.join(f'[{key}] {value}' for key,value in catalog.items()
                     if key.startswith(name + ':'))


def _inventory_segments(strategy: str, calendar: str):
    """Create deterministic, structure-aware evidence units for inventory review.

    The model selects host-issued sentence IDs; it never copies or paraphrases
    claim text. This keeps provenance exact while allowing a model to miss a
    boundary without corrupting the cited source span. Markdown section and
    table-field context travels with each segment: a proposed visual or
    measurement method must not be mistaken for an asset or practice the
    business already has.
    """
    result=[]
    for document, body in (('strategy',strategy),('calendar',calendar)):
        index=0
        heading_stack=[]
        table_headers=[]
        lines=body.splitlines()
        for line_number,line in enumerate(lines):
            row_slot=''
            if not line.strip():
                continue
            heading=re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$',line)
            if heading:
                level=len(heading.group(1))
                heading_stack=[(old_level,title) for old_level,title in heading_stack
                               if old_level<level]
                heading_stack.append((level,heading.group(2).strip()))
                headings=[title for _,title in heading_stack]
                table_headers=[]
                continue
            headings=[title for _,title in heading_stack]
            if '|' in line:
                cells=[cell.strip() for cell in re.split(r'(?<!\\)\|',line.strip().strip('|'))]
                if cells and all(re.fullmatch(r'[-: ]+',cell or '-') for cell in cells):
                    continue
                # Markdown's header/separator pair is an explicit structural
                # boundary. Trust it directly instead of guessing table fields
                # from a language-specific vocabulary (which broke English,
                # mixed-language, and user-defined headers).
                if line_number+1<len(lines):
                    next_line=lines[line_number+1]
                    next_cells=[cell.strip() for cell in re.split(r'(?<!\\)\|',next_line.strip().strip('|'))]
                    if ('|' in next_line and len(next_cells)==len(cells) and next_cells
                            and all(re.fullmatch(r'[-: ]+',cell or '-') for cell in next_cells)):
                        table_headers=cells
                        continue
                header_hits=sum(bool(re.search(
                    r'недел|день|площад|формат|тема|цель|сценар|визуал|текст|реплик|cta|kpi|метрик|результат|материал|ресурс|производств|публикац',cell,re.I))
                    and len(cell)<=90 for cell in cells)
                if cells and header_hits>=max(2,(len(cells)+1)//2):
                    table_headers=cells
                    continue
                if table_headers and len(cells)==len(table_headers):
                    row_slot=cells[0] if cells else ''
                    for header,cell in zip(table_headers,cells):
                        if not cell:
                            continue
                        for start,end in sentence_spans(cell):
                            fragment=cell[start:end]
                            if not fragment:
                                continue
                            # Sentences are evidence, not character buffers.
                            # A long sentence is reviewed whole or explicitly
                            # rejected by the context budget, never mid-word.
                            index+=1
                            result.append({'evidence_id':f'{document}:{index:04d}',
                                'document':document,'section_path':headings.copy(),
                                'field_role':header,'row_context':{'fields':table_headers.copy(),
                                                                   'slot':row_slot},
                                'field_type':publication_field_type(header),
                                'content':fragment})
                    continue
            cells=[cell.strip() for cell in re.split(r'(?<!\\)\|',line) if cell.strip()] if '|' in line else [line.strip()]
            for cell in cells:
                for start,end in sentence_spans(cell):
                    fragment=cell[start:end]
                    if not fragment:
                        continue
                    pieces=[fragment]
                    for piece in pieces:
                        index+=1
                        field_role = headings[-1] if headings else 'document_narrative'
                        labelled = re.match(
                            r'^\s*(?:[-*+]\s*)?(?:\*\*|__)?([^:*|]{2,70}?)(?:\*\*|__)?\s*[:—-]\s*(.*)$',
                            piece)
                        if labelled:
                            field_role=labelled.group(1).strip(' *_`')
                        result.append({'evidence_id':f'{document}:{index:04d}',
                                       'document':document,'section_path':headings.copy(),
                                       'field_role':field_role,
                                       'field_type':publication_field_type(field_role),
                                       'row_context':{},
                                       'content':piece})
    return result


def _bound_editor_segments(strategy,calendar,segments=None):
    """Use only unchanged units from the complete typed artifact catalog."""
    original=_inventory_segments(strategy,calendar)
    if segments is None:
        return original
    catalog={item['evidence_id']:item for item in original}
    if (not isinstance(segments,(list,tuple)) or not segments
            or any(not isinstance(item,dict) or item.get('evidence_id') not in catalog
                   or catalog[item['evidence_id']]!=item for item in segments)
            or len({item['evidence_id'] for item in segments})!=len(segments)):
        raise ValueError('Окно редактора не принадлежит полному исходному каталогу полей')
    return list(segments)


def _editor_evidence_index(strategy: str, calendar: str, *, segments=None):
    """One typed field-level index shared by claim inventory and final review."""
    segments=_bound_editor_segments(strategy,calendar,segments)
    catalog={item['evidence_id']:item['content'] for item in segments}
    def render(document):
        rows=[];previous=None
        for item in segments:
            if item['document']!=document:
                continue
            context={
                'section':item.get('section_path',[]),
                'field':item.get('field_role','document_narrative'),
                'field_type':item.get('field_type','document_narrative'),
                'row':item.get('row_context',{}).get('slot',''),
            }
            if context!=previous:
                rows.append('FIELD '+json.dumps(context,ensure_ascii=False))
                previous=context
            rows.append(f"[{item['evidence_id']}] "+item['content'])
        return '\n'.join(rows)
    return catalog,render


def _inventory_batches(segments, *, max_items: int = 16, max_chars: int = 5000):
    """Partition typed evidence without splitting or rewriting a source segment."""
    if type(max_items) is not int or max_items < 1 or type(max_chars) is not int or max_chars < 256:
        raise ValueError('Недопустимые границы пакета проверки фактов')
    batch=[];size=0
    for segment in segments:
        cost=len(segment.get('content',''))
        if batch and (len(batch)>=max_items or size+cost>max_chars):
            yield batch
            batch=[];size=0
        batch.append(segment);size+=cost
        # A single long sentence remains intact and is processed alone.
        if len(batch)>=max_items or size>=max_chars:
            yield batch
            batch=[];size=0
    if batch:
        yield batch


def _inventory_business_assertions(request: str, strategy: str, calendar: str, *,
                                   endpoint: str = ENDPOINT, model: str = MODEL,
                                   report=None) -> list[dict[str, Any]]:
    """Classify typed artifact fields in bounded, exactly-covered batches.

    Editorial quality and cross-document consistency are reviewed elsewhere.
    This operation has one narrow contract: identify statements that assert
    something about the actual business, its services, operations, assets,
    conditions, team, customer experience, or outcomes. It does not infer
    whether those statements are true; the source adjudicator owns that.
    """
    segments=_inventory_segments(strategy,calendar)
    review_segments=[]
    findings=[]
    host_classified_count=0
    for segment in segments:
        obvious_scope=classify_obvious_non_business(segment)
        if obvious_scope:
            host_classified_count+=1
            if report:
                report('business_assertion_inventory_host_classified',
                       evidence_id=segment.get('evidence_id'),
                       statement_class=obvious_scope['statement_class'],
                       reason=obvious_scope['reason'][:180])
            continue
        if requires_business_fact_review(segment):
            finding={'document':segment['document'],'type':'invented_fact',
                'quote':segment['content'],'claim':segment['content'],
                'reason':'Утверждение в публикуемом тексте требует прямой сверки с источниками.',
                'evidence_id':segment['evidence_id'],'evidence_segment':segment['content'],
                'section_path':segment.get('section_path',[]),
                'field_role':segment.get('field_role',''),
                'field_type':segment.get('field_type','document_narrative'),
                'row_context':segment.get('row_context',{})}
            findings.append(finding)
            if report:
                report('business_assertion_inventory_host_claimed',
                       evidence_id=segment.get('evidence_id'),
                       reason='Публичный текст содержит утверждение от лица проекта или приглашение посетить площадку.')
            continue
        review_segments.append(segment)
    pending_batches=list(_inventory_batches(review_segments))
    if not pending_batches:
        if report:
            report('business_assertion_inventory_completed',segments=len(segments),
                   host_classified=host_classified_count,batches=0,
                   business_claims=len(findings))
        return findings
    batch_number=0
    while pending_batches:
        batch=pending_batches.pop(0)
        batch_number+=1
        expected=[item['evidence_id'] for item in batch]
        refs=sorted(expected)
        rows=[]
        for item in batch:
            rows.append({'evidence_id':item['evidence_id'],
                'document':item['document'],'section':item.get('section_path',[]),
                'field_type':item.get('field_type','document_narrative'),
                'field_role':item.get('field_role','document_narrative'),
                'row':item.get('row_context',{}).get('slot',''),
                'text':item['content']})
        system=(
            'Ты классификатор фактических утверждений в материалах компании. '
            'Проверяй ТОЛЬКО сегменты текущего небольшого пакета и верни ровно одну классификацию для каждого ID. '
            'business_claim означает, что текст утверждает или обещает конкретный факт о реальном бизнесе: '
            'его услугах, реальных действиях и процессах, команде, оборудовании/ресурсах, условиях, '
            'доступности, клиентском опыте или результате. Проверяй смысл в контексте поля. '
            'not_business_claim — образовательный тезис об отрасли, практический совет или инструкция читателю, '
            'условный пример, тема/цель/аудитория/KPI/метод измерения, '
            'визуальная инструкция без утверждения о реальном бизнесе, вопрос, условный пример, '
            'отрицание/описание неизвестного либо редакторская инструкция. '
            'Идея контента не освобождает от проверки, если в тексте подписи/реплики прямо утверждается, '
            'что компания уже оказывает услугу, выполняет действие, использует процесс или ресурс, '
            'обладает свойством либо обещает клиентский опыт/результат. Такое утверждение всегда business_claim, '
            'даже внутри предлагаемой публикации или сценария. Постановочная сцена без утверждения о реальной '
            'работе компании остаётся not_business_claim. '
            'Обычная инструкция читателю (например, подготовить список, проверить сведения, '
            'сравнить варианты или разложить файлы) не является фактом о компании, даже если она '
            'предлагается в подписи. Не считай слова «поможет», «быстрее» или «сэкономит время» '
            'подтверждением действия компании, если исполнитель явно не назван. CTA «напишите нам» '
            'сам по себе не обещает конкретный ответ, срок или результат. Выбирай business_claim '
            'только если сегмент утверждает конкретный факт/обещание именно о компании или её услуге; '
            'при отсутствии явного исполнителя общий совет классифицируй not_business_claim. '
            'Не считай отсутствие подтверждения основанием '
            'для not_business_claim: наличие/отсутствие источника проверяет следующий арбитр. '
            'Наличие или отсутствие подтверждения в запросе НЕ влияет на тип классификации. '
            'Не объединяй соседние сегменты и не создавай текст. Возвращай только JSON.')
        user=('ЗАДАНИЕ ПОЛЬЗОВАТЕЛЯ (контекст, не цитата):\n'+request+
              '\n\nСЕГМЕНТЫ С ИХ СТРУКТУРНЫМИ ПОЛЯМИ:\n'+
              json.dumps(rows,ensure_ascii=False))
        # A JSON object with one required key per host-issued segment makes
        # missing, duplicate and invented IDs unrepresentable in the schema.
        # Arrays cannot express uniqueness or full key coverage in Ollama's
        # constrained JSON Schema grammar.
        schema={'type':'object','additionalProperties':False,
            'properties':{'segment_decisions':{'type':'object','additionalProperties':False,
                'properties':{ref:{'type':'string','enum':['business_claim','not_business_claim']}
                              for ref in refs},
                'required':refs}},
            'required':['segment_decisions']}
        if report:
            report('business_assertion_inventory_batch_started',batch=batch_number,
                   batches=batch_number+len(pending_batches),segments=len(batch))
        payload={'model':model,'stream':False,'think':False,'format':schema,
            'messages':[{'role':'system','content':system},{'role':'user','content':user}],
            'options':{'temperature':0,'num_ctx':16384,'num_predict':1200}}
        last_error=None
        by_id=None
        for response_attempt,budget in enumerate((1200,2400),1):
            payload['options']['num_predict']=budget
            try:
                answer=request_chat(payload,stage='классификация фактических утверждений',
                    timeout=240,attempts=1,report=report,endpoint=endpoint)
                if not isinstance(answer,dict) or not isinstance(answer.get('message'),dict):
                    raise ValueError('Классификатор не вернул структурированный ответ')
                raw=answer['message'].get('content')
                value=json.loads(raw) if isinstance(raw,str) else None
                decisions=value.get('segment_decisions') if isinstance(value,dict) else None
                if not isinstance(decisions,dict) or set(decisions)!=set(expected):
                    raise ValueError('Классификатор вернул неполное покрытие сегментов')
                if any(kind not in ('business_claim','not_business_claim') for kind in decisions.values()):
                    raise ValueError('Классификатор вернул недопустимый тип утверждения')
                by_id=decisions
                break
            except Exception as exc:
                if isinstance(exc,(OllamaStageError,OSError,TimeoutError)):
                    raise  # More output tokens cannot repair a transport outage.
                last_error=exc
                if response_attempt<2 and report:
                    report('business_assertion_inventory_retry',batch=batch_number,
                           attempt=response_attempt,error_type=type(exc).__name__,next_num_predict=2400)
        if by_id is None:
            exc=last_error or ValueError('Классификатор не вернул результат')
            split_reason=('incomplete_segment_coverage'
                          if isinstance(exc,ValueError) and
                          str(exc)=='Классификатор вернул неполное покрытие сегментов'
                          else 'invalid_json' if isinstance(exc,json.JSONDecodeError)
                          else 'timeout' if any(token in str(exc).casefold()
                              for token in ('timeout','timed out','время ожидания'))
                          else None)
            if len(batch)>1 and split_reason:
                middle=len(batch)//2
                pending_batches[0:0]=[batch[:middle],batch[middle:]]
                if report:
                    report('business_assertion_inventory_batch_split',batch=batch_number,
                           reason=split_reason,segments=len(batch),
                           child_segments=[len(batch[:middle]),len(batch[middle:])])
                continue
            if report:
                report('business_assertion_inventory_batch_failed',batch=batch_number,
                       batches=batch_number+len(pending_batches),error_type=type(exc).__name__,reason=str(exc)[:320])
            raise IndependentReviewUnavailable('Реестр фактических утверждений не завершён: '
                +type(exc).__name__+': '+str(exc)[:320]) from exc
        batch_business_claims=0
        for item in batch:
            if by_id[item['evidence_id']]!='business_claim':
                continue
            batch_business_claims+=1
            findings.append({'document':item['document'],'type':'invented_fact',
                'quote':item['content'],'claim':item['content'],
                'reason':'Бизнес-утверждение требует проверки по источникам.',
                'evidence_id':item['evidence_id'],'evidence_segment':item['content'],
                'section_path':item.get('section_path',[]),
                'field_role':item.get('field_role',''),
                'field_type':item.get('field_type','document_narrative'),
                'row_context':item.get('row_context',{})})
        if report:
            report('business_assertion_inventory_batch_completed',batch=batch_number,
                   batches=batch_number+len(pending_batches),segments=len(batch),
                   business_claims=batch_business_claims)
    if report:
        report('business_assertion_inventory_completed',segments=len(segments),
               batches=batch_number,business_claims=len(findings))
    return findings


def parse_verdict(raw: str, *, strategy: str, calendar: str, request: str = '',
                  require_claim: bool = False,
                  host_evidence_only: bool = False, table_cells: bool = False,
                  _evidence_catalog: dict[str, str] | None = None,
                  _segments=None,_substantive=False) -> dict[str, Any]:
    """Validate a reviewer issue against an exact claim and host-authored citation.

    Reviewer explanations may quote the user's request. The cited business claim
    is therefore a separate field and is matched byte-for-byte (whitespace
    normalized) to both the selected document and its immutable evidence segment.
    """
    value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get('pass')) is not bool or not isinstance(value.get('issues'), list):
        raise ValueError('Некорректный редакторский вердикт')
    issues = []
    discarded_unanchored_requirement = False
    evidence = (_evidence_catalog if _evidence_catalog is not None else
                _evidence_chunks(strategy, calendar, table_cells=table_cells))
    segment_metadata={item['evidence_id']:item for item in _bound_editor_segments(strategy,calendar,_segments)} \
        if host_evidence_only else {}
    for issue in value['issues']:
        if not isinstance(issue, dict):
            raise ValueError('Некорректная проблема в редакторском вердикте')
        kind=issue.get('type');source=issue.get('document');ref=issue.get('evidence_id')
        claim=issue.get('claim')
        # Historical offline fixtures used `quote` for the exact document
        # excerpt. Live acceptance always sets require_claim=True and cannot
        # fall back to a row or a reviewer explanation.
        if claim is None and not require_claim:
            claim=issue.get('quote')
        reason=issue.get('reason')
        if source not in ('strategy','calendar'):
            raise ValueError('Редактор не указал документ и подтверждающую цитату')
        if kind not in ('invented_fact','contradiction','unsafe_claim','missing_required_content','quality','other'):
            raise ValueError('Неизвестный тип проблемы редактора')
        if not isinstance(reason,str) or len(reason.strip())<9:
            raise ValueError('Редактор не объяснил проблему')
        if require_claim:
            if not host_evidence_only and (not isinstance(claim,str) or not claim.strip()):
                raise ValueError('Редактор не вернул точный claim и evidence_id')
            if isinstance(ref, str) and ref in evidence:
                # A valid host ID is authoritative about document identity,
                # even if the model's duplicate `document` label disagrees.
                source = ref.split(':',1)[0]
                if host_evidence_only:
                    claim=evidence[ref]
                grounded=_ground_quote(evidence[ref],claim)
            else:
                grounded = None
            if grounded is None:
                # Canonicalize stale, invalid, or wrong-row IDs only from one
                # exact location in the declared/host-canonical document.
                recovered = _recover_unique_claim_evidence(evidence, source, claim)
                if len(recovered) == 1:
                    ref,grounded=recovered[0]
                elif len(recovered) > 1:
                    raise ValueError('issue.claim неоднозначен: найден в нескольких сегментах документа')
                elif isinstance(ref, str) and ref in evidence:
                    # Preserve the specific ambiguity diagnostic for duplicate
                    # copies of the same exact wording in one document.
                    matches = [key for key, segment in evidence.items()
                               if key.startswith(source + ':') and
                               _ground_quote(segment, claim) is not None]
                    if len(matches) > 1:
                        raise ValueError('issue.claim неоднозначен: найден в нескольких сегментах документа')
                else:
                    raise ValueError('Редактор указал недостоверный идентификатор доказательства')
                if grounded is None and isinstance(ref, str) and ref in evidence:
                    raise ValueError('issue.claim отсутствует в документе: '+repr(str(claim)[:160]))
            source=ref.split(':',1)[0]
        elif ref is not None and (not isinstance(ref,str) or not ref.startswith(source+':') or ref not in evidence):
            raise ValueError('Редактор указал недостоверный идентификатор доказательства')
        target=strategy if source=='strategy' else calendar
        cited=evidence.get(ref,'') if ref is not None else ''
        if host_evidence_only and ref is not None:
            # In this review mode the model selects a host-issued segment ID;
            # it does not supply an independent quotation. Canonicalize both
            # claim and citation from that immutable segment exactly once.
            # This avoids a second string-containment check disagreeing with
            # the evidence catalog's Markdown/whitespace normalization.
            grounded=cited
            claim=cited
        if not require_claim:
            grounded=_ground_quote(target,claim)
        if grounded is None and (require_claim or ref is None):
            raise ValueError('Указанное issue.claim отсутствует в документе: '+repr(str(claim)[:160]))
        if ref is not None and not host_evidence_only:
            # The live reviewer selects a host-issued segment ID. In that mode
            # `grounded` and `cited` were just canonicalized from the same
            # immutable catalog value above; comparing them again adds no
            # evidence and can fail on normalization differences. Legacy and
            # offline callers still validate their separately supplied claim.
            if (grounded is not None and grounded not in cited
                    and _ground_quote(cited,grounded) is None):
                raise ValueError('issue.claim не находится в выбранном сегменте evidence_id')
            # Offline legacy fixtures sometimes supplied the complete host
            # segment as quote. Normalize those only outside the live gate.
            if grounded is None and not require_claim:
                grounded=cited.strip()[:250]
        if grounded is None:
            raise ValueError('Редактор не привёл проверяемое доказательство')
        related=issue.get('related_evidence_ids',[])
        if (not isinstance(related,list) or len(related)>4 or
                any(not isinstance(key,str) or key not in evidence for key in related)
                or len(related)!=len(set(related))):
            raise ValueError('Неверные ID связанных фрагментов')
        if host_evidence_only:
            validate_reason_reference(reason,'\n'.join([grounded,*[evidence[key] for key in related]]),
                                      evidence.values(),request)
        # Keep the exact claim separate from its enclosing table row/context.
        # Review reasons often quote the user's brief; using that prose as the
        # finding itself caused real unsupported claims to disappear inside an
        # unrelated row citation.
        normalized={'document':source,'type':kind,'quote':grounded,'claim':grounded,
                    'reason':reason.strip()}
        if 'criterion_id' in issue:normalized['criterion_id']=issue['criterion_id']
        if ref is not None:
            normalized['evidence_segment'] = evidence[ref]
            metadata=segment_metadata.get(ref,{})
            if metadata:
                normalized.update({key:metadata.get(key) for key in
                    ('section_path','field_role','field_type','row_context')})
                # A model must cite the field that actually supports its reason.
                # Metric-specific findings belong to KPI/method fields; claims
                # that a measurement method is missing must cite that method
                # field. A valid ID alone is not enough when the reason is about
                # a different row cell (seen in live R43 acceptance).
                if kind=='quality' and host_evidence_only and not _substantive:
                    field_type=str(metadata.get('field_type') or '')
                    reason_folded=reason.casefold()
                    metric_reason=bool(re.search(
                        r'\b(?:kpi|метрик\w*|показател\w*|охват\w*|сохранени\w*|'
                        r'входящ\w*\s+вопрос\w*|комментар\w*|reach|impressions)\b',
                        reason_folded,re.I))
                    method_reason=bool(re.search(
                        r'\b(?:как\s+именно\s+(?:измер\w*|собират\w*|анализир\w*)|'
                        r'метод\w*\s+измерен\w*|способ\w*\s+измерен\w*|'
                        r'вручную\s+(?:учитывать|подсчитывать)|собират\w*\s+и\s+анализир\w*)\b',
                        reason_folded,re.I))
                    if source=='calendar' and metric_reason and field_type not in {'kpi','measurement'}:
                        raise ValueError('Качество метрики процитировано из поля '+
                            repr(field_type)+' вместо KPI/measurement')
                    if source=='calendar' and method_reason and field_type!='measurement':
                        raise ValueError('Замечание о способе измерения должно ссылаться на поле measurement')
        if kind == 'missing_required_content':
            requirement_quote = issue.get('requirement_quote')
            requirement_grounded = (_ground_quote(request, requirement_quote)
                                    if isinstance(requirement_quote, str) else None)
            if host_evidence_only and requirement_grounded is None:
                raise ValueError('Замечание о требовании не связано с точной цитатой исходного задания')
            from requirement_grounding import requirement_quote_applies
            if (requirement_grounded is None or
                    (not host_evidence_only and not requirement_quote_applies(requirement_grounded, reason))):
                # Live review validates exact source provenance here. Semantic
                # applicability is decided by the independent AI adjudicator,
                # never by vocabulary overlap. Legacy offline parsing retains
                # its historical grounding adapter.
                discarded_unanchored_requirement = True
                continue
            normalized['requirement_quote'] = requirement_grounded
        if ref is not None: normalized['evidence_id']=ref
        if related:
            normalized.update(related_evidence_ids=list(related),
                              related_quotes=[evidence[key] for key in related])
        issues.append(normalized)
    if not value['pass'] and not issues:
        if discarded_unanchored_requirement:
            # The only findings were unsupported claims of explicit
            # requirements. Other factual/quality findings are unaffected.
            return {'pass': True, 'issues': []}
        raise ValueError('Редактор отверг материалы без конкретной проблемы')
    return {'pass':resolve_editorial_approval(value['pass'],issues),'issues':issues}


def _call_editor_once(request: str, strategy: str, calendar: str, *, endpoint: str = ENDPOINT,
                      part: str = "", strategy_only: bool = False,
                      review_scope: str = 'complete',
                      evidence_context=None, report=None, model: str = MODEL,
                      _segments=None,_strategy_context='',_substance_contract=None,
                      _portfolio=False) -> dict[str, Any]:
    available=(request,strategy) if strategy_only or review_scope=='strategy_only' else (request,calendar) if review_scope=='calendar_only' else (request,strategy,calendar)
    if not all(isinstance(x, str) and x.strip() for x in available):
        raise ValueError('Исходный запрос и документ текущей области проверки обязательны')
    system = (
        "Ты независимый строгий редактор, а НЕ автор документов. Сверь результат с ТРЕБОВАНИЯМИ пользователя. "
        "Проверь, что язык пользовательского текста соответствует языку исходного запроса, если пользователь явно не запросил другой язык. "
        "При явном несоответствии укажи точную цитату из документа и тип quality; не требуй один конкретный язык для всех задач. "
        "Выявляй только содержательные, проверяемые проблемы: выдуманные факты, обещания без оснований, "
        "неизвестные цены или каналы записи, противоречия между стратегией и календарём, пробелы в полях, "
        "несоответствие визуала/CTA площадке и повторы. Не отвергай за отсутствие выдуманных данных. "
        "Неизвестные цена, адрес или расписание НЕ обязаны быть выдуманы или заполнены. "
        "Исходный запрос — источник требований и фактов; стратегия является проверяемым результатом, "
        "а не источником новых обязательных запретов. Не приписывай пользователю ограничения из стратегии. "
        "Перед замечанием о неподтверждённом факте сравни конкретное утверждение с исходным запросом: "
        "если пользователь явно назвал услугу, процесс или порядок записи, не объявляй их выдуманными. "
        "Цель/тема публикации описывает, что контент должен раскрыть, но не доказывает фактический метод бизнеса. "
        "Пометка «не подтверждено/не указано в исходных данных» — это описание границ имеющихся сведений, "
        "а не отрицание самой услуги; не называй такую пометку выдуманным бизнес-фактом. "
        "Не требуй повторять в стратегии все тексты и форматы постов: контент-календарь вправе "
        "самостоятельно задать формат поста, экранные титры и структуру слайдов. Их отсутствие в стратегии "
        "не является противоречием. "
        "Различай общие образовательные сведения по теме и факты о конкретном бизнесе: не отклоняй "
        "нейтральный общеизвестный тезис только потому, что его дословно нет в брифе. "
        "Общее утверждение по теме не становится бизнес-фактом только из-за того, что его нет в источниках бизнеса. "
        "Слова «помогаем», «готовы помочь» и похожее приглашение к диалогу сами по себе не обещают конкретную "
        "услугу или результат. Не отклоняй CTA-вопросы, аудиториальные потребности, редакторские цели и "
        "постановочные сцены как факты компании. Проверяй полный фрагмент и его поле; не делай вывод по "
        "изолированному слову или ID календаря. "
        "Оценивай его как общеобразовательное утверждение: отмечай только конкретную фактическую ошибку, "
        "опасный совет или чрезмерно категоричную формулировку; не называй его выдуманным фактом компании. "
        "Требуй основания для локальных/коммерческих утверждений о компании, точных чисел, результатов, "
        "цен, гарантий, отзывов, команды, каналов и конкретных операционных правил. "
        "Фактическое описание реальных процессов компании тоже требует основания: используемые методы, "
        "этапы работ, оборудование, персонал, обслуживание и фактические результаты нельзя выводить "
        "из отрасли или придумывать ради конкретики. Если таких сведений нет в запросе/проектных фактах, "
        "отметь конкретную фразу как invented_fact. Художественную сцену считай сценой, пока она не "
        "утверждает, что компания реально выполняет этот процесс или гарантирует результат. "
        "Цель получить консультационные вопросы не означает требование сайта, ссылки в bio, формы или отдельного канала записи. "
        "Не требуй контактную инфраструктуру, которую пользователь не просил и о наличии которой неизвестно. "
        "Приглашение написать вопрос в доступном канале — допустимый CTA без обещания конкретного способа записи. "
        "Для каждого замечания укажи одну конкретную ошибку, не смешивай её с требованием неизвестных данных. "
        "ИСХОДНЫЙ ЗАПРОС содержит инструкции и НЕ является документом для цитирования. "
        "СТРАТЕГИЯ и КАЛЕНДАРЬ разбиты системой на пронумерованные реальные сегменты. "
        "Для КАЖДОЙ проблемы выбери evidence_id из квадратных скобок у конкретного ошибочного сегмента, "
        "например strategy:0001 или calendar:0001. Никогда не придумывай новый идентификатор. "
        "Для каждой проблемы выбери evidence_id точного сегмента, содержащего её. "
        "Табличные публикации разбиты на отдельные сегменты с полями field и row. Каждый ID "
        "указывает на одну ячейку или одно предложение; не склеивай разные поля и не используй "
        "ID публикации вместо ID сегмента. Программа сама добавит исходный текст выбранного сегмента. "
        "Строка FIELD задаёт раздел, роль и публикацию для следующих ID до следующей строки FIELD. "
        "В reason объясни, почему именно эта фраза ошибочна. "
        "Перед замечанием о пропущенном поле проверь ВСЮ строку календаря, "
        "а не только первый сегмент: одна строка может содержать несколько частей. "
        "Отсутствие подробности в стратегии не запрещает указывать съёмочные инструкции в календаре. "
        "Не считай описания вымышленных сцен фактическими утверждениями о реальных событиях. "
        "СТРАТЕГИЯ — документ верхнего уровня; полные тексты, визуальные сценарии, CTA и KPI каждого поста "
        "представляются в КАЛЕНДАРЕ, если запрос прямо не требует их в стратегии. "
        "Для замечания об ОТСУТСТВИИ поля сверяй реальную колонку таблицы и структурный индекс: "
        "наличие полного текста, CTA и визуала можно проверить прямо по значениям ячеек. "
        "Краткость или низкое качество существующего текста — проблема типа quality, а НЕ отсутствие текста. "
        "Не называй неизвестную бизнес-информацию обязательным полем, если пользователь не требовал её "
        "и не предоставил данных. Для неподтверждённого факта используй invented_fact или unsafe_claim, "
        "а missing_required_content оставь только для поля/условия, прямо требуемого пользователем. "
        "Для missing_required_content верни requirement_quote — точную выдержку из запроса, "
        "которая прямо и именно требует отсутствующее содержание. Простое совпадение слова из широкой инструкции "
        "не доказывает, что пользователь требовал описать любой связанный бизнес-процесс или условие; "
        "без прямого соответствия не указывай этот тип проблемы. "
        "Не считай утверждением или обещанием текст, который в самом документе запрещает, отрицает, "
        "обозначает как неизвестный либо приводит только как пример/вопрос. "
        + REVIEW_FACT_POLICY + " "
        "Если проблемы есть, верни pass=false и перечисли их. Если проблем нет, pass=true и issues=[]. "
        "Выбирай evidence_id точного сегмента из каталога; программа приложит его исходный текст. "
        "Для сравнения разных полей перечисли их ID в related_evidence_ids, сохранив основным ID место дефекта. "
        "Не возвращай claim или quote; программа формирует цитату из выбранного host-issued ID. "
        "Выдавай только JSON: {\"pass\": bool, \"issues\": "
        "[{\"document\":\"strategy|calendar\", \"type\":\"invented_fact|contradiction|unsafe_claim|missing_required_content|quality|other\", "
        "\"evidence_id\":\"strategy:0001\", \"reason\":\"почему это ошибка\", "
        "\"requirement_quote\":\"точная цитата из запроса для missing_required_content, иначе пустая строка\"}]}.")
    if strategy_only:
        system += (' Проверяется ТОЛЬКО стратегия ДО создания календаря. '
                   'Не требуй календарь или готовые публикации. Проверь соответствие стратегии запросу, '
                   'включая неподтверждённые исходные показатели, проценты роста и каналы связи. '
                   'Неизвестные показатели нельзя выдавать за существующую статистику.')
    if review_scope in ('strategy_only','calendar_only'):
        system += (' Сейчас доступен только документ: '+('СТРАТЕГИЯ' if review_scope=='strategy_only' else 'КАЛЕНДАРЬ')+'. '
                   'Проверь все доступные сегменты этого документа и не делай выводов о содержании отсутствующего документа. '
                   'Отсутствие второго документа отдельно фиксируется контролем комплектности задания.')
    if part:
        system += (' ВНИМАНИЕ: проверяемые документы представлены ЧАСТЯМИ. '
                   'Оценивай факты, доступные в текущем фрагменте, но не объявляй '
                   'поле отсутствующим только потому, что оно может быть в другом фрагменте. '
                   'Не оценивай полноту ВСЕГО задания по одному фрагменту. Часть '+part+'.')
    # Construct one evidence index for the complete reviewer transaction. The
    # prompt, constrained output schema, and host-side citation validation must
    # share this exact immutable mapping; rebuilding the index independently
    # at each boundary made ID parity implicit and difficult to diagnose.
    # Claim inventory and final editorial acceptance use the same host-issued
    # field segments. The old whole-row index fused verified captions with
    # proposed visuals/materials and let the reviewer classify unlike fields
    # as one business claim.
    evidence_catalog,numbered_document=_editor_evidence_index(strategy,calendar,segments=_segments)
    substance=(_substance_contract or document_substance_contract(request,_inventory_segments(strategy,calendar))) \
        .select(evidence_catalog,portfolio=_portfolio) if _segments is not None else None
    user = ('ТРЕБОВАНИЯ ПОЛЬЗОВАТЕЛЯ (НЕ ИСТОЧНИК ЦИТАТ):\n'+request+
            '\n\nСТРУКТУРНЫЙ ИНДЕКС ПОЛЕЙ (НЕ ИСТОЧНИК ФАКТОВ):\n'+structural_digest(calendar)+
            '\n\nСТРАТЕГИЯ — ДОКАЗАТЕЛЬСТВА ПО ID:\n'+numbered_document('strategy')+
            '\n\nКАЛЕНДАРЬ — ДОКАЗАТЕЛЬСТВА ПО ID:\n'+numbered_document('calendar'))
    if _strategy_context:
        if _strategy_context!=strategy:
            raise ValueError('Контекст стратегии не совпадает с исходным документом')
        user+=('\n\nПОЛНАЯ СТРАТЕГИЯ ДЛЯ СОПОСТАВЛЕНИЯ С ТЕКУЩИМИ ПОЛЯМИ КАЛЕНДАРЯ '
               '(не новый источник бизнес-фактов и не каталог ID):\n'+_strategy_context)
    if substance is not None:
        system+=('\nОтдельно выполни КАЖДЫЙ выданный содержательный критерий. '
            'Общий pass без assessments не принимается. Для satisfied объясни конкретную '
            'пользу или решение и выбери исходный evidence_id. Для defect выдай связанное '
            'замечание с criterion_id; исходную цитату добавит программа. '
            'При невозможности оценки укажи unknown: это незавершённая проверка. '
            'Для фактических замечаний вне содержательной матрицы criterion_id=none. '
            'Не удаляй пользу ради безопасного общего совета. Не требуй вымышленных фактов.')
        system+=(' quality_controls — отдельные вымышленные контрольные задания, не источники '
            'бизнеса и не материалы для исправления. Для каждого верни boolean: '
            'выполняет ли candidate указанную task по source_points. Общий призыв уточнить '
            'данные не выполняет объяснение, сравнение или последовательность при достаточных исходных данных.')
        user+='\n\nQUALITY_CRITERIA_JSON:\n'+json.dumps(substance.model_manifest(),ensure_ascii=False)
        if _portfolio:
            system+=(' Сейчас оценивается ТОЛЬКО весь набор по portfolio_distinction и strategy_alignment. '
                     'Для смыслового повтора выбери основной ID caption и related_evidence_ids caption '
                     'другой публикации, объясни одинаковый вывод. Повтор темы или KPI не доказывает дефект.')
    # Use Ollama's grammar-constrained JSON Schema, not an advisory prompt.
    # This is a formatting boundary only: every returned citation is still
    # checked against the immutable original documents below.
    valid_refs = sorted(evidence_catalog)
    # Large calendars can yield well over 100 host-issued evidence IDs in a
    # single review window. Ollama 0.40's grammar compiler rejects or stalls
    # on very large enum schemas with HTTP 400. Keep small enums constrained;
    # for larger catalogs use a bounded string and let parse_verdict enforce
    # exact membership against the same immutable host catalog.
    evidence_id_schema = {'type': 'string', 'maxLength': 48}
    if len(valid_refs) <= 48:
        evidence_id_schema['enum'] = valid_refs
    issue_schema = {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'document': {'type': 'string', 'enum': ['strategy', 'calendar']},
            'type': {'type': 'string', 'enum': [
                'invented_fact', 'contradiction', 'unsafe_claim',
                'missing_required_content', 'quality', 'other']},
            'evidence_id': evidence_id_schema,
            'related_evidence_ids':{'type':'array','maxItems':4,'uniqueItems':True,
                                    'items':evidence_id_schema},
            'reason': {'type': 'string', 'minLength': 9, 'maxLength': 260},
            'requirement_quote': {'type': 'string'}
        },
        'required': ['document', 'type', 'evidence_id', 'reason']
    }
    verdict_schema = {
        'type': 'object', 'additionalProperties': False,
        'properties': {'pass': {'type': 'boolean'},
                       'issues': {'type': 'array', 'items': issue_schema, 'maxItems': 12}},
        'required': ['pass', 'issues']
    }
    if substance is not None:
        issue_schema['properties']['criterion_id']={'type':'string','enum':['none',*substance.checks]}
        issue_schema['required'].append('criterion_id')
        verdict_schema['properties']['assessments']=substance.schema()
        verdict_schema['required'].append('assessments')
        verdict_schema['properties']['quality_controls']=substance.control_schema()
        verdict_schema['required'].append('quality_controls')
        if _portfolio is None:
            issue_schema['properties']['type']['enum']=['quality']
            system+=(' Сейчас выполняется независимое содержательное чтение всего комплекта. '
                'Оценивай только выданные критерии текста и стратегии. Поля visual здесь не проверяются; '
                'новые фактологические обвинения не выноси: факты проверяются отдельным источниковым конвейером.')
    def invoke(messages, *, output_budget=2000):
        last_error=None
        budgets=(output_budget,max(output_budget+1000,output_budget*2))
        for attempt,budget in enumerate(budgets,1):
            payload = {'model': model, 'stream': False, 'think': False, 'format': verdict_schema,
                       'messages': messages,
                       'options': {'temperature': 0, 'num_ctx': 32768 if substance is not None else 16384,
                                   'num_predict': budget}}
            try:
                answer=request_chat(payload,stage='независимая проверка содержания',timeout=240,
                                    attempts=1,report=report,endpoint=endpoint)
                if not isinstance(answer, dict) or not isinstance(answer.get('message'), dict):
                    raise ValueError('Пустой/некорректный ответ редактора')
                content = answer['message'].get('content')
                if isinstance(content,str) and content.strip():
                    return content
                raise ValueError('Редактор не вернул ответ')
            except Exception as exc:
                if isinstance(exc,(OllamaStageError,OSError,TimeoutError)):
                    raise
                last_error=exc
                if attempt<2 and report:
                    report('independent_editor_response_retry',attempt=attempt,
                           reason=type(exc).__name__+': '+str(exc)[:220],next_num_predict=budgets[1])
        raise ValueError(str(last_error or 'Редактор не вернул ответ')) from last_error

    def validated(raw):
        verdict=parse_verdict(raw,strategy=strategy,calendar=calendar,request=request,
            require_claim=True,
            host_evidence_only=True,table_cells=True,_evidence_catalog=evidence_catalog,
            _segments=_segments,_substantive=substance is not None)
        if substance is not None:
            value=json.loads(raw)
            if _portfolio is None and any(issue.get('type')!='quality' for issue in verdict['issues']):
                raise ValueError('Содержательный читатель не имеет источниковых полномочий')
            if substance is not None:
                metadata={row['evidence_id']:row for row in _bound_editor_segments(strategy,calendar,_segments)}
                for issue in verdict['issues']:
                    criterion=substance.checks.get(issue.get('criterion_id'),{}).get('criterion')
                    if criterion=='portfolio_distinction':
                        primary=metadata.get(issue.get('evidence_id'),{})
                        slot=primary.get('row_context',{}).get('slot')
                        if not slot or not any(ref in substance.checks[issue['criterion_id']]['evidence_ids']
                                and metadata.get(ref,{}).get('row_context',{}).get('slot')
                                not in (None,'',slot) for ref in issue.get('related_evidence_ids',[])):
                            raise ValueError('Смысловой повтор должен сравнивать разные публикации')
            verdict['substance_review']=substance.validate(value.get('assessments'),verdict['issues'],value.get('quality_controls'))
        return verdict

    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    output_budget=max(2500,min(10000,len(substance.checks)*320+1000)) if substance else 2000
    first = invoke(messages,output_budget=output_budget)
    try:
        return validated(first)
    except (ValueError, TypeError) as first_error:
        # An invalid citation is a model-output fault, not proof that the
        # actual content is good or bad. Give ONE strictly bounded opportunity
        # to correct the verdict. Still fail closed if the proof is not exact.
        correction = (
            'Твой вердикт отклонён проверкой доказательств: '+str(first_error)[:240]+'\n'
            'ПЕРЕПРОВЕРЬ содержание оригинальных документов. Каждая проблема должна иметь '
            'evidence_id конкретного сегмента в квадратных скобках, текст которого программа извлечёт сама: '
            'strategy:0001 или calendar:0001. Не включай требования пользователя в claim; '
            'выдумывать текст цитаты или id. '
            'Ошибка в доказательстве не превращает содержательную проблему в PASS. '
            'Проверь согласованность assessments и issues: для КАЖДОГО assessments[Q].status=defect '
            'обязательно добавь как минимум один элемент issues с criterion_id=Q и evidence_id '
            'из assessments[Q].evidence_id либо другого разрешённого для Q исходного поля. '
            'Тип замечания quality; reason должен описывать конкретный дефект проверяемого текста. '
            'Если после повторного чтения дефект отсутствует, допускается satisfied только с '
            'обоснованием на реальном исходном фрагменте; нельзя менять defect на satisfied '
            'ради прохождения формата. unknown не считается завершённой проверкой. '
            'Если проблема есть, покажи её на реальной строке по ID. Только JSON.')
        retry = invoke(messages + [
            {'role': 'assistant', 'content': first[:6500]},
            {'role': 'user', 'content': correction}], output_budget=max(4000,output_budget))
        try:
            return validated(retry)
        except (ValueError, TypeError) as retry_error:
            raise ValueError('Редактор не дал достоверный вердикт после повторной проверки; '
                             'первый отказ: '+str(first_error)[:160]+'; '
                             'последний отказ: '+str(retry_error)[:260]) from retry_error



EDITOR_REQUEST_CHARACTER_BUDGET = 6000
EDITOR_CHARACTER_BUDGET = 17000
MAX_EDITOR_WINDOWS = 32


@dataclass(frozen=True)
class EditorialWindow:
    """A selection of original evidence; source text is never re-parsed as a slice."""
    segments: tuple
    reference_strategy: str = ''
    portfolio: bool = False


def validate_editor_request(request):
    """Shared request boundary checked before author work and at each editor."""
    if not isinstance(request,str) or not request.strip():
        raise EditorialContextError('Для редакторской проверки нужен исходный запрос')
    if len(request)>EDITOR_REQUEST_CHARACTER_BUDGET:
        raise EditorialContextError('Исходное задание превышает доступный контекст редактора ('+
            str(EDITOR_REQUEST_CHARACTER_BUDGET)+' символов)')


def _typed_editor_windows(request,strategy,calendar,*,allow_partial=False):
    validate_editor_request(request)
    if (not allow_partial and isinstance(strategy,str) and isinstance(calendar,str)
            and not (strategy.strip() and calendar.strip())):
        raise EditorialContextError('Для итоговой проверки необходимы оба документа')
    if (not isinstance(request,str) or not request.strip() or len(request)>EDITOR_REQUEST_CHARACTER_BUDGET
            or not all(isinstance(body,str) for body in (strategy,calendar))
            or not (strategy.strip() or calendar.strip())
            or not allow_partial and not (strategy.strip() and calendar.strip())):
        raise EditorialContextError('Недопустимое исходное задание или комплект документов редактора')
    segments=_inventory_segments(strategy,calendar)
    available=EDITOR_CHARACTER_BUDGET-len(request)
    def cost(rows):
        # Use the actual compact field rendering; full table headers remain
        # host-side metadata and are not repeated for every sentence.
        result=0;previous=None
        for row in rows:
            context={key:row.get(key) for key in ('document','section_path','field_role','field_type')}
            context['row']=row.get('row_context',{}).get('slot','')
            if context!=previous:
                result+=len(json.dumps(context,ensure_ascii=False))+8;previous=context
            result+=len(row['content'])+len(row['evidence_id'])+4
        return result
    def batches(rows,budget):
        result=[];current=[]
        # Prefer complete rows/sections. Oversized groups fall back to whole
        # sentences with their original field, heading and row metadata.
        groups=[]
        for row in rows:
            key=(row['document'],tuple(row.get('section_path',[])),
                 row.get('row_context',{}).get('slot',''))
            if not groups or groups[-1][0]!=key:groups.append((key,[]))
            groups[-1][1].append(row)
        for _,group in groups:
            units=[group] if cost(group)<=budget else [[row] for row in group]
            for unit in units:
                unit_size=cost(unit)
                if unit_size>budget:
                    raise EditorialContextError('Целое предложение с контекстом поля превышает окно редактора; '
                                     'нужно расширить контекст, текст не обрезан')
                if current and cost(current+unit)>budget:
                    result.append(current);current=[]
                current.extend(unit)
        if current:result.append(current)
        return result
    st=[row for row in segments if row['document']=='strategy']
    ca=[row for row in segments if row['document']=='calendar']
    if not st or not ca:
        windows=[EditorialWindow(tuple(rows)) for rows in batches(segments,available)]
    else:
        st_size=cost(st)
        if st_size<=available-3200:
            windows=[EditorialWindow(tuple(st+rows)) for rows in batches(ca,available-st_size)]
        elif len(strategy)<=available-3200:
            # Review strategy evidence once, then compare every calendar field
            # with the exact full strategy as background. Repeating its verbose
            # evidence metadata in a Cartesian product adds calls, not coverage.
            windows=[EditorialWindow(tuple(rows)) for rows in batches(st,available)]
            windows.extend(EditorialWindow(tuple(rows),strategy)
                           for rows in batches(ca,available-len(strategy)))
        else:
            left=available//2
            windows=[EditorialWindow(tuple(a+b)) for a in batches(st,left)
                     for b in batches(ca,available-left)]
    contract=document_substance_contract(request,segments)
    if any(row['criterion']=='portfolio_distinction' for row in contract.checks.values()):
        summary=[row for row in segments if row['document']=='strategy'
                 or row.get('field_type') in ('caption','topic','goal')]
        if cost(summary)+len(request)>40000:
            raise EditorialContextError('Полный набор для смыслового сравнения превышает контекст; '
                             'неполное сравнение не выдаётся за PASS')
        windows.append(EditorialWindow(tuple(summary),portfolio=True))
    if not windows or len(windows)>MAX_EDITOR_WINDOWS:
        raise EditorialContextError('Для полного покрытия нужно больше допустимого числа редакторских окон')
    if {row['evidence_id'] for window in windows for row in window.segments}!={
            row['evidence_id'] for row in segments}:
        raise EditorialContextError('Неполное покрытие исходных полей редакторскими окнами')
    covered={key for window in windows for key in contract.select(
        (row['evidence_id'] for row in window.segments),portfolio=window.portfolio).checks}
    if covered!=set(contract.checks):
        raise EditorialContextError('Целый содержательный объект не помещается в окно проверки; '
                         'нужен расширенный контекст, неполная проверка не выдаётся за PASS')
    return windows


def _split_document(text: str, max_chars: int) -> list[str]:
    """Partition every input character exactly once (no silent truncation)."""
    if not isinstance(text,str) or not text.strip():
        raise ValueError('Не хватает содержимого для проверки')
    if max_chars < 1000:
        raise ValueError('Недопустимый размер окна редакторской проверки')
    chunks=[]
    current=''
    for line in text.splitlines(keepends=True):
        while line:
            space=max_chars-len(current)
            if not space:
                chunks.append(current);current='';space=max_chars
            piece,line=line[:space],line[space:]
            current+=piece
            if len(current)==max_chars:
                chunks.append(current);current=''
    if current: chunks.append(current)
    if ''.join(chunks)!=text or not chunks:
        raise ValueError('Редакторский документ неполон после сегментации')
    return chunks


def _review_windows(request: str, strategy: str, calendar: str, *, allow_partial: bool = False):
    if not isinstance(request,str) or not request.strip():
        raise ValueError('Для итоговой проверки необходим исходный запрос')
    if not all(isinstance(x,str) for x in (strategy,calendar)):
        raise ValueError('Документы должны быть текстовыми значениями')
    has_strategy=bool(strategy.strip());has_calendar=bool(calendar.strip())
    if not has_strategy and not has_calendar:
        raise ValueError('Нет документов или черновиков для независимой проверки')
    if not allow_partial and not (has_strategy and has_calendar):
        raise ValueError('Для итоговой проверки необходимы оба документа')
    if len(request)>EDITOR_REQUEST_CHARACTER_BUDGET:
        # A very long original brief is a real constraint; hiding its tail
        # would make editorial PASS unreliable.
        raise ValueError('Запрос превышает безопасный размер контекста редактора; требуется расширенное окно')
    if len(request)+len(strategy)+len(calendar)<=EDITOR_CHARACTER_BUDGET:
        return [(strategy,calendar)]
    available = EDITOR_CHARACTER_BUDGET-len(request)
    if available<5000:
        raise ValueError('Невозможно вместить условия задания в окно редактора')
    if not has_calendar:
        return [(chunk,'') for chunk in _split_document(strategy,available)]
    if not has_strategy:
        return [('',chunk) for chunk in _split_document(calendar,available)]
    if len(strategy)<=available-3200:
        calendar_chunks=_split_document(calendar,max(1200,available-len(strategy)))
        windows=[(strategy,chunk) for chunk in calendar_chunks]
    else:
        left=max(1500,int(available*.5))
        right=max(1500,available-left)
        strategy_chunks=_split_document(strategy,left)
        calendar_chunks=_split_document(calendar,right)
        windows=[(st,ca) for st in strategy_chunks for ca in calendar_chunks]
    if len(windows)>MAX_EDITOR_WINDOWS:
        raise ValueError('Задание требует больше 32 редакторских окон; итоговое подтверждение заблокировано')
    return windows



def _nonblocking_editor_false_positive(issue: dict, request: str) -> bool:
    """Dismiss directly refuted facts and narrowly scoped non-business findings."""
    if not isinstance(issue,dict) or issue.get('type') not in ('invented_fact','contradiction'):
        return False
    if disproved_user_stated_fact(issue,request):
        return True
    reason=str(issue.get('reason') or '').casefold()
    quote=str(issue.get('quote') or '').casefold()
    alleged=re.findall(r'[«“"\']([^»”"\']{3,180})[»”"\']',reason)
    claim_text=' '.join(alleged).casefold() if alleged else quote
    brief=str(request or '').casefold()
    # A reader-facing question about their own situation or a detail to
    # confirm is not a factual statement about the business. Keep questions
    # that attribute a promise or an actual offer to the business blocking.
    reader_question=quote.strip().endswith('?')
    business_attribution=bool(re.search(
        r'\b(?:мы|наш\w*|у\s+нас|в\s+(?:нашей|нашем)\s+'
        r'(?:студи\w*|клуб\w*|атель\w*|мастерск\w*|компани\w*)|'
        r'студи\w*|атель\w*|мастерск\w*|клуб\w*|компани\w*)\b',quote,re.I))
    if reader_question and not business_attribution:
        return True
    inquiry_goal=bool(re.search(r'вопрос|обращени|запрос|консультац',brief))
    neutral_cta=bool(re.search(r'напиш\w*|зада\w*\s+вопрос|спрос\w*',claim_text+' '+reason))
    risky_offer=bool(re.search(r'бесплат|гарант|обеща\w*\s+(?:результат|ответ)|точн\w+\s+ответ|цен[аы]|скидк|срок',claim_text))
    if inquiry_goal and neutral_cta and not risky_offer:
        return True
    advice_flag=bool(re.search(r'неподтвержд[её]нн\w*\s+совет|совет\w*\s+не\s+подтвержд',reason))
    imperative=bool(re.search(r'\b(?:не\s+игнорируй\w*|проверь\w*|сравни\w*|задай\w*\s+вопрос|сохрани\w*)\b',claim_text))
    company_claim=bool(re.search(r'\b(?:мы|у\s+нас|наш\w*|компани\w*|студи\w*|клуб\w*|мастерск\w*|предоставля\w*|гарантиру\w*)\b',claim_text))
    if advice_flag and imperative and not company_claim:
        return True
    staged=bool(re.search(r'постановочн\w*|иллюстративн\w*|сцен\w*\s+для\s+съ[её]мк',claim_text+' '+quote))
    uncertainty=bool(re.search(r'необходимо\s+(?:проверить|подтвердить|уточнить)|требуется\s+(?:проверка|подтверждение|уточнение)|неизвестн\w*|не\s+подтвержден\w*',claim_text+' '+quote))
    return staged and uncertainty


def _negated_claim_is_not_an_assertion(issue: dict) -> bool:
    """Use surrounding host-cited document text to distinguish bans from claims."""
    if not isinstance(issue, dict) or issue.get('type') not in ('invented_fact','unsafe_claim'):
        return False
    segment = str(issue.get('evidence_segment') or '')
    quote = str(issue.get('quote') or '')
    if not segment or not quote:
        return False
    grounded = _ground_quote(segment, quote)
    if grounded is None:
        return False
    from factual_claim_audit import _is_explicitly_prohibited
    folded = segment.casefold()
    start = folded.find(grounded.casefold())
    return start >= 0 and _is_explicitly_prohibited(segment, start, start + len(grounded))


def _nonblocking_scope_issue(issue: dict, request: str, calendar: str) -> bool:
    """Keep the acceptance boundary on business claims, not creative proposals or source notes."""
    if not isinstance(issue, dict) or issue.get('type') not in ('invented_fact', 'contradiction'):
        return False
    quote = str(issue.get('quote') or '').strip()
    quote_folded = quote.casefold().replace('ё', 'е')
    reason = str(issue.get('reason') or '').casefold()
    # A complete explicit negative knowledge statement is meta-content, not a
    # claim that the business provides or lacks a service. Require a negated
    # epistemic predicate; a proper noun such as «ул. Неизвестная, 999» does
    # not qualify merely because it contains the word stem «неизвестн».
    knowledge_boundary = bool(re.search(
        r'\bне\s+(?:подтвержден\w*|указан\w*|сообщен\w*|предоставлен\w*|определен\w*)\b',
        quote_folded, re.I))
    business_subject = bool(re.search(
        r'\b(?:мы|наш\w*|у\s+нас|компани\w*|студи\w*|мастерск\w*|клуб\w*)\b',
        quote_folded, re.I))
    if knowledge_boundary and not business_subject:
        return True
    if issue.get('document') != 'calendar':
        return False
    if not re.search(r'визуал|сценари|кадр|слайд|иллюстрац|постановоч', reason + ' ' + quote, re.I):
        return False
    rows = publication_rows(calendar)
    for row in rows:
        visual = _cell(row, ('визуал', 'сценар')) or ''
        if not visual or not (quote in row['raw'] or row['raw'] in quote or quote in visual or visual in quote):
            continue
        # The storyboard is an instruction for future production. A business
        # assertion in its own right remains reviewable and blocking.
        if re.search(r'\b(?:мы|наш\w*|у\s+нас|компани\w*\s+(?:имеет|использует|делает)|в\s+(?:студии|мастерской)\s+есть)\b', quote, re.I):
            return False
        return True
    return False

def _review_artifact_form(request,strategy,calendar,*,model,evidence_context=None,report=None,endpoint=ENDPOINT,
                          review_stage='complete_bundle',_segments=None):
    """One explicit request/format judgement over the same typed artifact fields.

    This is a model judgement, not deterministic language identification. It
    cannot establish facts or waive a completed factual finding.
    """
    from generic_text_review import review_dimension
    segments=_bound_editor_segments(strategy,calendar,_segments)
    fields={row['evidence_id']:row['content'] for row in segments}
    if not fields:
        raise ValueError('Нет полей для проверки формата документов')
    roles={row['evidence_id']:row.get('field_type','document_narrative') for row in segments}
    context={'evidence_context':evidence_context} if evidence_context is not None else {}
    scope={'stage':review_stage,'available_artifacts':[name for name,body in
        (('strategy',strategy),('calendar',calendar)) if body.strip()],
        'pending_artifacts':['calendar'] if review_stage=='strategy_preflight' else [],
        'artifact_kinds':{row['evidence_id']:row['document'] for row in segments},
        'partial_fields':_segments is not None}
    result=review_dimension(request,'\n\n'.join(fields.values()),context,'format_and_tone',model,
        report=report,artifact_fields=fields,artifact_roles=roles,endpoint=endpoint,review_scope=scope)
    by_field={row['evidence_id']:row for row in segments}
    issues=[]
    for item in result['issues']:
        row=by_field[item['field']]
        issues.append({**item,'document':row['document'],'type':'quality',
            'artifact_field_id':item['field'],'field':row.get('field_type','document_narrative'),
            'section_path':row.get('section_path',[]),'field_role':row.get('field_role',''),
            'field_type':row.get('field_type','document_narrative'),
            'row_context':row.get('row_context',{}),'evidence_segment':row['content']})
        if isinstance(item.get('review_scope'),dict):
            issues[-1]['review_scope']={**item['review_scope'],
                'artifact_kinds':{item['field']:row['document']}}
    if report:report('artifact_format_review_completed',model=model,fields=len(fields),
        passed=not issues,issue_count=len(issues))
    return issues


def call_strategy_editor(request, strategy, *, endpoint=ENDPOINT, evidence_context=None,
                         claim_adjudicator=None, require_inventory=False, report=None,
                         model=MODEL):
    if not isinstance(request, str) or not request.strip() or len(request)>EDITOR_REQUEST_CHARACTER_BUDGET:
        raise ValueError('Недопустимый запрос для проверки стратегии')
    native=_call_editor_once is _DEFAULT_CALL_EDITOR_ONCE
    windows=(_typed_editor_windows(request,strategy,'',allow_partial=True) if native else
             _split_document(strategy, EDITOR_CHARACTER_BUDGET-len(request)))
    if len(windows)>MAX_EDITOR_WINDOWS:
        raise ValueError('Слишком много окон проверки стратегии')
    issues=[]
    fact_disputes=[]
    for i, chunk in enumerate(windows):
        bound={'_segments':chunk.segments} if isinstance(chunk,EditorialWindow) else {}
        body=strategy if bound else chunk
        try:
            verdict=_call_editor_once(request,body,'',endpoint=endpoint,strategy_only=True,
                                      evidence_context=evidence_context, report=report,
                                      model=model,
                                      part=f'{i+1}/{len(windows)}' if len(windows)>1 else '',**bound)
            if (not isinstance(verdict,dict) or type(verdict.get('pass')) is not bool
                    or not isinstance(verdict.get('issues'),list)
                    or any(not isinstance(item,dict) for item in verdict['issues'])):
                raise ValueError('Редактор стратегии вернул неполный вердикт')
        except Exception as exc:
            raise IndependentReviewUnavailable('Проверка стратегии не вернула проверяемый вердикт: '
                +type(exc).__name__+': '+str(exc)[:320],issues) from exc
        for issue in verdict['issues']:
            # Live findings retain their exact source/criterion bindings for
            # independent AI applicability and remedy-scope review. Lexical
            # compatibility filters apply only to injected legacy adapters.
            from deliverable_contract import is_deferred_strategy_publication_requirement
            if not native and (_nonblocking_editor_false_positive(issue,request) or
                    _nonblocking_scope_issue(issue,request,'') or
                    is_deferred_strategy_publication_requirement(issue,request)):
                continue
            if issue.get('type') in ('invented_fact', 'unsafe_claim'):
                fact_disputes.append(issue)
            else:
                issues.append(issue)
    if require_inventory:
        try:
            for chunk in windows:
                bound={'_segments':chunk.segments} if isinstance(chunk,EditorialWindow) else {}
                issues.extend(_review_artifact_form(request,strategy if bound else chunk,'',model=model,
                    evidence_context=evidence_context,report=report,endpoint=endpoint,
                    review_stage='strategy_preflight',**bound))
            fact_disputes.extend(_inventory_business_assertions(
                request,strategy,'',endpoint=endpoint,model=model,report=report))
        except Exception as exc:
            raise IndependentReviewUnavailable(str(exc),issues) from exc
    if fact_disputes:
        # A segment can be reported both as a reviewer finding and as a
        # complete-inventory item. Resolve that assertion once, preserving
        # its exact document location.
        unique=[];seen=set()
        for item in fact_disputes:
            key=(item.get('document'),item.get('type'),item.get('quote'))
            if key not in seen:
                unique.append(item);seen.add(key)
        fact_disputes=unique
        from claim_support import adjudicate_factual_findings
        adjudicator=claim_adjudicator or adjudicate_factual_findings
        kwargs={'endpoint':endpoint,'evidence_context':evidence_context,'report':report}
        if claim_adjudicator is None: kwargs['model']=model
        try:
            resolution=adjudicator(request,fact_disputes,**kwargs)
            if (not isinstance(resolution,dict) or not isinstance(resolution.get('blocking'),list)
                    or not isinstance(resolution.get('supported'),list)
                    or any(not isinstance(item,dict) for item in resolution['blocking'])):
                raise ValueError('Источник-арбитр вернул неполный результат')
        except Exception as exc:
            pending=[{**item,'adjudication':'unavailable'} for item in fact_disputes]
            raise IndependentReviewUnavailable('Сверка источников стратегии не завершена: '
                +type(exc).__name__+': '+str(exc)[:320],issues+pending) from exc
        unavailable=[item for item in resolution.get('blocking',[]) if isinstance(item,dict)
                     and item.get('adjudication') in INCOMPLETE_ADJUDICATIONS]
        if unavailable:
            detail='; '.join(str(item.get('reason') or item.get('adjudication'))[:240]
                              for item in unavailable[:3])
            raise IndependentReviewUnavailable('Источник-арбитр не завершил сверку: '+detail,
                                               issues+resolution['blocking'])
        issues.extend(resolution['blocking'])
    return {'pass':not issues,'issues':issues}


def call_editor(request: str, strategy: str, calendar: str, *, endpoint: str = ENDPOINT,
                evidence_context=None, claim_adjudicator=None, editor_call=None,
                require_inventory: bool = False, report=None,
                allow_partial: bool = False, model: str = MODEL) -> dict[str, Any]:
    """Review every document segment, not an implicitly truncated context.

    Each window is independently evidence-grounded. Any inaccessible,
    malformed or contradictory opinion blocks acceptance of the whole set.
    """
    native=editor_call is None and _call_editor_once is _DEFAULT_CALL_EDITOR_ONCE
    windows=(_typed_editor_windows(request,strategy,calendar,allow_partial=allow_partial)
             if native else _review_windows(request,strategy,calendar,allow_partial=allow_partial))
    review_scope=('strategy_only' if strategy.strip() and not calendar.strip() else
                  'calendar_only' if calendar.strip() and not strategy.strip() else 'complete')
    if require_inventory and editor_call is not None:
        raise ValueError('Инъецированный редактор не поддерживает полный реестр утверждений')
    from factual_claim_audit import scan_unsupported_business_claims
    # Native production uses complete AI assertion inventory and source
    # adjudication below. A number/address pattern cannot establish whether a
    # passage asserts a company fact, proposes a plan or quotes an example.
    # Historical injected adapters retain their compatibility behavior.
    hard_findings=[] if native else scan_unsupported_business_claims(
        request,strategy,calendar,evidence_context,include_semantic_facts=False)
    candidate_findings=[] if native else scan_unsupported_business_claims(
        request,strategy,calendar,evidence_context,include_semantic_facts=True)
    hard_keys={(x.get('document'),x.get('type'),x.get('quote'),x.get('reason'))
               for x in hard_findings if isinstance(x,dict)}
    fact_disputes=[x for x in candidate_findings if isinstance(x,dict) and
                   (x.get('document'),x.get('type'),x.get('quote'),x.get('reason')) not in hard_keys]
    def fact_key(item):
        text=re.sub(r'\s+',' ',str(item.get('quote') or item.get('claim') or '')).strip()
        text=text.casefold().replace('ё','е').strip(' .,!?:;—-')
        return item.get('document'),item.get('type'),text
    dispute_keys={fact_key(x) for x in fact_disputes}
    found=list(hard_findings)
    reviewer=editor_call or _call_editor_once
    disproved=[]
    review_unavailable=False
    transport_unavailable=False
    control_unavailable=False
    substance_contract=document_substance_contract(request,_inventory_segments(strategy,calendar)) if native else None
    substance_records={}
    completed_control_calls=0
    for i,window in enumerate(windows):
        bound={'_segments':window.segments} if isinstance(window,EditorialWindow) else {}
        st,ca=(strategy,calendar) if bound else window
        review_kwargs={'endpoint':endpoint,'evidence_context':evidence_context,
                       'part':f'{i+1} из {len(windows)}' if len(windows)>1 else '',**bound}
        if isinstance(window,EditorialWindow) and window.reference_strategy:
            review_kwargs['_strategy_context']=window.reference_strategy
        if isinstance(window,EditorialWindow) and window.portfolio:
            review_kwargs['_portfolio']=True
        if report and bound:
            report('editor_window_bound',window=i+1,windows=len(windows),
                   evidence_ids=[row['evidence_id'] for row in window.segments],
                   field_types=sorted({row['field_type'] for row in window.segments}))
        if editor_call is None:
            review_kwargs['model']=model
        if report is not None and editor_call is None:
            review_kwargs['report']=report
        if editor_call is None and review_scope!='complete':
            review_kwargs['review_scope']=review_scope
        try:
            opinion=reviewer(request,st,ca,**review_kwargs)
            if not isinstance(opinion,dict) or type(opinion.get('pass')) is not bool or not isinstance(opinion.get('issues'),list):
                raise ValueError('Редактор вернул неполный вердикт')
            if native:
                proof=opinion.get('substance_review')
                if not isinstance(proof,dict) or proof.get('signature')!=substance_contract.signature:
                    raise ValueError('Нет содержательного подтверждения текущих исходных документов')
                completed_control_calls+=1
                for key,record in proof['assessments'].items():
                    if key not in substance_records or record['status']!='satisfied':substance_records[key]=record
        except Exception as exc:
            review_unavailable=True
            found.append({'document':'review','type':'review_unavailable','quote':'',
                'reason':'Независимая проверка документов не завершена: '
                         +type(exc).__name__+': '+str(exc)[:320],
                'adjudication':'unavailable'})
            if report:
                report('independent_editor_unavailable',window=i+1,windows=len(windows),
                       error_type=type(exc).__name__,reason=str(exc)[:320])
            if isinstance(exc,(OllamaStageError,OSError,TimeoutError)):
                transport_unavailable=True
                if report:report('editor_windows_deferred_after_transport_failure',
                    model=model,remaining=len(windows)-i-1)
                break
            if isinstance(exc,SubstanceControlFailure):
                control_unavailable=True
                if report:report('editor_windows_deferred_after_control_failure',model=model,
                    remaining=len(windows)-i-1)
                break
            continue
        if not opinion['pass']:
            for issue in opinion['issues']:
                if not native and (_nonblocking_editor_false_positive(issue,request) or
                    _nonblocking_scope_issue(issue,request,calendar) or
                    _negated_claim_is_not_an_assertion(issue) or
                    disproved_missing_field_claim(issue,calendar) or
                    disproved_document_role_claim(issue,request,calendar) or
                    disproved_unrequested_contact_link(issue,request) or
                    disproved_calendar_format_strategy_claim(issue,calendar)):
                    disproved.append(issue)
                    continue
                # An existing Markdown text/CTA/visual cell directly disproves
                # "there is no such field". Do NOT discard general quality or
                # factual concerns: those still block acceptance.
                if issue.get('type') in ('invented_fact', 'unsafe_claim'):
                    key=fact_key(issue)
                    if key not in dispute_keys:
                        fact_disputes.append(issue);dispute_keys.add(key)
                else:
                    key=(issue['document'],issue['type'],issue['quote'],issue['reason'])
                    if key not in {(x['document'],x['type'],x['quote'],x['reason']) for x in found}:
                        found.append(issue)
    if require_inventory and not (transport_unavailable or control_unavailable):
        for window in windows:
            if isinstance(window,EditorialWindow) and window.portfolio:continue
            bound={'_segments':window.segments} if isinstance(window,EditorialWindow) else {}
            st,ca=(strategy,calendar) if bound else window
            try:
                found.extend(_review_artifact_form(request,st,ca,model=model,
                    evidence_context=evidence_context,report=report,endpoint=endpoint,**bound))
            except Exception as exc:
                review_unavailable=True
                found.append({'document':'review','type':'review_unavailable','quote':'',
                    'reason':'Проверка языка и формата не завершена: '+str(exc)[:320],
                    'adjudication':'unavailable'})
        try:
            fact_disputes.extend(_inventory_business_assertions(
                request,strategy,calendar,endpoint=endpoint,model=model,report=report))
        except Exception as exc:
            review_unavailable=True
            found.append({'document':'review','type':'review_unavailable','quote':'',
                'reason':str(exc),'adjudication':'unavailable'})
    if fact_disputes:
        unique=[];seen=set()
        for item in fact_disputes:
            key=fact_key(item)
            if key not in seen:
                unique.append(item);seen.add(key)
        fact_disputes=unique
        from claim_support import adjudicate_factual_findings
        # The model reviewer identifies candidate claims; the canonical source
        # ledger resolves whether each claim was already established by the
        # user or a verified project source. Failure to resolve stays blocking.
        adjudicator=claim_adjudicator or adjudicate_factual_findings
        kwargs={'endpoint':endpoint,'evidence_context':evidence_context,'report':report}
        if claim_adjudicator is None: kwargs['model']=model
        try:
            if transport_unavailable or control_unavailable:
                raise RuntimeError('Сверка источников отложена после транспортного отказа модели')
            resolution=adjudicator(request,fact_disputes,**kwargs)
            if not isinstance(resolution,dict) or not isinstance(resolution.get('blocking'),list) or not isinstance(resolution.get('supported'),list):
                raise ValueError('Источник-арбитр вернул неполный результат')
        except Exception as exc:
            review_unavailable=True
            resolution={'blocking':[{**item,'adjudication':'unavailable',
                'reason':str(item.get('reason') or '')+' Сверка источника не завершена: '
                         +type(exc).__name__+': '+str(exc)[:260]} for item in fact_disputes],
                        'supported':[]}
            if report:
                report('source_adjudication_incomplete',blocking=len(fact_disputes),
                       error_type=type(exc).__name__,reason=str(exc)[:320])
        review_unavailable=review_unavailable or any(isinstance(issue,dict) and
            issue.get('adjudication') in INCOMPLETE_ADJUDICATIONS
            for issue in resolution.get('blocking',[]))
        if review_unavailable and report:
            report('source_adjudication_incomplete',blocking=len(resolution.get('blocking',[])),
                   reasons=[str(issue.get('reason') or '')[:260] for issue in resolution.get('blocking',[])
                            if isinstance(issue,dict) and issue.get('adjudication') in INCOMPLETE_ADJUDICATIONS][:4])
        existing={(x.get('document'),x.get('type'),x.get('quote')) for x in found}
        for issue in resolution['blocking']:
            key=(issue.get('document'),issue.get('type'),issue.get('quote'))
            if key not in existing:
                found.append(issue);existing.add(key)
        disproved.extend(resolution['supported'])
    substance_proof={}
    if native:
        complete=set(substance_records)==set(substance_contract.checks)
        if not complete:
            review_unavailable=True
            if not any(row.get('type')=='review_unavailable' for row in found):
                found.append({'document':'review','type':'review_unavailable','quote':'',
                    'reason':'Не завершены все содержательные критерии','adjudication':'unavailable'})
        if complete and not found and any(row['status']!='satisfied' for row in substance_records.values()):
            review_unavailable=True
            found.append({'document':'review','type':'review_unavailable','quote':'',
                'reason':'Содержательный отказ не разрешён полным согласованным вердиктом',
                'adjudication':'unavailable'})
        from artifact_review import review_source_signature
        substance_proof={'substance_review':{'policy':SUBSTANCE_POLICY,'signature':substance_contract.signature,
            'source_signature':review_source_signature(request,{'strategy':strategy,'calendar':calendar}),
            'expected_checks':list(substance_contract.checks),'assessments':substance_records,
            'controls':{'passed':complete and not control_unavailable,'checked_calls':completed_control_calls},
            'complete':complete,'pass':complete and all(row['status']=='satisfied' for row in substance_records.values())}}
    return ValidatedEditorialVerdict({'pass':not bool(found),'issues':found,
            'disproved_absence_claims':disproved,
            'review_state':'unavailable' if review_unavailable else 'complete',
            'review_transport_unavailable':transport_unavailable,
            'review_control_unavailable':control_unavailable,**substance_proof})


def substance_reader_contract(request,strategy,calendar):
    """Shared deterministic inputs for the reader and the storage boundary."""
    segments=_inventory_segments(strategy,calendar)
    has_caption=any(row.get('field_type')=='caption' for row in segments if row['document']=='calendar')
    summary=[row for row in segments if row['document']=='strategy'
             or row.get('field_type') in ('topic','goal','caption') or not has_caption]
    contract=document_substance_contract(request,summary)
    wanted={'topic_delivery','audience_fit','execution_budget',
            'measurement_decisions','portfolio_distinction','strategy_alignment'}
    contract=SubstanceReviewContract(contract.signature,contract.catalog,
        {key:row for key,row in contract.checks.items() if row['criterion'] in wanted},contract.controls)
    return summary,contract


def call_substance_reader(request,strategy,calendar,*,model,evidence_context=None,report=None):
    """A different model reads every caption and strategic decision together."""
    summary,contract=substance_reader_contract(request,strategy,calendar)
    if sum(len(row['content']) for row in summary)+len(request)>35000:
        raise EditorialContextError('Полный комплект превышает контекст независимого содержательного чтения')
    try:
        raw=_call_editor_once(request,strategy,calendar,model=model,evidence_context=evidence_context,
            report=report,_segments=summary,_substance_contract=contract,_portfolio=None,
            part='независимое содержательное чтение полного комплекта')
        from artifact_review import review_source_signature
        proof={**raw.get('substance_review',{}),
            'source_signature':review_source_signature(request,{'strategy':strategy,'calendar':calendar})}
        return ValidatedEditorialVerdict({**raw,'substance_review':proof,'review_state':'complete'})
    except Exception as exc:
        return ValidatedEditorialVerdict({'pass':False,'review_state':'unavailable',
            'review_control_unavailable':isinstance(exc,SubstanceControlFailure),
            'review_transport_unavailable':isinstance(exc,(OllamaStageError,OSError,TimeoutError)),
            'issues':[{'document':'review','type':'review_unavailable','quote':'',
                'adjudication':'unavailable','reason':'Содержательное чтение не завершено: '+str(exc)[:500]}]})


_DEFAULT_CALL_EDITOR = call_editor
_DEFAULT_CALL_EDITOR_ONCE = _call_editor_once


def _ready_for_independent_review(data):
    """Route completed upstream candidates to audit; this cannot grant PASS."""
    if not isinstance(data,dict) or not isinstance(data.get('response'),dict):return False
    response=data['response']
    profile=data.get('task_profile')
    workflow=data.get('workflow_type') or (profile.get('workflow_type') if isinstance(profile,dict) else None)
    if workflow=='standalone_post':
        quality=response.get('quality_review')
        return (response.get('action')=='write' and isinstance(response.get('answer'),str)
            and bool(response['answer'].strip()) and isinstance(quality,dict)
            and quality.get('pass') is True and not quality.get('review_errors') and not quality.get('issues')
            and quality.get('review_state','complete')=='complete')
    if workflow not in (None,'compound_strategy_calendar'):return False
    docs=response.get('documents');delivery=response.get('content_delivery')
    if not isinstance(docs,list) or not isinstance(delivery,dict):return False
    required=[d for d in docs if isinstance(d,dict) and d.get('kind') in ('strategy','calendar')]
    editor=delivery.get('independent_editor')
    return (len(required)==2 and {d['kind'] for d in required}=={'strategy','calendar'}
        and all(d.get('valid') is True and isinstance(d.get('content'),str) and d['content'].strip() for d in required)
        and not delivery.get('unfulfilled') and isinstance(editor,dict) and editor.get('status')=='PASS')


def _audit_saved_standalone(data, answer):
    """Use the same creator exclusions and content contract for real/control text."""
    from generic_text_review import review_text,acceptance_model_pool,list_acceptance_models,_model_family
    from text_task_contract import bind_creative_permission
    response=data['response']
    initial_author=data.get('author_model','qwen3:8b')
    revision_author=(response.get('quality_review') or {}).get('revision_author_model')
    audit_model=revision_author or initial_author
    audit_context={}
    permission=bind_creative_permission(data['request'],response.get('content_contract'))
    if permission:
        audit_context['proposed_content_contract']=permission
    if revision_author and _model_family(revision_author)!=_model_family(initial_author):
        creators={_model_family(initial_author),_model_family(revision_author)}
        pool=[m for m in list_acceptance_models() if _model_family(m) not in creators]
        with acceptance_model_pool(pool):
            return review_text(data['request'],answer,audit_context,model=audit_model)
    return review_text(data['request'],answer,audit_context,model=audit_model)


def _audit_control_model(candidates, *, live):
    """Reuse a qualified independent audit voice, never the module default."""
    if not live:
        return 'test-adapter'
    from generic_text_review import _model_family
    from review_qualification import cached_qualification, ReviewerQualificationUnavailable
    for data,model in candidates:
        if not isinstance(model,str) or not model.strip():
            continue
        response=data.get('response') or {}
        creators=[data.get('author_model')]
        creators.append((response.get('generation_roles') or {}).get('revision_author'))
        creators.append((response.get('quality_review') or {}).get('revision_author_model'))
        if _model_family(model) in {_model_family(m) for m in creators if isinstance(m,str) and m.strip()}:
            continue
        if (cached_qualification(model) or {}).get('pass') is True:
            return model
    raise ReviewerQualificationUnavailable('Нет квалифицированного независимого голоса для контрольных материалов аудита')


def review_saved_results(folder: Path, *, editor_call=None, claim_adjudicator=None,
                         ready_only=False, checkpoint_path=None) -> dict[str, Any]:
    results = {'status': 'FAIL', 'cases': [], 'negative_control': {'status': 'NOT_TESTED'},
               'operational_claim_control': {'status': 'NOT_TESTED'},
               'explicit_fact_control': {'status': 'NOT_TESTED'}}
    results.update(scope='completed_candidates_only' if ready_only else 'all_saved_drafts',
                   review_state='running',finished=False,active_stage='initializing',active_case=None)
    def checkpoint(stage,case=None):
        results.update(active_stage=stage,active_case=case)
        if checkpoint_path is not None:
            from durable_json import atomic_write_json
            atomic_write_json(checkpoint_path,results)
    checkpoint('initializing')
    complete_samples=[]
    review_samples=[]
    standalone_samples=[]
    control_candidates=[]
    live_controls=(editor_call is None and call_editor is _DEFAULT_CALL_EDITOR
                   and _call_editor_once is _DEFAULT_CALL_EDITOR_ONCE)
    eligible_count=0
    for path in sorted(folder.glob('*_synthetic_documents.json')):
        case = path.stem.replace('_synthetic_documents', '')
        item = {'case': case, 'status': 'RUNNING'}
        results['cases'].append(item)
        checkpoint('case_started',case)
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if ready_only and not _ready_for_independent_review(data):
                item.update(status='NOT_RUN',reason='upstream_candidate_incomplete_or_blocked',
                            upstream_status='BLOCKED')
                checkpoint('case_skipped_upstream',case)
                continue
            eligible_count+=1
            workflow_type=data.get('workflow_type') or data.get('task_profile',{}).get('workflow_type')
            if workflow_type=='standalone_post':
                response=data.get('response',{})
                answer=str(response.get('answer') or '').strip() if isinstance(response,dict) else ''
                if not answer or not isinstance(response,dict) or response.get('action')!='write':
                    raise ValueError('Одиночное задание не вернуло готовый текст для независимой проверки')
                standalone_samples.append((data,answer))
                verdict=_audit_saved_standalone(data,answer)
                voices=verdict.get('reviewer_models')
                if isinstance(voices,dict):
                    control_candidates.append((data,voices.get('factual_grounding')))
                item.update(workflow_type=workflow_type,independent_audit_models=verdict.get('reviewer_models',{}),
                            verdict=verdict,status='PASS' if verdict.get('pass') is True else 'FAIL')
                checkpoint('case_completed',case)
                continue
            docs = data['response'].get('documents', [])
            if not isinstance(docs, list):
                raise ValueError('Поле documents должно быть списком')
            contents = {doc['kind']: doc['content'] for doc in docs if isinstance(doc,dict)
                        and isinstance(doc.get('kind'),str) and isinstance(doc.get('content'),str)}
            present = {kind for kind in ('strategy','calendar') if contents.get(kind)}
            if not present:
                raise ValueError('Нет готовых документов или черновиков для независимой проверки')
            normalized={'strategy':contents.get('strategy',''),
                        'calendar':contents.get('calendar','')}
            using_live_editor=(editor_call is None and call_editor is _DEFAULT_CALL_EDITOR
                               and _call_editor_once is _DEFAULT_CALL_EDITOR_ONCE)
            if using_live_editor:
                from generic_text_review import select_independent_model
                audit_model=select_independent_model(
                    data.get('author_model','qwen3:8b'),
                    exclude_models=[m for m in (data.get('review_model'),
                        (data.get('response',{}).get('generation_roles') or {}).get('revision_author')) if m])
            else:
                # Injected review adapters are deterministic unit-test doubles;
                # they must not query a live model server.
                audit_model='test-adapter'
            item['independent_audit_model']=audit_model
            control_candidates.append((data,audit_model))
            review_samples.append((data,normalized,audit_model))
            invalid = {doc['kind'] for doc in docs if isinstance(doc, dict)
                       and doc.get('kind') in ('strategy', 'calendar') and doc.get('valid') is False}
            complete = present == {'strategy','calendar'} and not invalid
            if complete:
                complete_samples.append((data,normalized))
            item['document_coverage']={'complete':complete,
                'present':sorted(present),'missing':sorted({'strategy','calendar'}-present),
                'invalid':sorted(invalid)}
            item['verdict'] = call_editor(data['request'], normalized['strategy'], normalized['calendar'],
                                          editor_call=editor_call,
                                          claim_adjudicator=claim_adjudicator,
                                          require_inventory=editor_call is None,
                                          allow_partial=not complete,model=audit_model)
            item['status'] = 'PASS' if complete and item['verdict']['pass'] else 'FAIL'
        except Exception as exc:
            item['status']='FAIL'
            item['error'] = type(exc).__name__+': '+str(exc)[:500]
        checkpoint('case_completed',case)
    if ready_only and eligible_count==0:
        results.update(status='BLOCKED',review_state='blocked_upstream',finished=True,
                       reason='Нет завершённого кандидата для независимой приёмки; новые модельные обращения не выполнялись')
        for key in ('negative_control','operational_claim_control','explicit_fact_control'):
            results[key].update(reason='no_completed_candidate')
        checkpoint('complete')
        return results
    try:
        control_model=_audit_control_model(control_candidates,live=live_controls)
        results['control_review_model']=control_model
    except Exception as exc:
        for key in ('negative_control','operational_claim_control','explicit_fact_control'):
            results[key]={'status':'FAIL','error':type(exc).__name__+': '+str(exc)[:500]}
        results.update(finished=True,review_state='complete')
        checkpoint('complete')
        return results
    # Negative control proves that the independent editor is capable of
    # detecting even an obvious forbidden assertion, rather than blanket PASS.
    if review_samples or standalone_samples:
        checkpoint('negative_control')
        injected=('\nПроверенный факт о компании: всем клиентам гарантируем результат за 1 день, '
                  'адрес ул. Неизвестная 999, цена 50000 рублей.\n')
        control_workflow='compound_strategy_calendar' if review_samples else 'standalone_post'
        try:
            if review_samples:
                data,docs,audit_model=review_samples[0]
                altered=docs['strategy']+injected
                verdict=call_editor(data['request'],altered,docs['calendar'],
                                    editor_call=editor_call,claim_adjudicator=claim_adjudicator,
                                    require_inventory=editor_call is None,
                                    allow_partial=not bool(docs['calendar'].strip()),
                                    model=audit_model)
            else:
                data,answer=standalone_samples[0]
                verdict=_audit_saved_standalone(data,answer+injected)
            issues=verdict['issues']
            caught=(verdict.get('pass') is False
                    and verdict.get('review_state','complete')=='complete'
                    and not verdict.get('review_errors')
                    and any(isinstance(i,dict)
                            and (i.get('type') in ('invented_fact','unsafe_claim','contradiction')
                                 or (i.get('check')=='factual_grounding' and i.get('adjudication')=='unsupported'))
                            and i.get('adjudication') not in INCOMPLETE_ADJUDICATIONS
                            and ('Неизвестная 999' in i.get('quote','') or '50000' in i.get('quote','')
                                 or 'гарантируем результат' in i.get('quote','')) for i in issues))
            results['negative_control']={'status': 'PASS' if caught else 'FAIL', 'detected':caught,
                                         'workflow_type':control_workflow,'issues':issues[:5]}
        except Exception as exc:
            results['negative_control']={'status':'FAIL','workflow_type':control_workflow,
                                         'error':type(exc).__name__+': '+str(exc)[:500]}
    # A4's real GUI draft contained specific alteration procedures and outcome
    # claims that passed the old editor. Keep a dedicated negative control so
    # a blanket-PASS reviewer cannot be mistaken for independent validation.
    op_request=('Составь один пост для мастерской по ремонту одежды. '
                'Процессы, способы работы, сроки и результаты проекта не указаны.')
    op_strategy='Цель публикации — начать разговор с потенциальными клиентами.'
    op_calendar=('# Календарь\n\n'
                 '## Публикация 1\n'
                 'В нашей мастерской снимаем мерки и вручную подгоняем каждое платье. '
                 'Добиваемся идеальной посадки за один день.')
    checkpoint('operational_claim_control')
    try:
        verdict=call_editor(op_request,op_strategy,op_calendar,
                            editor_call=editor_call,claim_adjudicator=claim_adjudicator,
                            require_inventory=editor_call is None,model=control_model)
        phrase='снимаем мерки'
        caught=not verdict['pass'] and any(
            i.get('type') in ('invented_fact','unsafe_claim','contradiction')
            and i.get('adjudication') not in INCOMPLETE_ADJUDICATIONS
            and phrase in str(i.get('quote','')).casefold()
            and re.search(r'не\s+(?:указан\w*|подтвержд\w*|сообщен\w*)|неизвестн\w*|не\s+предостав',
                          str(i.get('reason','')).casefold())
            for i in verdict['issues'])
        results['operational_claim_control']={
            'status':'PASS' if caught else 'FAIL','detected':caught,
            'issues':verdict['issues'][:5]}
    except Exception as exc:
        results['operational_claim_control']={'status':'FAIL',
            'error':type(exc).__name__+': '+str(exc)[:500]}
    # Positive control: facts explicitly supplied by the user must remain valid.
    known_request=('Составь публикацию для Instagram мастерской «Веломир». Мастерская делает '
                   'диагностику велосипедов и замену тормозных колодок, принимает клиентов '
                   'по предварительной записи. Цель — объяснить пользу профилактики. '
                   'Стоимость и адрес неизвестны.')
    known_strategy='Цель публикации — объяснить пользу профилактики велосипедов.'
    known_calendar=('# Контент-план\n\n'
        '| Неделя/день | Площадка и формат | Конкретная тема | Цель | Сценарий/визуал | Текст/ключевые реплики | CTA | KPI |\n'
        '|---|---|---|---|---|---|---|---|\n'
        '| 1/Вт | Instagram, Карусель | Уход за тормозными колодками | Объяснить профилактику | '
        'Слайд 1 — изношенные колодки; слайд 2 — надпись «Изношенные колодки снижают эффективность торможения»; '
        'слайд 3 — CTA о диагностике. | Изношенные тормозные колодки снижают эффективность торможения. '
        'Если заметили износ, запишитесь на диагностику велосипеда в «Веломир» по предварительной записи. | '
        'Запишитесь на диагностику в «Веломир» | Вопросы о диагностике; измерять по обращениям |')
    checkpoint('explicit_fact_control')
    try:
        verdict=call_editor(known_request,known_strategy,known_calendar,
                            editor_call=editor_call,claim_adjudicator=claim_adjudicator,
                            require_inventory=editor_call is None,model=control_model)
        results['explicit_fact_control']={'status':'PASS' if verdict['pass'] else 'FAIL',
            'accepted':verdict['pass'],'issues':verdict['issues'][:5]}
    except Exception as exc:
        results['explicit_fact_control']={'status':'FAIL',
            'error':type(exc).__name__+': '+str(exc)[:500]}
    if (len(results['cases']) >= 2 and all(x['status']=='PASS' for x in results['cases'])
            and results['negative_control']['status']=='PASS'
            and results['operational_claim_control']['status']=='PASS'
            and results['explicit_fact_control']['status']=='PASS'):
        results['status']='PASS'
    results.update(finished=True,review_state='complete')
    checkpoint('complete')
    return results

if __name__ == '__main__':
    import sys
    source=Path(sys.argv[1])
    destination=Path(sys.argv[2])
    report=review_saved_results(source,ready_only='--ready-only' in sys.argv,checkpoint_path=destination)
    print(json.dumps({'status':report['status'],
                      'cases':[{'case':x['case'],'status':x['status'],
                                'issue_count':len(x.get('verdict',{}).get('issues',[])),
                                'error':x.get('error','')} for x in report['cases']],
                      'negative_control':report['negative_control']['status'],
                      'operational_claim_control':report['operational_claim_control']['status'],
                      'explicit_fact_control':report['explicit_fact_control']['status']},
                     ensure_ascii=False,indent=2),flush=True)
    sys.exit(0 if report['status']=='PASS' else 2)
