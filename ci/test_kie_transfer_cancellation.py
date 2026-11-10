"""Offline Kie result I/O deadline, cancellation and adapter ownership tests.

No network, paid API, GPU or Qt. The real default downloader is exercised with
in-memory HTTP responses; Windows GUI acceptance remains a separate gate.
"""
import io
import math
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from kie_media_download import download_result
import kie_media_download as dl
from kie_media_transfer import fetch_result

URL='https://cdn.example.org/clip.png'


class Response(io.BytesIO):
    status=200
    def __init__(self,data,headers=None):
        super().__init__(data)
        self.headers=headers or {}


def test_bounded_download_publishes_exact_bytes_and_cleans_spool(tmp_path):
    target=tmp_path/'clip.png'
    timeouts=[]
    def opener(req,timeout):
        timeouts.append(timeout)
        return Response(b'picture',{'Content-Length':'7'})
    assert download_result(URL,target,opener=opener)==target
    assert target.read_bytes()==b'picture'
    assert timeouts==[10]
    assert list(tmp_path.iterdir())==[target]


def test_cancellation_midstream_cleans_partial_bytes(tmp_path):
    target=tmp_path/'clip.png'
    calls=[]
    class Chunked(Response):
        def read1(self,n=-1):
            return super().read1(2)
    def check():
        calls.append(1)
        if len(calls)>5:raise InterruptedError('stop')
    with pytest.raises(InterruptedError,match='stop'):
        download_result(URL,target,opener=lambda *a,**kw:Chunked(b'abcdef'),check=check)
    assert list(tmp_path.iterdir())==[]


def test_trickling_response_obeys_total_monotonic_deadline(tmp_path):
    target=tmp_path/'clip.png'
    clock=[0.]
    class Slow(Response):
        def read1(self,n=-1):
            clock[0]+=.4
            return super().read1(1)
    with patch.object(dl.time,'monotonic',side_effect=lambda:clock[0]):
        with pytest.raises(TimeoutError,match='deadline'):
            download_result(URL,target,opener=lambda *a,**kw:Slow(b'abcdef'),total_timeout=1)
    assert list(tmp_path.iterdir())==[]


def test_socket_read1_preferred_over_blocking_read(tmp_path):
    target=tmp_path/'clip.png'
    class ReadOnce(Response):
        def read(self,n=-1):raise AssertionError('unbounded buffered read')
        def read1(self,n=-1):return io.BytesIO.read(self,n)
    download_result(URL,target,opener=lambda *a,**kw:ReadOnce(b'ok'))
    assert target.read_bytes()==b'ok'


def test_expired_transfer_does_not_publish_after_fsync(tmp_path):
    target=tmp_path/'clip.png'
    clock=[0.]
    class Delayed(Response):
        def __exit__(self,*args):
            clock[0]+=2
            return super().__exit__(*args)
    with patch.object(dl.time,'monotonic',side_effect=lambda:clock[0]):
        with pytest.raises(TimeoutError):
            download_result(URL,target,opener=lambda *a,**kw:Delayed(b'ok'),total_timeout=1)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('value',[0,-1,True,float('inf'),float('nan'),'3'])
def test_invalid_deadline_never_opens_network(value,tmp_path):
    opener=Mock()
    with pytest.raises(ValueError):
        download_result(URL,tmp_path/'clip.png',opener=opener,total_timeout=value)
    opener.assert_not_called()


def test_adapter_forwards_same_check_without_retries():
    seen=[]
    def check():seen.append('check')
    def adapter(url,target,*,check):
        seen.append('fetch')
        check()
        return target
    assert fetch_result(adapter,URL,'target',check)=='target'
    assert seen==['check','fetch','check','check']


def test_legacy_adapter_keeps_two_argument_contract():
    calls=[]
    def adapter(url,target):
        calls.append((url,target))
        return 'legacy'
    assert fetch_result(adapter,URL,'target',lambda:None)=='legacy'
    assert calls==[(URL,'target')]


def test_cancelled_adapter_never_invoked():
    adapter=Mock()
    with pytest.raises(InterruptedError):
        fetch_result(adapter,URL,'target',lambda:(_ for _ in ()).throw(InterruptedError()))
    adapter.assert_not_called()


def test_adapter_typeerror_does_not_retry_transfer():
    calls=[]
    def adapter(url,target,*,check):
        calls.append(1)
        raise TypeError('internal adapter error')
    with pytest.raises(TypeError,match='internal adapter error'):
        fetch_result(adapter,URL,'target',lambda:None)
    assert calls==[1]


def test_existing_output_and_mismatched_length_fail_closed(tmp_path):
    target=tmp_path/'clip.png'
    target.write_bytes(b'original')
    with pytest.raises(ValueError):
        download_result(URL,target,opener=lambda *a,**kw:Response(b'new'))
    assert target.read_bytes()==b'original'
    target.unlink()
    with pytest.raises(ValueError,match='content-length mismatch'):
        download_result(URL,target,opener=lambda *a,**kw:Response(b'short',{'Content-Length':'10'}))
    assert list(tmp_path.iterdir())==[]
