"""Persistent, conservative task facts extracted from explicit user replies.

No model calls; uncertain facts stay unknown. Project-scoped by design.
"""
import json
import re


def extract_facts(message, existing=None):
    facts = dict(existing or {})
    s = (message or '').strip()
    low = s.casefold()
    if not s:
        return facts
    channels = [name for name in ('instagram', 'telegram', 'tiktok', 'facebook', 'вконтакте', 'youtube') if name in low]
    if channels:
        facts['channels'] = ', '.join(channels)
    m = re.search(r'(\d{1,2})\s*(месяц(?:а|ев)?|недел[ьиь])', low)
    if m:
        facts['period'] = m.group(0)
    else:
        words = {'один':1,'одного':1,'два':2,'две':2,'три':3,'четыре':4,'пять':5,'шесть':6}
        wm = re.search(r'\b(один|одного|два|две|три|четыре|пять|шесть)\s+(месяц(?:а|ев)?|недел[ьиь])', low)
        if wm:
            facts['period'] = f"{words[wm.group(1)]} {wm.group(2)}"
    m = re.search(r'(?<!\d)(\d[\d\s]{2,8})\s*(?:₸|тенге|тг|kzt)', low)
    if m:
        facts['budget'] = m.group(1).replace(' ', '') + ' тенге'
    elif re.fullmatch(r'\s*\d{4,8}\s*', s) and ('budget' not in facts):
        # Bare numbers are ambiguous and must not be interpreted as budget.
        pass
    # Prefer explicitly labelled fields. The original hard-coded industry and
    # demographic word lists rejected valid, fully specified briefs.
    business = re.search(r'(?im)^\s*(?:[-*]\s*)?(?:бизнес|тип\s+бизнеса|ниша|сфера\s+деятельности)\s*[:：—-]\s*(.+?)\s*$', s)
    if business:
        value = business.group(1).strip().rstrip('.')
        if value and not re.match(r'^(?:не\s+указан|неизвест|нет\b)', value, re.I):
            facts['business'] = value[:250]
    elif not facts.get('business'):
        # Free-form briefs often begin with "для небольшого ... магазина".
        match = re.search(r'(?i)\bдля\s+((?:небольшого|маленького|локального|местного|розничного|семейного|частного|крупного|нового|онлайн|интернет[ -])?\s*[а-яёa-z-]+\s+(?:магазина|салона|кафе|ресторана|аптеки|студии|компании|бизнеса))\b', s)
        if match:
            facts['business'] = match.group(1).strip()[:250]
    # Capture audience as a multiline section up to the next numbered section
    # or a new labelled field. Preserve the user's wording, not an invented persona.
    audience = re.search(
        r'(?im)^\s*(?:[-*]\s*)?(?:основная\s+)?целевая\s+аудитория\s*[:：—-][ \t]*(.*)$', s)
    if audience:
        first = audience.group(1).strip()
        tail = s[audience.end():]
        lines = [first] if first else []
        for line in tail.splitlines():
            stripped = line.strip()
            if re.match(r'^(?:\d+[.)]\s+|(?:[-*]\s*)?(?:рекламный\s+бюджет|бюджет|срок\s+стратегии|частота\s+публикаций|основные\s+цели|площадка|важные\s+ограничения|контент-план)\s*[:：—-])', stripped, re.I):
                break
            if not stripped:
                if lines: break
                continue
            if len(' '.join(lines)) > 900: break
            lines.append(stripped)
        value = ' '.join(lines).strip()
        if value and not re.match(r'^(?:не\s+указана|неизвестна|нет\b)', value, re.I):
            facts['audience'] = value[:1000]
    elif not facts.get('audience'):
        match = re.search(r'(?im)^\s*(?:аудитория|для\s+кого)\s*[:：—-]\s*(.+)$', s)
        if match and match.group(1).strip():
            facts['audience'] = match.group(1).strip()[:500]
    if re.search(r'пока никаких|не знаю|неизвестн|пока нет ограничен', low):
        facts['restrictions'] = 'не указаны пользователем; проверить применимые требования'
    return facts


def load_facts(db, project_id):
    row = db.execute('SELECT facts_json FROM ai_task_facts WHERE project_id=?', (project_id,)).fetchone()
    try:
        value=json.loads(row[0]) if row else {}
        return value if isinstance(value,dict) else {}
    except (ValueError, TypeError):
        return {}


def update_facts(db, project_id, message, project_description='', *, reset=False):
    """Write task facts in a complete SQLite unit of work.

    Standalone callers own one durable commit; callers with an active result
    transaction keep ownership of that transaction. A failure never commits
    unrelated work or leaves a partial task-facts replacement behind.
    """
    from sqlite_transaction_core import compose, own
    with (compose(db, 'task_facts') if db.in_transaction else own(db)):
        current = {} if reset else load_facts(db, project_id)
        if project_description:
            current = extract_facts(project_description, current)
        new = extract_facts(message, current)
        db.execute('INSERT OR REPLACE INTO ai_task_facts(project_id,facts_json) VALUES(?,?)',
                   (project_id, json.dumps(new, ensure_ascii=False)))
    return new


def missing_essentials(facts):
    return [key for key in ('business', 'audience', 'period', 'channels') if not facts.get(key)]


def question_for_missing(missing):
    questions = {'business':'Какой бизнес продвигаем?',
                 'audience':'Кто основная целевая аудитория?',
                 'period':'На какой период нужен контент-план?',
                 'channels':'На каких площадках планируете продвижение?'}
    return ' '.join(questions[key] for key in missing[:2])
