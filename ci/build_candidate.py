#!/usr/bin/env python3
"""Build and clean-unpack a NOT_RELEASE candidate from the exact Git checkout.
The original source tree and the release policy are never modified.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile
from verify_tree import verify

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'AI_SMM_Work_Architecture_A75'
FOLDER_NAME='AI_SMM_Work_Architecture_A75'


def build(output:Path):
    status=verify()
    if status['status']!='PASS':
        raise RuntimeError('source tree is not ready: '+repr(status['errors']))
    sys.path.insert(0,str(SOURCE))
    import release_guard
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    archive=output/'AI_SMM_CLOUD_CANDIDATE_NEEDS_REAL_TEST.zip'
    extracted=output/'candidate' / FOLDER_NAME
    if extracted.exists():
        raise FileExistsError('clean extracted candidate already exists: '+str(extracted))
    with tempfile.TemporaryDirectory(prefix='smm-stage-') as folder:
        stage=Path(folder)/FOLDER_NAME
        # Exclude the previous manifest: release_guard regenerates one for the
        # new frozen snapshot, so stale signatures cannot be silently reused.
        shutil.copytree(SOURCE,stage,ignore=shutil.ignore_patterns(
            '.git','__pycache__','.venv','.pytest_cache','VALIDATION_RESULTS',
            'PACKAGE_MANIFEST.json','*.pyc'))
        verdict=release_guard.package(stage,archive,'acceptance')
    verified=release_guard.verify(archive)
    if verified.get('status')!='PASS' or verified.get('profile')!='acceptance':
        raise RuntimeError('freshly built candidate archive did not verify')
    extracted.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if any(not name.startswith(FOLDER_NAME+'/') for name in names):
            raise RuntimeError('unexpected archive root')
        z.extractall(extracted.parent)
    # The original project's tree guard verifies every installed byte.
    tree=release_guard.verify_tree(extracted)
    if tree.get('status')!='PASS':
        raise RuntimeError('clean unpack did not verify: '+repr(tree))
    report={'status':'PASS','archive':str(archive),'clean_candidate':str(extracted),
            'source_preflight_fingerprint':status['fingerprint_sha256'],
            'candidate_zip_sha256':verified['zip_sha256'],
            'candidate_source_fingerprint':verified['source_fingerprint'],
            'profile':'acceptance','release_status':'NOT_RELEASE'}
    (output/'build_candidate.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'ci_evidence'/'packaged_candidate')
    a=parser.parse_args()
    try:
        print(json.dumps(build(a.output),indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,zipfile.BadZipFile) as exc:
        print('BLOCKED:',str(exc),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
