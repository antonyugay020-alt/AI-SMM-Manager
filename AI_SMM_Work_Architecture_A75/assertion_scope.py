"""Host-owned scope classification for editorial fact review.

This layer answers one narrow question before source adjudication: does a
sentence make an assertion about an identifiable business, or is it clearly
planning, a prompt, or general domain information? The model remains the
reviewer for ambiguous language; these rules only remove categories whose
meaning is structurally evident from the text and publication schema.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from publication_schema import is_planning_field, publication_field_type


_ENTITY_REFERENCE = re.compile(
    r"\b(?:мы|наш\w*|наша|наши|у\s+нас|мне|мой|моя|наши\s+клиент\w*|"
    r"компани\w*|организаци\w*|бренд\w*|ателье|мастерск\w*|студи\w*|"
    r"клиник\w*|салон\w*|школ\w*|центр\w*|агентств\w*|кафе|ресторан\w*|"
    r"магазин\w*|сервис\w*|команд\w*|специалист\w*|сотрудник\w*|нам|нас|"
    r"we\b|our\b|company\b|business\b|brand\b|studio\b|team\b)\b", re.I)
_EXPLICIT_BUSINESS_ACTOR = re.compile(
    r"\b(?:мы|нам|нас|наш\w*|компани\w*|бренд\w*|"
    r"ателье|мастерск\w*|студи\w*|клиник\w*|салон\w*|школ\w*|"
    r"центр\w*|агентств\w*|команд\w*|we\b|our\b|company\b|brand\b|team\b)", re.I)
_FIRST_PARTY_POSSESSIVE = re.compile(
    r"\b(?:мы|нам|нас|наш\w*|у\s+нас|we|our|us|our\s+(?:company|team|business))\b",re.I)
_FIRST_PARTY_ACTOR = re.compile(
    r"\b(?:мы|наш\w*|у\s+нас|we|our)\b",re.I)
_OWNERSHIP_SUBJECT = re.compile(
    r"(?:^|[.!?;,\n:—–]|\b(?:если|когда|поскольку|пока|но|и|"
    r"if|when|because|while|but|and)\s+)\s*(?:наш|наша|наше|наши|our)\s+\w+",re.I)
_EDITORIAL_TOPIC_PREFIX = re.compile(
    r'^(?:наш\w*|our)\s+(?:[\w-]+\s+)*'
    r'(?:тема|рубрика|заголовок|topic|title)(?:\s+(?:сегодня|today))?$',re.I)
_PHYSICAL_VISIT_INVITATION = re.compile(
    r"\b(?:приходите|заходите|загляните|посетите|жд[её]м\s+вас|"
    r"come\s+(?:by|visit)|visit\s+us|drop\s+in)\b",re.I)

_QUESTION_START = re.compile(
    r"^(?:как|почему|зачем|что|кто|где|когда|какой|какая|какие|сколько|"
    r"можно\s+ли|стоит\s+ли|хочешь\b|хотите\b|интересно\s+ли|"
    r"how|why|what|who|where|when|which|can\s+we|would\s+you\s+like)\b", re.I)

# A speech act performed by this very publication needs no business source.
# Its prefix must express asking/listening, not an operational/service claim.
# No audience, industry or maximum question length is encoded here.
_QUERY_PREFACE = re.compile(
    r"(?:(?:поэтому|теперь|а|и|so|now|and)\s+)?(?:"
    r"(?:мы\s+)?(?:хотим|хотели\s+бы)\s+(?:узнать|понять|услышать|спросить)|"
    r"(?:нам\s+)?(?:интересно|важно)\s+(?:узнать|понять|услышать)|"
    r"вопрос(?:\s+(?:к|для)\s+(?:вам|вас|читателям|читателей|аудитории|подписчиков))?|"
    r"we\s+(?:want|would\s+like)\s+to\s+(?:know|hear|ask)|"
    r"(?:a\s+)?question\s+for\s+(?:you|readers|our\s+readers))", re.I)

_PLANNING_SENTENCE = re.compile(
    r"^(?:(?:данн\w*|эт\w*)\s+)?(?:smm[- ]?)?(?:стратеги\w*|кампан\w*|"
    r"контент[- ]?план\w*|план\w*|публикаци\w*|контент\w*)\b.{0,220}\b"
    r"(?:направлен\w*\s+на|ориентирован\w*\s+на|ставит\s+цель|"
    r"предусматрива\w*|включа\w*\s+\d+\s+публикац\w*|"
    r"поможет\s+(?:объяснить|рассказать|показать)|будет\s+(?:объяснять|рассказывать|показывать))\b",
    re.I)
_STRATEGY_RECOMMENDATION = re.compile(
    r"^(?:работа\s+с|продвижение\s+в|ведение\s+(?:аккаунта|страницы)\s+в)\b"
    r".{0,180}\b(?:направлен\w*\s+на|будет\s+вестись|планируется)\b|"
    r"^(?:контент|публикаци\w*|материал\w*)\b.{0,160}\b"
    r"(?:долж\w*|будет|планируется)\b.{0,100}\b"
    r"(?:отражать|объяснять|рассказывать|показывать|освещать)\b|"
    r"^(?:[^:]{1,60}:\s*)?(?:использовать|публиковать|создавать|подготовить|разбирать|чередовать|"
    r"показывать|объяснять|рассказывать|тестировать|взаимодействовать|собирать|"
    r"отслеживать|анализировать|проверять|сравнивать|use|test|interact|collect|"
    r"track|analy[sz]e|check|compare)\b",
    re.I)
_EDITORIAL_PLAN_ACTION = re.compile(
    r"^(?:(?:в|для|после|затем|далее)\s+[^.!?]{1,90}?\s+)?"
    r"(?:использовать|публиковать|создавать|подготовить|разбирать|чередовать|"
    r"отделять|учитывать|собирать|отслеживать|анализировать|проверять|"
    r"согласовать|уточнить|предложить|тестировать|взаимодействовать|"
    # Russian perfective infinitives are common in action plans and do not
    # assert that the business already performs the action.
    r"опубликовать|разместить|создать|разобрать|отделить|учесть|"
    r"отследить|проанализировать|проверить|протестировать)\b",re.I)

_GENERAL_PROPOSITION = re.compile(
    r"^(?:важно|следует|нужно|полезно|регулярн\w*\s+\w+|"
    r"изношенн\w*\s+\w+|правильн\w*\s+\w+|диагностик\w*\s+\w+|"
    r"[\wа-яё-]+(?:\s+[\wа-яё-]+){0,5})\s+(?:—|–|это\b|явля\w*\b|"
    r"снижа\w*\b|повыша\w*\b|помога\w*\b|позволя\w*\b|"
    r"оставля\w*\b|вызыва\w*\b|использу\w*\b|требу\w*\b|"
    r"вход\w*\b|включа\w*\b|содерж\w*\b|"
    r"уменьша\w*\b|увеличива\w*\b|зависит\s+от\b|может\s+привести\s+к\b)",
    re.I)
_GENERAL_GUIDANCE = re.compile(r"^(?:важно|следует|нужно|полезно)\b", re.I)
# Generic explanatory sentences often start with a demonstrative rather than
# a named industry subject ("Это нужно, чтобы...", "Такая проверка помогает...").
# Keep those out of the business-fact ledger when there is no organization or
# fulfillment actor. Explicit company/service language is still reviewed.
_IMPERSONAL_INFORMATION = re.compile(
    r"^(?:это\s+(?:важно|нужно|необходимо|помогает|позволяет|может|снижает|"
    r"повышает|требует|зависит|приводит)\b|"
    r"(?:такой|такая|такое|такие|подобн\w*)\s+"
    r"(?:провер\w*|изменени\w*|действи\w*|подход\w*|шаг\w*|"
    r"признак\w*|услови\w*|свойств\w*|материал\w*)\b"
    r"[^.!?\n]{0,180}\b(?:важно|нужно|необходимо|помога\w*|позволя\w*|"
    r"может|сниж\w*|повыш\w*|требу\w*|зависит|привод\w*)\b)", re.I)
_GENERAL_ADVICE_LEAD = re.compile(
    r"^чтобы\b[^.!?]{0,140},?\s*(?:важно|следует|нужно|полезно)\b",re.I)
_CONSUMER_GUIDANCE = re.compile(
    r"^(?:перед\b.{0,100}\b(?:подготовьтесь|подготовьте|проверьте|уточните|" 
    r"сравните|выберите|запишите|разделите|отметьте|сохраните)\b|"
    r"(?:подготовьтесь|подготовьте|проверьте|уточните|сравните|выберите|" 
    r"запишите|разделите|отметьте|сохраните|составьте|обратите внимание)\b|"
    r"(?:before|when|if)\b.{0,100}\b(?:prepare|check|compare|choose|write down|sort|save)\b|"
    r"(?:prepare|check|compare|choose|write down|sort|save)\b)",re.I)
_EDITORIAL_SECTION = re.compile(
    r"\b(?:стратеги\w*|контент[- ]?план\w*|редакционн\w*\s+направлен\w*|"
    r"направлени\w*\s+контент\w*|контентн\w*\s+стратеги\w*|strategy|content\s+plan)\b", re.I)
_CONTENT_INTENT = re.compile(
    r"\b(?:публикаци\w*|контент\w*|материал\w*|серия\s+(?:публикаци\w*|пост\w*|материал\w*))\b",
    re.I)
_EDITORIAL_NOMINAL_OPERATION = re.compile(
    r'^(?:использовани\w*|подготовк\w*|планировани\w*|публикаци\w*|'
    r'размещени\w*|мониторинг\w*|измерени\w*|анализ\w*|подсч[её]т\w*|'
    r'preparation|planning|publication|publishing|posting|monitoring|measurement|analysis|use)\b',re.I)
_EDITORIAL_OPERATION_TARGET = re.compile(
    r'\b(?:пост\w*|публикаци\w*|контент\w*|метрик\w*|kpi|'
    r'posts?|publications?|content|metrics?)\b',re.I)
_COMPLETED_EDITORIAL_OPERATION = re.compile(
    r'\b(?:уже|ранее|прошл\w*|опубликован\w*|размещ[её]н\w*|'
    r'провед[её]н\w*|осуществл[её]н\w*|already|previously|published|completed)\b',re.I)
_EDITORIAL_PROMISE_CONTENT = re.compile(
    r'\b(?:гаранти\w*|бесплатн\w*|без\s+сбо\w*|guarantee\w*|guaranteed|promise\w*)\b',re.I)
_COMMUNICATION_ACTION = re.compile(
    r"\b(?:рассказыва\w*|объясня\w*|показыва\w*|покаж\w*|освеща\w*|информир\w*|"
    r"предоставля\w*\s+информаци\w*|разбира\w*\s+тему)\b", re.I)
_EDITORIAL_COMMUNICATION_COMMITMENT = re.compile(
    r"^(?:(?:в\s+)?публикаци\w*\s+)?(?:мы\s+)?(?:расскаж\w*|объясн\w*|покаж\w*|разбер\w*|"
    r"рассмотр\w*|опиш\w*|освет\w*|подготов\w*\s+материал\w*)\b", re.I)
_EDITORIAL_OUTPUT_STATEMENT = re.compile(
    r"^(?:контент|материал\w*|публикаци\w*|план|стратеги\w*)\s+"
    r"(?:разработан\w*|подготовлен\w*|составлен\w*|оформлен\w*)\b", re.I)
_MEASUREMENT_PLAN_SECTION = re.compile(
    r"\b(?:план\s+измерени\w*|метод\w*\s+измерени\w*|"
    r"kpi|метрик\w*|measurement\s+plan)\b", re.I)
_MEASUREMENT_METHOD_STATEMENT = re.compile(
    r"\b(?:отслежива\w*|измеря\w*|оценива\w*|фиксиру\w*)\b.{0,100}"
    r"\b(?:через|по|с\s+помощью|в\s+статистик\w*|в\s+аналитик\w*)\b", re.I)
_CONCRETE_FULFILLMENT = re.compile(
    # Match fulfillment actions, not topical nouns such as "вопросы о записи"
    # or "ответы аудитории". Topic mentions are common in strategy briefs and
    # must not turn editorial plans into unsupported claims about the company.
    r"\b(?:ответим|ответить\s+(?:вам|на\s+(?:ваш|каждый|все)\s+вопрос)|"
    r"помога\w*\s+(?:подобрат|записат|оформит|выбрат)|"
    r"записыва\w*\s+(?:клиент\w*|вас|на\s+(?:прием|диагностик|консультац))|"
    r"запиш\w*\s+(?:вас|клиент\w*)|доставля\w*|гарантир\w*|обеспечива\w*|"
    r"выполня\w*\s+работ\w*|ремонтиру\w*)\b", re.I)

_EDITORIAL_SOURCE_BOUNDARY = re.compile(
    r"\b(?:публиковать|размещать|использовать|сообщать|указывать|обещать)\b"
    r"[^.!?]{0,180}\b(?:только|лишь)\s+после\s+"
    r"(?:подтверждени\w*|согласовани\w*|проверки)\b", re.I)
_NEUTRAL_CTA = re.compile(
    r"^(?:напиш\w*\s+(?:нам|в\s+сообщени\w*)|зада\w*\s+(?:нам\s+)?вопрос|"
    r"спрос\w*\s+(?:нас\s+)?о\s+запис\w*|узна\w*\s+подробност\w*|"
    r"подел\w*\s+своим\s+вопрос\w*)[.!?… ]*$", re.I)
_INFORMATIONAL_INVITATION = re.compile(
    r"^(?:узнайте|посмотрите|разберитесь)\s*,?\s+"
    r"(?:как|что|где|когда|почему|зачем)\b", re.I)
_EDITORIAL_VERIFICATION_TASK = re.compile(
    r"^(?:уточнить|подтвердить|проверить|согласовать|запросить)\b", re.I)
_AUDIENCE_QUESTION_ACTION = re.compile(
    r"^(?:напиши\w*|задай\w*|спроси\w*)\b", re.I)
_PUBLIC_QUESTION_CHANNEL = re.compile(
    r"\b(?:вопрос\w*|комментари\w*|директ\w*|сообщени\w*)\b", re.I)
_AUDIENCE_SHARING_INVITE = re.compile(
    r"^(?:напиш\w*|подел\w*|расскаж\w*)\s*,?\s+"
    r"(?:какие?\b|какова\b|что\b|как\b|с\s+какими?\b|о\s+чем\b)", re.I)
_CONDITIONAL_AUDIENCE_INVITE = re.compile(
    r"^если\s+(?:у\s+вас\s+есть|вас\s+интересу\w*)\s+"
    r"(?:вопрос\w*|сомнен\w*|интерес\w*)\b", re.I)
_NEUTRAL_AVAILABILITY_INQUIRY = re.compile(
    r"\b(?:уточни\w*|спроси\w*|проверь\w*|узнай\w*)\b"
    r"[^.!?]{0,140}\b(?:доступн\w*|есть\s+ли|возможност\w*|услови\w*)\b|"
    r"\b(?:доступност\w*|возможност\w*)\b[^.!?]{0,100}"
    r"\b(?:уточни\w*|спроси\w*|проверь\w*|узнай\w*)\b",re.I)
_NON_SUBSTITUTION_CAUTION = re.compile(
    r"\b(?:наблюдени\w*|впечатлени\w*|самостоятельн\w*\s+провер\w*|"
    r"домашн\w*\s+провер\w*)\b[^.!?]{0,100}\bне\s+заменя\w*\b|"
    r"\bне\s+заменя\w*\b[^.!?]{0,100}"
    r"\b(?:наблюдени\w*|впечатлени\w*|самостоятельн\w*\s+провер\w*)\b",re.I)
_EDITORIAL_DIRECTIVE = re.compile(
    r"^(?:созда\w*|предлож\w*|подготов\w*|разработ\w*|состав\w*|сформулиру\w*|"
    r"напис\w*|оформ\w*|укаж\w*|выбер\w*|рассмотр\w*|добав\w*|"
    r"включ\w*|опис\w*|собер\w*)\b", re.I)
_DELIVERABLE_DIRECTIVE = re.compile(
    r"^(?:для\s+)?кажд\w*\s+(?:публикац\w*|пост\w*|материал\w*)\s+"
    r"(?:созда\w*|укаж\w*|подготов\w*|разработ\w*|состав\w*|сформулиру\w*|"
    r"напис\w*|оформ\w*|добав\w*|включ\w*)\b", re.I)
_EDITORIAL_LABEL = re.compile(
    r"^(?:образовательн\w*\s+пост|"
    r"(?:рекомендаци\w*|совет\w*|инструкци\w*)\s+(?:по|для)\b|"
    r"(?:тема|идея|формат|сценари\w*)\s*[:—-])", re.I)
_BRIEF_COPY_INSTRUCTION = re.compile(
    r"^(?:созда\w*|состав\w*|подготов\w*|разработ\w*)\b|"
    r"^(?:считай\w*\s+неизвестн\w*|не\s+(?:придумывай|выдумывай|использова\w*|"
    r"указыва\w*|обеща\w*|добавля\w*)|"
    r"для\s+kpi\s+выбер\w*|для\s+кажд\w*\s+(?:публикац\w*|пост\w*)\s+укаж\w*)",re.I)
_EDITORIAL_WORKPLAN_FRAGMENT = re.compile(
    r"^(?:создани\w*\s+и\s+публикаци\w*|создани\w*|публикаци\w*|"
    r"мониторинг\s+и\s+анализ|анализ\s+результат\w*|"
    r"корректировк\w*\s+стратеги\w*|неправильн\w*\s+интерпретаци\w*)\b",re.I)
_STRATEGY_ACTION_FRAGMENT = re.compile(
    r"^(?:публикаци\w*\s+контент\w*|мониторинг\s+(?:статистик\w*|отклик\w*)|"
    r"анализ\s+(?:результат\w*|отклик\w*)|создани\w*\s+контент\w*|"
    r"(?:publish|monitor|track|analy[sz]e|create|review)\b)", re.I)
_INFINITIVE_EDITORIAL_ACTION = re.compile(
    r"^(?:\d+[.)]\s*)?[а-яё-]+(?:ться|тись|ть|ти|чь)\b", re.I)
_UNKNOWN_DATA_NOTE = re.compile(
    r"^(?:нет\s+информаци\w*|не\s+указан\w*|неизвестн\w*|"
    r"требу\w*\s+уточн\w*|уточнить\s+(?:цен\w*|адрес\w*|расписан\w*|услови\w*)|"
    r"[^.!?\n]{1,100}\s+(?:не\s+указан\w*|неизвестн\w*|отсутству\w*))", re.I)
_KPI_LABEL = re.compile(
    r"^(?:(?:число|количество|уровень)\s+)?(?:лайк\w*|комментари\w*|"
    r"просмотр\w*|сохранени\w*|репост\w*|взаимодейств\w*|реакци\w*|"
    r"вопрос\w*|обращени\w*|запрос\w*|"
    r"переход\w*|охват\w*|подписчик\w*|сообщени\w*)\b|"
    r"^(?:число|количество|уровень)\s+.{0,80}\b(?:direct|директ|dm|личн\w*\s+сообщени\w*)\b",re.I)
_UNCONFIRMED_PLANNING_NOTE = re.compile(
    r"\b(?:необходим\w*|нужно|требу\w*)\s+(?:уточн\w*|подтверд\w*|согласова\w*)\b|"
    r"\b(?:неизвестн\w*|не\s+указан\w*|пока\s+нет\s+данн\w*)\b",re.I)
_CHANNEL_PLANNING_SECTION = re.compile(r"\b(?:канал\w*|продвижени\w*|распространени\w*)\b",re.I)
_FUTURE_EDITORIAL_SUBJECT = re.compile(
    r"^(?:[\w-]+[\s-]+){0,5}(?:канал\w*|аккаунт\w*|страниц\w*|рассылк\w*|"
    r"публикаци\w*|контент\w*|channel|account|page|newsletter|publication)\b"
    r"[^.!?]{0,60}\b(?:будет|будут|will)\s+(?:be\s+)?"
    r"(?:использова\w*|публикова\w*|размеща\w*|освеща\w*|"
    r"use\w*|publish\w*|distribut\w*|cover\w*)\b", re.I)
_EDITORIAL_USE_PURPOSE = re.compile(
    r"\b(?:для|for)\s+(?:публикац\w*|контент\w*|информирован\w*|"
    r"взаимодейств\w*|размещени\w*|объяснени\w*|publishing|content|"
    r"communication|interaction|collecting\s+questions)\b", re.I)
_CHANNEL_MECHANISM = re.compile(
    r"\b(?:платформ\w*|формат\w*|визуальн\w*|текстов\w*|видеоформат\w*)\b.{0,120}"
    r"\b(?:можно|позволя\w*|подход\w*|удобн\w*|показыва\w*|покаж\w*|достига\w*|"
    r"использован\w*|демонстрац\w*|привлечен\w*)\b|"
    r"\b(?:можно|позволя\w*|подход\w*|удобн\w*|показыва\w*|покаж\w*|достига\w*|"
    r"использован\w*|демонстрац\w*|привлечен\w*)\b.{0,120}"
    r"\b(?:контент\w*|аудитори\w*|платформ\w*|формат\w*|внимани\w*)\b",re.I)
_CHANNEL_USAGE_RECOMMENDATION = re.compile(
    r"^(?:[^:]{1,48}:\s*)?(?:использован\w*|размещен\w*|публикаци\w*|"
    r"подготовк\w*|прием\w*|получени\w*)\b.{0,220}\b(?:для|чтобы|с\s+целью)\b"
    r".{0,220}\b(?:информирован\w*|информаци\w*|ответ\w*\s+на\s+вопрос\w*|"
    r"демонстрац\w*|показ\w*|объяснен\w*|разъяснен\w*|освещен\w*|охват\w*|"
    r"(?:получени\w*\s+)?(?:запрос\w*|вопрос\w*|обращени\w*)|взаимодейств\w*)",re.I)
_STANDALONE_HEADING = re.compile(
    r"^(?:этапы|риски|kpi|цели|задачи|каналы|контент[- ]план|"
    r"план\s+измерени\w*|заключени\w*)\s*[:—-]?$",re.I)
_EDITORIAL_RISK_TERMS = re.compile(
    r"\b(?:бесплатн\w*|скидк\w*|гаранти\w*|цен\w*|стоимост\w*|"
    r"адрес\w*|расписан\w*|запиш\w*|предостав\w*|достав\w*|"
    r"ответим|поможем|обеспечим|выполним|услуг\w*|товар\w*|ремонт\w*|"
    r"заняти\w*|курс\w*|диагностик\w*|консультац\w*)\b", re.I)
_FINITE_ASSERTION = re.compile(
    r"\b(?:мы|наш\w*|у\s+нас|есть|явля\w*|предоставля\w*|предлага\w*|"
    r"включа\w*|помога\w*|гарантир\w*|обеспечива\w*|снижает|повышает|"
    r"позволяет|требует|содержит|будет|бывает|составляет)\b", re.I)
_EXPLICIT_RESPONSE_PROMISE = re.compile(
    r"\b(?:ответим|расскажем|объясним|поможем|подберем|подберём|"
    r"запишем|оформим|гарантируем|обеспечим|предоставим|дадим)\b", re.I)
_MARKDOWN_ONLY = re.compile(r'^(?:```[a-z0-9_-]*|(?:[-*_]\s*){3,}|\d{1,3}[.)])$', re.I)


def _clean_statement(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^[\s>*#`_-]+", "", text)
    text = re.sub(r'^\d+[.)]\s+', '', text)
    text = re.sub(r"^\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = text.strip(" \t\r\n*_`\"'«»")
    return text


def editorial_operation_asserts_reality(issue):
    """One shared exclusion for inventory and source-adjudication fast paths."""
    if not isinstance(issue,dict):
        return False
    kind=issue.get('field_type') or publication_field_type(issue.get('field_role',''))
    statement=_clean_statement(issue.get('evidence_segment') or issue.get('quote')
        or issue.get('claim') or issue.get('content') or '')
    return bool(kind=='document_narrative' and _EDITORIAL_NOMINAL_OPERATION.match(statement)
        and (_COMPLETED_EDITORIAL_OPERATION.search(statement) or _EDITORIAL_PROMISE_CONTENT.search(statement)))


def requires_business_fact_review(issue: dict[str, Any] | None = None, *,
                                  text: str | None = None,
                                  field_type: str | None = None,
                                  field_role: str | None = None) -> bool:
    """Force source review for first-party claims and visit invitations in copy.

    A probabilistic inventory model may not silently discard a concrete
    assertion from publishable text. Host routing makes first-party claims and
    invitations to visit go through the same evidence ledger every time.
    """
    item=issue if isinstance(issue,dict) else {}
    role=field_role if field_role is not None else str(item.get('field_role') or '')
    kind=field_type if field_type is not None else str(item.get('field_type') or '')
    if not kind:
        kind=publication_field_type(role)
    if kind not in {'caption','cta'}:
        return False
    statement=_clean_statement(text if text is not None else
        item.get('evidence_segment') or item.get('quote') or item.get('claim') or item.get('content') or '')
    if not statement:
        return False
    return bool(_FIRST_PARTY_POSSESSIVE.search(statement) or
                _PHYSICAL_VISIT_INVITATION.search(statement))


def requires_business_assertion_review(issue, interpretation=None):
    """Distinguish a business actor from a first-party recipient.

    All first-party mentions still route through source review. A recipient
    pronoun alone cannot override a validated reader invitation into a company
    service promise. Direct company actors and physical visits stay protected.
    """
    if not requires_business_fact_review(issue):
        return False
    statement=_clean_statement(issue.get('evidence_segment') or issue.get('quote')
        or issue.get('claim') or issue.get('content') or '')
    # Ownership/recipient words route a claim to source review, but are not
    # proof that the company acts. A complete, coherent semantic decision may
    # distinguish a reader's communication from a company/service assertion.
    # Direct first-person actors and physical visits cannot use this path.
    reader_communication=(isinstance(interpretation,dict)
        and interpretation.get('statement_class')=='invitation_cta'
        and interpretation.get('speech_act')=='invitation'
        and interpretation.get('assertion_subject')=='reader'
        and interpretation.get('invitation_kind')=='communication'
        and interpretation.get('source_relation')=='not_needed'
        and interpretation.get('source_quote_id')=='none'
        and interpretation.get('verdict')=='supported')
    # A nominal declaration of this publication's question title is metadata,
    # not an action of its owner. It still crosses source review and needs a
    # complete typed label decision. The quoted question must contain no
    # company actor, service fulfillment or promise; company assertions do not
    # gain immunity by being put inside quotation marks.
    label_scope=False
    if (isinstance(interpretation,dict)
            and interpretation.get('statement_class')=='general_information'
            and interpretation.get('speech_act')=='label'
            and interpretation.get('assertion_subject')=='general'
            and interpretation.get('invitation_kind')=='none'
            and interpretation.get('source_relation')=='not_needed'
            and interpretation.get('source_quote_id')=='none'
            and interpretation.get('verdict')=='supported'):
        nominal=re.sub(r'^[^\w]+','',statement)
        label=re.fullmatch(r'(.+?)\s*[:—–]\s*[«“"]([^»”"]+)[»”"]\s*[.!?…]*',nominal)
        if label:
            prefix,title=label.groups()
            without_owner=re.sub(r'^(?:наш\w*|our)\s+','',prefix,flags=re.I)
            label_scope=bool(_EDITORIAL_TOPIC_PREFIX.fullmatch(prefix.strip())
                and not _ENTITY_REFERENCE.search(without_owner)
                and _QUESTION_START.match(title.strip())
                and not _ENTITY_REFERENCE.search(title)
                and not _CONCRETE_FULFILLMENT.search(title)
                and not _EDITORIAL_PROMISE_CONTENT.search(nominal))
    if label_scope and not _PHYSICAL_VISIT_INVITATION.search(statement):
        return False
    direct_actor=bool(re.search(r'\b(?:мы|we)\b|\bу\s+нас\b',statement,re.I)
                      or _OWNERSHIP_SUBJECT.search(statement))
    if reader_communication and not direct_actor and not _PHYSICAL_VISIT_INVITATION.search(statement):
        return False
    return bool(_FIRST_PARTY_ACTOR.search(statement) or _PHYSICAL_VISIT_INVITATION.search(statement))


def classify_obvious_non_business(issue: dict[str, Any] | None = None, *,
                                  text: str | None = None,
                                  field_type: str | None = None,
                                  field_role: str | None = None,
                                  section_path: list[str] | None = None) -> dict[str, str] | None:
    """Return a typed non-business scope only when the form is unambiguous.

    This is deliberately not a general claim classifier. Explicit first-person
    or organization references are never downgraded by the educational-text
    rule and continue through the evidence ledger.
    """
    item = issue if isinstance(issue, dict) else {}
    raw_statement = str(text if text is not None else
                        item.get('evidence_segment') or item.get('quote') or item.get('claim')
                        or item.get('content') or '')
    if (_MARKDOWN_ONLY.fullmatch(raw_statement.strip()) or
            (raw_statement.strip() and not any(ch.isalnum() for ch in raw_statement))):
        return {'statement_class':'formatting_only',
                'reason':'Сегмент содержит только маркер разметки, а не утверждение.'}
    statement = _clean_statement(raw_statement)
    if not statement:
        return None
    role = field_role if field_role is not None else str(item.get('field_role') or '')
    kind = field_type if field_type is not None else str(item.get('field_type') or '')
    if not kind:
        kind = publication_field_type(role)
    # Public copy can combine a question/instruction prefix with an action of
    # the company in any later clause. No prefix heuristic may waive that
    # complete span. Route entity/first-party/ownership/visit candidates to the typed
    # semantic reviewer before all overlapping non-business shortcuts.
    # A complete performative question is the narrow exception: its preface
    # asks the question here, and the question itself has no company actor.
    if kind in {'caption', 'cta'} and (
            _ENTITY_REFERENCE.search(statement)
            or _PHYSICAL_VISIT_INVITATION.search(statement)):
        if _NEUTRAL_CTA.fullmatch(statement):
            return {'statement_class':'invitation_cta',
                    'reason':'Полная фраза только приглашает читателя задать вопрос или написать.'}
        preface, separator, query = statement.partition(':')
        if (separator and statement.endswith('?')
                and _QUERY_PREFACE.fullmatch(preface.strip())
                and _QUESTION_START.match(query.strip())
                and not _ENTITY_REFERENCE.search(query)
                and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
                and not _CONCRETE_FULFILLMENT.search(statement)):
            return {'statement_class':'audience_prompt',
                    'reason':'Публикация непосредственно задаёт вопрос без утверждения о действии компании.'}
        return None
    # Shared exclusions precede all overlapping planning heuristics below.
    # A nominal editorial heading cannot turn a completed operation or an
    # explicit promise in its content into an unperformed planning action.
    if editorial_operation_asserts_reality({'field_type':kind,'quote':statement}):
        return None
    if _BRIEF_COPY_INSTRUCTION.match(statement):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза повторяет редакторское задание, ограничение или требование к материалу.'}
    if _STANDALONE_HEADING.fullmatch(statement):
        return {'statement_class':'formatting_only',
                'reason':'Фрагмент является заголовком плана, а не утверждением о бизнесе.'}
    if is_planning_field(kind):
        return {'statement_class': 'planning_constraint',
                'reason': 'Поле описывает параметры будущего материала, а не факт о бизнесе.'}
    if _PLANNING_SENTENCE.search(statement):
        return {'statement_class': 'planning_constraint',
                'reason': 'Фраза описывает намерение стратегии или контент-плана.'}
    if _EDITORIAL_VERIFICATION_TASK.match(statement):
        return {'statement_class': 'planning_constraint',
                'reason': 'Инфинитив формулирует внутреннюю задачу проверки или уточнения, а не публичное обещание.'}

    # Strategy narrative often describes what the proposed communications will
    # do. Resolve that role only when the section and the sentence both point
    # to editorial work. Publishable copy (caption/CTA) remains fully reviewable,
    # as do concrete promises about customer service or fulfillment.
    sections = ' '.join(str(x) for x in (section_path if section_path is not None
                                          else item.get('section_path', []))
                        if isinstance(x, str))
    if (kind not in {'caption','cta'}
            and re.search(r'стратеги|направлени|strategy|direction',sections+' '+role,re.I)
            and re.match(r'^(?:ориентация|направленность|фокус|orientation|focus)\s+(?:на\b|on\b)',statement,re.I)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and not re.search(r'\b(?:гарант\w*|цен\w*|стоимост\w*|бесплатн\w*|скидк\w*|срок\w*|достав\w*)',statement,re.I)):
        return {'statement_class':'planning_constraint',
                'reason':'Редакционный раздел задаёт направление будущей коммуникации, а не подтверждённый процесс компании.'}
    if _UNKNOWN_DATA_NOTE.match(statement):
        return {'statement_class':'knowledge_boundary',
                'reason':'Фраза явно сообщает, что сведения не указаны или неизвестны; это не подтверждение наличия бизнес-условия.'}
    # Editorial instructions that explicitly defer publication until facts are
    # checked are workflow constraints, even when the sentence mentions a
    # service, audience questions, or answers. They do not claim those facts.
    if (_EDITORIAL_SOURCE_BOUNDARY.search(statement)
            and kind == 'document_narrative'
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза ограничивает будущую публикацию подтверждёнными сведениями; это редакционное правило, а не бизнес-факт.'}
    if (_UNCONFIRMED_PLANNING_NOTE.search(statement)
            and kind == 'document_narrative'
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _FINITE_ASSERTION.search(statement)
            and (re.search(r'риски|неизвестн|материал|подготов|стратеги|контент|ресурс',
                           sections+' '+role,re.I) or len(statement.split())<=14)):
        return {'statement_class':'planning_constraint',
                'reason':'Фрагмент явно просит уточнить или подтвердить исходные данные; он не утверждает факт об их наличии.'}
    if (re.search(r'риски|неизвестн|уточнени', sections, re.I)
            and not _FINITE_ASSERTION.search(statement)
            and len(statement.split())<=8):
        return {'statement_class':'planning_constraint',
                'reason':'Короткий пункт в разделе неизвестных/рисков обозначает данные для уточнения, а не утверждение о бизнесе.'}
    if (kind == 'document_narrative'
            and (_STRATEGY_ACTION_FRAGMENT.match(statement)
            or _INFINITIVE_EDITORIAL_ACTION.match(statement))
            and re.search(r'план\s+действ|strategy|implementation', sections, re.I)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _FINITE_ASSERTION.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза в плане действий задаёт предлагаемый этап работы, а не описывает текущий процесс компании.'}
    editorial_context = bool(_EDITORIAL_SECTION.search(sections))
    editorial_context = editorial_context or (
        kind == 'document_narrative' and bool(_EDITORIAL_SECTION.search(statement[:100]))
    )
    if (kind == 'document_narrative' and editorial_context
            and _EDITORIAL_NOMINAL_OPERATION.match(statement)
            and _EDITORIAL_OPERATION_TARGET.search(statement)
            and not _COMPLETED_EDITORIAL_OPERATION.search(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _FINITE_ASSERTION.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _EDITORIAL_RISK_TERMS.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Операция над будущим контентом или его метриками в редакционной стратегии задаёт план работы, а не существующий бизнес-процесс.'}
    editorial_form = unicodedata.normalize('NFKC', statement).translate(
        str.maketrans({char: '-' for char in '\u2010\u2011\u2012\u2013\u2014\u2212'}))
    if (kind == 'document_narrative' and editorial_context
            and _FUTURE_EDITORIAL_SUBJECT.match(editorial_form)
            and _EDITORIAL_USE_PURPOSE.search(editorial_form)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _EDITORIAL_RISK_TERMS.search(statement)):
        return {'statement_class': 'planning_constraint',
                'reason': 'Будущее использование редакционного канала задано как часть стратегии; это не утверждение о текущей работе бизнеса.'}
    if (kind == 'document_narrative' and editorial_context
            and _INFINITIVE_EDITORIAL_ACTION.match(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _FINITE_ASSERTION.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _EDITORIAL_RISK_TERMS.search(statement)):
        return {'statement_class': 'planning_constraint',
                'reason': 'Инфинитив в редакционной стратегии задаёт предлагаемую работу, а не существующий бизнес-факт.'}
    if (kind == 'document_narrative' and editorial_context
            and _STRATEGY_RECOMMENDATION.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза предлагает направление коммуникации, а не описывает уже оказываемую услугу.'}
    if (kind == 'document_narrative' and editorial_context
            and _EDITORIAL_PLAN_ACTION.match(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Инфинитив задаёт предлагаемое действие контент-плана, а не утверждает факт о выполнении услуги.'}

    # A bare topic/pillar is an editorial label, not a claim. Keep phrases that
    # mention service outcomes or advantages reviewable even in this section.
    raw_path=item.get('section_path',[])
    path_items=raw_path if isinstance(raw_path,(list,tuple)) else [raw_path]
    role_or_section=' '.join([role,*[str(x) for x in path_items]])
    is_content_pillar=bool(re.search(
        r'\b(?:контентн\w*\s+направлени\w*|контентн\w*\s+опор\w*|'
        r'тем\w*\s+контент\w*|контентн\w*\s+столп\w*)\b',role_or_section,re.I))
    if (kind == 'document_narrative' and editorial_context and is_content_pillar
            and not _FINITE_ASSERTION.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)):
        return {'statement_class':'creative_direction',
                'reason':'Короткая тема контента не утверждает факт о компании.'}
    # Statements about the proposed communication are editorial commitments,
    # not promises that the business will deliver a service. Fulfillment verbs
    # such as answer, advise, book, provide, repair, and guarantee stay reviewable.
    if (_EDITORIAL_COMMUNICATION_COMMITMENT.match(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and not _EDITORIAL_RISK_TERMS.search(statement)):
        return {'statement_class':'creative_direction',
                'reason':'Фраза описывает тему или содержание будущей публикации, а не услугу или результат бизнеса.'}

    if (_MEASUREMENT_PLAN_SECTION.search(sections)
            and _MEASUREMENT_METHOD_STATEMENT.search(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза в плане измерения задаёт предлагаемый способ оценки результата.'}
    if (_MEASUREMENT_PLAN_SECTION.search(sections)
            and _KPI_LABEL.match(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза задаёт метрику или способ её подсчёта, а не сообщает фактическое значение.'}
    if (_CHANNEL_PLANNING_SECTION.search(sections+' '+role)
            and _CHANNEL_USAGE_RECOMMENDATION.search(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза описывает планируемое использование канала для публикаций и коммуникации, а не уже выполняемый процесс компании.'}
    if (_CHANNEL_PLANNING_SECTION.search(sections+' '+role)
            and _CHANNEL_MECHANISM.search(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза описывает свойства канала и способ размещения контента, а не факт о работе компании.'}
    if (re.search(r'стратеги|контент[- ]?план|направлени\w*|план\s+действ',
                  sections+' '+role,re.I)
            and re.match(r'^(?:мониторинг|анализ|отслеживание|сбор|ответы\s+на)',statement,re.I)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза называет предлагаемое действие стратегии или метод наблюдения, а не уже выполняемый процесс.'}

    if (_EDITORIAL_OUTPUT_STATEMENT.match(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _EDITORIAL_RISK_TERMS.search(statement)):
        return {'statement_class':'creative_direction',
                'reason':'Фраза описывает подготовленный редакционный материал и его соответствие заданию.'}
    if (_EDITORIAL_WORKPLAN_FRAGMENT.match(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _EDITORIAL_RISK_TERMS.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фрагмент называет этап или действие контент-плана, а не процесс оказания услуги.'}
    preface, separator, query = statement.partition(':')
    if (separator and statement.endswith('?') and (_QUERY_PREFACE.fullmatch(preface.strip()) or
                (kind not in {'caption','cta'} and editorial_context and len(preface.split())<=8
                 and not _ENTITY_REFERENCE.search(preface) and not _FINITE_ASSERTION.search(preface)))
            and _QUESTION_START.match(query.strip())
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)):
        return {'statement_class':'audience_prompt',
                'reason':'Фраза непосредственно задаёт вопрос аудитории и не утверждает процесс или результат компании.'}
    if _KPI_LABEL.match(statement) and not _EXPLICIT_BUSINESS_ACTOR.search(statement):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза обозначает метрику для измерения публикаций.'}
    if (editorial_context and kind not in {'caption', 'cta'}
            and _CONTENT_INTENT.search(statement)
            and _COMMUNICATION_ACTION.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)):
        return {'statement_class': 'creative_direction',
                'reason': 'Фраза в редакционном разделе задаёт тему будущих материалов.'}

    # A short standalone question has no asserted predicate. Keep it out of
    # business-fact adjudication even when it asks about the brand in question.
    question = statement.rstrip(' .!;:')
    if (statement.rstrip().endswith('?') and _QUESTION_START.match(question)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)):
        return {'statement_class': 'audience_prompt',
                'reason': 'Фраза сформулирована как вопрос аудитории, а не утверждение.'}


    # A reader may ask whether an unconfirmed consultation, service, or option
    # exists. A request to check availability is not a claim that it exists.
    if (_NEUTRAL_AVAILABILITY_INQUIRY.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)):
        return {'statement_class':'audience_prompt',
                'reason':'Вопрос предлагает уточнить неизвестную доступность и не утверждает, что услуга существует.'}

    # A caution that observations or a self-check do not replace a professional
    # diagnosis is general reader guidance, not a statement about the company's
    # process or a promised service outcome.
    if (_NON_SUBSTITUTION_CAUTION.search(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'general_information',
                'reason':'Фраза даёт общее предостережение читателю и не описывает процесс компании.'}

    # A short imperative that invites the reader to explore a topic is an
    # editorial prompt, even when an LLM has placed it in a ready-copy field.
    # Do not waive invitations that attribute an action to the business or
    # promise fulfillment; those still require source adjudication.
    informational_invite = bool(_INFORMATIONAL_INVITATION.match(statement))
    question_action = bool(_AUDIENCE_QUESTION_ACTION.match(statement)
                           and _PUBLIC_QUESTION_CHANNEL.search(statement))
    if ((informational_invite or question_action)
            and not _ENTITY_REFERENCE.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class': 'audience_prompt',
                'reason': 'Приглашение изучить тему или задать вопрос не утверждает процесс компании и не обещает ответ.'}

    if (_AUDIENCE_SHARING_INVITE.match(statement)
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'audience_prompt',
                'reason':'Приглашение аудитории поделиться вопросом или опытом не обещает действие компании.'}

    if (_CONDITIONAL_AUDIENCE_INVITE.match(statement)
            and _PUBLIC_QUESTION_CHANNEL.search(statement)
            and not _EXPLICIT_RESPONSE_PROMISE.search(statement)):
        return {'statement_class':'audience_prompt',
                'reason':'Условное приглашение написать или задать вопрос не обещает ответ или услугу.'}

    # Internal drafting instructions and requested post fields describe what
    # the author should prepare. Explicit business offers and outcomes still
    # go through normal source review.
    editorial_directive = bool(_EDITORIAL_DIRECTIVE.match(statement)
                               or _DELIVERABLE_DIRECTIVE.match(statement))
    if (editorial_directive and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _EDITORIAL_RISK_TERMS.search(statement)):
        return {'statement_class':'planning_constraint',
                'reason':'Фраза задаёт редакторское действие или структуру будущего материала.'}

    # Short noun headings such as a post type or a recommendation topic are
    # labels, not claims. A predicate or explicit business actor keeps them
    # reviewable.
    if (_EDITORIAL_LABEL.match(statement) and len(statement.split())<=14
            and not _EXPLICIT_BUSINESS_ACTOR.search(statement)
            and not _FINITE_ASSERTION.search(statement)):
        return {'statement_class':'creative_direction',
                'reason':'Фраза задаёт тему или формат материала без утверждения о бизнесе.'}

    # A CTA may invite a question or a message without asserting that the
    # business will respond, advise, book, or deliver a result. Keep the
    # exemption narrow and tied to the canonical CTA field.
    if kind in {'caption','cta'} and _NEUTRAL_CTA.fullmatch(statement):
        return {'statement_class': 'invitation_cta',
                'reason': 'Нейтральный CTA приглашает аудиторию к действию без обещания ответа или результата.'}

    # General educational propositions may mention the industry's subject, but
    # they do not establish that a particular organization performs an action
    # or guarantees an outcome. Organization/person references stay reviewable.
    if (not _ENTITY_REFERENCE.search(statement)
            and not _CONCRETE_FULFILLMENT.search(statement)
            and (_GENERAL_GUIDANCE.match(statement)
                 or _IMPERSONAL_INFORMATION.match(statement)
                 or _GENERAL_ADVICE_LEAD.match(statement)
                 or _GENERAL_PROPOSITION.match(statement)
                 or _CONSUMER_GUIDANCE.match(statement))):
        return {'statement_class': 'general_information',
                'reason': 'Фраза объясняет общее свойство или принцип отрасли, не приписывая его компании.'}
    return None
