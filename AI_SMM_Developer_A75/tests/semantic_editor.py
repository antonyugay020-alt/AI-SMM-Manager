"""Independent, task-scoped semantic quality review of model-written posts.

The editorial model is another heuristic, NOT a proof of factual correctness.
Fail closed if its JSON verdict cannot be parsed. Revised text is checked again
by deterministic structural/factual-risk gates before checkpointing.
"""
import json
import re
import inspect
from artifact_review import ArtifactReviewContract, deduplicate_findings, validated_review_call
from publication_schema import PUBLICATION_FIELDS, publication_field_type
from model_roles import model_family
from ollama_resilience import model_call_role
from editorial_claim_policy import BUSINESS_FACT_POLICY

REVIEW_VERSION = 'semantic_editor_v1'  # historical format; policy version is tracked separately
POLICY_VERSION = 'substantive_delivery_r82'


def audit_post(ask, model, request, facts, slot, post, previous_posts=(), previous_topics=(), report=None):
    system = (
        'Ты независимый строгий редактор SMM-контента. У тебя есть исходный запрос, '
        'его ограничения, плановый слот и готовый текст. Проверь: '
        'Язык всех пользовательских полей должен соответствовать запрошенному языку, '
        'а если другой язык не указан — языку исходного запроса. Названия моделей/брендов '
        'и общепринятые обозначения могут сохраняться; целиком другой язык является quality-дефектом. '
        '(1) полезность для конкретной аудитории и цели в пределах подтверждённых сведений: '
        'конкретный факт, ясное объяснение, вопрос или применимый нейтральный совет; '
        'не требуй бизнес-процесс, пример, действие или условие, если пользователь его не сообщил; '
        '(2) нет ложных фактов о бизнесе, непроверенных услуг, цен, отзывов и обещаний; '
        '(3) нет опасных или универсальных рекомендаций без важных оговорок; '
        'При технических, бытовых и профессиональных советах проверяй, зависят ли шаги '
        'от конкретного типа устройства, материала, местных правил или состояния вещи; '
        'не одобряй безусловный интервал обслуживания или заведомо нелогичную операцию; '
        '(4) текст полностью раскрывает тему конкретного слота и не повторяет смысл других публикаций, даже если названия разные; '
        '(5) визуальный сценарий содержит конкретную постановочную идею, сцены или экранные надписи, '
        'логично связанные с текстом; не требуй утверждать, что предложенные сцены или действия бизнеса уже существуют; '
        '(6) CTA соответствует теме, KPI измерим, не выдуманы исходные показатели; '
        '(7) нет бессмысленного совета, тавтологии, нелепых инструкций, грамматических ошибок, '
        'выдуманных названий, фиктивных экспертов, обещаний услуг или заявлений о наличии товаров. '
        'Сверяй профессиональные шаги с реальной последовательностью процесса: например, этап подготовки, промежуточной обработки и финальной отделки нельзя переставлять только ради красоты текста. '
        'Вычитай все реплики и экранные титры по отдельности: опечатки, странные слова и несогласованные падежи тоже являются ошибками. '
        'Принимай во внимание фактический формат публикации (Stories, Reels, пост) и соответствие CTA/KPI этому формату. '
        'Если KPI выражен приростом от неизвестной базы, требуй метрику без вымышленного прироста. '
        'Откажи публикациям, подходящим без изменений любому другому бизнесу; '
        'проверь смысловую связь с целями, аудиторией и стратегией. '
        'Ты только проверяешь: не переписывай публикацию и не одобряй собственную правку. '
        'id и topic принадлежат плану; замечание о теме привяжи к её полю, не меняй её сам. '
        'Верни ТОЛЬКО approved и issues по переданной схеме. '
        'Каждое замечание свяжи с точным field и выданным evidence_id из artifact_evidence. '
        'Если сравниваешь разные поля, перечисли связанные ID в related_evidence_ids; '
        'основной ID должен указывать место дефекта. '
        'Цитату вставит программа. Для requirement_violation укажи requirement_id исходного запроса; '
        'для иных типов выбери none, если отдельное требование не нужно. '
        'Unsupported_fact относится к утверждению о реальном бизнесе. Визуал, реквизит, '
        'кадры и необходимые материалы являются предложением будущей постановки; их отсутствие '
        'в источнике бизнеса само по себе не является дефектом. Если задание требует подлинных '
        'материалов, отметь нарушение именно этого требования с его ID. '
        'Грамматика, опасный совет, нелогичная сцена и конфликт с текстом всё равно проверяются. '
        'Если публикация действительно готова, approved=true и issues=[]. '
        'Формальное наличие длинного текста, KPI и кадров не является основанием для одобрения. '
        'Если найдены дефекты, approved=false и issues с конкретными причинами и точными ID. '
        'Нельзя одобрять выдуманные результаты, запрещённые пользователем возможности '
        'или общие бессодержательные публикации. Не исправляй проблему удалением важного содержания. '
        + BUSINESS_FACT_POLICY
    )
    fields={field:post[field] for field in ('topic',*PUBLICATION_FIELDS)
            if isinstance(post.get(field),str) and post[field].strip()}
    contract=ArtifactReviewContract.create(str(request),fields,
        {field:publication_field_type(field) for field in fields})
    payload = {
        'original_request': str(request),
        'artifact_evidence':contract.evidence_payload(),
        'requirement_bindings':[{'id':ident,'quote':quote} for ident,quote in contract.requirements],
        'known_facts': facts,
        'slot':slot,
        'post':post,
        'other_recent_captions':[str(p)[:550] for p in previous_posts
                                 if not str(p).startswith('Ранее утверждённая тема: ')][-8:],
        'other_approved_topics':([str(t)[:180] for t in previous_topics] +
              [str(p).split(': ',1)[1][:180] for p in previous_posts
               if str(p).startswith('Ранее утверждённая тема: ')])[-24:],
    }
    try:
        parameters=inspect.signature(ask).parameters
        strict=('json_schema' in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()))
    except (TypeError,ValueError):
        strict=False
    if strict:
        issues, dismissed = validated_review_call(ask, model, system,
            json.dumps(payload, ensure_ascii=False), contract.schema(), contract.validate, report=report)
        return (None, issues) if issues else (dict(post), [])
    raw = ask(model, system, json.dumps(payload, ensure_ascii=False)).strip()
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.I).strip()
    try:
        result = json.loads(raw)
    except ValueError:
        a, z = raw.find('{'), raw.rfind('}')
        if a < 0 or z <= a:
            return None, ['Редактор вернул неструктурированный ответ']
        try:
            result = json.loads(raw[a:z+1])
        except ValueError:
            return None, ['Редактор вернул повреждённый JSON']
    if not isinstance(result, dict) or type(result.get('approved')) is not bool:
        return None, ['Редактор не сообщил однозначный результат проверки']
    if not result['approved']:
        issues = result.get('issues')
        if not isinstance(issues,list):
            issues=[]
        return None, [str(i)[:230] for i in issues[:5]] or ['Независимая редакторская проверка не пройдена']
    revised = result.get('revised_post')
    if revised is None:
        if isinstance(result.get('issues'), list) and result['issues']:
            return None, ['Редактор подтвердил публикацию, но оставил нерешённые замечания']
        return dict(post), []
    return None, ['Редактор не может одобрить собственную правку; нужен отдельный автор и новая проверка']


def audit_post_with_independent_model(ask, author_model, reviewer_models, request,
                                     facts, slot, post, previous_posts=(),
                                     previous_topics=(), report=None,
                                     audit_function=None, adjudicate_function=None):
    """Review immutable content, then validate the applicability of typed findings.

    An unavailable primary may fail over once. A substantive finding cannot be
    removed by a bare second PASS: a different judge must validate its exact
    rationale, followed by a full independent review when a finding is dismissed.
    Confirmed findings remain blocking; uncertainty cannot authorize rewriting.
    """
    import copy
    from review_arbitration import adjudicate_findings, FindingReviewUnavailable
    raw=reviewer_models if isinstance(reviewer_models,(list,tuple)) else [reviewer_models]
    candidates=[];seen={model_family(author_model)}
    for value in raw:
        name=str(value or '').strip();family=model_family(name)
        if name and family and family not in seen:
            candidates.append(name);seen.add(family)
    if not candidates:
        raise ValueError('Нет независимого семейства моделей для проверки публикации')

    def invoke(reviewer,role):
        function=audit_function or audit_post
        parameters=inspect.signature(function).parameters
        extra={'previous_topics':previous_topics} if previous_topics and ('previous_topics' in parameters
            or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())) else {}
        if 'report' in parameters:
            extra['report'] = report
        with model_call_role(role):
            result=function(ask,reviewer,request,facts,slot,copy.deepcopy(post),previous_posts,**extra)
        if (not isinstance(result,(tuple,list)) or len(result)!=2 or not isinstance(result[1],list)
                or (not result[1] and not isinstance(result[0],dict))):
            raise ValueError('Неверный контракт результата редактора публикации')
        revised,issues=result
        if not issues and revised!=post:
            raise ValueError('Редактор не может изменить проверяемый артефакт')
        unavailable=bool(issues) and any(isinstance(issue,str) and any(token in issue.casefold() for token in (
            'неструктурирован','повреждённый json','не сообщил однозначный',
            'неполную правку','не может одобрить собственную','ollama','timeout','таймаут','ошибка соединения'))
            for issue in issues)
        if unavailable:
            raise RuntimeError('Независимая редакторская проверка публикации недоступна: '+str(issues)[:240])
        return revised,issues

    def validate_adjudication_partition(original, confirmed, dismissed):
        """An adjudicator may classify findings, never create, drop or alter them."""
        if not isinstance(confirmed,list) or not isinstance(dismissed,list):
            raise ValueError('Неверный контракт арбитража замечаний')
        def identity(row):
            if not isinstance(row,dict):
                raise ValueError('Арбитраж требует структурированные замечания')
            # The independent adjudicator adds a structured decision receipt.
            # Original content and all other fields must remain byte-for-byte
            # equivalent at the JSON-value level; no arbitrary fields are allowed.
            return json.dumps(row,ensure_ascii=False,sort_keys=True)
        source=[identity(row) for row in original]
        decided=[]
        for row,expected_decision in [(r,'confirmed') for r in confirmed]+[(r,'dismissed') for r in dismissed]:
            receipt=row.get('finding_validation') if isinstance(row,dict) else None
            if receipt is not None and (not isinstance(receipt,dict) or receipt.get('decision')!=expected_decision
                    or not isinstance(receipt.get('model'),str) or not receipt['model'].strip()
                    or not isinstance(receipt.get('finding_id'),str)):
                raise ValueError('Отсутствует подтверждённое решение арбитра')
            original_row={key:value for key,value in row.items() if key!='finding_validation'}
            decided.append(identity(original_row))
        if len(decided)!=len(source) or sorted(decided)!=sorted(source):
            raise ValueError('Арбитраж изменил или потерял исходные замечания')

    for index,reviewer in enumerate(candidates[:2]):
        try:
            revised,issues=invoke(reviewer,'reviewer_backup' if index else 'reviewer')
        except Exception as exc:
            if index==0 and len(candidates)>1:
                if report:report('publication_reviewer_failover',from_model=reviewer,
                    to_model=candidates[1],error_type=type(exc).__name__)
                continue
            raise
        final_reviewer=reviewer
        typed=bool(issues) and all(isinstance(i,dict) and i.get('field') in post
            and isinstance(i.get('quote'),str) and i['quote'] in str(post[i['field']]) for i in issues)
        if typed and (index>0 or len(candidates)<2):
            raise FindingReviewUnavailable('Нет другого независимого редактора для проверки применимости замечаний',issues)
        if index==0 and typed and len(candidates)>1:
            backup=candidates[1]
            try:
                with model_call_role('reviewer_backup'):
                    confirmed,dismissed=(adjudicate_function or adjudicate_findings)(ask,backup,request,
                        {k:v for k,v in post.items() if isinstance(v,str)},issues,
                        source_context=facts.get('evidence_context') if isinstance(facts,dict) else None,report=report)
            except FindingReviewUnavailable:
                raise
            except Exception as exc:
                raise FindingReviewUnavailable('Не завершена проверка применимости замечаний',issues) from exc
            try:
                validate_adjudication_partition(issues,confirmed,dismissed)
            except ValueError as exc:
                raise FindingReviewUnavailable('Арбитраж изменил или потерял исходные замечания', issues) from exc
            if report:report('publication_findings_resolved',primary_model=reviewer,
                validation_model=backup,confirmed_findings=confirmed,dismissed_findings=dismissed)
            if dismissed and not confirmed:
                try:
                    _,fresh_issues=invoke(backup,'reviewer_backup')
                except Exception as exc:
                    if report:
                        report('publication_full_recheck_failed', model=backup,
                            error_type=type(exc).__name__, reason=str(exc)[:500])
                    raise FindingReviewUnavailable(
                        'Нет полного независимого вердикта после снятия замечаний: '+str(exc)[:300],
                        deduplicate_findings(confirmed+issues)) from exc
                if fresh_issues:
                    pending=[{**item,'adjudication':'unavailable'} if isinstance(item,dict) else item
                             for item in fresh_issues]
                    # The second full reviewer can surface previously unseen findings.
                    # A third distinct family may adjudicate them; a raw finding is
                    # never a license to rewrite the artifact. If no third family is
                    # available, retain the exact unresolved findings for recovery.
                    typed_fresh=all(isinstance(item,dict) and item.get('field') in post
                        and isinstance(item.get('quote'),str)
                        and item['quote'] in str(post[item['field']]) for item in fresh_issues)
                    if typed_fresh and len(candidates)>2:
                        third=candidates[2]
                        try:
                            with model_call_role('reviewer_backup'):
                                next_confirmed,next_dismissed=(adjudicate_function or adjudicate_findings)(
                                    ask,third,request,
                                    {k:v for k,v in post.items() if isinstance(v,str)},fresh_issues,
                                    source_context=facts.get('evidence_context') if isinstance(facts,dict) else None,
                                    report=report)
                        except Exception as exc:
                            if report:report('publication_new_findings_adjudication_failed',
                                model=third,error_type=type(exc).__name__)
                            raise FindingReviewUnavailable(
                                'Не завершена независимая проверка новых замечаний',pending) from exc
                        try:
                            validate_adjudication_partition(fresh_issues,next_confirmed,next_dismissed)
                        except ValueError as exc:
                            raise FindingReviewUnavailable(
                                'Арбитраж изменил или потерял новые замечания', pending) from exc
                        if report:report('publication_new_findings_adjudicated',
                            reviewer=backup,validation_model=third,
                            confirmed_findings=next_confirmed,dismissed_findings=next_dismissed)
                        if next_confirmed:
                            issues=next_confirmed
                            revised=None
                            final_reviewer=backup
                            if report:report('publication_reviewer_completed',model=final_reviewer,
                                primary_model=reviewer,status='REJECTED',issue_count=len(issues))
                            return revised,issues
                        # Dismissed new findings still require a fresh full review;
                        # the adjudicator must not implicitly become that reviewer.
                    raise FindingReviewUnavailable(
                        'Новые замечания полной проверки ещё не прошли окончательную оценку',pending)
                issues=[]
                final_reviewer=backup
                revised=None if issues else dict(post)
                if revised is not None:
                    revised['_review_adjudication']={'primary_model':reviewer,'validation_model':backup,
                        'confirmed':confirmed,'dismissed':dismissed,'full_independent_recheck':True}
            else:
                issues=confirmed
        if report:report('publication_reviewer_completed',model=final_reviewer,primary_model=reviewer,
            status='PASS' if not issues else 'REJECTED',issue_count=len(issues))
        return revised,issues
    raise RuntimeError('Независимая редакторская проверка недоступна')
