"""Stage 63: conservative editorial risk gate. Heuristics are not fact verification."""
import re

UNVERIFIED = [
 (r'(?:наш\w*|официальн\w*|сайт\w*\s+аптек\w*|на\s+сайт\w*\s+аптек\w*)\s+(?:сайт\w*|приложени\w*)', 'Не подтверждён сайт или приложение аптеки'),
 (r'(?:в|через|скача\w*|использу\w*)\s+(?:наш\w*\s+)?приложени\w*', 'Не подтверждено приложение аптеки'),
 (r'(?:запис\w*\s+на\s+(?:при[её]м|консультаци\w*)|горяч\w*\s+лини\w*|скача\w*\s+шаблон\w*|qr[- ]?код\w*\s+(?:на|со)\s+ссылк\w*)', 'Не подтверждена услуга или цифровой ресурс'),
 (r'(?:в\s+наш\w*\s+(?:магазин\w*|аптек\w*)\s+режим\s+работ\w*|режим\s+работ\w*\s*:\s*пн)', 'Не подтверждён график работы'),
 (r'(?:\d{1,2}[:.]\d{2}\s*[–-]\s*\d{1,2}[:.]\d{2})', 'Не подтверждены часы работы'),
 (r'(?:сда\w*|переда\w*|принима\w*)\W+(?:\w+\W+){0,8}(?:просроченн\w*|лекарств\w*)\W+(?:\w+\W+){0,8}аптек\w*', 'Не подтверждён приём лекарств на утилизацию'),
 (r'сда\w*\s+просроченн\w*\s+лекарств\w*\s+в\s+аптечн\w*', 'Не подтверждён приём лекарств на утилизацию'),
 (r'(?:утилизир\w*|просроченн\w*\s+лекарств\w*)\W+(?:\w+\W+){0,9}(?:специальн\w*\s+контейнер\w*|не\s+выбрасыва\w*\s+в\s+мусор)', 'Не подтверждён местный способ утилизации'),
 (r'(?:паспор\w*\s*[—-]\s*обязательн\w*|паспорт\s+и\s+\w+\s*[—-]\s*обязательн\w*)', 'Неподтверждённое обязательное требование'),
 (r'(?:гарантир\w*|гаранти\w*)\W+(?:\w+\W+){0,6}(?:безопасност\w*|эффективност\w*|излечени\w*|качеств\w*)', 'Категоричное медицинское обещание'),
 (r'(?:температур\w*\s+не\s+превыша\w*|все\s+лекарств\w*\s+хран\w*)\W+(?:\w+\W+){0,3}\d{1,2}\s*°?', 'Универсальная температура хранения'),
]
FILLER = ('удобно и безопасно — так вы всегда готовы', 'заботимся о вашем здоровье и комфорте',
          'мы всегда рядом', 'забота о здоровье начинается', 'врач — окончательный решитель',
          'неинформативный черновик', 'слишком кратко', 'текст будет добавлен', 'заглушка для текста')

def inspect_post(post):
    from task_semantics import ai_content_review_active
    if ai_content_review_active():
        from publication_schema import publication_structure_issues
        return [{'severity':'critical','message':x} for x in publication_structure_issues(post)]
    if not isinstance(post,dict):
        return [{'severity':'critical','message':'Некорректная структура публикации'}]
    caption=str(post.get('caption') or '')
    visual=str(post.get('visual') or '')
    cta=str(post.get('cta') or '')
    topic=str(post.get('topic') or '')
    kpi=str(post.get('kpi') or '')
    full=' '.join((caption,visual,cta)).casefold()
    issues=[]
    def add(level,msg):
        if msg not in [i['message'] for i in issues]: issues.append({'severity':level,'message':msg})
    for pattern,msg in UNVERIFIED:
        if re.search(pattern,full,re.I): add('critical',msg)
    if any(x in full for x in FILLER): add('critical','Шаблонная или неестественная формулировка')
    if 'кратким выводом из текста публикации' in visual.casefold() or 'понятную схему или список из 2–3 пунктов' in visual.casefold():
        add('critical','Визуальный сценарий подставлен шаблоном и требует конкретной редакторской разработки')
    if not caption.strip() and not re.search(r'(?:текст|надпись|реплик|озвучк)', visual, re.I):
        add('critical', 'Нет текста публикации или текста для размещения в сценах')
    if re.search(r'(?:переход\w*\s+на\s+сайт|звонк\w*\s+на\s+горяч\w*\s+лини\w*)',kpi.casefold()):
        add('warning','KPI зависит от неподтверждённого канала')
    if re.search(r'(?:возврат\w*|вернут\w*)',topic.casefold()) and not re.search(r'возврат|обмен|правил|закон|услови\w*\s+возврата',caption.casefold()):
        add('critical','Текст не раскрывает заявленную тему возврата')
    if re.search(r'утилиз',topic.casefold()) and 'уточн' not in caption.casefold() and 'местн' not in caption.casefold():
        add('warning','Требуется проверить местные правила утилизации')
    return issues

def critical_issues(post):
    return [x['message'] for x in inspect_post(post) if x['severity']=='critical']
