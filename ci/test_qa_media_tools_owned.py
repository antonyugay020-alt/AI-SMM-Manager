"""Real native QA sampling and fail-closed media receipt tests (offline)."""
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'AI_SMM_Work_Architecture_A75'
sys.path.insert(0,str(WORK))
import qa_media_tools as m


def test_sample_rejects_invalid_times_before_native_spawn(monkeypatch,tmp_path):
    called=[]
    monkeypatch.setattr(m,'run_owned_media_process',lambda *a,**kw:called.append(True))
    for value in (-1,float('nan'),float('inf'),True,'1'):
        with pytest.raises(ValueError):m.extract_frame('x',tmp_path/'out.png',value)
        with pytest.raises(ValueError):m.extract_pcm('x',value,1)
    with pytest.raises(ValueError):m.extract_pcm('x',0,0)
    assert not called


def test_frame_stale_file_removed_on_native_failure(monkeypatch,tmp_path):
    dest=tmp_path/'out.png';dest.write_bytes(b'stale')
    def fail(*a,**kw):return 1,b'',b'error'
    monkeypatch.setattr(m,'run_owned_media_process',fail)
    with pytest.raises(ValueError,match='frame extraction failed'):
        m.extract_frame('x',dest,0)
    assert not dest.exists()


def test_pcm_bounded_capture_and_nonzero_fail_closed(monkeypatch):
    calls=[]
    def run(cmd,**kw):
        calls.append((cmd,kw));return 0,b'\x01\x00'*16000,b''
    monkeypatch.setattr(m,'run_owned_media_process',run)
    samples=m.extract_pcm('fixture',0,4)
    assert len(samples)==4000
    assert calls[0][1]=={'timeout':60,'capture_stdout':True}
    assert calls[0][0][calls[0][0].index('-t')+1]=='2'
    monkeypatch.setattr(m,'run_owned_media_process',lambda *a,**kw:(1,b'\x01\x00',b''))
    with pytest.raises(ValueError):m.extract_pcm('fixture',0,1)


def test_real_owned_ffmpeg_samples_video_audio(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg unavailable')
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
        'testsrc2=s=160x120:r=10','-f','lavfi','-i','sine=frequency=500:sample_rate=8000',
        '-t','1','-c:v','mpeg4','-c:a','aac','-shortest',str(source)],
        check=True,capture_output=True,timeout=15)
    frame=m.extract_frame(source,tmp_path/'frame.png',.25)
    with Image.open(frame) as image:
        assert image.size==(160,120)
    assert len(m.extract_pcm(source,0,1))>100
    with pytest.raises(ValueError):m.extract_frame(source,tmp_path/'frame.png',100)
    assert not frame.exists()


def test_linked_source_and_destination_are_rejected_before_spawn(monkeypatch,tmp_path):
    target=tmp_path/'source';target.write_bytes(b'bytes')
    link=tmp_path/'source-link'
    try:link.symlink_to(target)
    except OSError:pytest.skip('symlink creation unavailable')
    calls=[]
    monkeypatch.setattr(m,'run_owned_media_process',lambda *a,**kw:calls.append(True))
    with pytest.raises(ValueError):m.extract_frame(link,tmp_path/'frame.png',0)
    with pytest.raises(ValueError):m.extract_pcm(link,0,1)
    dest=tmp_path/'linked.png';dest.symlink_to(target)
    with pytest.raises(ValueError):m.extract_frame(target,dest,0)
    assert not calls


def test_pcm_oversized_or_odd_receipt_fails_closed(monkeypatch):
    for data in (b'X'*32002,b'X'*3,b''):
        monkeypatch.setattr(m,'run_owned_media_process',lambda *a,**kw:(0,data,b''))
        with pytest.raises(ValueError,match='QA audio extraction failed'):
            m.extract_pcm('fixture',0,1)


def test_quality_worker_has_no_unowned_ffmpeg_calls():
    quality=(WORK/'local_media_quality.py').read_text(encoding='utf-8')
    assert 'subprocess.run(' not in quality
    assert 'import subprocess' not in quality
    assert quality.count('extract_frame(')==2
    assert quality.count('extract_pcm(')==1
    assert 'from qa_media_tools import extract_frame,extract_pcm' in quality
