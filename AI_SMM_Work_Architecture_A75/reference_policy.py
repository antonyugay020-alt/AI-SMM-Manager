"""Shared opt-in boundary for GUI and standalone media workers.

Ambiguous or negative references never authorize reading protected pixels.
This conservative grammar recognizes explicit use requests, not bare mentions.
"""
import re

_REFERENCE = r'(?:эталон\w*|референс\w*|reference(?:\s+image)?|образец|образц\w*)'
_USE = r'(?:использ\w*|учт\w*|учитыв\w*|ориентир\w*|сравни\w*|возьми|возьм\w*|повтори|use|using|match|follow|compare)'


def reference_requested(instruction):
    text = str(instruction or '').casefold()
    if not re.search(r'\b'+_REFERENCE+r'\b', text):
        return False
    # Negative intent wins in a conflicting instruction. Ask for a clearer
    # command rather than guessing that a protected input was authorized.
    if re.search(r'\b(?:не|без|никогда|not|never|without|ignore|исключ\w*)\b[^.!?;\n]{0,90}\b'+_REFERENCE+r'\b', text):
        return False
    return bool(re.search(r'\b'+_USE+r'\b[^.!?;\n]{0,90}\b'+_REFERENCE+r'\b', text)
                or re.search(r'\b(?:как\s+на|по|с\s+уч[её]том)\s+'+_REFERENCE+r'\b', text))


def explicit_reference(reference, enabled=False, *, source=None):
    """Runtime authority: semantic boolean or explicit user choice, never text."""
    if enabled is not None and type(enabled) is not bool:
        raise ValueError('Reference permission must be an exact boolean')
    if enabled is not True:return None
    if not reference:raise ValueError('Reference requested but not supplied')
    if source is not None:
        from pathlib import Path
        a,b=Path(source),Path(reference)
        if a.resolve()==b.resolve() or (a.exists() and b.exists() and a.samefile(b)):
            raise ValueError('Source and reference must be different files')
    return reference
