"""Offline owned ffprobe and cancellation integration across media validators."""
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from media_subprocess_runtime import run_owned_media_process
from media_artifact_validation import validate_media_bytes
from kie_media_runtime import verified_video
import local_media_jobs as jobs


@pytest.fixture
def kling_video(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local FFmpeg/ffprobe unavailable')
    target=tmp_path/'clip.mp4'
    code,_,error=run_owned_media_process(
        ['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
         'color=c=blue:s=720x720:r=24','-t','3.2','-c:v','mpeg4',str(target)],
        timeout=30)
    assert code==0,error.decode('utf-8','replace')
    return target


def test_local_and_cloud_video_validation_share_owned_probe(kling_video):
    with patch('subprocess.run',side_effect=AssertionError('unowned ffprobe call')):
        validate_media_bytes(kling_video,is_video=True)
        path,metadata=verified_video(kling_video)
    assert path==kling_video
    assert metadata.width==720 and metadata.height==720
    assert 3<=metadata.duration_seconds<4


def test_video_validator_cancellation_before_spawn(tmp_path):
    target=tmp_path/'source.mp4';target.write_bytes(b'fixture')
    class Cancel(Exception):pass
    def stop():raise Cancel()
    with patch('media_subprocess_runtime.subprocess.Popen') as popen:
        with pytest.raises(Cancel):validate_media_bytes(target,is_video=True,check=stop)
        with pytest.raises(Cancel):verified_video(target,check=stop)
        popen.assert_not_called()


def test_local_render_probes_receive_same_cancel_callback(tmp_path):
    source=tmp_path/'source.mp4'
    seen=[]
    def probe(path,*,full=False,check=None):
        seen.append(check)
        return 2,False
    def stop():pass
    request={'task':'video_format','sources':[str(source)],'width':160,'height':120}
    with patch.object(jobs,'_probe',side_effect=probe),patch.object(jobs,'_tool') as tool,\
            patch.object(jobs.shutil,'which',return_value='/fixture/ffmpeg'):
        jobs._render(request,[source],tmp_path/'out.mp4',stop)
    assert seen==[stop]
    tool.assert_called_once()


def test_local_readback_probes_receive_same_cancel_callback(tmp_path):
    source=tmp_path/'source.mp4';target=tmp_path/'out.mp4'
    seen=[]
    def probe(path,*,full=False,check=None):
        seen.append(check)
        return (2,False,(160,120)) if full else (2,False)
    def stop():pass
    request={'task':'video_format','sources':[str(source)],'width':160,'height':120}
    with patch.object(jobs,'_probe',side_effect=probe):
        jobs._verify_output(request,[source],target,check=stop)
    assert seen==[stop,stop]


def test_source_validation_probe_is_cancelled_before_spawn(tmp_path):
    source=tmp_path/'missing.mp4'
    class Cancel(Exception):pass
    def stop():raise Cancel()
    with patch('media_subprocess_runtime.subprocess.Popen') as popen:
        with pytest.raises(Cancel):jobs._probe(source,check=stop)
        popen.assert_not_called()


def test_infinite_probe_output_is_rejected_before_deadline():
    command=[sys.executable,'-c','import os;\nwhile True:os.write(1,b"X"*65536)']
    with pytest.raises(ValueError,match='safe bound'):
        run_owned_media_process(command,timeout=5,capture_stdout=True)
