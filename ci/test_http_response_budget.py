"""Offline bounded transport contract; no network, paid API, model or GPU."""
import io
from pathlib import Path
import sys
import time
from unittest.mock import patch
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from http_response_budget import read_bounded_body


def test_budget_exact_and_overflow():
    assert read_bounded_body(io.BytesIO(b'abcd'),max_bytes=4,deadline=time.monotonic()+2)==b'abcd'
    with pytest.raises(ValueError,match='byte budget'):
        read_bounded_body(io.BytesIO(b'abcde'),max_bytes=4,deadline=time.monotonic()+2)


def test_trickle_deadline_and_cancel():
    clock=[0.]
    class Slow(io.BytesIO):
        def read1(self,n=-1):
            clock[0]+=.4
            return super().read1(1)
    with patch('http_response_budget.time.monotonic',side_effect=lambda:clock[0]):
        with pytest.raises(TimeoutError,match='deadline'):
            read_bounded_body(Slow(b'abcdefgh'),max_bytes=100,deadline=1)
    calls=[0]
    def check():
        calls[0]+=1
        if calls[0]>=4:raise InterruptedError('cancelled')
    class Tiny(io.BytesIO):
        def read1(self,n=-1):return super().read1(1)
    with pytest.raises(InterruptedError,match='cancelled'):
        read_bounded_body(Tiny(b'abcdef'),max_bytes=10,deadline=time.monotonic()+2,check=check)


def test_legacy_bytesio_and_invalid():
    class Broken(io.BytesIO):
        def read(self,n=-1):raise OSError('read crashed')
    with pytest.raises(OSError,match='read crashed'):
        read_bounded_body(Broken(b'abc'),max_bytes=10,deadline=time.monotonic()+2)
    for size in (0,-1,True,32*1024*1024+1):
        with pytest.raises(ValueError):
            read_bounded_body(io.BytesIO(),max_bytes=size,deadline=time.monotonic()+2)


def test_llama_cpp_budget():
    from local_text_runtime import request_local
    env={'AI_SMM_LOCAL_LLM_BACKEND':'llama_cpp','AI_SMM_LOCAL_LLM_MODEL':'fixture',
         'AI_SMM_LOCAL_LLM_URL':'http://127.0.0.1:18080'}
    with pytest.raises(ValueError,match='byte budget'):
        request_local({'model':'fixture','messages':[{'role':'user','content':'hello'}]},
            environ=env,opener=lambda req,timeout:io.BytesIO(b'x'*(16*1024*1024+1)))


def test_kie_budget_and_stop():
    from kie_media_client import KieMediaClient
    client=KieMediaClient(opener=lambda req,timeout:io.BytesIO(b'x'*120001))
    with pytest.raises(RuntimeError,match='response'):client._read_json('fixture')
    client=KieMediaClient(opener=lambda req,timeout:pytest.fail('network opened'))
    with pytest.raises(InterruptedError):
        client._read_json('fixture',check=lambda:(_ for _ in ()).throw(InterruptedError()))


def test_cloud_budget_without_paid_dispatch():
    import text_providers
    with patch('urllib.request.urlopen',return_value=io.BytesIO(b'x'*(16*1024*1024+1))):
        with pytest.raises(ValueError,match='byte budget'):
            text_providers._chat_request('gemini','fixture',[{'role':'user','content':'hello'}],2,'fixture-key',0)


def test_no_unbounded_response_read():
    for name in ('ollama_resilience','text_providers','local_text_runtime','kie_media_client'):
        source=(ROOT/'AI_SMM_Work_Architecture_A75'/(name+'.py')).read_text(encoding='utf-8')
        assert 'read_bounded_body(' in source
        assert 'response.read()' not in source
        assert 'exc.read()' not in source


def test_llama_cpp_deadline_includes_header_wait():
    from local_text_runtime import request_local
    env={'AI_SMM_LOCAL_LLM_BACKEND':'llama_cpp','AI_SMM_LOCAL_LLM_MODEL':'fixture',
         'AI_SMM_LOCAL_LLM_URL':'http://127.0.0.1:18080'}
    def slow_headers(req,timeout):
        time.sleep(.03)
        return io.BytesIO(b'{"choices":[]}')
    with pytest.raises(TimeoutError,match='deadline'):
        request_local({'model':'fixture','messages':[{'role':'user','content':'hello'}]},
            environ=env,opener=slow_headers,timeout=.01)


def test_ollama_http_deadline_starts_before_open():
    source=(ROOT/'AI_SMM_Work_Architecture_A75/ollama_resilience.py').read_text(encoding='utf-8')
    assert 'http_started=time.monotonic()\n                        with urllib.request.urlopen(' in source
    assert 'deadline=http_started+local_timeout' in source


def test_cloud_http_error_body_cancellation_is_not_reclassified():
    from cooperative_cancellation import TaskCancelled,cancellation_scope
    from text_providers import call_text
    import urllib.error
    error=urllib.error.HTTPError('https://fixture.invalid',503,'unavailable',{},
                                io.BytesIO(b'{"error":{"code":"busy"}}'))
    calls=[0]
    def check():
        calls[0]+=1
        if calls[0]>1:raise TaskCancelled('user stopped')
    with patch('text_providers._chat_request',side_effect=error):
        with cancellation_scope(check):
            with pytest.raises(TaskCancelled,match='user stopped'):
                call_text('groq/openai/gpt-oss-20b',[{'role':'user','content':'hello'}],
                    environ={'GROQ_API_KEY':'fixture','AI_SMM_GROQ_FREE_TIER_ACK':'1'})
