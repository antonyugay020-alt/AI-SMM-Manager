"""Deterministic QA masks are genuine binary masks and preserve real photos."""
import json
from pathlib import Path
import tempfile
from PIL import Image,ImageDraw
from qa_fixture_masks import prepare_qa_plan,stage_qa_evidence,_valid_mask,_sha


def test_photo_substitutes_become_distinct_audited_binary_masks():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);photo=root/'Фото со спортом.jpg';Image.new('RGB',(80,64),(81,140,100)).save(photo)
        bad=root/'different-photo.jpg';Image.new('RGB',(80,64),(45,80,72)).save(bad)
        before=_sha(photo)
        plan=root/'selected_plan.json'
        plan.write_text(json.dumps({'version':1,'cases':[
            {'profile':'inpaint','sources':[str(photo),str(bad)],'prompt':'background'},
            {'profile':'inpaint','sources':[str(photo),str(bad)],'prompt':'object'}]}),encoding='utf-8')
        session=root/'session';session.mkdir()
        selected=prepare_qa_plan(plan,{'inpaint':{'engine':'sd15_inpaint'}},root,session)
        cases=json.loads(Path(selected).read_text(encoding='utf-8'))['cases']
        masks=[Path(case['sources'][1]) for case in cases]
        assert masks[0]!=masks[1]
        assert all(_valid_mask(p,(80,64)) for p in masks)
        assert _sha(photo)==before
        assert json.loads((session/'qa_fixture_mask_provenance.json').read_text())['items'][1]['kind']=='object'
        session2=root/'session2';session2.mkdir()
        selected2=prepare_qa_plan(plan,{'inpaint':{'engine':'sd15_inpaint'}},root,session2)
        assert json.loads(Path(selected2).read_text(encoding='utf-8'))['cases']==cases
        staged=stage_qa_evidence(session,root/'report')
        audit=json.loads((staged/'qa_fixture_mask_provenance.json').read_text(encoding='utf-8'))
        assert len(audit['items'])==2
        assert _sha(staged/'case_0_mask.png')==audit['items'][0]['mask_sha256']
        assert _sha(staged/'case_1_mask.png')==audit['items'][1]['mask_sha256']


def test_existing_matching_binary_mask_is_not_replaced():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);photo=root/'real.png';Image.new('RGB',(64,64),(40,50,60)).save(photo)
        mask=root/'real_mask.png';m=Image.new('L',(64,64),0);ImageDraw.Draw(m).rectangle((8,8,32,32),fill=255);m.save(mask)
        hash_before=_sha(mask)
        plan=root/'plan.json';plan.write_text(json.dumps({'version':1,'cases':[
            {'profile':'inpaint','sources':[str(photo),str(mask)]}]}),encoding='utf-8')
        session=root/'session';session.mkdir()
        selected=prepare_qa_plan(plan,{'inpaint':{'engine':'sd15_inpaint'}},root,session)
        result=json.loads(Path(selected).read_text())
        assert result['cases'][0]['sources'][1]==str(mask)
        assert _sha(mask)==hash_before
        assert json.loads((session/'qa_fixture_mask_provenance.json').read_text())['items']==[]


def test_diagnostics_refuse_mask_evidence_mutation():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);photo=root/'photo.jpg';Image.new('RGB',(64,64),(3,5,7)).save(photo)
        fake=root/'not_a_mask.jpg';Image.new('RGB',(64,64),(55,8,9)).save(fake)
        plan=root/'plan.json';plan.write_text(json.dumps({'version':1,'cases':[
            {'profile':'inpaint','sources':[str(photo),str(fake)]}]}),encoding='utf-8')
        session=root/'session';session.mkdir()
        prepared=json.loads(Path(prepare_qa_plan(
            plan,{'inpaint':{'engine':'sd15_inpaint'}},root,session)).read_text(encoding='utf-8'))
        Path(prepared['cases'][0]['sources'][1]).write_bytes(b'changed')
        try:
            stage_qa_evidence(session,root/'report')
        except ValueError as error:
            assert 'QA mask changed' in str(error)
        else:raise AssertionError('Mutated QA mask was accepted')
