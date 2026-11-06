"""Version-bound draft storage and lossless targeted text repair.

No business taxonomy. Host owns line identities and immutable unmodified bytes;
models propose patches. This module never approves its own repaired output.
"""
from pathlib import Path
from dataclasses import dataclass
import copy
import hashlib
import json
import re
from durable_json import atomic_write_json


@dataclass(frozen=True)
class GeneratedDocument:
    """A decoded proposal or its exact rejected bytes, never an approval.

    A decoder failure travels with its raw artifact into recovery and storage;
    it must not be reinterpreted as an ordinary Markdown proposal downstream.
    """
    content: str
    contract_error: str = ''

    def __post_init__(self):
        if not isinstance(self.content, str) or not isinstance(self.contract_error, str):
            raise TypeError('Содержимое документа и ошибка контракта должны быть строками')


def digest(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _unique_json_object(pairs):
    """Reject ambiguous persisted records rather than silently taking last key."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field in document checkpoint: ' + key)
        result[key] = value
    return result


class DocumentDraft:
    def __init__(self, drafts, signature, kind):
        if not isinstance(kind,str) or not re.fullmatch(r'(?:strategy|calendar|publication_[0-9]{1,5}|publication_rejected_[0-9]{1,5})',kind):
            raise ValueError('Unsupported draft kind')
        if not isinstance(signature,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',signature):
            raise ValueError('Invalid task signature')
        self.signature=signature
        self.kind=kind
        # Address storage by the full task identity. Prefixes are not unique.
        self.path=Path(drafts)/f'{kind}_candidate_{hashlib.sha256(signature.encode("utf-8")).hexdigest()}.json' if drafts else None
        self.legacy_path=Path(drafts)/f'{kind}_candidate_{signature[:16]}.json' if drafts else None

    @staticmethod
    def _review_record(record, content, valid):
        """Validate a verdict attached to the *exact* artifact being stored.

        This is evidence binding, not a substitute for independent review.
        """
        if record is None:
            return None
        if not isinstance(record, dict) or set(record) != {'document_sha256', 'verdict', 'review_id'}:
            raise ValueError('Malformed review provenance')
        if record['document_sha256'] != digest(content):
            raise ValueError('Review verdict refers to a different document version')
        if type(record['verdict']) is not bool or record['verdict'] != valid:
            raise ValueError('Review verdict disagrees with document state')
        if not isinstance(record['review_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', record['review_id']):
            raise ValueError('Invalid review identifier')
        return dict(record)

    def load(self):
        if self.path is None:return None
        path=self.path
        if not path.exists():
            # Read-only backward compatibility: only a matching embedded signature
            # can authorize restoring a legacy prefix-addressed checkpoint.
            if self.legacy_path is None or not self.legacy_path.exists():return None
            path=self.legacy_path
        # A legacy prefix may belong to a different task. Never claim it;
        # a matching legacy record remains subject to full integrity checks.
        obj=json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_json_object)
        if path == self.legacy_path and isinstance(obj, dict) and isinstance(obj.get('signature'), str) and obj['signature'] != self.signature:
            return None
        # Canonical checkpoints must declare their schema and kind explicitly.
        # Missing schema is permitted only on the legacy-addressed read-only path.
        if path != self.legacy_path:
            if type(obj) is not dict or type(obj.get('schema')) is not int or obj['schema'] != 1 or obj.get('kind') != self.kind:
                raise ValueError('Unsupported canonical checkpoint schema or kind')
        elif type(obj) is dict and 'schema' in obj and (type(obj['schema']) is not int or obj['schema'] != 1):
            raise ValueError('Unsupported legacy checkpoint schema')
        if (not isinstance(obj,dict) or obj.get('signature')!=self.signature or
            obj.get('kind',self.kind)!=self.kind or
            not isinstance(obj.get('content'),str) or not obj['content'].strip() or
            obj.get('sha256')!=digest(obj['content']) or
            not isinstance(obj.get('reason'),str) or type(obj.get('valid')) is not bool):
            raise ValueError('Document checkpoint is malformed or belongs to another task')
        self._review_record(obj.get('review'), obj['content'], obj['valid'])
        history=obj.get('history',[])
        if not isinstance(history,list) or len(history)>8:
            raise ValueError('Document checkpoint history is malformed')
        for item in history:
            if (not isinstance(item,dict) or not isinstance(item.get('content'),str) or
                not item['content'].strip() or item.get('sha256')!=digest(item['content']) or
                not isinstance(item.get('reason'),str) or type(item.get('valid')) is not bool):
                raise ValueError('Document checkpoint history is malformed or corrupted')
            self._review_record(item.get('review'), item['content'], item['valid'])
        return obj

    def save(self, content, valid, reason, *, review=None):
        # An approval flag is a security boundary, not a truthy/falsy hint.
        # In particular bool('false') is True, so never coerce model data.
        if type(valid) is not bool:
            raise TypeError('Document approval must be a boolean')
        if not isinstance(reason, str):
            raise TypeError('Document review reason must be a string')
        if not isinstance(content,str) or not content.strip():
            raise ValueError('Document checkpoint content must be nonempty text')
        review=self._review_record(review, content, valid)
        if self.path:
            history=[]
            if self.path.exists() or (self.legacy_path and self.legacy_path.exists()):
                previous=self.load()
                if previous:
                    history=list(previous.get('history',[]))
                    snapshot={'content':previous['content'],'sha256':previous['sha256'],
                              'valid':previous['valid'],'reason':previous['reason'], 'review':previous.get('review')}
                    changed=(previous['content']!=content or previous['valid']!=valid or
                             previous['reason']!=reason or previous.get('review')!=review)
                    if changed and (not history or history[-1]!=snapshot):
                        history.append(snapshot)
                    history=history[-8:]
            atomic_write_json(self.path,{'schema':1,'signature':self.signature,'kind':self.kind,
                'content':content,'sha256':digest(content),'valid':valid,'reason':reason,
                'history':history, 'review':review})


def patch_document(ask, model, request, document, issues, *, constrained=False,
                   evidence_context=None, findings=None):
    """A bounded edit proposal; all content must pass fresh checks afterwards."""
    lines=document.splitlines(keepends=True)
    numbered={f'L{i+1:05d}':line.rstrip('\r\n') for i,line in enumerate(lines)}
    editable={key:value for key,value in numbered.items() if value.strip() and not value.lstrip().startswith(('#','```','---'))}
    if not editable: raise ValueError('No editable document lines')
    schema={'type':'object','additionalProperties':False,'required':['changes'],
            'properties':{'changes':{'type':'array','minItems':1,'maxItems':len(editable),
              'items':{'type':'object','additionalProperties':False,'required':['line_id','text'],
                'properties':{'line_id':{'type':'string','enum':list(editable)},
                              'text':{'type':'string','minLength':1}}}}}}
    system=('Ты редактор отклонённого документа. Верни ТОЛЬКО JSON '
        '{"changes":[{"line_id":"L00001","text":"исправленная строка"}]}. '
        'Исправь ВСЕ перечисленные нарушения только в необходимых строках. '
        'Не переписывай весь документ, не меняй заголовки, бизнес и требования. '
        'Не выдумывай числовую базу, факты, услуги или каналы записи. '
        'Если услуга запрещена или не подтверждена, убери предложение и сохрани соседнее полезное содержание. '
        'Не заменяй бесплатную услугу другой бесплатной услугой. '
        'Если данных нет, предложи способ их измерить или уточнить. '
        'Процент или абсолютное значение нельзя объявлять фактом без источника. '
        'Не маскируй выдуманную базу словом гипотеза. Не удаляй строки: замени ошибку '
        'содержательным исправлением. При неизвестной базе укажи необходимость измерения. '
        'Верни ТОЛЬКО реально изменённые строки из editable_lines, каждый ID один раз.')
    # Keep the independent review's structured evidence attached to the edit
    # request. Flattening findings to a prose error string used to discard the
    # exact quote, field, and evidence location before repair; this made the
    # editor guess which line to change and often repeat the same defect.
    finding_rows=[]
    if isinstance(findings,list):
        for item in findings:
            if not isinstance(item,dict):
                continue
            # These findings have already crossed the host review boundary.
            # Preserve its complete proof, scope and required_change. A second
            # field whitelist silently discarded later contract additions.
            finding_rows.append(copy.deepcopy(item))
    quoted=[]
    for item in finding_rows:
        quote=str(item.get('quote') or item.get('claim') or '').strip()
        if quote:
            quoted.append(quote)
    quoted.extend((a or b) for a,b in re.findall(
        r"['‘]([^'’]{4,300})['’]|[«“]([^»”]{4,300})[»”]",str(issues)))
    targets=[]
    for needle_value in quoted:
        needle=str(needle_value).casefold().strip()
        if not needle:
            continue
        for key,value in editable.items():
            folded=value.casefold()
            if (needle in folded or (len(needle)>30 and folded in needle)) and key not in targets:
                targets.append(key)
    payload={'original_request':request,'rejection_reasons':issues,
             'structured_findings':finding_rows,
             'evidence_context':(evidence_context.to_dict() if hasattr(evidence_context,'to_dict') else evidence_context),
             'priority_line_ids':targets,
             'instruction':'Сначала исправь каждую исходную причину отказа, особенно строки priority_line_ids. '
                           'Точная цитата показывает пример дефекта, а не ограничивает область правки. '
                           'Выполни finding_validation.required_change каждого подтверждённого замечания '
                           'во всех затронутых строках доступного артефакта, соблюдая review_scope. '
                           'Если предыдущее изменение отклонено, не повторяй его; замени строку нейтральной, '
                           'подтверждённой исходным заданием формулировкой.',
             'document_sha256':digest(document),
             'protected_lines':{k:v for k,v in numbered.items() if k not in editable},
             'editable_lines':editable}
    arguments=(model,system,json.dumps(payload,ensure_ascii=False))
    raw=ask(*arguments,json_schema=schema) if constrained else ask(*arguments)
    obj=json.loads(raw)
    if not isinstance(obj,dict) or set(obj)!={'changes'} or not isinstance(obj['changes'],list) or not obj['changes']:
        raise ValueError('Editor did not return a nonempty patch')
    changed={}
    seen=set()
    proposed={}
    for entry in obj['changes']:
        if not isinstance(entry,dict) or set(entry)!={'line_id','text'}:
            raise ValueError('Malformed patch entry')
        key=entry['line_id'];text=entry['text']
        if not isinstance(key,str) or key not in numbered or not isinstance(text,str):
            raise ValueError('Unknown patch line or non-text replacement')
        text=text.replace('\r\n','\n').replace('\r','\n').rstrip('\n')
        if key in seen:
            if proposed.get(key)==text:
                continue
            raise ValueError('Conflicting duplicate line replacement')
        seen.add(key)
        proposed[key]=text
        if text == numbered[key]: continue
        if not text.strip(): raise ValueError('Patch cannot erase required content; replace it with a meaningful correction')
        if len(text)>4000: raise ValueError('Patch replacement exceeds the bounded line-edit size')
        new_lines=text.split('\n')
        if any(line.lstrip().startswith(('#','```','---')) for line in new_lines):
            raise ValueError('Patch cannot change document structure')
        original=numbered[key]
        prefix=re.match(r'^(\s*(?:[-*+]\s+|\d+[.)]\s+|>\s+))',original)
        if prefix:
            # The line identity and Markdown structure belong to the host. A
            # model often returns only the revised prose, so preserve the
            # original list/quote marker here instead of treating a missing
            # marker as a content failure. Reject an explicitly changed
            # marker: that would alter list numbering or nesting.
            marker=prefix.group(1)
            proposed_prefix=re.match(r'^(\s*(?:[-*+]\s+|\d+[.)]\s+|>\s+))',new_lines[0])
            if proposed_prefix and proposed_prefix.group(1)!=marker:
                raise ValueError('Patch cannot change the original Markdown block marker')
            if not proposed_prefix:
                new_lines[0]=marker+new_lines[0].lstrip()
            continuation=' '*len(marker)
            new_lines=[new_lines[0],*(line if not line.strip() or line.startswith(continuation)
                                      else continuation+line.lstrip()
                                      for line in new_lines[1:])]
            text='\n'.join(new_lines)
        if '|' in original and ('\n' in text or text.count('|')!=original.count('|')):
            raise ValueError('Patch must preserve the table row and column structure')
        if key not in editable:
            raise ValueError('Patch cannot change protected document structure')
        changed[key]=text
    output=[]
    for i,line in enumerate(lines):
        key=f'L{i+1:05d}'
        if key not in changed:output.append(line);continue
        newline='\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
        replacement=changed[key].replace('\n',newline or '\n')
        output.append(replacement+newline)
    result=''.join(output)
    if result==document or not result.strip():raise ValueError('Patch made no useful change')
    return result
