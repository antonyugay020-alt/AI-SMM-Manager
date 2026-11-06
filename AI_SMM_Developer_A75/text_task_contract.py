"""Host-owned task and revision contracts shared by all text entry points.

No industry vocabulary, generated content, model calls or provider selection.
"""
from dataclasses import dataclass
import copy
import hashlib
import json
from pathlib import Path

from deliverable_contract import review_requirement_bindings
from evidence_context import build_evidence_context, EvidenceContext
from durable_json import atomic_write_json


def bind_creative_permission(request, proposal):
    """Bind a proposed truth/fiction policy to the user's exact instruction.

    This validates provenance only. An independent requirements reviewer must
    separately authorize the meaning before it can affect source adjudication.
    """
    if proposal is None or (isinstance(proposal,dict) and proposal.get('mode')=='factual'):
        return None
    if not isinstance(proposal,dict) or proposal.get('mode') not in ('fictional','hypothetical'):
        raise ValueError('Неверный режим фактической опоры материала')
    quote=proposal.get('permission_quote')
    if not isinstance(quote,str) or not quote.strip() or quote not in request:
        raise ValueError('Разрешение на вымысел не связано с исходным заданием')
    return {'mode':proposal['mode'],'permission_quote':quote,
            'source_id':'user_request:'+hashlib.sha256(request.encode()).hexdigest()[:16]}


def record_model_failure(drafts, signature, model, stage, error):
    """Project-local response evidence; public progress contains only its path."""
    response = getattr(error, 'response_text', None)
    if not drafts or not isinstance(response, str):
        return None
    if not isinstance(signature, str) or len(signature) != 64 or any(c not in '0123456789abcdef' for c in signature):
        raise ValueError('Неверная идентичность сбоя модели')
    stage_id = hashlib.sha256(str(stage).encode()).hexdigest()[:8]
    path = Path(drafts)/f'model_failure_{signature[:16]}_{stage_id}.json'
    atomic_write_json(path, {'schema_version': 1, 'task_signature': signature,
                            'model': model, 'stage': str(stage),
                            'error_type': type(error).__name__,
                            'diagnostics': getattr(error, 'response_diagnostics', {}),
                            'response': response})
    return path.name


@dataclass(frozen=True)
class TextTaskContract:
    request: str
    evidence: EvidenceContext
    signature: str
    execution_parameters: str = ''

    @classmethod
    def create(cls, request, context):
        if not isinstance(request, str) or not request.strip():
            raise ValueError('Нужно исходное текстовое задание')
        evidence = build_evidence_context(request, context)
        evidence.render()  # Refuse silent truncation before any model call.
        from task_semantics import semantic_parameters
        parameters=semantic_parameters(context)
        material={'request':request,'evidence':evidence.to_dict()}
        if parameters is not None:material['execution_parameters']=parameters
        encoded = json.dumps(material,
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return cls(request,evidence,hashlib.sha256(encoded.encode()).hexdigest(),
            json.dumps(parameters,sort_keys=True) if parameters is not None else '')

    def bind(self, context):
        from task_semantics import semantic_parameters
        parameters=semantic_parameters(context)
        if self.execution_parameters!=(json.dumps(parameters,sort_keys=True) if parameters is not None else ''):
            raise ValueError('Параметры исполнителя изменились после фиксации контракта')
        if build_evidence_context(self.request,context).to_dict()!=self.evidence.to_dict():
            raise ValueError('Источники или контекст изменились после фиксации контракта')
        return {**copy.deepcopy(context), 'evidence_context': self.evidence.to_dict(),
                'text_task_signature': self.signature}

    def metadata(self):
        result={'schema_version': 1, 'signature': self.signature,
                'source_ids': [s.source_id for s in self.evidence.sources],
                'requirement_ids': [r['id'] for r in review_requirement_bindings(self.request)]}
        if self.execution_parameters:result['execution_parameters']=json.loads(self.execution_parameters)
        return result


@dataclass(frozen=True)
class TextRevisionContract:
    draft: str
    findings: tuple

    @classmethod
    def create(cls, draft, issues):
        if not isinstance(draft, str) or not draft.strip() or not isinstance(issues, list) or not issues:
            raise ValueError('Для правки нужны черновик и конкретные замечания')
        findings = []
        for index, issue in enumerate(issues, 1):
            if not isinstance(issue, dict):
                raise ValueError('Неверный контракт замечания')
            quote = issue.get('quote')
            reason = issue.get('reason')
            if not isinstance(quote, str) or not quote.strip() or quote not in draft:
                raise ValueError('Замечание не привязано к отклонённому черновику')
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('Не указана причина правки')
            digest = hashlib.sha256((quote+'\0'+reason).encode()).hexdigest()[:8]
            factual = issue.get('check') == 'factual_grounding'
            findings.append({**copy.deepcopy(issue),'id': f'F{index:03d}_{digest}', 'quote': quote, 'reason': reason,
                'dimension': issue.get('check', 'editorial'),
                'operation': 'remove_or_reframe_unverified_claim' if factual else 'resolve_requested_defect',
                'instruction': ('Удали неподтверждённое утверждение о компании или замени его общим '
                    'образовательным пояснением без приписывания компании процесса/результата. '
                    'Не сохраняй обещание другими словами. Подтверждённые услуги можно оставить.'
                    if factual else 'Выполни исходное требование, исправив указанную проблему; '
                    'не добавляй новое требование от себя.')})
            for key in ('evidence_id','review_signature','field','field_type',
                        'requirement_id','requirement_quote','requirement_mode','failure_type'):
                if isinstance(issue.get(key),str):
                    findings[-1][key]=issue[key]
            for key in ('related_evidence_ids','related_quotes'):
                if isinstance(issue.get(key),list) and all(isinstance(v,str) for v in issue[key]):
                    findings[-1][key]=list(issue[key])
            if isinstance(issue.get('finding_validation'),dict):
                findings[-1]['finding_validation']=copy.deepcopy(issue['finding_validation'])
            if isinstance(issue.get('review_scope'),dict):
                findings[-1]['review_scope']=copy.deepcopy(issue['review_scope'])
        return cls(draft, tuple(findings))

    def schema(self):
        return {'type': 'object', 'additionalProperties': False,
                'required': ['revised_text'], 'properties': {
                    'revised_text': {'type': 'string', 'minLength': 1}}}

    def validate(self, value):
        if not isinstance(value, dict) or 'revised_text' not in value or set(value)-{'revised_text','resolutions'}:
            raise ValueError('Правка не вернула текст по контракту')
        # Old adapters may include resolutions. A model's change report is not
        # evidence: derive changes from immutable input/output and independently
        # review the resulting text. Never discard it for a fictitious new quote.
        text = value['revised_text']
        if not isinstance(text, str) or not text.strip() or text.strip() == self.draft.strip():
            raise ValueError('Правка не изменила отклонённый результат')
        # Retaining a substring does not establish retention of its meaning:
        # it may now be explicitly negated, quoted as a rejected claim, or
        # correctly scoped. This is a proposal, never semantic approval.
        # The caller must independently review the complete revised artifact.
        return text.strip()

    def evidence(self, text):
        """Host-observed changes, never a claim that a semantic defect is fixed."""
        return {'input_sha256':hashlib.sha256(self.draft.encode()).hexdigest(),
                'output_sha256':hashlib.sha256(text.encode()).hexdigest(),
                'findings':[{'id':f['id'],'original_quote_retained':f['quote'] in text}
                            for f in self.findings],
                'semantic_resolution':'requires_independent_review'}
