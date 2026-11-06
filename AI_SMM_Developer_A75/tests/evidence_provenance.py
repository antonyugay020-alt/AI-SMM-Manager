# -*- coding: utf-8 -*-
"""Check that a Windows report was produced by EXACTLY this extracted candidate.

This does not convert a FAIL into a PASS. It only prevents old test reports from
being misattributed to newer code, regardless of their green/failed test counts.
"""
from __future__ import annotations
import json
from pathlib import Path, PureWindowsPath
import zipfile
from candidate_identity import CANDIDATE_ID, EXPECTED_FOLDER


def validate_report(archive: Path, fingerprint: str) -> dict:
    with zipfile.ZipFile(archive) as z:
        prefix='VALIDATION_RESULTS/' if 'VALIDATION_RESULTS/acceptance.json' in z.namelist() else ''
        report=json.loads(z.read(prefix+'acceptance.json').decode('utf-8'))
        backend=(json.loads(z.read(prefix+'real_workflow_results.json').decode('utf-8'))
                 if prefix+'real_workflow_results.json' in z.namelist() else None)
    problems=[]
    if not isinstance(report,dict):
        return {'identity_valid':False,'reasons':['acceptance.json is not an object']}
    if report.get('build')!=CANDIDATE_ID:
        problems.append('Неверный build: отчёт создан другой (возможно старой) версией проверки')
    integrity=report.get('checks',{}).get('package_integrity',{})
    if integrity.get('status')!='PASS':
        problems.append('Распакованные исходники не прошли проверку целостности')
    if integrity.get('source_fingerprint')!=fingerprint or report.get('attested_source_fingerprint')!=fingerprint:
        problems.append('Хеш отчёта не совпадает с хешем отправленных исходников')
    if integrity.get('candidate_id')!=CANDIDATE_ID:
        problems.append('Внутренний идентификатор исходников отсутствует или неверен')
    source_path=report.get('runner_path','')
    if not isinstance(source_path,str) or PureWindowsPath(source_path).name != 'windows_acceptance.py' or PureWindowsPath(source_path).parent.name != EXPECTED_FOLDER:
        problems.append('Тест запущен не из ожидаемой папки с исходниками')
    if backend is not None and (not isinstance(backend,dict) or backend.get('build')!=CANDIDATE_ID):
        problems.append('Исполнитель использовал код или диагностику другой версии')
    return {'identity_valid':not problems,'reasons':problems,
            'candidate_id':CANDIDATE_ID,'overall_status':report.get('status'),
            'source_root':source_path}


if __name__=='__main__':
    import sys
    manifest=json.loads((Path(__file__).parent/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
    result=validate_report(Path(sys.argv[1]),manifest['source_fingerprint'])
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['identity_valid'] else 2)
