"""Bounded, owned native media sampling for independent visual QA.

Every FFmpeg extraction is owned by the same native process boundary as local
media rendering. No unbounded subprocess.run/PIPE or surviving grandchildren.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path

from media_subprocess_runtime import run_owned_media_process
from project_file_boundary import reject_path_link


def _time(value, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (value <= 0 if positive else value < 0)):
        raise ValueError('Invalid QA media sample time')
    return value


def extract_frame(source, destination, at_seconds):
    """Extract one fresh image; stale frames cannot certify a later QA run."""
    _time(at_seconds)
    source=reject_path_link(Path(source))
    target=reject_path_link(Path(destination))
    if not target.parent.is_dir() or target.suffix.lower()!='.png':
        raise ValueError('Invalid QA frame destination')
    # Do not let an old frame satisfy a new, failed extraction.
    target.unlink(missing_ok=True)
    code,_,_=run_owned_media_process(
        ['ffmpeg','-nostdin','-v','error','-n','-ss',str(at_seconds),
         '-i',str(source),'-frames:v','1',str(target)],timeout=60)
    if code or not target.is_file() or target.is_symlink() or target.stat().st_size==0:
        target.unlink(missing_ok=True)
        raise ValueError('QA frame extraction failed')
    return target


def extract_pcm(source, offset, duration):
    """Capture at most two seconds of bounded mono PCM for audio comparison."""
    _time(offset)
    _time(duration,positive=True)
    source=reject_path_link(Path(source))
    length=min(duration,2)
    code,data,_=run_owned_media_process(
        ['ffmpeg','-nostdin','-v','error','-ss',str(offset),'-i',str(source),
         '-t',str(length),'-vn','-ac','1','-ar','8000','-f','s16le','-'],
        timeout=60,capture_stdout=True)
    if code or not data or len(data)>32000 or len(data)%2:
        raise ValueError('QA audio extraction failed')
    samples=array('h')
    samples.frombytes(data)
    return list(samples)[::4]
