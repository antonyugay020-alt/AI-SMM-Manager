"""CPU-only QA evidence integrity; no native process or vision dependency."""
import hashlib
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_SMM_Work_Architecture_A75'))
from qa_receipt_provenance import make_request,output_sha256,validate_output_binding,validate_provenance


def test_current_request_and_output_pass(tmp_path):
    output=tmp_path/'result.png';output.write_bytes(b'valid-output')
    request=make_request(output)
    report={**request,'status':'PASS'}
    assert validate_provenance(report,request,output)==report
    assert output_sha256(output)==hashlib.sha256(b'valid-output').hexdigest()


def test_stale_receipt_rejected_even_when_output_bytes_identical(tmp_path):
    output=tmp_path/'result.png';output.write_bytes(b'valid-output')
    old=make_request(output);new=make_request(output)
    assert old['request_id']!=new['request_id']
    with pytest.raises(ValueError,match='PROVENANCE_INVALID'):
        validate_provenance({**old,'status':'PASS'},new,output)


def test_mutated_output_rejected(tmp_path):
    output=tmp_path/'result.png';output.write_bytes(b'valid-output')
    request=make_request(output);output.write_bytes(b'changed-output')
    with pytest.raises(ValueError,match='OUTPUT_BINDING_INVALID'):
        validate_provenance({**request,'status':'PASS'},request,output)


def test_forged_sha_rejected(tmp_path):
    output=tmp_path/'result.png';output.write_bytes(b'valid-output')
    request=make_request(output)
    with pytest.raises(ValueError,match='PROVENANCE_INVALID'):
        validate_provenance({**request,'status':'PASS','output_sha256':'0'*64},request,output)


def test_malformed_request_id_rejected(tmp_path):
    output=tmp_path/'result.png';output.write_bytes(b'valid-output')
    request=make_request(output)
    for bad in ('',None,'f'*33,'Z'*32):
        with pytest.raises(ValueError,match='OUTPUT_BINDING_INVALID'):
            validate_output_binding({**request,'request_id':bad},output)


def test_malformed_output_digest_rejected(tmp_path):
    output=tmp_path/'result.png';output.write_bytes(b'valid-output')
    request=make_request(output)
    for bad in ('',None,'f'*65,'Z'*64):
        with pytest.raises(ValueError,match='OUTPUT_BINDING_INVALID'):
            validate_output_binding({**request,'output_sha256':bad},output)
