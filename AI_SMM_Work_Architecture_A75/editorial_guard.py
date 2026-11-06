"""Task-aware production metadata and conservative publication risk checks.

No claim of semantic fact verification: these are deterministic safeguards. Source
materials are *requirements for production*, never claims of owned media.
"""
import re
from factual_claim_audit import scan_unsupported_business_claims
from difflib import SequenceMatcher


def add_production_materials(post, slot):
    """Populate only missing operational metadata; never invent business facts.

    The field is a request to obtain/create resources, not an assertion that
    images, staff, case studies, inventory or services already exist.
    """
    from task_semantics import ai_content_review_active
    if ai_content_review_active():return post,False
    if not isinstance(post, dict):
        return post, False
    # Copy only task/slot metadata that is already confirmed by the planner.
    # This does not manufacture business claims, performance results or images.
    result = dict(post)
    changed = False
    audience = str(slot.get('audience') or '').strip()
    if not str(result.get('audience') or '').strip() and audience and audience != 'Определить из запроса':
        result['audience'] = audience
        changed = True
    if not str(result.get('measurement') or '').strip():
        kpi = str(result.get('kpi') or '').casefold()
        if re.search(r'сохранени|просмотр|охват|лайк|коммент|ответ|реакци|репост|подпис', kpi):
            result['measurement'] = ('Посмотреть доступные показатели публикации в статистике Instagram; '
                                     'недоступные значения обозначить как требующие проверки')
            changed = True
    if isinstance(result.get('materials'), str) and result['materials'].strip():
        return result, changed
    fmt = str(slot.get('format') or '').casefold()
    topic = str(slot.get('topic') or '').strip()
    visual = str(post.get('visual') or '').strip()
    if 'reels' in fmt or 'рилс' in fmt:
        medium = 'видеофрагменты или иллюстрированные сцены и экранные титры'
    elif 'stories' in fmt or 'сторис' in fmt:
        medium = 'вертикальные макеты экранов и элементы интерактива'
    else:
        medium = 'макеты слайдов, схемы или фотографии с разрешением на использование'
    # Constrain length and avoid treating proposed visual as already available.
    details = ' '.join(re.findall(r'(?:Кадр|Слайд|Экран)\s*\d+[^.]{0,90}', visual, flags=re.I)[:2])
    addition = (f'Необходимо подготовить {medium} для темы «{topic}». '
                'Реальные фото, демонстрации, характеристики и заявления согласовать '
                'с владельцем до публикации; отсутствие материалов не заменять вымышленными кейсами.')
    if details:
        addition += ' Ориентиры для подготовки: ' + details[:160].strip()
    result['materials'] = addition
    return result, True


def editorial_risks(post, request='', evidence_context=None):
    """Block clear risky advice and unverified first-person service claims.

    This is a high-precision fallback, not a substitute for expert verification.
    """
    from task_semantics import ai_content_review_active
    if ai_content_review_active():
        from publication_schema import publication_structure_issues
        return publication_structure_issues(post)
    if not isinstance(post, dict):
        return ['Ответ модели не содержит публикацию']
    content = ' '.join(str(post.get(k) or '') for k in ('caption', 'visual', 'cta'))
    folded = content.casefold()
    user = str(request or '').casefold()
    issues = []
    risky_patterns = (
        (r'(?:суши\w*|высуши\w*|сушк\w*)\W+(?:\w+\W+){0,5}(?:на\s+батаре\w*|на\s+радиатор\w*)',
         'Сушка одежды на батарее не является универсальной безопасной рекомендацией'),
        (r'(?:все|любые|кажд\w*)\W+(?:\w+\W+){0,4}(?:ткан\w*|вещ\w*|корм\w*|животн\w*)\W+(?:\w+\W+){0,5}(?:можно|нужно|обязательно)',
         'Категоричное универсальное правило требует проверки применимости'),
    )
    for pat, label in risky_patterns:
        if re.search(pat, folded, re.I):
            issues.append(label)
    # Stronger limitation applies only when the actual request explicitly says
    # service details are unconfirmed, not to every client with confirmed facts.
    no_service = bool(re.search(r'(?:не\s+(?:придумывай|выдумывай|утверждай)|не\s+подтвержден\w*|нет\s+подтвержден\w*)\W+(?:\w+\W+){0,14}(?:услуг\w*|перечен\w*\s+услуг\w*|ассортимент\w*)',user))
    if no_service and re.search(r'\b(?:мы\s+(?:поможем|выполним|подгоним|предлагаем|оказываем)|закажи\w*\s+у\s+нас|у\s+нас\s+вы\s+можете|получите\s+(?:совет|консультацию)\s+(?:от\s+)?(?:нашего\s+)?эксперт\w*)\b',folded,re.I):
        issues.append('Неподтверждённое обещание услуги или возможности компании')
    for finding in scan_unsupported_business_claims(request, '', content, evidence_context,
                                                    include_semantic_facts=False):
        if finding['document'] == 'calendar':
            issues.append(f"{finding['reason']}: {finding['quote']!r}")
    return list(dict.fromkeys(issues))


def copy_too_similar(new_caption, previous_captions):
    """Reject highly similar copy even when a few words or CTAs change."""
    from task_semantics import ai_content_review_active
    if ai_content_review_active():return False
    def normal(s):
        return re.sub(r'\W+', ' ', str(s or '').casefold()).strip()
    candidate = normal(new_caption)
    if len(candidate) < 65:
        return False
    for prev in previous_captions:
        before = normal(prev)
        if candidate == before:
            return True
        if len(before) < 65:
            continue
        ratio = SequenceMatcher(None, candidate[:1800], before[:1800], autojunk=False).ratio()
        if ratio >= 0.88:
            return True
    return False
