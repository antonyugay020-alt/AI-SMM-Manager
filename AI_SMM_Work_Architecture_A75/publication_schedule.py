"""Canonical publication slot schedule shared by plan, executor and audit."""
DAYS_BY_FREQUENCY = {
    1: ('Ср',),
    2: ('Вт', 'Пт'),
    3: ('Пн', 'Ср', 'Пт'),
    4: ('Пн', 'Вт', 'Чт', 'Пт'),
    5: ('Пн', 'Вт', 'Ср', 'Чт', 'Пт'),
    6: ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'),
    7: ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'),
}


def days_for_frequency(per_week):
    if per_week not in DAYS_BY_FREQUENCY:
        raise ValueError('Неподдерживаемое число публикаций в неделю')
    return DAYS_BY_FREQUENCY[per_week]


def publication_ids(weeks, per_week):
    return [f'{week}/{day}' for week in range(1, weeks+1) for day in days_for_frequency(per_week)]
