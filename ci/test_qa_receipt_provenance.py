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


def test_sources_and_mask_are_bound_to_review_receipt(tmp_path):
    output=tmp_path/'output.png';output.write_bytes(b'output')
    source=tmp_path/'source.png';source.write_bytes(b'source')
    mask=tmp_path/'mask.png';mask.write_bytes(b'mask')
    case={'sources':[str(source),str(mask)]}
    receipt=make_request(output,case)
    assert validate_provenance(dict(receipt,status='PASS'),receipt,output,case)
    source.write_bytes(b'changed')
    with pytest.raises(ValueError,match='SOURCE_BINDING_INVALID'):
        validate_provenance(dict(receipt,status='PASS'),receipt,output,case)
    source.write_bytes(b'source')
    mask.write_bytes(b'changed')
    with pytest.raises(ValueError,match='SOURCE_BINDING_INVALID'):
        validate_output_binding(receipt,output,case)


def test_source_receipt_missing_or_forged_fails_closed(tmp_path):
    output=tmp_path/'output.png';output.write_bytes(b'output')
    source=tmp_path/'source.png';source.write_bytes(b'source')
    case={'sources':[str(source)]}
    receipt=make_request(output,case)
    for invalid in ([],['f'*64],None):
        with pytest.raises(ValueError,match='PROVENANCE_INVALID'):
            validate_provenance({**receipt,'source_sha256':invalid},receipt,output,case)
    with pytest.raises(ValueError,match='SOURCE_BINDING_INVALID'):
        validate_output_binding(receipt,output,{'sources':[]})
    source.unlink()
    with pytest.raises(ValueError,match='SOURCE_BINDING_INVALID'):
        validate_output_binding(receipt,output,case)


def test_image_metrics_must_be_finite_and_prove_edit(tmp_path):
    from qa_receipt_provenance import validate_media_metrics
    metrics={'width':256,'height':256,'contrast':.2,'mean_absolute_change':.3}
    assert validate_media_metrics({'task':'image_instruction','width':256},metrics)==metrics
    for changed in ({'contrast':float('nan')},{'contrast':0},{'width':True},
                    {'height':0},{'mean_absolute_change':0},{'width':512}):
        with pytest.raises(ValueError,match='METRICS_INVALID'):
            validate_media_metrics({'task':'image_instruction','width':256},{**metrics,**changed})
    with pytest.raises(ValueError,match='METRICS_INVALID'):
        validate_media_metrics({'task':'image_inpaint'},metrics)
    assert validate_media_metrics({'task':'image_inpaint'},{**metrics,'protected_pixels_exact':True})


def test_video_metrics_require_frames_duration_and_source_audio(tmp_path):
    from qa_receipt_provenance import validate_media_metrics
    metrics={'width':256,'height':256,'duration':2.,'audio':True,'source_audio':True,
             'audio_comparison':{'best_correlation':.94},'sampled_luma_jump':.2,
             'sampled_frames':[{'contrast':.2,'luma':.5}]*3}
    case={'task':'video_generative','fps':8,'frames':16}
    assert validate_media_metrics(case,metrics)==metrics
    for changed in ({'duration':3},{'audio':False},{'source_audio':None},
                    {'sampled_frames':[{'contrast':.2,'luma':.5}]},
                    {'sampled_luma_jump':.81},
                    {'audio_comparison':{'best_correlation':.8}}):
        with pytest.raises(ValueError,match='METRICS_INVALID'):
            validate_media_metrics(case,{**metrics,**changed})
    silent={**metrics,'source_audio':False,'audio':False}
    silent.pop('audio_comparison')
    assert validate_media_metrics(case,silent)==silent
