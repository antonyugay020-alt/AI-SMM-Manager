"""Topic-independent semantic obligations for a model's source decision.

These fields expose what was interpreted; they do not prove that an LLM's
semantic judgement is correct. Exact provenance and source checks remain host owned.
"""
from __future__ import annotations

SPEECH_ACTS = ('assertion', 'advice', 'question', 'invitation',
               'editorial_instruction', 'fiction', 'opinion', 'social_expression', 'label', 'uncertain')
SUBJECTS = ('business', 'reader', 'general', 'editorial', 'fictional', 'unclear')
RELATIONS = ('entails', 'contradicts', 'unrelated', 'instruction', 'unknown', 'not_needed')
INCOMPLETE_ADJUDICATIONS = frozenset({'unavailable', 'invalid_decision', 'uncertain'})
SEMANTIC_PROPERTIES = {
    'speech_act': {'type': 'string', 'enum': list(SPEECH_ACTS)},
    'assertion_subject': {'type': 'string', 'enum': list(SUBJECTS)},
    'source_relation': {'type': 'string', 'enum': list(RELATIONS)},
}
SEMANTIC_POLICY = (
    '\nОтдельно укажи speech_act (что делает фраза), assertion_subject (кто действует), '
    'source_relation (как точная выбранная цитата подтверждает утверждение). '
    'Совет читателю сделать действие не описывает процесс компании. Императив сам по себе '
    'не является ценой, услугой или обещанием. Приглашение читателю ответить на вопросы '
    'не обещает ответ или результат от компании. Проверяй также придаточные части: совет '
    'с обещанным результатом или конкретной услугой сохраняет business как subject. '
    'Связь с компанией из соседней фразы не меняет явно названного исполнителя действия. '
    'Обещание от имени компании, даже в условной фразе, является assertion/business. '
    'Цитата с заданием автору, запретом или условным предложением как писать текст имеет '
    'source_relation=instruction; она не доказывает услугу или результат. '
    'source_relation относится к выбранному source_quote_id, а не к речевой форме самого утверждения. '
    'При source_quote_id=none нельзя выбирать entails, contradicts или instruction: сравниваемая цитата отсутствует. '
    'Само приглашение не делает source_relation=instruction; если источник утверждает доступность услуги, '
    'такой источник может entail её предложение читателю. '
    'Entails допустимо только если цитата прямо подтверждает всю проверяемую пропозицию. '
    'Не выводи скорость, удобство, результат, условия и этапы из самого названия услуги. '
    'Для совета, вопроса или нейтрального приглашения без утверждений о компании выбери '
    'subject=reader, source_relation=not_needed, source_quote_id=none. '
    'Нейтральное обращение, приветствие, благодарность или поздравление без бизнес-утверждения '
    'имеет speech_act=social_expression, subject=reader/general, invitation_kind=none. '
    'Приглашение воспользоваться конкретной услугой (invitation_kind=service_access) '
    'содержит утверждение о её доступности, даже когда действие совершает читатель. '
    'Если источник прямо подтверждает эту услугу и условия, выбери entails и её source_quote_id. '
    'Для названия, тематической метки, хэштега или элемента навигации есть speech_act=label. '
    'Отсутствие предложения с глаголом не означает uncertain: классифицируй смысл метки. '
    'Тематическая метка без бизнес-утверждения имеет subject=general, '
    'statement_class=general_information, source_relation=not_needed, source_quote_id=none, '
    'invitation_kind=none и verdict=supported. Формат метки не освобождает содержание от проверки: '
    'утверждение об услуге, условии, свойстве или результате компании внутри метки сохраняет '
    'subject=business, бизнес-класс и требует точного источника; при неясном смысле выбери uncertain. '
    'Для общей информации, редакционного намерения и разрешённого вымысла источник '
    'бизнеса также не нужен. Не принимай предварительную оценку редактора за доказательство.')


def bind_interpretation(decision, *, require=False, business_scope=False):
    """Canonicalize scope from explicit semantic roles, never from a keyword list."""
    present = any(key in decision for key in SEMANTIC_PROPERTIES)
    if not present and not require:
        return dict(decision)  # explicit injected legacy adapters remain supported
    for key, enum in (('speech_act', SPEECH_ACTS), ('assertion_subject', SUBJECTS),
                      ('source_relation', RELATIONS)):
        if decision.get(key) not in enum:
            raise ValueError('Нет допустимого семантического поля ' + key)
    out = dict(decision)
    act, subject, relation = (out[key] for key in SEMANTIC_PROPERTIES)
    invitation=out.get('invitation_kind')
    business_classes = {'business_fact', 'service_outcome', 'commercial_condition', 'operational_process'}
    if (act == 'uncertain' or subject == 'unclear'
            or (act == 'invitation' and out.get('invitation_kind') == 'uncertain')):
        out.update(statement_class='uncertain', verdict='uncertain')
        return out
    service_access = invitation == 'service_access'
    if business_scope and subject not in ('business','unclear') and not service_access:
        raise ValueError('Роль субъекта противоречит явному действию компании в проверяемом фрагменте; '
            'проверь все части фразы и укажи assertion/business для обещания компании. '
            'При неустановленной роли выбери uncertain/unclear, не выдумывай неподтверждённый факт.')
    if business_scope or subject == 'business' or service_access:
        if out.get('statement_class') not in business_classes:
            out['statement_class'] = 'business_fact'
        if out.get('verdict') == 'supported' and relation != 'entails':
            out.update(verdict='unsupported', source_quote_id='none')
        return out
    # Communication can accompany advice or a question. It cannot waive a
    # company assertion or service availability: those were handled above.
    if (subject=='reader' and act in ('advice','question','social_expression')
            and invitation=='communication' and relation=='not_needed'
            and out.get('source_quote_id')=='none'):
        out['invitation_kind']='none'
        invitation='none'
    if (act=='invitation' and invitation not in ('communication','uncertain')
            or act!='invitation' and invitation not in (None,'none')):
        raise ValueError('invitation_kind не соответствует речевому акту')
    if subject == 'reader' and act in ('advice', 'question', 'invitation'):
        if relation != 'not_needed' or out.get('source_quote_id') != 'none':
            raise ValueError('Совет/вопрос читателю ошибочно выдан за подтверждение бизнес-факта')
        out['statement_class'] = {'advice': 'general_information',
                                  'question': 'audience_prompt',
                                  'invitation': 'invitation_cta'}[act]
        out['verdict'] = 'supported'
    elif act=='social_expression' and subject in ('reader','general'):
        if relation!='not_needed' or out.get('source_quote_id')!='none':
            raise ValueError('Нейтральное обращение не является подтверждением бизнес-факта')
        out.update(statement_class='audience_prompt',verdict='supported')
    elif out.get('statement_class') in business_classes:
        raise ValueError('Бизнес-класс противоречит указанному субъекту утверждения')
    if out.get('statement_class') not in business_classes and (
            relation != 'not_needed' or out.get('source_quote_id') != 'none'):
        raise ValueError('Нефактический речевой акт не может доказывать бизнес-факт цитатой')
    return out
