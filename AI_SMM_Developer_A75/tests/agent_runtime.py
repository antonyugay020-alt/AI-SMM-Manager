"""Extensible task router. No hardcoded scene, industry or example.

The local LLM chooses an operation from the installed tool capabilities.
The application, not the model, validates inputs and performs side effects.
"""
import json, re, shutil, math
from pathlib import Path
from ollama_resilience import request_chat
from claim_support import PUBLIC_COPY_FACT_POLICY

CAPABILITIES = {
    'write': 'Write plans, marketing strategies, scripts, captions, analyses and other text in the requested language; no source file required.',
    'image_edit': 'Edit selected image using natural-language instruction, optionally with a reference image. Requires image engine.',
    'video_trim': 'Trim an existing video between start and end seconds using FFmpeg. Requires a selected video and numeric start/end.',
    'respond': 'Answer a question, explain capabilities or ask for genuinely missing information.'
}

TASK_PLAN_SCHEMA = {'type':'object','additionalProperties':False,
    'required':['action','answer','start','end','content_contract'],
    'properties':{'action':{'type':'string','enum':list(CAPABILITIES)},
        'instruction':{'type':'string'},'answer':{'type':'string'},
        'start':{'type':['number','null']},'end':{'type':['number','null']},
        'content_contract':{'type':['object','null'],'additionalProperties':False,
            'properties':{'mode':{'type':'string','enum':['factual','fictional','hypothetical']},
                'permission_quote':{'type':'string'}},'required':['mode']}}}


def parse_task_plan_response(raw):
    """Accept a JSON object or one whole fenced JSON object, never prose or fragments."""
    if not isinstance(raw, str):
        raise ValueError('AI не вернул JSON-план')
    text = raw.strip().lstrip('\ufeff').strip()
    fenced = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', text, flags=re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('AI не вернул JSON-объект плана')
    return value


def validate_task_plan(value):
    if (not isinstance(value,dict) or set(TASK_PLAN_SCHEMA['required'])-set(value)
            or set(value)-set(TASK_PLAN_SCHEMA['properties'])
            or not isinstance(value.get('action'),str)
            or value.get('action') not in CAPABILITIES
            or not isinstance(value.get('answer'),str)
            or ('instruction' in value and not isinstance(value['instruction'],str))):
        raise ValueError('AI не вернул план действия по контракту')
    if value['action'] in ('write','respond') and not value['answer'].strip():
        raise ValueError('Нет содержимого выбранного текстового действия')
    for key in ('start','end'):
        number=value[key]
        if number is not None and (type(number) not in (int,float) or not math.isfinite(number) or number<0):
            raise ValueError('Некорректная граница видео: '+key)
    if value['action']=='video_trim' and (value['start'] is None or value['end'] is None
            or value['end']<=value['start']):
        raise ValueError('Для обрезки нужен полный возрастающий интервал')
    contract=value['content_contract']
    if contract is not None and (not isinstance(contract,dict)
            or set(contract)-{'mode','permission_quote'}
            or contract.get('mode') not in ('factual','fictional','hypothetical')
            or ('permission_quote' in contract and not isinstance(contract['permission_quote'],str))):
        raise ValueError('Некорректная декларация фактической политики')
    return dict(value)

def available_tools():
    return {'local_media_tasks':['image_resize']+(['video_cut','video_format','video_montage','video_from_images'] if shutil.which('ffmpeg') and shutil.which('ffprobe') else []),
            'text': True, 'image_local_legacy': True, 'image_cloud_optional': True,
            'video_trim_ffmpeg': bool(shutil.which('ffmpeg')),
            'voice_transcription': __import__('os').name=='nt', 'voice_input_backend':'Windows voice typing in composer', 'advanced_local_image_edit': False}

def is_discussion_request(text):
    return bool(re.match(r'^(что|чем|как|почему|зачем|какой|какая|какие|можно ли|стоит ли|расскажи|объясни|сравни|подскажи|посоветуй|а если|интересно|например)\b',
                         str(text or '').strip(),re.I))


def explicit_creation_request(text):
    """Conservative guard: discussion/questions never trigger document writes."""
    message = (text or '').strip().lower()
    if not message:
        return False
    if is_discussion_request(message):
        return False
    return bool(re.search(r'\b(создай|сделай|разработай|подготовь|составь|напиши|сформируй|сохрани|запиши|оформи|построй|сгенерируй|переработай|переделай)\b', message))


def is_document_request(text):
    return bool(re.search(r'стратег|контент.план|план продвижени|маркетингов.*план|сценари|программ.*продвижени', text or '', re.I))


def validate_document(request, answer):
    """Cheap format sanity check; generic artifacts may legitimately be short."""
    body = (answer or '').strip()
    if not body:
        return False, 'пустой результат'
    if re.search(r'^(?:вот пример текста|вот текст для социальных сетей)', body, re.I):
        return False, 'ответ выглядит как шаблон публикации, а не запрошенный документ'
    if re.search(r'стратег', request or '', re.I):
        # Format checks establish section coverage. Character count cannot
        # establish usefulness; independent editorial review owns that gate.
        headings = sum(bool(re.search(pattern, body, re.I)) for pattern in (
            r'цел[ьи]|задач[иа]', r'аудитори|клиент', r'позиционирован|ценност|предложени',
            r'направлени|приоритет|канал', r'этап|реализац|внедрен'))
        if headings < 3:
            return False, 'не раскрыты ключевые разделы стратегии'
    if 'исходное задание:' in (request or '').casefold() and re.search(r'стратег', request, re.I):
        low = body.casefold()
        required = (
            ('аудитория', r'аудитори|сегмент|клиент'),
            ('цели', r'цел[ьи]|задач'),
            # Strategy mechanisms can be described as content distribution,
            # publication formats or platform tactics without using the exact
            # noun "продвижение". The independent reviewer still checks that
            # the section is meaningful rather than a bare keyword.
            ('продвижение', r'продвижени|канал|инструмент|контент|публикац|формат|распростран|дистрибуц|площадк'),
            ('метрики', r'kpi|метрик|охват|конверси|обращени|сохранени'),
            ('план действий', r'этап|внедрени|недел|план действий'),
        )
        missing = [title for title, rx in required if not re.search(rx, low, re.I)]
        if missing:
            return False, 'В стратегии не раскрыты: '+', '.join(missing)
        if re.search(r'бюджет', request, re.I) and not re.search(r'бюджет|распределени[ея] средств|расход\w*', low):
            return False, 'Не раскрыто распределение рекламного бюджета'
    return True, ''


def conversational_answer(message, context, model='qwen3:8b', *, report=None):
    """Free-form conversation, not constrained by the tool-router JSON schema."""
    system = ("Ты профессиональный русскоязычный SMM-консультант и собеседник. "
        "Отвечай непосредственно на заданный вопрос, содержательно и естественно. "
        "Обсуждай идеи, сравнивай подходы, объясняй возможности; не требуй командной формулировки. "
        "Не утверждай, что выполнил действие, создал документ, скачал файл или изменил проект. "
        "Если вопрос о возможностях, честно различай теоретические навыки и подключённые инструменты. "
        "Используй факты текущего проекта и историю беседы, не придумывай фактов. "
        "При нехватке данных для совета можешь уточнить главное, но сначала ответь на вопрос. "
        "Верифицированные материалы и история проекта: только факты из запроса и verified_project_materials "
        "можно считать подтверждёнными; existing_documents — прежние результаты для непрерывности, "
        "их бизнес-факты не считать подтверждёнными без источника. Не передавай и не анализируй бинарные "
        "эталонные изображения автоматически. Контекст проекта: " + json.dumps({k:context.get(k) for k in
        ('project','category','description','verified_project_materials','existing_documents','memory_policy','capabilities')},ensure_ascii=False))
    messages = [{'role':'system','content':system}]
    for item in context.get('recent_messages', [])[-12:]:
        role = item.get('role')
        if role in ('user','assistant'):
            messages.append({'role':role,'content':str(item.get('content',''))[:2400]})
    if not messages or messages[-1].get('role') != 'user' or messages[-1].get('content') != message:
        messages.append({'role':'user','content':message})
    payload={'model':model,'stream':False,'think':False,'messages':messages,
        'options':{'num_ctx':8192,'temperature':0.5}}
    data=request_chat(payload,stage='свободный ответ',timeout=300,attempts=1,report=report)
    answer=str(data.get('message',{}).get('content') or '').strip()
    if not answer:
        raise ValueError('Локальная модель не вернула текст ответа.')
    return {'action':'respond','instruction':message,'answer':answer}


def plan_task(message, context, model='qwen3:8b', *, report=None):
    if not explicit_creation_request(message) and not re.search(r'\b(обрежь|вырежи|смонтируй|удали|замени|измени|обработай|скачай|найди)\b', message, re.I):
        return conversational_answer(message, context, model,report=report)
    instruction = ('You are a tool dispatcher for an AI content studio. Select the actual user intent, not a canned example. '
        'Return ONLY a JSON object with keys: action (write|image_edit|video_trim|respond), '
        'answer (for write/respond only; empty for media actions), '
        'start (number or null), end (number or null), content_contract (object or null). '
        'content_contract declares the requested truth policy, not an industry or genre template. '
        'Use {"mode":"factual"} by default. Only when the user explicitly authorizes invented or '
        'hypothetical content, use {"mode":"fictional" or "hypothetical","permission_quote":'
        '"exact verbatim instruction granting that permission"}. A request for useful marketing copy '
        'does not authorize invented facts. Never classify an instruction banning invention as permission. '
        'For write: produce the complete requested content in answer, in the user language, structured and useful. '
        'Use explicit project names, categories, audience details, and verified services from the request when they help the deliverable; do not misclassify them as invented or forbidden facts. '
        'Make the body substantively useful for its purpose: include concrete, actionable detail grounded in the request or reliable general knowledge, not only a hook, generic praise, and a CTA. Cover every requested element. '
        'If context has pending_text_candidate, revise that saved rejected draft against pending_text_candidate.issues while preserving the original task; the draft is not factual evidence. If context has pending_original_request, use clarification_answer only when a clarification was actually requested; an explicit resume continues the original task. Use existing_documents and recent_messages as project memory. When facts are insufficient choose respond; never mark a generic sample post as a strategy. Do not claim a document is complete when you only asked questions. '
        'Before writing a specific business strategy or content plan, assess whether the supplied project context and request contain enough business-specific information for an actionable result. If critical information is missing, choose respond and ask only the 2-4 highest-impact questions in one concise message; do not invent company facts or generate generic filler. If the user explicitly requests a draft with assumptions, produce one and clearly label assumptions. Do not demand production shot lists when the user requested high-level strategy. For respond: answer clearly in answer. For image_edit: do not pretend to have edited; leave answer empty. '
        'For video_trim: extract explicit seconds, do not invent times. '
        'Use write ONLY when user explicitly commands creation of a deliverable. Questions, comparisons, brainstorming and facts shared are respond, never write, even if they mention strategy or content plans. If pending clarification exists but user asks an unrelated question, answer it and retain the pending task. '
        'Never claim a tool ran, a file was saved, or a media edit happened. '
        'Factual authority: original user request and explicitly verified project sources in context.evidence_context or verified_project_materials. '
        'existing_documents are continuity memory, not factual evidence. Do not infer missing services, methods, process, prices, schedules, outcomes or experience from the industry. '
        +PUBLIC_COPY_FACT_POLICY+'\nNo fixed backgrounds, places, styles or templates. Context: '+json.dumps(context,ensure_ascii=False))
    payload={'model':model,'stream':False,'format':TASK_PLAN_SCHEMA,'think':False,
        'messages':[{'role':'system','content':instruction},{'role':'user','content':message}],
        'options':{'num_ctx':8192,'temperature':0.2}}
    data=request_chat(payload,stage='маршрутизация и создание отдельного материала',
                      timeout=300,attempts=1,report=report)
    raw=data.get('message',{}).get('content','')
    result=validate_task_plan(parse_task_plan_response(raw))
    result['instruction']=message  # always preserve user's exact text
    return result


def route_text_intent(message, context, model='qwen3:8b', *, report=None):
    """Classify an ordinary message without asking the router to draft content.

    The router chooses an action; the author owns the complete text. This
    prevents a dispatch payload (including a copy of the user instruction)
    from being presented to the editor as a finished deliverable.
    """
    if not explicit_creation_request(message) and not re.search(
            r'\b(обрежь|вырежи|смонтируй|удали|замени|измени|обработай|скачай|найди)\b',
            message, re.I):
        return conversational_answer(message,context,model,report=report)
    route_schema={'type':'object','additionalProperties':False,
        'required':['action','answer','start','end','content_contract'],
        'properties':{
            'action':{'type':'string','enum':list(CAPABILITIES)},
            'answer':{'type':'string'},
            'start':{'type':['number','null']},'end':{'type':['number','null']},
            'content_contract':TASK_PLAN_SCHEMA['properties']['content_contract']}}
    instruction=(
        'You are an intent router, NOT a content author. Choose the actual user action. '
        'Return only the structured routing decision. For write, answer MUST be empty: '
        'a separate content author creates the deliverable. For respond, answer must '
        'contain a useful direct answer. For media actions, answer must be empty. '
        'Do not repeat the user instruction in answer. Do not claim actions occurred. '
        'Content contract defaults to {"mode":"factual"}; fictional or hypothetical '
        'requires an exact permission_quote from the user request. '
        'A question or explanation is respond, never write. A request to create a '
        'document or post is write. Media actions require actual inputs and tools. '
        'Context: '+json.dumps(context,ensure_ascii=False))
    payload={'model':model,'stream':False,'format':route_schema,'think':False,
        'messages':[{'role':'system','content':instruction},
                    {'role':'user','content':message}],
        'options':{'num_ctx':8192,'temperature':0.1}}
    data=request_chat(payload,stage='классификация намерения',
                      timeout=300,attempts=1,report=report)
    value=parse_task_plan_response(data.get('message',{}).get('content',''))
    if set(value)!={'action','answer','start','end','content_contract'}:
        raise ValueError('Маршрутизатор не вернул отдельный контракт намерения')
    # A router has no authority to downgrade an unambiguous user command to
    # produce a document into chat.  Windows acceptance showed two real
    # single-publication briefs receiving 'respond' plus an unreviewed draft.
    # Discard the entire router-produced draft and send the original request
    # to the independent author and quality-review pipeline.
    if value.get('action') == 'respond' and explicit_creation_request(message):
        if report:
            report('routing_action_corrected',reason='explicit_creation_misclassified_as_chat')
        value = {'action':'write', 'answer':'', 'start':None, 'end':None,
                 'content_contract':{'mode':'factual'}}
    if value.get('action')=='write':
        if value.get('answer')!='':
            # Some models put a full draft in a routing-only answer. Discard
            # those bytes; ask ONCE for a valid route instead of presenting
            # router text as an independently authored/approved artifact.
            if report:
                report('routing_contract_retry',reason='unexpected_router_draft')
            retry_payload={**payload,'messages':payload['messages']+[{'role':'user',
                'content':'Верни только решение о маршруте по JSON-схеме. При action=write answer строго пустая строка. Никакого текста публикации.'}]}
            retry=request_chat(retry_payload,stage='повторная проверка маршрута',
                               timeout=300,attempts=1,report=report)
            value=parse_task_plan_response(retry.get('message',{}).get('content',''))
            if (set(value)!={'action','answer','start','end','content_contract'} or
                    value.get('action')!='write' or value.get('answer')!=''):
                raise ValueError('Маршрутизатор попытался передать текст вместо решения о маршруте')
        checked=validate_task_plan({**value,'answer':'awaiting_author'})
        checked['answer']=''
    else:
        checked=validate_task_plan(value)
    checked['instruction']=message
    return checked


def write_requested_artifact(message,context,model='qwen3:8b',*,report=None):
    """Author for an already resolved semantic action. No second intent router."""
    schema={'type':'object','additionalProperties':False,'required':['answer','content_contract'],
        'properties':{'answer':{'type':'string','minLength':1},
                      'content_contract':TASK_PLAN_SCHEMA['properties']['content_contract']}}
    instruction=('Ты автор уже порученного материала. Решение о выполнении принято AI в диалоге. '
        'Создай полный результат по исходному заданию и выбранному разделу, независимо от формы реплики. '
        'Не требуй командных глаголов. Не сообщай о сохранении, проверке или выполнении инструментов. '
        'При revision_document доработай выбранный текст, сохраняя незатронутые части. Это непроверенный '
        'черновик, его утверждения о бизнесе не считаются фактами. Исходный запрос, описание проекта '
        'и verified_project_materials являются источниками; не выдумывай сведения о бизнесе. '
        'Соблюдай точный состав, объём, язык и ограничения поручения. Не подменяй результат общим советом. '
        'content_contract описывает factual/fictional/hypothetical режим; вымысел разрешён только точной '
        'цитатой permission_quote из пользовательского задания. '+PUBLIC_COPY_FACT_POLICY)
    response=request_chat({'model':model,'stream':False,'think':False,'format':schema,
        'messages':[{'role':'system','content':instruction},{'role':'user','content':json.dumps(
            {'request':message,'execution_context':context},ensure_ascii=False)}],
        'options':{'num_ctx':32768,'temperature':0.3}},stage='создание порученного материала',timeout=300,attempts=1,report=report)
    value=json.loads(response.get('message',{}).get('content',''))
    if not isinstance(value,dict) or set(value)!={'answer','content_contract'}:raise ValueError('Автор не вернул материал по контракту')
    return validate_task_plan({**value,'action':'write','instruction':message,'start':None,'end':None})
