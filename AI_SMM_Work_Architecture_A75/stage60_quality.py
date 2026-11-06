"""Editorial diagnostics; not a substitute for factual verification."""
from collections import Counter
from professional_content import count_calendar_entries
from content_quality_engine import inspect_post

def quality_warnings(content):
    rows=count_calendar_entries(content)
    warnings=[]
    if not rows: return warnings
    # Shared actions/metrics are legitimate. Their suitability is evaluated
    # against each goal by the substantive contract, not by duplicate counts.
    if any('гарантирует безопасность' in ' '.join(r).casefold() for r in rows):
        warnings.append('Обнаружены категоричные медицинские обещания')
    issue_counts=Counter()
    for row in rows:
        if len(row)>7:
            for issue in inspect_post({'caption':row[5], 'visual':row[4], 'cta':row[6], 'kpi':row[7]}):
                if issue['severity']=='critical':
                    issue_counts[issue['message']]+=1
    for message,count in issue_counts.items():
        warnings.append(f'Проверить фактический риск ({count}): {message}')
    if issue_counts:
        warnings.append('Не все фактические риски устранены; требуется адресное исправление')
    return warnings
