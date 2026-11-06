"""Exact sentence spans shared by publication and compound evidence catalogs."""
from __future__ import annotations

import re


def sentence_spans(text: str):
    """Yield verbatim spans; an ordered-list marker belongs to its first sentence.

    This is structural segmentation, independent of topic, author, or language.
    Decimal points and list ordinals cannot become separate evidence claims.
    """
    for line in re.finditer(r'[^\n]+', str(text)):
        raw = line.group()
        marker = re.match(r'^\s*(?:\d+[.)]|[-*+])\s+', raw)
        protected = marker.end() if marker else 0
        start = 0
        ends = [m.end() for m in re.finditer(r'[.!?](?=\s|$)', raw)
                if m.start() >= protected]
        for end in [*ends, len(raw)]:
            if end <= start:
                continue
            value = raw[start:end]
            if end < len(raw) and re.fullmatch(r'\s*\d+[.)]\s*', value):
                continue
            left = len(value) - len(value.lstrip())
            right = len(value.rstrip())
            if right > left:
                yield line.start() + start + left, line.start() + start + right
            start = end
