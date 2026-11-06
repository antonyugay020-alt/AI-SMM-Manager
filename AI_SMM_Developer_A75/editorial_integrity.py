"""Stage 71: high-precision, domain-neutral cross-field editorial safeguards.

A structural/consistency safeguard, not an expert fact-checker. No hidden business
facts are inferred and the model never receives blanket permission to invent them.
"""
import re


def _fold(value):
    return str(value or '').casefold()


def content_consistency_issues(post, slot, request=''):
    """Return blocking inconsistencies with the specified task/format.

    Keep this gate narrow; uncertain factual judgments remain for the independent
    semantic editor. Reject only situations that can be proven from task metadata.
    """
    from task_semantics import ai_content_review_active
    if ai_content_review_active():
        from publication_schema import publication_structure_issues
        return publication_structure_issues(post,slot)
    if not isinstance(post, dict):
        return ['Публикация должна быть объектом']
    fmt = _fold(slot.get('format'))
    caption = _fold(post.get('caption'))
    cta = _fold(post.get('cta'))
    kpi = _fold(post.get('kpi'))
    measurement = _fold(post.get('measurement'))
    task = _fold(request)
    problems=[]

    # An explicit self-reference to another media format is a demonstrable error,
    # irrespective of the business niche or editorial tone.
    if 'stories' in fmt or 'сторис' in fmt:
        if re.search(r'\b(?:в\s+этом|этот|наш|смотрите)\s+(?:reel\w*|рилс\w*|reels\w*)\b', caption):
            problems.append('Текст Stories ошибочно описывает публикацию как Reels')
        if re.search(r'\bкомментари\w*\s+в\s+посте\b', measurement):
            problems.append('Для Stories указан сбор комментариев обычного поста: уточнить доступную механику')
    elif 'reels' in fmt or 'рилс' in fmt:
        if re.search(r'\b(?:в\s+этой|эта|наша)\s+(?:stories|сторис)\b', caption):
            problems.append('Текст Reels ошибочно описывает публикацию как Stories')

    # Numeric growth targets without actual historical metrics are not verified
    # KPIs. A qualitative goal or absolute count observed *after* posting is fine.
    # Apply only when the user expressly states that statistics/base is missing.
    no_baseline = bool(re.search(
        r'(?:статистик\w*|базов\w*\s+(?:метрик\w*|показател\w*|значени\w*)|'
        r'исходн\w*\s+(?:метрик\w*|показател\w*))[^.!?\n]{0,140}'
        r'(?:не\s+предоставлен\w*|неизвестн\w*|отсутству\w*|нет\b)', task)
        or re.search(
        r'(?:не\s+предоставлен\w*|неизвестн\w*|отсутству\w*|нет\b)[^.!?\n]{0,140}'
        r'(?:статистик\w*|базов\w*\s+(?:метрик\w*|показател\w*|значени\w*))',task))
    numeric_target = bool(re.search(r'\+\s*\d+(?:[.,]\d+)?\s*%?',kpi) or
                          re.search(r'(?:рост|увеличени\w*|прирост)\W{0,20}\d+(?:[.,]\d+)?\s*%',kpi))
    if no_baseline and numeric_target and not re.search(r'(?:гипотез\w*|ориентир\w*|условн\w*\s+цел\w*|после\s+замера\s+баз\w*)',kpi):
        problems.append('Числовой прирост KPI не обоснован: исходная статистика не предоставлена')

    # A production plan cannot invent an already-existing publication channel.
    # This is limited to an explicit task statement denying a known channel.
    no_site = bool(re.search(r'(?:сайт\w*|ссылк\w*)\W{0,30}(?:нет|отсутству\w*|не\s+предоставлен\w*)', task))
    if no_site and re.search(r'(?:перейд\w*|запиш\w*|куп\w*)\W{0,35}(?:на\s+(?:наш\w*\s+)?сайт|по\s+ссылк\w*\s+на\s+сайте)',cta):
        problems.append('CTA требует сайта, наличие которого отрицается в исходном задании')

    return list(dict.fromkeys(problems))
