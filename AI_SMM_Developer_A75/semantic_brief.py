"""Model-driven analysis of the COMPLETE user request; no industry keyword taxonomy.

The original request remains the source of truth. Extracted fields are advisory and
never authorize claims absent from the request. A model outage is not a reason to
ask canned questions or invent missing information.
"""
import json
import re
from publication_writer import _parse_json
from publication_schedule import days_for_frequency
from platform_registry import explicit_platforms, canonicalize_platforms
from deliverable_contract import planning_request_contract
from task_semantics import ai_content_review_active


_STOPWORDS = frozenset('как что зачем почему когда нужно важно правильно выбрать подходящие для при в на по с со и или от к из это эти новый новые если чтобы где без об о который какие какая какое уникальная уникальный уникальное полезная полезный полезное конкретная конкретный тема темы тематика материал материалы недели неделя номер вариант варианта пункт раздел для клиентов раздел'.split())

def topic_fingerprint(topic):
    """Lightweight Russian topic overlap signal, not semantic fact verification."""
    text = re.sub(r'[^\w\s]', ' ', str(topic or '').casefold())
    return frozenset(w[:5] if len(w)>5 else w for w in text.split() if len(w) > 2 and w not in _STOPWORDS and not w.isdecimal())

def topic_repeats(topic, old_topics):
    if ai_content_review_active():
        return any(str(topic or '').strip()==str(previous or '').strip() for previous in old_topics)
    compact = re.sub(r'\W+', ' ', str(topic or '').casefold()).strip()
    tokens = topic_fingerprint(topic)
    for previous in old_topics:
        other = re.sub(r'\W+', ' ', str(previous or '').casefold()).strip()
        if compact == other:
            return True
        prev_tokens = topic_fingerprint(previous)
        if len(tokens) >= 3 and len(prev_tokens) >= 3:
            overlap = len(tokens & prev_tokens) / len(tokens | prev_tokens)
            if overlap >= 0.76:
                return True
    return False


def analyze(ask, model, request, project_context=''):
    system = ('Ты анализатор произвольных пользовательских задач. Прочитай ВЕСЬ текст, '
              'включая ограничения, отрицания, вложенные пункты и ожидаемые файлы. '
              'Верни только JSON-объект с ключами: intent, business, audience, channels, '
              'period, budget, goals, requirements, prohibitions, unknowns, deliverables, '
              'critical_questions, channels_basis. Массивы содержат короткие ТОЧНЫЕ выдержки/перефразы '
              'запроса. Не выдумывай фактов и не делай выводов из названия отрасли. '
              'В project_context различай evidence_context, existing_documents и сообщения истории: '
              'только исходный запрос и подтверждённые источники могут подтверждать бизнес-факты; '
              'старые AI-документы служат для непрерывности проекта, но их факты нужно заново подтвердить. '
              'Если площадка не указана пользователем, предложи подходящую площадку и укажи channels_basis="recommendation"; '
              'не выдавай рекомендацию за выбор пользователя. Если площадка названа, укажи channels_basis="explicit". '
              'Если прочее поле не задано, оставь пустую строку или пустой массив. '
              'critical_questions — только действительно блокирующие выполнение вопросы; '
              'если можно продолжить с оговоркой, вопросов нет. Никаких списков отраслей.')
    raw = ask(model, system, 'Полный запрос:\n'+request+'\nКонтекст проекта (не заменяет запрос):\n'+project_context)
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        begin, end = raw.find('{'), raw.rfind('}')
        if begin < 0 or end <= begin: raise ValueError('Семантический анализ не вернул JSON')
        obj = json.loads(raw[begin:end+1])
    if not isinstance(obj, dict): raise ValueError('Семантический анализ должен быть объектом')
    result={key:obj.get(key, [] if key in ('goals','requirements','prohibitions','unknowns','deliverables','critical_questions') else '')
            for key in ('intent','business','audience','channels','period','budget','goals','requirements','prohibitions','unknowns','deliverables','critical_questions')}
    from task_semantics import current_semantics
    literal=[] if current_semantics() is not None else explicit_platforms(request)
    if literal:
        result['channels']=literal
        result['channels_basis']='explicit'
    else:
        result['channels']=canonicalize_platforms(result.get('channels'))
        result['channels_basis']='explicit' if obj.get('channels_basis')=='explicit' else 'recommendation'
    return result


def _platform_name(value):
    """Normalize the semantic model's scalar/list channel value for the UI."""
    if isinstance(value, list):
        return ', '.join(str(v).strip() for v in value if str(v).strip()) or 'площадка из запроса'
    if isinstance(value, str):
        return value.strip() or 'площадка из запроса'
    return 'площадка из запроса'



def propose_topic_replacement(ask, model, request, strategy, known, slot, previous_topics,
                              review_issues, rejected_topics=(), return_all=False):
    """Replan a single *unaccepted* slot from several genuinely distinct proposals.

    The original request is authoritative. Candidate briefs are NOT publications:
    the executor must write each candidate, apply deterministic checks, and obtain
    independent semantic approval before changing any persisted plan or checkpoint.
    Legacy one-topic replies remain supported (e.g. smaller local models).
    """
    if not isinstance(slot, dict):
        raise ValueError('Нет исходного слота')
    if not ai_content_review_active() and str(slot.get('topic') or '').casefold() in str(request).casefold():
        raise ValueError('Тема явно названа пользователем, менять её автоматически нельзя')
    system = (
        'Ты редактор контент-плана, а не автор готовых публикаций. Предыдущая тема '
        'противоречила исходному заданию или не могла пройти содержательную редактуру. '
        'Сохрани конкретную цель задания; не меняй явно закреплённую пользователем тему: если изменение запрещено, верни keep=true. Каждая альтернатива должна помогать именно этому результату, '
        'а не заменять его соседним поводом или ситуацией. Если для конкретного совета не хватает '
        'подтверждённых характеристик товара/услуги, используй нейтральные критерии выбора или вопрос аудитории, '
        'не придумывая эти характеристики. '
        'Предложи от 3 до 5 РАЗНЫХ конкретных и полезных альтернатив для ОДНОГО '
        'НЕЗАВЕРШЁННОГО слота. Сохрани неделю, день, формат и изначальные цели пользователя. '
        'Используй только специализацию бизнеса, описанную в исходном запросе; '
        'не придумывай новый ассортимент, услуги, статистику, обещания или этапы реальных процессов. '
        'Альтернативы должны быть различны между собой и не повторять темы или подходы '
        'из уже сохранённого плана и ранее отклонённых предложений. '
        'Учитывай замечания независимого редактора, избегай повторов и узких вариаций '
        'старых тем. Верни ТОЛЬКО JSON: '
        '{"alternatives":[{"topic":"...","brief":"...","goal":"...","audience":"..."},...]}. '
        'Если полностью безопасных альтернатив нет, верни {"keep":true}. '
        'Никаких готовых постов и жёстко зашитых отраслевых примеров.'
    )
    forbidden = [str(t).strip() for t in list(previous_topics) + list(rejected_topics) if str(t).strip()]
    payload = {'original_request':str(request)[:8500], 'known_facts':known,
               'request_contract':planning_request_contract(request),
               'strategy_excerpt':str(strategy)[:3500], 'failed_slot':slot,
               'independent_editor_issues':[str(x) for x in review_issues][-8:],
               'existing_topics':forbidden[-55:],
               'rejected_proposals':[str(t) for t in rejected_topics][-12:]}
    raw=str(ask(model, system, json.dumps(payload,ensure_ascii=False))).strip()
    raw=re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.I).strip()
    try:
        obj=json.loads(raw)
    except ValueError:
        start,end=raw.find('{'),raw.rfind('}')
        if start<0 or end<=start: raise ValueError('Нет ответа редактора плана')
        obj=json.loads(raw[start:end+1])
    if not isinstance(obj,dict) or obj.get('keep') is True:
        raise ValueError('Редактор не предложил альтернативную тему')
    raw_candidates=obj.get('alternatives', [obj] if 'topic' in obj else [])
    if not isinstance(raw_candidates,list):
        raise ValueError('Альтернативные темы должны быть массивом JSON')
    candidates=[]
    for candidate in raw_candidates[:8]:
        if not isinstance(candidate,dict):
            continue
        topic=re.sub(r'\s+',' ',str(candidate.get('topic') or '')).strip()
        if (not topic or (not ai_content_review_active() and len(topic)<12)
                or topic==str(slot.get('topic') or '').strip()):
            continue
        if topic_repeats(topic,forbidden) or topic_repeats(topic,[c['topic'] for c in candidates]):
            continue
        if any(not isinstance(candidate.get(k),str) or not candidate[k].strip()
               for k in ('brief','goal','audience')):
            continue
        replacement=dict(slot)
        replacement.update({k:candidate[k].strip() for k in ('brief','goal','audience')})
        replacement['topic']=topic
        candidates.append(replacement)
    if not candidates:
        raise ValueError(f'Предложенная тема повторяет прежний контент или неполна: {len(raw_candidates)} предложений')
    return candidates if return_all else candidates[0]


def _topic_candidates(raw):
    """Normalize common list/object envelopes without trusting model slot metadata."""
    source=str(raw or '').strip()
    source=re.sub(r'^```(?:json)?\s*|\s*```$', '', source, flags=re.I).strip()
    try:
        parsed=json.loads(source)
    except (ValueError, TypeError):
        begin,end=source.find('{'),source.rfind('}')
        if begin<0 or end<=begin:
            return []
        try:
            parsed=json.loads(source[begin:end+1])
        except (ValueError, TypeError):
            return []
    if isinstance(parsed,list):
        return parsed
    if isinstance(parsed,dict):
        for key in ('posts','topics','items','publications','alternatives','suggestions','results'):
            if isinstance(parsed.get(key),list):
                return parsed[key]
        if any(isinstance(parsed.get(key),str) for key in ('topic','title','subject')):
            if not parsed.get('topic'):
                parsed=dict(parsed,topic=parsed.get('title') or parsed.get('subject'))
            return [parsed]
    return []


def _goal_fallback(brief):
    goals=brief.get('goals') if isinstance(brief,dict) else None
    if isinstance(goals,list):
        return '; '.join(str(x).strip() for x in goals if str(x).strip()) or 'Поддержать цель исходного запроса'
    if isinstance(goals,str) and goals.strip():
        return goals.strip()
    return 'Поддержать цель исходного запроса'


def plan_slots(ask, model, request, strategy, brief, weeks, per_week=3, on_week=None,
               existing_slots=None, first_pass_only=False):
    """Plan host-owned slots; randomized acceptance can measure one first pass."""
    if type(first_pass_only) is not bool:
        raise TypeError('first_pass_only должен быть булевым значением')
    from calendar_planner import formats_for_platform
    platforms=canonicalize_platforms(brief.get('channels'))
    if len(platforms)!=1:
        raise ValueError('Для одного календаря требуется ровно одна подтверждённая или предложенная площадка')
    platform=platforms[0]
    platform_basis=str(brief.get('channels_basis') or 'explicit')
    formats=formats_for_platform(platform)
    days=days_for_frequency(per_week)
    request_contract=planning_request_contract(request,weeks*per_week)
    slots=[]
    existing_slots=existing_slots or []
    for week in range(1,weeks+1):
        cached=[s for s in existing_slots if isinstance(s,dict) and s.get('week')==week]
        previous_topics=[str(s.get('topic') or '') for s in slots]
        if (len(cached)!=per_week or
            any(s.get('day')!=days[i] or s.get('platform')!=platform or
                s.get('platform_basis','explicit')!=platform_basis or
                (not str(s.get('topic') or '').strip() or (not ai_content_review_active() and len(str(s.get('topic') or '').strip())<12)) or
                topic_repeats(s.get('topic'),previous_topics)
                for i,s in enumerate(cached)) or
            any(topic_repeats(s.get('topic'),[p.get('topic') for p in cached[:i]])
                for i,s in enumerate(cached))):
            break
        slots.extend(cached)
    resume_week=len(slots)//per_week+1

    def accept_topic(post, week, slot_index, seen):
        if not isinstance(post,dict):
            return None,'ответ темы должен быть объектом'
        topic=re.sub(r'\s+',' ',str(post.get('topic') or '')).strip()
        if not topic or (not ai_content_review_active() and len(topic)<12):
            return None,'тема пустая или слишком короткая'
        if topic_repeats(topic,seen):
            return None,f'повтор или слишком близкая тема «{topic}»'
        # Platform and schedule are host-owned contract fields. A model may
        # phrase its format in another language; it cannot change capability.
        slot={
            'week':week,'day':days[slot_index],'platform':platform,
            'platform_basis':platform_basis,
            'format':formats[(week-1+slot_index)%len(formats)],
            'topic':topic,
            'brief':str(post.get('brief') or topic).strip(),
            'goal':str(post.get('goal') or _goal_fallback(brief)).strip(),
            'audience':str(post.get('audience') or brief.get('audience') or
                           'Аудитория из исходного задания').strip(),
        }
        return slot,None

    for week in range(resume_week,weeks+1):
        errors=[]
        week_slots=[]
        batch_attempt_limit=1 if first_pass_only else 3
        for attempt in range(1,batch_attempt_limit+1):
            needed=per_week-len(week_slots)
            if needed<=0: break
            next_index=len(week_slots)
            open_slots=[{'day':days[i],'format':formats[(week-1+i)%len(formats)]}
                        for i in range(next_index,per_week)]
            system=(
                f'Ты главный редактор. Создай {needed} новые конкретные темы для недели {week}. '
                'Пиши на языке исходного запроса, если пользователь не указал другой язык. '
                'Формат публикации и день уже назначены системой и не выбираются моделью. Верни JSON '
                '{"posts":[{"topic":"...","brief":"...","goal":"...","audience":"..."},...]}. '
                'Не повторяй предыдущие темы, не придумывай факты о бизнесе, этапы занятий, методы и результаты. '
                +('Каждая тема должна быть содержательной и отличаться по смыслу. ' if ai_content_review_active() else 'Каждая тема должна содержать минимум 12 символов и отличаться по смыслу. ')
                +'Все темы строго соответствуют специализации бизнеса, ограничению ассортимента и контексту клиента; не расширяй профиль на соседние отрасли. '
                'Сначала прочитай request_contract: он сохраняет исходные цели без сокращения. '
                'В brief опиши содержательную пользу, которая раскрывает эти цели. CTA не заменяет содержание. '
                'В поле goal укажи результат из полного запроса; не подменяй его соседним поводом. '
                'Если для полезного выбора не хватает подтверждённых характеристик, используй нейтральные критерии или вопрос аудитории, не выдумывай свойства товара/услуги. '
                'brief.unknowns — явно неизвестные сведения: не превращай их в факты; для неизвестного процесса выбирай вопрос или чек-лист уточнений. '
                'Не планируй неподтверждённые преимущества и обещания результата. '
                'Для каждого пункта выбери ДРУГОЙ конкретный предмет обсуждения; не считай замену пары слов новой темой. '
                'Исправь все проблемы, перечисленные в запросе, если они есть.'
            )
            user=json.dumps({
                'request':request,'request_contract':request_contract,'brief':brief,'strategy':strategy[:6500],
                'week':week,'open_slots':open_slots,
                'previous_topics':[s['topic'] for s in slots+week_slots],
                'retry':attempt,'previous_errors':errors[-5:],
                'instruction':'Предложи конкретные полезные темы, отличающиеся объектом и ситуацией.'
            },ensure_ascii=False)
            try:
                proposed=_topic_candidates(ask(model,system,user))
                if not proposed:
                    raise ValueError('не найден список тем в ответе модели')
                seen=[s['topic'] for s in slots+week_slots]
                invalid=[]
                for post in proposed:
                    if len(week_slots)>=per_week: break
                    candidate,issue=accept_topic(post,week,len(week_slots),seen)
                    if issue:
                        invalid.append(issue);continue
                    week_slots.append(candidate);seen.append(candidate['topic'])
                if len(week_slots)<per_week:
                    errors.extend(invalid or [f'модель вернула {len(proposed)} предложений, принято {len(week_slots)} из {per_week}'])
            except (ValueError,TypeError,KeyError) as exc:
                errors.append(str(exc))

        # A synthetic first-pass benchmark records an incomplete topic batch as
        # a failure. Production retains bounded single-slot completion below.
        if first_pass_only and len(week_slots)<per_week:
            raise ValueError(f'Неделя {week}: первый ответ дал {len(week_slots)} из {per_week} тем')
        # Fill only missing slots; valid sibling topics from earlier batches
        # remain intact and are supplied as exclusions to each repair request.
        while len(week_slots)<per_week:
            slot_index=len(week_slots)
            slot_errors=[]
            for individual_attempt in range(1,4):
                sys_prompt=(
                    'Ты главный редактор. Предложи одну новую конкретную тему для незаполненного слота. '
                    'Пиши на языке исходного запроса, если пользователь не указал другой язык. '
                    'Формат и площадка уже назначены системой; не возвращай их. Верни JSON '
                    '{"posts":[{"topic":"...","brief":"...","goal":"...","audience":"..."}]}. '
                    'Тема должна отличаться по предмету и ситуации от всех запрещённых тем. '
                    'Не выдумывай бизнес-факты, свойства, процессы или результаты.'
                )
                usr=json.dumps({
                    'request':request,'request_contract':request_contract,'week':week,'day':days[slot_index],
                    'format':formats[(week-1+slot_index)%len(formats)],
                    'platform':platform,'slot':{'day':days[slot_index],
                    'platform':platform,'format':formats[(week-1+slot_index)%len(formats)]},
                    'brief':brief,'strategy':strategy[:3500],
                    'forbidden_topics':[s['topic'] for s in slots+week_slots],
                    'errors':errors[-3:]+slot_errors[-2:],'attempt':individual_attempt,
                },ensure_ascii=False)
                try:
                    proposed=_topic_candidates(ask(model,sys_prompt,usr))
                    if not proposed:
                        raise ValueError('не найдена тема в ответе модели')
                    seen=[s['topic'] for s in slots+week_slots]
                    candidate=None
                    for post in proposed:
                        candidate,issue=accept_topic(post,week,slot_index,seen)
                        if candidate: break
                        slot_errors.append(issue or 'неподходящая тема')
                    if candidate:
                        week_slots.append(candidate)
                        break
                except (ValueError,TypeError,KeyError) as exc:
                    slot_errors.append(str(exc))
            else:
                raise ValueError(f'Неделя {week}, публикация {slot_index+1}: '
                                 'не удалось получить уникальную тему после 3 попыток отдельной генерации; '
                                 + '; '.join((errors+slot_errors)[-5:]))
        slots.extend(week_slots)
        if on_week:
            on_week(week,slots)
    return slots
