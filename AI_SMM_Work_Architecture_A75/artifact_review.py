"""Immutable, field-bound review evidence; a reviewer cannot author its verdict.

The same contract works for arbitrary named text fields. Publication callers
supply their existing field roles; no topics, industries or generated copy are
encoded here. Only the host selects evidence text from issued IDs.
"""
from __future__ import annotations
from dataclasses import dataclass
import copy
import hashlib
import json
import re
import secrets
from evidence_segments import sentence_spans
from deliverable_contract import review_requirement_bindings
from publication_schema import is_planning_field


class ReviewContractError(ValueError):
    """An invalid reviewer answer is unavailable, never a substantive rejection."""


def review_source_signature(request, artifacts):
    """Bind review transport to the exact canonical text saved by the host."""
    if (not isinstance(request,str) or not isinstance(artifacts,dict) or not artifacts
            or any(not isinstance(key,str) or not isinstance(body,str)
                   for key,body in artifacts.items())):
        raise ValueError('Нет точных исходников редакторской проверки')
    return hashlib.sha256(json.dumps({'request':request,
        'artifacts':{key:body.strip() for key,body in artifacts.items()}},
        ensure_ascii=False,sort_keys=True).encode()).hexdigest()


SUBSTANCE_POLICY = 'substantive_delivery_r82'
WRITING_QUALITY_POLICY = (
    ' Выполни содержательное обещание темы и цель задания. Объяснение должно объяснять, '
    'сравнение — давать основание выбора, памятка — понятные применимые ориентиры. '
    'Оговорка, вопрос аудитории или отсылка к инструкции не заменяют основное содержание. '
    'Различай неизвестные факты компании и допустимое общее объяснение: не выдумывай услуги, '
    'но и не удаляй всю пользу ради отсутствия бизнес-данных. '
    'Не публикуй служебные оправдания автора. Укажи условия применимости там, где это нужно. '
    'Новый материал должен добавлять собственный вывод, а не повторять предыдущий под другим заголовком. '
    'Согласуй цель, визуал, CTA и способ наблюдения результата без вымышленной базы. '
    'В обычном тексте используй настоящие переводы строк; буквальные escape-последовательности '
    'оставляй только в цитатах, программных примерах и по явному заданию. '
)
SUBSTANCE_CRITERIA = {
    'topic_delivery': 'Материал выполняет обещание темы и исходную задачу: объяснение содержит причину, сравнение — различия и основание выбора, инструкция — применимые действия. Заголовок, вопрос аудитории, общая оговорка или отсылка к другому источнику сами по себе не раскрывают обещанное содержание. Не требуй неподтверждённых фактов компании.',
    'reader_value': 'Есть конкретная полезная мысль, объяснение или выполнимое действие для этой аудитории. Тавтология и повтор цели не являются пользой. Ограничения безопасности сохраняются; вместо удаления содержания нужны допустимые содержательные ориентиры. Не навязывай инструкцию, если задача художественная, новостная или иная.',
    'clarity': 'Текст и надписи понятны, вычитаны, пригодны для своей роли. Служебный отчёт о том, чего автор намеренно не написал, не должен подменять публичное содержание. Непреднамеренные буквальные escape-последовательности исправляются; программные примеры, цитаты и явно требуемый буквальный текст сохраняются.',
    'execution': 'Сценарий соответствует содержанию и формату; понятно, что подготовить и показать, последовательность кадров и надписи выполнимы. Не требуй голоса, музыки, числа кадров или длительности, если они не нужны задаче. Предлагаемая постановка не означает наличие готовых материалов.',
    'action_measurement': 'Цель, уместное действие аудитории, KPI и метод измерения согласованы. Есть выполнимый способ наблюдения результата без вымышленной базы. Один показатель допустим у разных материалов; повтор KPI не является содержательным повтором.',
    'audience_fit': 'Аудитория связана с потребностями, гипотезами сообщений и действиями; одно перечисление групп не заменяет запрошенную сегментацию. Не выдавай гипотезы за исследованные факты.',
    'strategic_choices': 'Стратегия содержит применимые к задаче решения и объясняет их связь с целью и известным бизнесом. Неизвестные преимущества и услуги остаются запросом данных; их нельзя выдумать или требовать как уже известные.',
    'execution_budget': 'Понятна последовательность исполнения, выбора материалов и проверки гипотез. Если задан бюджет, категории и сумма согласованы, обозначены основания и границы распределения. Не требуй бюджет или рекламный канал, которых нет в задаче.',
    'measurement_decisions': 'Описаны наблюдаемые показатели, сопоставимый период/условие наблюдения и решения по результатам. Недостающая исходная статистика не запрещает план измерения; прогнозы и существующую базу нельзя выдумывать.',
    'portfolio_distinction': 'Разные материалы добавляют разные полезные выводы или действия. Другой заголовок, предмет или ситуация не оправдывают одинаковое основное содержание. Обоснованное закрепление, серия и повторяемая метрика допустимы. Для дефекта сравни точные фрагменты двух разных публикаций.',
    'strategy_alignment': 'Содержимое календаря реализует заявленные цели и направления стратегии в пределах исходного задания. Не навязывай продажи, воронку или услуги, если пользователь просит другую задачу. Не превращай стратегию в источник бизнес-фактов.',
}


class SubstanceControlFailure(RuntimeError):
    review_unavailable=True
    retryable=False


def substance_controls():
    """Fresh, closed-world toy tasks; never facts or requirements of the user."""
    rng=secrets.SystemRandom();token=secrets.token_hex(3)
    a,b=rng.sample(range(11,90),2)
    variants=[
        ('Объяснить причину.',[f'Сигнал {token} возникает из-за режима {a}.'],
         f'Сигнал {token} возникает из-за режима {a}. Поэтому причина сигнала — этот режим.',
         f'Сигнал {token} возникает из-за режима {b}.'),
        ('Сравнить два варианта по доступным функциям.',
         [f'Вариант {token}-A поддерживает функцию {a}.',f'Вариант {token}-B поддерживает функцию {b}.'],
         f'Вариант {token}-A поддерживает функцию {a}. Вариант {token}-B поддерживает функцию {b}. Различие — поддерживаемая функция; выбирать следует по нужной функции.',
         f'Оба варианта {token}-A и {token}-B поддерживают только функцию {a}.'),
        ('Описать заданную последовательность действий.',
         [f'Сначала открыть карточку {a}.',f'Затем выбрать пункт {b}.'],
         f'Сначала открыть карточку {a}. Затем выбрать пункт {b}.',
         f'Сначала выбрать пункт {b}. Затем открыть карточку {a}.'),
        ('Составить план проверки двух вариантов без прогноза результата.',
         [f'Проверить вариант {a}.',f'Проверить вариант {b}.',f'Сравнить число ответов за {a} условных тактов.'],
         f'Проверить вариант {a}. Проверить вариант {b}. Сравнить число ответов за {a} условных тактов.',
         f'Проверить только вариант {a} и закончить без сравнения.'),
    ]
    task,points,answer,wrong=rng.choice(variants)
    vague=rng.choice(('Условия могут различаться. Уточните сведения и задайте вопрос.',
        'Рассмотрите доступные возможности. Для подробного ответа обратитесь к справке.',
        'Важен индивидуальный подход. Поделитесь своим опытом и сохраните напоминание.'))
    controls={}
    for expected,candidate in rng.sample([(True,answer),(False,rng.choice((wrong,vague)))],2):
        ident='C_'+secrets.token_hex(4)
        controls[ident]={'task':task,'source_points':points,'candidate':candidate,'expected':expected}
    return controls


@dataclass(frozen=True)
class SubstanceReviewContract:
    """Required semantic work records, bound to exact fields, never a score."""
    signature: str
    catalog: dict
    checks: dict
    controls: dict

    def matches_proof(self, proof):
        """Reconstruct required work and source quotes at the receiving boundary.

        This validates provenance/completeness, not the truth of model opinions.
        No model call or new qualification is performed here.
        """
        if (not isinstance(proof,dict) or proof.get('policy')!=SUBSTANCE_POLICY
                or proof.get('signature')!=self.signature
                or proof.get('complete') is not True or proof.get('pass') is not True):
            return False
        expected=proof.get('expected_checks');records=proof.get('assessments')
        if (not isinstance(expected,list) or not all(isinstance(key,str) for key in expected)
                or len(expected)!=len(self.checks) or set(expected)!=set(self.checks)
                or not isinstance(records,dict) or set(records)!=set(self.checks)):
            return False
        for key,check in self.checks.items():
            row=records[key]
            if (not isinstance(row,dict) or row.get('status')!='satisfied'
                    or any(row.get(field)!=value for field,value in check.items())
                    or row.get('evidence_id') not in check['evidence_ids']
                    or row.get('quote')!=self.catalog[row['evidence_id']]
                    or not isinstance(row.get('reason'),str)
                    or not 16<=len(row['reason'].strip())<=400):
                return False
        controls=proof.get('controls')
        return (isinstance(controls,dict) and controls.get('passed') is True
            and any(type(controls.get(key)) is int and controls[key]>0
                    for key in ('count','checked_calls')))

    @classmethod
    def create(cls,request,catalog,units):
        if (not isinstance(catalog,dict) or not catalog
                or any(not isinstance(k,str) or not isinstance(v,str) for k,v in catalog.items())):
            raise ValueError('Нет исходных полей содержательной проверки')
        signature=hashlib.sha256(json.dumps({'request':request,'catalog':catalog,'units':units},
            ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        checks={}
        for unit in units:
            for criterion,refs in unit['criteria'].items():
                if criterion not in SUBSTANCE_CRITERIA or not refs or any(ref not in catalog for ref in refs):
                    raise ValueError('Критерий не привязан к исходным полям')
                ident='Q_'+hashlib.sha256((unit['id']+'\n'+criterion).encode()).hexdigest()[:12]
                if ident in checks:raise ValueError('Повтор содержательного критерия')
                checks[ident]={'unit_id':unit['id'],'criterion':criterion,'evidence_ids':list(refs),
                    'instruction':SUBSTANCE_CRITERIA[criterion]}
        return cls(signature,copy.deepcopy(catalog),checks,substance_controls())

    def select(self,refs,*,portfolio=False):
        available=set(refs)
        return type(self)(self.signature,self.catalog,{key:row for key,row in self.checks.items()
            if (portfolio is None or (row['criterion'] in ('portfolio_distinction','strategy_alignment'))==portfolio)
            and set(row['evidence_ids'])<=available},self.controls)

    def control_payload(self):
        return {key:{k:v for k,v in row.items() if k!='expected'} for key,row in self.controls.items()}

    def model_manifest(self):
        return {'checks':{key:{k:v for k,v in row.items() if k!='instruction'} for key,row in self.checks.items()},
            'definitions':{row['criterion']:row['instruction'] for row in self.checks.values()},
            'controls':self.control_payload()}

    def control_schema(self):
        return {'type':'object','additionalProperties':False,'required':list(self.controls),
                'properties':{key:{'type':'boolean'} for key in self.controls}}

    def schema(self):
        return {'type':'object','additionalProperties':False,'required':list(self.checks),
            'properties':{key:{'type':'object','additionalProperties':False,
                'required':['status','evidence_id','reason'], 'properties':{
                    'status':{'type':'string','enum':['satisfied','defect','unknown']},
                    'evidence_id':{'type':'string','maxLength':64},
                    'reason':{'type':'string','minLength':16,'maxLength':400}}}
                for key in self.checks}}

    def validate(self,value,issues,control_answers=None):
        if (not isinstance(control_answers,dict) or set(control_answers)!=set(self.controls)
                or any(type(control_answers[key]) is not bool
                       or control_answers[key]!=row['expected'] for key,row in self.controls.items())):
            raise SubstanceControlFailure('Редактор не прошёл случайный контроль содержательной проверки')
        if not isinstance(value,dict) or set(value)!=set(self.checks):
            raise ReviewContractError('Не выполнен полный набор содержательных проверок')
        records={}
        for ident,check in self.checks.items():
            item=value[ident]
            if (not isinstance(item,dict) or set(item)!={'status','evidence_id','reason'}
                    or item.get('status') not in ('satisfied','defect','unknown')
                    or item.get('evidence_id') not in check['evidence_ids']
                    or not isinstance(item.get('reason'),str) or not 16<=len(item['reason'].strip())<=400):
                raise ReviewContractError('Содержательный вывод не привязан к исходному полю')
            linked=[issue for issue in issues if issue.get('criterion_id')==ident]
            if item['status']=='unknown':
                raise ReviewContractError('Содержательная проверка не завершена: '+check['criterion'])
            if item['status']=='defect' and not linked:
                raise ReviewContractError('Дефект критерия '+ident+' не имеет связанного замечания issues с criterion_id='+ident)
            if linked and any(issue.get('evidence_id') not in check['evidence_ids'] for issue in linked):
                raise ReviewContractError('Замечание относится к другому содержательному объекту')
            if check['criterion']=='portfolio_distinction':
                for issue in linked:
                    related=issue.get('related_evidence_ids',[])
                    if not related or not any(ref in check['evidence_ids'] and ref!=issue['evidence_id'] for ref in related):
                        raise ReviewContractError('Смысловой повтор не подтверждён двумя публикациями')
            records[ident]={**copy.deepcopy(check),**item,
                'status':'defect' if linked else item['status'],
                'quote':self.catalog[item['evidence_id']]}
        for issue in issues:
            if issue.get('criterion_id','none') not in ('none',*self.checks):
                raise ReviewContractError('Выдан чужой содержательный критерий')
            if (issue.get('type')=='quality' or issue.get('check')=='completeness') and issue.get('criterion_id') not in self.checks:
                raise ReviewContractError('Содержательное замечание не связано с выполненным критерием')
            if issue.get('criterion_id') in self.checks:
                check=self.checks[issue['criterion_id']]
                issue['quality_criterion']={'id':issue['criterion_id'],'name':check['criterion'],
                    'unit_id':check['unit_id'],'instruction':check['instruction'],
                    'source_signature':self.signature,'policy':SUBSTANCE_POLICY}
        return {'policy':SUBSTANCE_POLICY,'signature':self.signature,
                'expected_checks':list(self.checks),'assessments':records,
                'controls':{'passed':True,'count':len(self.controls)},
                'complete':True,'pass':all(row['status']=='satisfied' for row in records.values())}


def document_substance_contract(request,segments):
    """One role-aware contract for complete strategy/publication units."""
    catalog={row['evidence_id']:row['content'] for row in segments}
    grouped={}
    for row in segments:
        slot=row.get('row_context',{}).get('slot','')
        key=row['document']+(':'+slot if slot else ':narrative')
        grouped.setdefault(key,[]).append(row)
    units=[]
    for key,rows in grouped.items():
        if (key=='calendar:narrative' and any(
                other.startswith('calendar:') and other!=key for other in grouped)):
            continue  # Calendar headings/rhythm are context, not separate posts.
        all_refs=[row['evidence_id'] for row in rows]
        def refs(*roles):
            return [row['evidence_id'] for row in rows if row.get('field_type') in roles] or all_refs
        if rows[0]['document']=='strategy':
            criteria={name:all_refs for name in ('audience_fit','strategic_choices','execution_budget','measurement_decisions','clarity')}
        else:
            criteria={'topic_delivery':refs('caption','visual'),'reader_value':refs('caption'),
                      'clarity':refs('caption','cta')}
            if any(row.get('field_type')=='visual' for row in rows):criteria['execution']=refs('visual')
            if any(row.get('field_type') in ('cta','kpi','measurement') for row in rows):
                criteria['action_measurement']=refs('cta','kpi','measurement')
        units.append({'id':key,'criteria':criteria})
    captions=[row['evidence_id'] for row in segments if row['document']=='calendar'
        and row.get('field_type')=='caption' and row.get('row_context',{}).get('slot')]
    slots={row.get('row_context',{}).get('slot') for row in segments if row['evidence_id'] in captions}
    strategy=[row['evidence_id'] for row in segments if row['document']=='strategy']
    if len(slots)>1:
        criteria={'portfolio_distinction':captions}
        if strategy:criteria['strategy_alignment']=captions+strategy
        units.append({'id':'portfolio','criteria':criteria})
    return SubstanceReviewContract.create(request,catalog,units)


def standalone_substance_contract(request, draft):
    """Same source spans and criteria for generation review and acceptance."""
    contract=ArtifactReviewContract.create(request,{'standalone':draft},
        {'standalone':'caption'},scope={'dimension':'completeness'})
    catalog={span.ident:span.text for span in contract.spans}
    return SubstanceReviewContract.create(request,catalog,[{'id':'standalone',
        'criteria':{name:list(catalog) for name in ('topic_delivery','reader_value','clarity')}}])


def resolve_editorial_approval(flag, issues):
    """Concrete validated findings outrank a model's redundant approval bit.

    This can only make a verdict more restrictive. A rejection without findings
    remains malformed; it cannot become permission to save or rewrite content.
    """
    if type(flag) is not bool or not isinstance(issues,list):
        raise ValueError('Нет однозначного редакторского решения и списка замечаний')
    if issues:
        return False
    if not flag:
        raise ValueError('Редактор отклонил материал без конкретного замечания')
    return True


def validate_reason_reference(reason, selected_text, evidence_texts, request=''):
    """Reject explicit textual evidence copied from a different artifact field.

    Reasons may quote requirements. This check only verifies exact provenance
    of quoted text; an explanation without quotations still needs semantic review.
    """
    def normalized(text):
        return re.sub(r'\s+',' ',re.sub(r'[`*_~]','',str(text))).strip().casefold()
    selected=normalized(selected_text);original=normalized(request)
    catalog=[normalized(text) for text in evidence_texts]
    quotes=re.findall(r'["«“]([^"»”\n]{12,})["»”]|\x27([^\x27\n]{12,})\x27',reason)
    for pair in quotes:
        quote=normalized(next(part for part in pair if part))
        if quote in selected or quote in original:
            continue
        if any(quote in text for text in catalog):
            raise ValueError('Причина цитирует другое поле артефакта; выбери ID фрагмента, к которому относится замечание')


def validated_review_call(ask, model, system, user, schema, validate, *, report=None):
    """One bounded correction of a malformed verdict on the same immutable input.

    Only contract errors are retried. Transport failures and time budgets still
    belong to the gateway. A negative valid verdict is returned immediately.
    """
    error = None
    for attempt in range(2):
        correction = '' if error is None else (
            '\nПредыдущий вердикт нарушил контракт: ' + str(error)[:400] +
            '. Исправь только вердикт по той же схеме и неизменённому артефакту. '
            'Не меняй содержание и не выбирай одобрение только ради корректного JSON.')
        try:
            raw = ask(model, system + correction, user, json_schema=schema)
            value = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.I)) if isinstance(raw, str) else raw
            return validate(value)
        except (ValueError, SubstanceControlFailure) as exc:
            # A malformed response or a failed blind control gets at most one
            # fresh attempt against the SAME evidence and undisclosed answers.
            # Never turn either failure into approval or reveal control answers.
            error = exc
            if report:
                report('review_contract_invalid', model=model, attempt=attempt+1,
                       error_type=type(exc).__name__, reason=str(exc)[:500])
    if isinstance(error, SubstanceControlFailure):
        raise error
    raise ReviewContractError('Не завершена проверка контракта редактора: ' + str(error)[:400]) from error


def deduplicate_findings(items):
    """Preserve typed evidence while deduplicating both legacy and typed issues."""
    if not isinstance(items,list):
        raise ValueError('Замечания должны быть списком')
    seen=set();result=[]
    for item in items:
        key=json.dumps(item,ensure_ascii=False,sort_keys=True)
        if key not in seen:
            seen.add(key);result.append(copy.deepcopy(item))
    return result


@dataclass(frozen=True)
class ReviewSpan:
    ident: str
    field: str
    role: str
    text: str


@dataclass(frozen=True)
class ArtifactReviewContract:
    spans: tuple
    requirements: tuple
    signature: str

    @classmethod
    def create(cls, request, fields, roles=None, *, scope=None):
        if not isinstance(request,str):
            raise ValueError('Исходное задание должно быть строкой')
        if not isinstance(fields,dict) or not fields:
            raise ValueError('Нет полей артефакта для проверки')
        if roles is not None and not isinstance(roles,dict):
            raise ValueError('Роли полей должны быть объектом')
        if any(field not in fields for field in (roles or {})):
            raise ValueError('Роль относится к неизвестному полю артефакта')
        for field,text in fields.items():
            if not isinstance(field,str) or not field.strip() or not isinstance(text,str) or not text.strip():
                raise ValueError('Некорректное текстовое поле артефакта')
            role=(roles or {}).get(field,'document_narrative')
            if not isinstance(role,str) or not role.strip():
                raise ValueError('Некорректная роль поля артефакта')
        if scope is not None and not isinstance(scope,dict):
            raise ValueError('Область проверки должна быть объектом')
        signature=hashlib.sha256(json.dumps({'request':request,'fields':list(fields.items()),
            'roles':roles or {},'scope':scope or {}},
            ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        rows=[]
        for field,text in fields.items():
            role=(roles or {}).get(field,'document_narrative')
            pieces=[text]+[text[a:b] for a,b in sentence_spans(text) if text[a:b]!=text]
            for value in dict.fromkeys(pieces):
                rows.append(ReviewSpan(f'E{len(rows)+1:04d}_{signature[:16]}',field,role,value))
        refs=tuple((r['id'],r['quote']) for r in review_requirement_bindings(request))
        return cls(tuple(rows),refs,signature)

    def resolve_evidence(self, ident, field=None):
        """Resolve only host-issued IDs from this exact request and artifact."""
        if not isinstance(ident,str):
            raise ValueError('ID фрагмента должен быть строкой')
        span=next((s for s in self.spans if s.ident==ident),None)
        if span is None:
            raise ValueError('ID фрагмента не выдан для этой версии артефакта')
        if field is not None and span.field!=field:
            raise ValueError('Доказательство принадлежит другому полю артефакта')
        return span

    def evidence_payload(self):
        return [{'evidence_id':s.ident,'field':s.field,'field_type':s.role,'text':s.text}
                for s in self.spans]

    def related_evidence(self, item):
        ids=item.get('related_evidence_ids',[])
        if (not isinstance(ids,list) or len(ids)>4 or
                any(not isinstance(value,str) for value in ids) or len(ids)!=len(set(ids))):
            raise ValueError('Некорректные ссылки на связанные фрагменты')
        return [self.resolve_evidence(ident) for ident in ids]

    def schema(self):
        return {'type':'object','additionalProperties':False,'required':['approved','issues'],
            'properties':{'approved':{'type':'boolean'},'issues':{'type':'array','maxItems':10,
                'items':{'type':'object','additionalProperties':False,
                    'required':['field','evidence_id','kind','requirement_id','reason'],
                    'properties':{
                        'field':{'type':'string','enum':list(dict.fromkeys(s.field for s in self.spans))},
                        'evidence_id':{'type':'string','enum':[s.ident for s in self.spans]},
                        'related_evidence_ids':{'type':'array','maxItems':4,'uniqueItems':True,
                            'items':{'type':'string','enum':[s.ident for s in self.spans]}},
                        'kind':{'type':'string','enum':['unsupported_fact','requirement_violation','quality','unsafe_advice']},
                        'requirement_id':{'type':'string','enum':['none',*[r[0] for r in self.requirements]]},
                        'reason':{'type':'string','minLength':10,'maxLength':500}}}}}}

    def validate(self, value):
        if (not isinstance(value,dict) or type(value.get('approved')) is not bool
                or not isinstance(value.get('issues'),list)):
            raise ValueError('Редактор не вернул проверяемый вердикт')
        if 'revised_post' in value:
            raise ValueError('Редактор не может одобрить собственную правку')
        if set(value)-{'approved','issues','revised_post'}:
            raise ValueError('Неизвестные поля вердикта редактора')
        if len(value['issues'])>10:
            raise ValueError('Превышено число замечаний редактора')
        # Validate all evidence below before deriving the host decision.
        resolve_editorial_approval(value['approved'],value['issues'])
        refs=dict(self.requirements)
        issues=[];dismissed=[]
        for item in value['issues']:
            required={'field','evidence_id','kind','requirement_id','reason'}
            if (not isinstance(item,dict) or not required<=set(item)
                    or set(item)-required-{'related_evidence_ids'}):
                raise ValueError('Замечание не привязано к полю и доказательству')
            if not all(isinstance(item.get(key),str) for key in required):
                raise ValueError('Поля замечания должны быть строками')
            span=self.resolve_evidence(item.get('evidence_id'),item.get('field'))
            related=self.related_evidence(item)
            kind=item.get('kind');ref=item.get('requirement_id')
            if kind not in ('unsupported_fact','requirement_violation','quality','unsafe_advice'):
                raise ValueError('Неизвестный тип замечания')
            reason=item.get('reason')
            if not isinstance(reason,str) or not 10<=len(reason.strip())<=500:
                raise ValueError('Нет конкретной причины замечания')
            validate_reason_reference(reason,'\n'.join([span.text,*[s.text for s in related]]),(s.text for s in self.spans),
                                      '\n'.join(quote for _,quote in self.requirements))
            if ref!='none' and ref not in refs:
                raise ValueError('Редактор сослался на невыданное требование')
            if kind=='requirement_violation' and ref not in refs:
                raise ValueError('Нарушение требования не связано с исходным заданием')
            grounded={'quote':span.text,'reason':reason,'field':span.field,
                'field_role':span.field,'field_type':span.role,'evidence_id':span.ident,
                'review_signature':self.signature,
                'type':{'unsupported_fact':'invented_fact','requirement_violation':'missing_required_content',
                        'quality':'quality','unsafe_advice':'unsafe_claim'}[kind]}
            if related:
                grounded.update(related_evidence_ids=[s.ident for s in related],
                                related_quotes=[s.text for s in related])
            if ref in refs:
                grounded.update(requirement_id=ref,requirement_quote=refs[ref])
            if kind=='unsupported_fact' and (is_planning_field(span.role)
                    or not any(ch.isalnum() for ch in span.text)):
                dismissed.append({**grounded,'adjudication':'planning_or_formatting_not_business_fact'})
            else:
                issues.append(grounded)
        return issues,dismissed
