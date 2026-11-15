"""Explicit output sizes, not platform duration claims or model-quality presets.

The existing durable request stores the actual width/height. These controls do
not crop time, start a provider, change source files or bypass model limits.
"""
EXPORT_SIZES=(
    ('portrait','Вертикальный 9:16 · 1080×1920',1080,1920),
    ('square','Квадрат 1:1 · 1080×1080',1080,1080),
    ('landscape','Горизонтальный 16:9 · 1920×1080',1920,1080),
    ('feed','Портрет 4:5 · 1080×1350',1080,1350),
)


def export_size(identifier):
    for key,label,width,height in EXPORT_SIZES:
        if identifier==key:return width,height
    raise ValueError('Unknown export size')
