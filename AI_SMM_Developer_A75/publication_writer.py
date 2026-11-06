"""Week-level content generation and deterministic editorial checks.

Model output is untrusted. Reject short/generic entries rather than relabelling
structural scaffolding as finished content. No network or persistence here.
"""
import json
import copy
import re
import inspect
from editorial_claim_policy import BUSINESS_FACT_POLICY
from publication_schema import CALENDAR_COLUMNS, POST_FIELDS, publication_response_schema
from artifact_review import WRITING_QUALITY_POLICY

FIELDS = POST_FIELDS
# The same production contract applies to single items, batches and repairs.
# Explicit requirements remain anchored in the original request; these are
# authoring obligations, not fabricated factual evidence or a quality waiver.
DELIVERY_COMPLETENESS_POLICY = (
    'ПЕРЕД ОТВЕТОМ СВЕРЬ СО ВСЕМ ИСХОДНЫМ ЗАДАНИЕМ: число материалов, '
    'площадку, назначение, обязательные элементы, ограничения и форму выдачи. '
    'Ни один требуемый элемент нельзя заменить красивым вступлением, обещанием '
    'сделать его позже или полем без содержимого. Если нужен визуал, явно укажи '
    'какой исходный материал требуется получить/создать, и где он используется; '
    'сценарий съёмки сам по себе не является исходным материалом. '
    'Разделяй опубликованный текст и внутренние указания исполнителю: '
    'caption — готовый к публикации текст, materials — конкретный перечень '
    'необходимых исходников и подтверждений. Сверь все требования перед JSON. '
    'Проверяющий может отклонить неполный, неподтверждённый или пустой ответ. '
)

GENERIC = ('практический выпуск', 'разберите организационный вопрос',
           'покажите на экране конкретные шаги', 'уточняйте актуальные условия',
           'сохраните публикацию или задайте организационный вопрос',
           'неинформативный черновик', 'слишком кратко', 'текст будет добавлен',
           'заглушка для текста')


def _ask_publication(ask, model, system, user, schema):
    """Expose the same contract to transports and simple injected callers."""
    system += WRITING_QUALITY_POLICY+DELIVERY_COMPLETENESS_POLICY+'\nJSON Schema ответа: ' + json.dumps(schema, ensure_ascii=False, separators=(',', ':'))
    try:
        parameters = inspect.signature(ask).parameters
        supports_schema = ('json_schema' in parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()))
    except (TypeError, ValueError):
        supports_schema = False
    return (ask(model, system, user, json_schema=schema) if supports_schema
            else ask(model, system, user))


def normalize_model_post(post):
    """Lossless schema adaptation for common valid model JSON shapes.

    This runs at the model boundary, not inside the strict validator. It does
    not add missing information, change id/topic or approve editorial quality.
    Malformed/non-text arrays remain invalid so the writer can repair them.
    """
    if not isinstance(post, dict):
        return post, ()
    normalized = dict(post)
    changed = []
    for key in ('audience', 'materials', 'caption'):
        value = normalized.get(key)
        if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
            pieces = [x.strip() for x in value if x.strip()]
            if pieces:
                normalized[key] = ('\n\n' if key == 'caption' else '; ').join(pieces)
                changed.append(key)
    visual = normalized.get('visual')
    if isinstance(visual, list) and visual and all(isinstance(x, str) for x in visual):
        pieces = [x.strip() for x in visual if x.strip()]
        if pieces:
            normalized['visual'] = '\n'.join(pieces)
            changed.append('visual')
    elif isinstance(visual, dict) and isinstance(visual.get('scenes'), list):
        scenes = visual['scenes']
        lines = []
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict):
                lines = []
                break
            details = []
            for field, label in (('image', 'Кадр'), ('action', 'Действие'),
                                 ('description', 'Описание'), ('text', 'Надпись'),
                                 ('voice_over', 'Озвучка')):
                item = scene.get(field)
                if isinstance(item, str) and item.strip():
                    details.append(f'{label}: {item.strip()}')
            if not details:
                lines = []
                break
            lines.append(f'Сцена {index}: ' + '; '.join(details))
        if lines:
            normalized['visual'] = '\n'.join(lines)
            changed.append('visual')
    return normalized, tuple(changed)


def bind_post_to_slot(post, slot):
    """Bind model-authored content to host-owned plan identity and topic."""
    if not isinstance(post, dict) or not isinstance(slot, dict):
        return post, ()
    result = dict(post)
    expected_id = f"{slot['week']}/{slot['day']}"
    expected_topic = str(slot.get('topic') or '').strip()
    changed = []
    if result.get('id') != expected_id:
        result['id'] = expected_id
        changed.append('id')
    if result.get('topic') != expected_topic:
        result['topic'] = expected_topic
        changed.append('topic')
    return result, tuple(changed)


def _parse_json(raw):
    s = (raw or '').strip()
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', s, flags=re.I).strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find('{'), s.rfind('}')
        if start < 0 or end <= start:
            raise ValueError('Модель не вернула JSON-объект')
        obj = json.loads(s[start:end+1])
    if isinstance(obj, list): return obj
    if isinstance(obj, dict):
        if isinstance(obj.get('posts'), list): return obj['posts']
        if isinstance(obj.get('publications'), list): return obj['publications']
        if isinstance(obj.get('items'), list): return obj['items']
    return None


def write_week(ask, model, request, facts, strategy, slots, previous_topics=None, feedback=''):
    """Generate from verified facts and host-approved slots.

    ``strategy`` stays in this compatibility signature for existing callers.
    Generated strategy prose is a derived artifact, not evidence, and is not
    passed into the publication model.
    """
    assignments = [{'id':f"{s['week']}/{s['day']}", 'format':s['format'], 'platform':s.get('platform',''),
                    'topic':s['topic'], 'brief':s['brief'], 'audience':s.get('audience',''), 'goal':s.get('goal','')} for s in slots]
    system = (
        'Ты профессиональный SMM-редактор. Верни только JSON {"posts":[...]}, по одному '
        'объекту на каждое задание со всеми полями id, topic, visual, caption, cta, kpi, '
        'audience, measurement, materials. Соблюдай площадку, формат, тему и ID задания. '
        'Пиши на языке, запрошенном пользователем; если другой язык не задан, используй язык исходного запроса. '
        'Текст должен быть готов к публикации, конкретен, полезен и отличаться от соседних. '
        'visual — объект со списком scenes: в description каждой сцены опиши содержание и композицию '
        'кадра/слайда, явно как будущую постановку или макет; при необходимости добавь text для надписи. '
        'Формат задания и сценарий — разные данные: не копируй название формата в visual. '
        'укажи материалы, которые ещё нужно запросить или подготовить. KPI должны быть '
        'измеримыми; не придумывай рост или базовые показатели. Если подробности услуги '
        'неизвестны, не заполняй их отраслевыми догадками: выбери подтверждённую тему, '
        'нейтральный образовательный формат или вопрос для уточнения. task_analysis.unknowns — пробелы, не факты; не заполняй пробел практическими шагами. ' + BUSINESS_FACT_POLICY
    )
    user = ('Исходное задание: '+request+'\nИзвестные сведения: '+json.dumps(facts,ensure_ascii=False)+
            '\nЗадания недели: '+
            json.dumps(assignments,ensure_ascii=False)+'\nРанее запланированные рубрики: '+
            json.dumps((previous_topics or [])[-15:],ensure_ascii=False)+
            '\nОшибки предыдущей попытки: '+feedback)
    posts = _parse_json(_ask_publication(ask, model, system, user,
                        publication_response_schema(batch=True, count=len(slots))))
    if not isinstance(posts, list):
        raise ValueError('Отсутствует массив posts')
    return posts



def repair_visual(post, slot):
    """Do not fabricate a generic storyboard to pass structural validation.

    An incomplete visual must be sent back to the AI writer/editor, not silently
    replaced by a template that later fails the quality gate.
    """
    return post, False


def validate_week(posts, slots):
    problems=[]
    if not isinstance(posts,list) or not isinstance(slots,list) or len(posts)!=len(slots):
        return ['неверное число публикаций']
    from task_semantics import ai_content_review_active
    if ai_content_review_active():
        from publication_schema import publication_structure_issues
        slot_map={}
        for slot in slots:
            errors=publication_structure_issues({},slot)
            if 'Некорректный утверждённый слот' in errors:return ['Некорректный утверждённый слот']
            ident=str(slot['week'])+'/'+slot['day']
            if ident in slot_map:return ['Повторный утверждённый слот']
            slot_map[ident]=slot
        seen=set()
        for post in posts:
            problems.extend(publication_structure_issues(post))
            ident=post.get('id') if isinstance(post,dict) else None
            if not isinstance(ident,str) or ident not in slot_map or ident in seen:
                problems.append('не совпадают идентификаторы публикаций')
                continue
            seen.add(ident)
            problems.extend(publication_structure_issues(post,slot_map[ident]))
        return list(dict.fromkeys(problems))
    expected={f"{s['week']}/{s['day']}" for s in slots}
    ids=[p.get('id') for p in posts if isinstance(p,dict)]
    if (len(ids) != len(slots) or any(not isinstance(ident, str) for ident in ids)
            or len(set(ids)) != len(slots) or set(ids) != expected):
        problems.append('не совпадают идентификаторы публикаций')
    topics=[]
    captions=[]
    for post in posts:
        if not isinstance(post,dict):
            problems.append('публикация не объект'); continue
        for key in FIELDS:
            if not isinstance(post.get(key),str) or not post[key].strip():
                problems.append(f'пустое поле {key}')
        topic=post.get('topic','')
        caption=post.get('caption','')
        if not isinstance(topic,str) or not isinstance(caption,str): continue
        # Caption length depends on the platform and format. Stories and short
        # video scripts can carry the complete copy in their scene text, so a
        # character threshold only creates padding and retry loops.
        if not isinstance(post.get('visual'), str) or len(post['visual'].strip()) < 65:
            problems.append('слишком общий визуальный сценарий')
        if any(phrase in (topic+' '+caption).casefold() for phrase in GENERIC):
            problems.append('обнаружена шаблонная фраза')
        topics.append(re.sub(r'\W+',' ',topic.casefold()).strip())
        captions.append(re.sub(r'\W+',' ',caption.casefold()).strip())
    if len(set(topics))!=len(topics): problems.append('повторяются заголовки')
    if len(set(captions))!=len(captions): problems.append('повторяются тексты')
    return list(dict.fromkeys(problems))


def _cell(value):
    return re.sub(r'\s+',' ',str(value)).replace('|','/').strip()


def week_to_markdown(posts, slots):
    from platform_registry import platform_label
    lookup={p['id']:p for p in posts}
    headers=[label for label,_ in CALENDAR_COLUMNS]
    header='| '+' | '.join(headers)+' |\n|'+ '|'.join('---' for _ in headers)+'|'
    rows=[]
    for s in slots:
        p=lookup[f"{s['week']}/{s['day']}"]
        values={
            'slot':f"{s['week']}/{s['day']}",
            'platform_format':f"{platform_label(s['platform'],s.get('platform_basis','explicit'))}, {s['format']}",
            'topic':p['topic'],
            'goal':s['goal'],
            'visual':p['visual'],
            'caption':p['caption'],
            'cta':p['cta'],
            'kpi':p['kpi'],
            'measurement':p['measurement'],
            'audience':p['audience'],
            'materials':p['materials'],
        }
        cells=[values[key] for _,key in CALENDAR_COLUMNS]
        rows.append('| '+' | '.join(_cell(x) for x in cells)+' |')
    return header+'\n'+'\n'.join(rows)


def write_single(ask, model, request, facts, strategy, slot, previous_topics=(), feedback=''):
    """Generate one post from verified facts and a host-approved slot.

    ``strategy`` remains accepted for compatibility but is not model input;
    generated prose cannot ground business claims.
    """
    ident=f"{slot['week']}/{slot['day']}"
    system=(
        'Ты профессиональный SMM-редактор. Подготовь одну готовую к публикации запись. '
        'Используй запрошенный язык, по умолчанию язык исходного задания. '
        'Верни только JSON-объект с полями id,topic,visual,caption,cta,kpi,audience,measurement,materials. '
        'ID и тему сохрани из задания. Текст конкретный и полезный; visual — объект scenes, '
        'для каждой сцены заполни description (содержание и композиция), при необходимости text. '
        'Визуал — будущая постановка/макет, а не название формата; '
        'materials перечисляет то, что ещё требуется подготовить или подтвердить. KPI измеримый, без '
        'выдуманных процентов. Соблюдай площадку и формат. ' + BUSINESS_FACT_POLICY
    )
    user=json.dumps({'original_request':request,'confirmed_facts':facts,
        'approved_slot':{**slot,'id':ident},'previous_topics':list(previous_topics)[-12:],
        'rejection_feedback':feedback},ensure_ascii=False)
    raw=_ask_publication(ask,model,system,user,publication_response_schema()).strip()
    raw=re.sub(r'^```(?:json)?\s*|\s*```$', '', raw,flags=re.I).strip()
    try: obj=json.loads(raw)
    except json.JSONDecodeError:
        start,end=raw.find('{'),raw.rfind('}')
        if start<0 or end<=start: raise ValueError('ответ не содержит JSON')
        obj=json.loads(raw[start:end+1])
    if not isinstance(obj,dict): raise ValueError('ответ не является объектом')
    return obj


def validate_single(post, slot, previous_topics=()):
    from task_semantics import ai_content_review_active
    if ai_content_review_active():
        from publication_schema import publication_structure_issues
        if not isinstance(slot,dict):return ['Некорректный утверждённый слот']
        return publication_structure_issues(post,slot)
    errors=validate_week([post],[slot])
    if isinstance(post,dict):
        expected_id = f"{slot['week']}/{slot['day']}"
        if post.get('id') != expected_id:
            errors.append('публикация принадлежит другому слоту')
        if str(post.get('topic') or '').strip().casefold() != str(slot.get('topic') or '').strip().casefold():
            errors.append('тема публикации не соответствует утверждённому плану')
        title=str(post.get('topic','')).casefold().strip()
        if title and title in {str(t).casefold().strip() for t in previous_topics}:
            errors.append('тема уже использована')
        for key in ('topic','caption','visual','cta'):
            value=str(post.get(key,'')).casefold()
            if any(phrase in value for phrase in ('конкретная тема','соберите реальные вопросы аудитории',
                       'разберите организационный вопрос','практический выпуск','ракурс 1','ракурс 2')):
                errors.append('служебный шаблон в поле '+key)
    return list(dict.fromkeys(errors))


def repair_single(ask, model, request, facts, strategy, slot, previous_post, issues, previous_topics=()):
    """Repair one failed slot, constrained by its approved brief, never another task.

    No post is trusted until the caller repeats structural and independent quality
    review. This function cannot mark its own response approved. ``strategy`` is
    retained for compatibility and is intentionally excluded from model input.
    """
    ident = f"{slot['week']}/{slot['day']}"
    system = (
        'Ты строгий SMM-редактор. Исправь ОДНУ отклонённую публикацию, не другие слоты. '
        'ГРАНИЦА ФАКТОВ: единственные источники фактов о компании — original_request и confirmed_facts. '
        'Не бери утверждения из черновика, стратегии, темы, цели или предыдущей редакторской оценки как доказанные факты. '
        'Название темы и цель контента не подтверждают реальные этапы услуги, оборудование, материалы, действия сотрудников, '
        'последствия неисправности или результаты. Не заменяй отвергнутую бизнес-деталь отраслевой догадкой или советом. '
        'Если тема зависит от неизвестного процесса, напиши публикацию как вопросы владельцу/аудитории: '
        'спроси, какие этапы, материалы или условия нужно подтвердить; не перечисляй предполагаемые ответы. '
        'В materials укажи конкретный недостающий факт, который надо запросить до публикации. '
        'Верни ТОЛЬКО один JSON-объект с полями '
        'id,topic,visual,caption,cta,kpi,audience,measurement,materials. '
        f'id обязан быть строго {ident}, topic — строго «{slot["topic"]}». '
        'Исправь перечисленные конкретные замечания, сохрани полезные корректные части. '
        'Категориальные ограничения из rejection_reasons обязательны для всех полей: нельзя обходить замечание '
        'синонимичной формулировкой той же услуги, обещания или результата. '
        'Если факт о процессе, комфорте, эффективности, внимании, методике или результате не подтверждён, убери утверждение '
        'и замени вопросом владельцу, нейтральным общим советом или точным запросом подтверждения в materials. '
        'Исправляй подтверждённое замечание по смыслу; перенос или перефразирование того же '
        'неподтверждённого утверждения в другое поле не устраняет дефект. '
        'caption — готовый содержательный текст, а не описание будущей работы; '
        'visual — объект scenes с description каждой сцены и при необходимости text для надписи, '
        'подробный будущий сценарий, а не название формата или готовые медиафайлы; '
        'укажи применимость советов и не выдумывай цены, товары, услуги, обещания или факты. '
        'Пиши в естественном объёме площадки и формата. Не добавляй воду ради '
        'длины; для Stories и коротких видео распределяй смысл между подписью '
        'и текстом сцен, сохраняя все обязательные сведения. '
        'Не переписывай чужие публикации и не меняй утверждённую тему. '
        'Укажи конкретный CTA, измеримые KPI и measurement; реальные материалы перечисляй '
        'как необходимые, а не якобы существующие. Все поля обязательны. ' + BUSINESS_FACT_POLICY
    )
    payload = {
        'original_request':str(request), 'confirmed_facts':facts,
        'approved_slot':slot,
        'draft_to_repair':previous_post, 'rejection_reasons':format_repair_issues(issues)[-8:],
        'review_findings':copy.deepcopy([i for i in issues if isinstance(i,dict)]),
        'category_repair_constraints':_repair_claim_constraints(issues),
        'other_topics':list(previous_topics)[-12:],
    }
    raw = _ask_publication(ask, model, system, json.dumps(payload,ensure_ascii=False),
                          publication_response_schema())
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', str(raw).strip(), flags=re.I).strip()
    try:
        obj=json.loads(raw)
    except ValueError:
        a,z=raw.find('{'),raw.rfind('}')
        if a<0 or z<=a: raise ValueError('Редактор не вернул JSON-объект')
        obj=json.loads(raw[a:z+1])
    if isinstance(obj,dict) and isinstance(obj.get('posts'),list) and len(obj['posts'])==1:
        obj=obj['posts'][0]
    if not isinstance(obj,dict): raise ValueError('Редактор не вернул один объект публикации')
    return obj


# Stage 72: preserve correct fields when the rejection is field-specific.
# These are repair HINTS, not an approval path; all validation still belongs to
# the executor and the independent editor.
def _format_repair_issue(issue):
    """Preserve the exact disputed claim when structured review crosses into repair."""
    if not isinstance(issue,dict):
        return str(issue)
    parts=[]
    reason=str(issue.get('reason') or '').strip()
    claim=str(issue.get('quote') or issue.get('claim') or '').strip()
    if reason: parts.append(reason)
    if claim: parts.append('Точная отклонённая формулировка: «'+claim[:900]+'»')
    role=str(issue.get('field_role') or '').strip()
    if role: parts.append('Поле документа: '+role[:180])
    section=issue.get('section_path')
    if isinstance(section,list):
        path=' → '.join(str(x) for x in section if isinstance(x,str) and x.strip())
        if path: parts.append('Раздел: '+path[:300])
    evidence_id=str(issue.get('evidence_id') or '').strip()
    if evidence_id: parts.append('Идентификатор проверяемого фрагмента: '+evidence_id[:100])
    validation=issue.get('finding_validation')
    if isinstance(validation,dict) and validation.get('decision')=='confirmed':
        change=str(validation.get('required_change') or '').strip()
        if change: parts.append('Подтверждённое необходимое изменение: '+change)
    return ' '.join(parts) if parts else str(issue)


def format_repair_issues(issues):
    """Normalize strings and evidence-grounded findings at the writer boundary."""
    return [_format_repair_issue(item) for item in (issues or [])]


def repeated_rejected_claims(post, issues):
    """Return literal-retention hints for review, never a semantic verdict.

    A quoted sentence may contain both correct and incorrect parts. Keeping
    words from it does not establish that the original defect is still present.
    Paraphrases must be judged on the complete candidate and evidence ledger.
    """
    import re
    def normalize(value):
        return re.sub(r'\s+', ' ', re.sub(r'[^\w]+', ' ',
                      str(value or '').casefold().replace('ё', 'е'))).strip()

    output=' '.join(str(post.get(field) or '') for field in
                    ('caption','visual','cta','materials')) if isinstance(post,dict) else ''
    normalized_output=normalize(output)
    repeated=[]
    for issue in issues or []:
        if not isinstance(issue,dict):
            continue
        quote=str(issue.get('quote') or issue.get('claim') or '').strip()
        normalized_quote=normalize(quote)
        # Short/common fragments create false matches, so those remain for
        # the independent reviewer to assess.
        if len(normalized_quote) >= 12 and len(normalized_quote.split()) >= 2 \
                and normalized_quote in normalized_output:
            repeated.append('Сохранена точная отклонённая формулировка: «'+quote[:220]+'»')
    return list(dict.fromkeys(repeated))


def _repair_claim_constraints(issues):
    """Carry confirmed edit instructions without guessing intent from words."""
    constraints=[]
    for issue in issues or []:
        if not isinstance(issue,dict):
            continue
        proof=issue.get('finding_validation')
        if isinstance(proof,dict) and proof.get('decision')=='confirmed':
            change=str(proof.get('required_change') or '').strip()
            if change: constraints.append(change)
        if issue.get('adjudication')=='unsupported':
            constraints.append('Устрани неподтверждённое утверждение по смыслу, сохрани полезное содержание. '
                'Не заменяй его синонимом или новым неподтверждённым фактом; сверяйся с источниками задания.')
    return list(dict.fromkeys(constraints))


def repair_field_targets(issues):
    targets = set()
    legacy=[]
    for issue in issues:
        field=(issue.get('field') or issue.get('field_type')) if isinstance(issue,dict) else None
        if field=='topic':
            return ()
        if field in FIELDS:
            targets.add(field)
        else:
            legacy.append(issue)
    text = ' '.join(format_repair_issues(legacy)).casefold()
    hints = {
        'caption': ('caption', 'текст публикации', 'текст слишком', 'короче', 'орфограф',
                    'грамматическ', 'недостаточно развёрнут', 'недостаточно содерж',
                    'кратко', 'шаблонная фраза', 'нелогичн', 'фактическ', 'инструкц', 'рекомендац'),
        'visual': ('visual', 'визуал', 'сценарий', 'кадр', 'экран', 'слайд'),
        'cta': ('cta', 'призыв', 'призыва', 'переход на сайт'),
        'kpi': ('kpi', 'метрик', 'показател', 'прирост', 'процент', 'числов', 'рост на'),
        'measurement': ('measurement', 'измерени', 'статистик', 'отслеживан', 'комментари'),
        'audience': ('audience', 'аудитори'),
        'materials': ('materials', 'материал'),
    }
    for key, markers in hints.items():
        if any(marker in text for marker in markers):
            targets.add(key)
    # If the issue names only a wrong id or topic, a field patch cannot fix it.
    if any(marker in text for marker in (
            'не совпадают идентификаторы', 'чужому слоту',
            'тема публикации не соответствует', 'публикация принадлежит другому слоту')):
        return ()
    return tuple(key for key in FIELDS if key in targets and key != 'topic')


def repair_fields(ask, model, request, facts, strategy, slot, previous_post, issues, previous_topics=()):
    """Ask for a small, explicitly bounded patch; merge with the saved draft.

    No deterministic content is invented. Malformed/no-op patches fail closed,
    and the caller must repeat the full gates before checkpointing. ``strategy``
    is retained for compatibility but never used as evidence or prompt input.
    """
    if not isinstance(previous_post,dict):
        raise ValueError('Нет исходного черновика для адресной правки')
    allowed = repair_field_targets(issues)
    if not allowed:
        raise ValueError('Замечания не позволяют безопасную адресную правку полей')
    identity = f"{slot['week']}/{slot['day']}"
    if previous_post.get('id') != identity or previous_post.get('topic') != slot.get('topic'):
        raise ValueError('Исходный черновик не соответствует утверждённому слоту')
    from artifact_revision import ArtifactRevisionContract
    contract=ArtifactRevisionContract.create(previous_post,issues,allowed)
    system = (
        'Ты автор адресного исправления. Верни только replacements по выданным ID. '
        'Для каждого ID напиши полноценную содержательную замену указанного фрагмента. '
        'Программа заменит только этот фрагмент и сохранит весь остальной текст. '
        'Исправь причину отказа, сохрани цель, язык, формат и обязательное содержание задания. '
        'Нельзя исправлять ложное объяснение удалением всей пользы, общим вступлением '
        'или обещанием рассказать позже. Не меняй утверждённую тему и не создавай другие материалы. '
        'Источники фактов о компании — original_request и known_facts. Общие объяснения '
        'допустимы без приписывания компании неизвестных действий или результатов. '
        'Учитывай условия применимости советов. Не заменяй одно неподтверждённое утверждение другим. '
        'Если необходимый бизнес-факт неизвестен, объясни границу известных сведений; '
        'не выдавай отсутствие данных за разрешение придумать факт. '
        'Результат для читателя, без отчёта о правках. ' + BUSINESS_FACT_POLICY
    )
    payload = {'approved_slot':slot,'revision_spans':contract.payload(),
        'rejection_reasons':format_repair_issues(issues),
        'review_findings':copy.deepcopy([i for i in issues if isinstance(i,dict)]),
        'confirmed_edit_constraints':_repair_claim_constraints(issues),
        'previous_post':previous_post,'original_request':str(request),
        'known_facts':facts,'previous_topics':list(previous_topics)[-12:]}
    schema=contract.schema()
    raw=str(_ask_publication(ask,model,system,json.dumps(payload,ensure_ascii=False),schema)).strip()
    raw=re.sub(r'^```(?:json)?\s*|\s*```$', '', raw,flags=re.I).strip()
    try:
        output=json.loads(raw)
    except ValueError as exc:
        raise ValueError('Автор правки не вернул JSON по контракту фрагментов') from exc
    return contract.apply(output)
