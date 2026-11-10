"""Bounded FFmpeg frame artifacts for independent media QA (no cloud/GPU)."""
from pathlib import Path
import shutil
import sys

import pytest
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
import qa_media_tools as m


def test_large_source_is_bounded(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    source=tmp_path/'large.png'
    with Image.new('RGB',(2048,1400),(90,110,120)) as image:
        image.save(source)
    target=m.extract_frame(source,tmp_path/'frame.png',0)
    with Image.open(target) as image:
        assert max(image.size)<=m.MAX_QA_FRAME_SIDE
    assert 0<target.stat().st_size<=m.MAX_QA_FRAME_BYTES


def test_stale_frame_removed_after_native_exception(tmp_path,monkeypatch):
    target=tmp_path/'frame.png'
    target.write_bytes(b'stale')
    def run(*args,**kwargs):
        target.write_bytes(b'partial')
        raise TimeoutError('native deadline')
    monkeypatch.setattr(m,'run_owned_media_process',run)
    with pytest.raises(TimeoutError,match='native deadline'):
        m.extract_frame('unused',target,0)
    assert not target.exists()


def test_oversized_native_output_is_rejected(tmp_path,monkeypatch):
    target=tmp_path/'frame.png'
    def run(*args,**kwargs):
        target.write_bytes(b'X'*(m.MAX_QA_FRAME_BYTES+1))
        return 0,b'',b''
    monkeypatch.setattr(m,'run_owned_media_process',run)
    with pytest.raises(ValueError,match='frame extraction failed'):
        m.extract_frame('unused',target,0)
    assert not target.exists()


def test_ffmpeg_receives_output_budget(tmp_path,monkeypatch):
    target=tmp_path/'frame.png'
    commands=[]
    def run(command,**kwargs):
        commands.append((command,kwargs))
        target.write_bytes(b'png')
        return 0,b'',b''
    monkeypatch.setattr(m,'run_owned_media_process',run)
    assert m.extract_frame('unused',target,0)==target
    command,kwargs=commands[0]
    assert command[command.index('-fs')+1]==str(m.MAX_QA_FRAME_BYTES)
    assert str(m.MAX_QA_FRAME_SIDE) in command[command.index('-vf')+1]
    assert kwargs=={'timeout':60}


def test_nonzero_and_stale_output_fail_closed(tmp_path,monkeypatch):
    target=tmp_path/'frame.png'
    target.write_bytes(b'old')
    monkeypatch.setattr(m,'run_owned_media_process',lambda *args,**kwargs:(1,b'',b''))
    with pytest.raises(ValueError,match='frame extraction failed'):
        m.extract_frame('unused',target,0)
    assert not target.exists()


def test_pcm_capture_contract_unchanged(monkeypatch):
    monkeypatch.setattr(m,'run_owned_media_process',
                        lambda *args,**kwargs:(0,bytes((1,0))*16000,b''))
    assert len(m.extract_pcm('unused',0,3))==4000
