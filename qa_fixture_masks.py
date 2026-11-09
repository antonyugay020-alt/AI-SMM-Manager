"""Deterministic QA-only inpainting masks for real user-supplied photographs.

A selected JPEG/photo is not a mask. Never modify the source or silently count
it as a valid binary mask. Generate a separately hashed fixture and audit it.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from PIL import Image, ImageDraw


def _sha(path):
    h=sha256()
    with Path(path).open('rb') as stream:
        while data:=stream.read(1024*1024):h.update(data)
    return h.hexdigest()


def _valid_mask(path,size):
    if path.suffix.lower()!='.png' or not path.is_file():return False
    try:
        with Image.open(path) as image:
            if image.size!=size or image.mode not in ('1','L'):return False
            counts=image.convert('L').getcolors(maxcolors=257)
            return counts is not None and {value for count,value in counts}=={0,255}
    except (OSError,ValueError):return False


def prepare_qa_plan(plan_path,installed,reports_root,session):
    """Make invalid inpaint mask choices explicit, auditable QA fixtures.

    Only the acceptance plan is adapted; no production workflow or original
    photograph is changed. The exact generated fixture is hashed in the QA
    evidence and can be reproduced byte-for-byte on the second test host.
    """
    plan=json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    if not isinstance(plan,dict) or plan.get('version')!=1 or not isinstance(plan.get('cases'),list):
        raise ValueError('Invalid Windows QA plan')
    root=Path(reports_root)
    audit=[]
    mask_index=0
    for index,case in enumerate(plan['cases']):
        profile=installed.get(case.get('profile'))
        if not profile:raise ValueError('Unknown QA model profile')
        if profile.get('engine')!='sd15_inpaint':continue
        sources=case.get('sources')
        if not isinstance(sources,list) or len(sources)!=2:
            raise ValueError('Inpainting QA requires source photo and separate mask')
        photo=Path(sources[0]);auxiliary=Path(sources[1])
        if not photo.is_file():raise ValueError('Inpainting source photograph is missing')
        with Image.open(photo) as image:size=image.size
        if min(size)<8:raise ValueError('Inpainting QA image is too small for a meaningful mask')
        kind='background' if mask_index==0 else 'object'
        mask_index+=1
        if _valid_mask(auxiliary,size):continue
        mask=Image.new('L',size,255 if kind=='background' else 0)
        box=(size[0]//4,size[1]//5,size[0]*3//4,size[1]*4//5)
        ImageDraw.Draw(mask).ellipse(box,fill=0 if kind=='background' else 255)
        stream=BytesIO();mask.save(stream,format='PNG');payload=stream.getvalue()
        folder=root/'QA_FIXTURES';folder.mkdir(parents=True,exist_ok=True)
        source_hash=_sha(photo)
        dest=folder/f'mask_{kind}_{source_hash[:20]}_{size[0]}x{size[1]}.png'
        fixture_hash=sha256(payload).hexdigest()
        if dest.exists():
            if _sha(dest)!=fixture_hash:raise ValueError('Existing QA mask fixture changed')
        else:
            pending=dest.with_suffix('.pending')
            pending.write_bytes(payload);pending.replace(dest)
        case['sources'][1]=str(dest.resolve())
        audit.append({'case':index,'kind':kind,'source_sha256':source_hash,
            'mask_sha256':fixture_hash,'mask_path':str(dest),
            'replaced_invalid_auxiliary':str(auxiliary),'fixture':'deterministic_binary_QA_only'})
        print('QA inpaint mask prepared:',index,kind,flush=True)
    session=Path(session)
    audit_path=session/'qa_fixture_mask_provenance.json'
    audit_path.write_text(json.dumps({'version':1,'items':audit},indent=2,ensure_ascii=False),encoding='utf-8')
    effective=session/'effective_plan.json'
    effective.write_text(json.dumps(plan,indent=2,ensure_ascii=False),encoding='utf-8')
    return str(effective)


def stage_qa_evidence(session,report_folder):
    """Embed checksum-verified QA masks and provenance in the child QA ZIP.

    The Windows acceptance child removes stale top-level JSON at startup;
    subdirectory evidence is retained and included by the recursive ZIP packer.
    """
    session=Path(session)
    audit=json.loads((session/'qa_fixture_mask_provenance.json').read_text(encoding='utf-8'))
    folder=Path(report_folder)/'qa_fixtures'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'qa_fixture_mask_provenance.json').write_text(
        json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    for item in audit['items']:
        origin=Path(item['mask_path'])
        if not origin.is_file() or _sha(origin)!=item['mask_sha256']:
            raise ValueError('QA mask changed before attested execution')
        output=folder/('case_'+str(item['case'])+'_mask.png')
        if output.exists() and _sha(output)!=item['mask_sha256']:
            raise ValueError('Archived QA mask unexpectedly changed')
        if not output.exists():
            from shutil import copyfile
            copyfile(origin,output)
    return folder
