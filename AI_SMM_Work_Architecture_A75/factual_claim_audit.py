# -*- coding: utf-8 -*-
"""Conservative deterministic protection against invented business assertions.

Only explicit claims forbidden by the supplied brief can be rejected here.
All findings carry verbatim text evidence; no model text is trusted as a quote.
This is one independent layer, NOT a semantic quality replacement.
"""
from __future__ import annotations
import re

PRICE = re.compile(r'(?<![\w])\d[\d\s.,]{0,12}\s*(?:руб(?:лей|ля|\.)?\b|₽|тенге\b|₸|USD\b|EUR\b|доллар(?:ов|а)?\b|евро\b)',re.I)
ADDRESS = re.compile(r'\b(?:ул\.|улица|проспект|пр-т|переулок|адрес\s*:)\s*[А-Яа-яЁёA-Za-z0-9\s-]{2,65}?\s+\d+[А-Яа-яA-Za-z]?\b',re.I)
PROMISE = re.compile(r'\bгарантир(?:уем|уется|ован|ую|ия)\b[^\n.]{0,100}?\b(?:за|в течение)\s+\d+\s*(?:день|дня|дней|час(?:а|ов)?|недел(?:и|ю|ь))',re.I)
# No absolute list of banned topics: user brief determines what facts are supported.
FREE_OFFER = re.compile(r'\bбесплатн\w*\s+(?:консультаци\w*|урок\w*|заняти\w*|диагностик\w*|услуг\w*|доставк\w*|пробн\w*\s+заняти\w*|провер\w*)', re.I)
UNSOURCED = re.compile(
    r'\b(?:нельзя\s+|не\s+(?:следует\s+|нужно\s+|надо\s+)?'
    r'(?:придум\w*|выдум\w*|фантазир\w*|утвержд\w*)|'
    r'без\s+(?:выдуманн\w*|подтвержд[её]нн\w*)|'
    r'неподтвержд[её]нн\w*|(?:процесс|этапы|порядок)\s+(?:не\s+)?(?:описан|указан|подтвержден))', re.I)

# Claims about delivered customer experience and the business's real process
# require the same provenance as prices or guarantees. Keep this inventory in
# the shared deterministic audit so every generation path gets one policy.
BUSINESS_EXPERIENCE = (
    # Broad grammatical ownership rule: first-person plural claims in a
    # business document are candidate facts even when the action verb is new
    # to the product domain. The source adjudicator decides whether the brief
    # actually supports them. This catches unseen process verbs without
    # maintaining a business-specific verb catalogue.
    (re.compile(r'\b(?:мы\s+|в\s+наш(?:ей|ем|их)\s+[а-яёa-z-]{3,}\s+)'
                r'[а-яёa-z-]{3,}(?:ем|им|аем|яем|уем|ируем|ываем|иваем|имся|емся|'
                r'ают|яют|уют|ируют|ывают|ивают|ятся|ются)\b[^.!?\n]{0,120}', re.I),
     'Не подтверждён заявленный компанией процесс или результат'),
    # Promotional attributes attached to the business are assertions too;
    # general praise in a reader-facing topic is not enough to match this
    # business-subject boundary.
    (re.compile(r'\b(?:мы|наш\w*|наша|наше|нашем|компани\w*|бренд\w*|'
                r'студи\w*|мастерск\w*|атель\w*|клуб\w*|салон\w*|центр\w*)\b'
                r'[^.!?\n]{0,100}\b(?:профессиональн\w*|над[её]жн\w*|доступн\w*|'
                r'уникальн\w*|эффективн\w*|идеальн\w*|лучш\w*|безопасн\w*)\b', re.I),
     'Не подтверждена заявленная характеристика бизнеса'),
    # Russian promotional descriptions commonly put the attribute before the
    # business noun ("профессиональная мастерская", "доступный салон"). Keep
    # the subject boundary explicit so ordinary praise of a product or topic
    # is not treated as a company claim.
    (re.compile(r'\b(?:профессиональн\w*|над[её]жн\w*|доступн\w*|уникальн\w*|'
                r'эффективн\w*|идеальн\w*|лучш\w*|безопасн\w*|высококлассн\w*|'
                r'премиальн\w*|качественн\w*)\b[^.!?\n]{0,55}\b(?:компани\w*|бренд\w*|'
                r'студи\w*|мастерск\w*|атель\w*|клуб\w*|салон\w*|центр\w*|клиник\w*|'
                r'школ\w*|агентств\w*)\b', re.I),
     'Не подтверждена заявленная характеристика бизнеса'),
    # Attribute customer comfort only when the sentence actually presents it
    # as a feature of the company's service/space. A generic content topic such
    # as comfortable riding is not a promise made by the business.
    (re.compile(r'\b(?:заняти\w*|урок\w*|курс\w*|групп\w*|обстановк\w*)[^.!?\n]{0,55}\bкомфорт\w*|\bкомфорт\w*[^.!?\n]{0,55}\b(?:заняти\w*|урок\w*|курс\w*|групп\w*|обстановк\w*)', re.I), 'Не подтверждено обещание комфорта клиентского опыта'),
    (re.compile(r'\b(?:заняти\w*|урок\w*|обучени\w*|курс\w*)[^.!?\n]{0,45}\bэффективн\w*|\bэффективн\w*[^.!?\n]{0,45}\b(?:заняти\w*|урок\w*|обучени\w*|курс\w*)', re.I), 'Не подтверждено заявление об эффективности услуги'),
    (re.compile(r'\b(?:да[её]т|да[её]м|предоставля\w*|обеспечива\w*|получа\w*|получают|включа\w*)[^.!?\n]{0,55}\bобратн\w+\s+связ\w*|\bобратн\w+\s+связ\w*[^.!?\n]{0,55}\b(?:на\s+(?:заняти\w*|урок\w*)|преподавател\w*|мастер\w*|мы\s+(?:да[её]м|предоставля\w*|обеспечива\w*))', re.I), 'Не подтверждено наличие обратной связи'),
    (re.compile(r'\b(?:кажд\w*|больше|достаточн\w*)\s+вниман\w*', re.I), 'Не подтверждён уровень внимания клиентам'),
    (re.compile(r'\b(?:чувств\w*\s+себя\s+уверенн\w*|помога\w*\s+.{0,45}\bуверенн\w*|стан\w*\s+уверенн\w*)', re.I), 'Не подтверждено обещание результата для уверенности клиента'),
    (re.compile(r'\b(?:залог|гарант\w*|обеспеч\w*|сохран\w*)[^.!?\n]{0,45}\bбезопасност\w*|\b(?:полную|абсолютную)\s+безопасност\w*', re.I), 'Не подтверждено заявление о безопасности'),
    (re.compile(r'\b(?:быстр\w*|гарантированн\w*|заметн\w*)\s+(?:результат\w*|прогресс\w*)', re.I), 'Не подтверждено обещание результата/срока'),
    (re.compile(r'\b(?:заняти\w*|урок\w*|сеанс\w*)\s+(?:состоит|включает|проходит|строится)\b[^.!?\n]{0,120}', re.I), 'Не подтверждён фактический порядок оказания услуги'),
    (re.compile(r'\b(?:преподавател\w*|сотрудник\w*|мастер\w*|специалист\w*)\s+(?:показыва\w*|помога\w*|проверя\w*|объясня\w*|сопровожда\w*)\b[^.!?\n]{0,100}', re.I), 'Не подтверждено действие сотрудника/специалиста'),
    (re.compile(r'\bмы\s+(?:начина\w*|готов\w*|использу\w*|провод\w*|созда[её]м|формиру\w*|дела\w*|подбира\w*|суш\w*|обжига\w*|проверя\w*|обуча\w*)\b[^.!?\n]{0,160}|\bв\s+(?:студии|мастерской|компании)\b[^.!?\n]{0,70}\b(?:заняти\w*|услуг\w*|процесс\w*)[^.!?\n]{0,60}\b(?:включа\w*|состо\w*|начина\w*|проход\w*|стро\w*)\b[^.!?\n]{0,120}', re.I), 'Не подтверждён фактический процесс/метод или действие компании'),
    (re.compile(r'\b(?:в\s+(?:студии|мастерской)|на\s+(?:занятиях|уроках))\b[^.!?\n]{0,100}\b(?:вы|участник\w*|ученик\w*)\s+(?:узнаете|научитесь|сможете|будете)\b[^.!?\n]{0,140}\b(?:подготов\w*|созда\w*|сформир\w*|формовк\w*|суш\w*|обжиг\w*|настро\w*|провер\w*)', re.I), 'Не подтверждено, что услуга включает обучение конкретным шагам/методам'),
    (re.compile(r'\b(?:мы|у\s+нас|в\s+(?:студии|мастерской|компании))\b[^.!?\n]{0,100}\b(?:использу\w*|работа\w*\s+с|есть|име\w*|предоставля\w*)\b[^.!?\n]{0,100}\b(?:материал\w*|оборудовани\w*|инструмент\w*|печ\w*|глин\w*|краск\w*)', re.I), 'Не подтверждено наличие конкретных материалов/оборудования'),
)

_AUDIENCE_NEED = re.compile(
    r'^\s*(?:(?:потребност\w*|желани\w*|интерес\w*\s+аудитори\w*)\s*[:—-]|'
    r'(?:аудитори\w*|клиент\w*|ученик\w*|учащ\w*|посетител\w*)\s+(?:хот\w*|жел\w*|стрем\w*|интересу\w*|ищ\w*)\b|'
    r'(?:для\s+аудитори\w*|люд\w*|учащ\w*|ученик\w*|клиент\w*)\s+важн\w*\b)',re.I)
_BUSINESS_PREDICATE = re.compile(
    r'\b(?:мы|у\s+нас|наш\w*|предоставля\w*|да[её]т\s+обратн\w*\s+связь|'
    r'обеспечива\w*|помога\w*\s+(?:вам|клиент\w*|ученик\w*)|гарантир\w*|'
    r'в\s+(?:студии|мастерской|компании))\b',re.I)

def _business_experience_findings(request: str, document: str, source: str,
                                  verified_sources: str = '') -> list[dict]:
    findings=[]
    line_offset=0
    for raw_line in document.splitlines(keepends=True):
        line=raw_line.rstrip('\r\n')
        # Tables often contain both a proposed visual and factual caption in one
        # source line. Review cells separately so a staged visual cannot mask
        # an unsupported claim in another field.
        cell_cursor=0
        for cell in re.split(r'\|',line):
          cell=cell.strip()
          cell_position=line.find(cell,cell_cursor) if cell else -1
          if cell_position>=0: cell_cursor=cell_position+len(cell)
          if re.search(r'\b(?:постановочн\w*|иллюстративн\w*|сцен\w*\s+(?:для\s+)?съ[её]мк\w*|макет)\b',cell,re.I):
            continue
          # Split prose into claims; a conditional CTA is not a service fact.
          sentence_cursor=0
          for sentence in re.split(r'(?<=[.!?])\s+|(?<=\n)', cell):
            sentence_position=cell.find(sentence,sentence_cursor) if sentence else -1
            if sentence_position>=0: sentence_cursor=sentence_position+len(sentence)
            normalized=sentence.casefold().replace('ё','е').strip(' >-*\t')
            if not normalized:
                continue
            # Audience motivation is not evidence of an offered feature. Keep
            # it when it is explicitly framed as a need, unless the same clause
            # also says the business provides the feature.
            if (_AUDIENCE_NEED.search(sentence.strip(' >-*\t'))
                    and not _BUSINESS_PREDICATE.search(sentence)):
                continue
            if re.match(r'^(?:если\s+(?:вы|вам)\b|хотите\s+ли\b|цель\s*[:—-]|(?:наша|проектная)\s+цель\b)', normalized):
                continue
            if normalized.endswith('?'):
                continue
            for pattern,reason in BUSINESS_EXPERIENCE:
                for match in pattern.finditer(sentence):
                    absolute_start=line_offset+max(cell_position,0)+max(sentence_position,0)+match.start()
                    absolute_end=absolute_start+(match.end()-match.start())
                    if _is_explicitly_prohibited(document, absolute_start, absolute_end):
                        continue
                    local=sentence.casefold()
                    if ('telegram' in local and re.search(r'\b(?:получа\w*|получают)\b',local)
                            and 'обратн' in match.group(0).casefold()):
                        continue
                    # A shared regex family is not evidence that this specific
                    # business action is authorized. A candidate is grounded
                    # only when one affirmative source sentence carries its
                    # specific content terms; semantic disputes still go to
                    # the typed source adjudicator.
                    source_grounded=False
                    if request:
                        from claim_support import _claim_source_overlap, _source_quote_authority
                        for source_sentence in re.split(r'(?<=[.!?])\s+|\n+',request):
                            authoritative,_=_source_quote_authority(source_sentence)
                            if not authoritative:
                                continue
                            matched,total=_claim_source_overlap(match.group(0),source_sentence)
                            if matched>=2 and matched/max(total,1)>=0.65:
                                source_grounded=True
                                break
                    if _claim_authorized_in_request(request, match.group(0)) or source_grounded:
                        continue
                    findings.append({'document':source,'type':'invented_fact',
                        'quote':match.group(0).strip(),'reason':reason,
                        'evidence_segment':sentence.strip()})
        line_offset+=len(raw_line)
    return findings


def _claim_authorized_in_request(request: str, claim: str) -> bool:
    """Exact wording inside a prohibition is not authorization for the claim."""
    source=request.casefold();needle=claim.casefold()
    start=source.find(needle)
    while start>=0:
        prefix=source[max(0,start-150):start]
        if not re.search(r'(?:не\s+(?:придумывай|выдумывай|предлагай|упоминай|обещай)|'
                         r'не\s+допускается|запрещено|без\s+выдуманн\w*)[^.!?\n]{0,130}$',prefix):
            return True
        start=source.find(needle,start+1)
    return False


def _is_explicitly_prohibited(document: str, start: int, end: int) -> bool:
    """A warning or explicit statement of absence is not itself an offer."""
    prefix=document[max(0,start-220):start].casefold().replace('ё','е')
    # Do not audit a brief's copied restriction, a confirmed gap, or an
    # explicit statement that a business feature is not offered as though it
    # were a positive commercial claim.
    if re.search(r'(?:без\s+(?:обещан\w*|гарант\w*|указан\w*|подтвержден\w*)|'
                 r'нельзя\s+(?:упоминать|предлагать|обещать|заявлять|придумывать)|'
                 r'не\s+(?:подтвержден\w*|указан\w*|известен|предусмотрен\w*|предоставля\w*)\s*[:—-]?)'
                 r'[^.!?\n]{0,180}$',prefix):
        return True
    if re.search(
        r'(?:не\s+(?:упоминать|предлагать|обещать|обеща\w*|добавлять|заявлять|придумывать|выдумывать)|'
        r'не\s+допускается\s+упоминание|запрещено\s+(?:упоминать|предлагать)|'
        r'не\s+предлагать)\s+(?:о\s+)?[^.!?\n]{0,140}$',prefix):
        return True
    if re.search(r'(?:вопросы?\s+(?:для\s+)?(?:подтверждени\w*|уточнени\w*)|'
                 r'(?:не\s+подтвержден\w*|неизвестн\w*|не\s+указан\w*)\s*[:—-])'
                 r'[^\n]{0,220}$',prefix):
        return True
    # Bullets beneath an explicit "to verify" heading are unresolved research
    # questions, even when the heading is several lines above the item.
    section_start=max(document.rfind('\n## ',0,start),document.rfind('\n### ',0,start),
                      document.rfind('\n#### ',0,start),document.rfind('\n##### ',0,start))
    section_prefix=document[section_start if section_start>=0 else 0:start].casefold()
    if re.search(r'(?:необходимо\s+подтвердить|вопросы?\s+для\s+подтверждения|'
                 r'что\s+нужно\s+уточнить)\s*[:—-]?',section_prefix):
        return True
    # Summaries often repeat the user's prohibition as "нет информации о
    # бесплатных занятиях" or "данных о скидках нет". Those are explicitly
    # negative mentions, not commercial offers.
    sentence=document[max(document.rfind('.',0,start),document.rfind('!',0,start),
                          document.rfind('?',0,start),document.rfind('\n',0,start))+1:
                     min([x for x in (document.find('.',end),document.find('!',end),
                                      document.find('?',end),document.find('\n',end)) if x>=0]
                         or [len(document)])].casefold().replace('ё','е')
    # Absence can itself be sold as a promise ("гарантируем отсутствие
    # поломок"). In that construction it is an affirmative outcome claim,
    # never a disclosure that the business lacks a feature.
    affirmative_absence_promise = re.search(
        r'\b(?:гарантир\w*|обеща\w*|обеспеч\w*|исключа\w*|предотвраща\w*|'
        r'защища\w*)\b[^.!?\n]{0,90}\b(?:отсутств\w*|нет\b|без\s+риска|'
        r'никак\w*\s+(?:поломок|ошибок|проблем))', sentence, re.I)
    if (not affirmative_absence_promise and re.search(r'\b(?:нет|отсутств\w*|не\s+(?:указан\w*|предусмотрен\w*|предоставля\w*|'
                 r'предлага\w*|провод\w*|сообщен\w*|известн\w*))[^.!?\n]{0,200}',sentence)):
        return True
    if (not affirmative_absence_promise and re.search(r'\b(?:нет\s+(?:сведен\w*|данн\w*|информаци\w*)|'
                 r'(?:сведен\w*|данн\w*|информаци\w*)\s+нет)\b',sentence)):
        return True
    return False


def _verified_source_text(evidence_context) -> str:
    if evidence_context is None:
        return ''
    try:
        from evidence_context import coerce_evidence_context
        value = coerce_evidence_context(evidence_context)
        return '\n'.join(s.content for s in value.sources if s.source_type != 'user_request')
    except (ImportError, TypeError, ValueError):
        return ''


def scan_unsupported_business_claims(request: str, strategy: str, calendar: str,
                                     evidence_context=None, *,
                                     include_semantic_facts: bool = True) -> list[dict]:
    if type(include_semantic_facts) is not bool:
        raise TypeError('include_semantic_facts должен быть bool')
    if not all(isinstance(x,str) for x in (request,strategy,calendar)):
        raise TypeError('Требования и документы должны быть строками')
    restrictions=request.casefold()
    policies=[]
    if any(x in restrictions for x in ('цен','стоимост','прайс','сумм')):
        policies.append((PRICE,'invented_fact','Не подтверждена указанная конкретная стоимость'))
    if any(x in restrictions for x in ('адрес','локаци','местоположени')):
        policies.append((ADDRESS,'invented_fact','Не подтверждён конкретный адрес компании'))
    if any(x in restrictions for x in ('гарант','обещан','срок')):
        policies.append((PROMISE,'unsafe_claim','Не подтверждена гарантия конкретного срока'))
    # Free services are concrete commercial conditions, not neutral creative
    # ideas. Flag an unconfirmed free offer when user explicitly bans invention
    # of conditions/pricing. The supplied brief can authorize the exact offer.
    if any(x in restrictions for x in ('цен','стоимост','прайс','услови','бесплатн')):
        policies.append((FREE_OFFER,'invented_fact','Не подтверждённое предложение бесплатной услуги'))
    findings=[]
    if not UNSOURCED.search(request):
        # Historical numeric/offer checks are only activated by a relevant
        # user instruction. Otherwise the audit would invent requirements.
        policies=[]
    verified_sources = _verified_source_text(evidence_context)
    support_context = request + ('\n' + verified_sources if verified_sources else '')
    for source, document in (('strategy',strategy),('calendar',calendar)):
        if include_semantic_facts:
            findings.extend(_business_experience_findings(support_context,document,source,
                                                           verified_sources))
        for pattern,kind,reason in policies:
            for match in pattern.finditer(document):
                claim=match.group(0).strip()
                if _is_explicitly_prohibited(document,match.start(),match.end()):
                    continue
                # If the fact is explicitly supplied in the original brief,
                # it is not an invention for this deterministic validator.
                if _claim_authorized_in_request(request,claim):
                    continue
                # Avoid treating conditional examples as verified company facts.
                prefix=document[max(0,match.start()-60):match.start()].casefold()
                if re.search(r'(?:например|условн|гипотетич|если|в примере)\s*[:—-]?\s*$',prefix):
                    continue
                findings.append({'document':source,'type':kind,'quote':claim,'reason':reason})
    return list({(x['document'], x['type'], x['quote'], x['reason']): x for x in findings}.values())
