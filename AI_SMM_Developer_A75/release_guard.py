# -*- coding: utf-8 -*-
"""Fail-closed packaging and final-byte verification for AI SMM Manager.

Usage:
    python release_guard.py package <source-folder> <archive.zip> --mode acceptance
    python release_guard.py package <source-folder> <archive.zip> --mode release --evidence <evidence.json>
    python release_guard.py verify <archive.zip>

Packaging is *not* proof of semantic correctness or Windows/Ollama acceptance.
The release profile rejects missing or stale evidence. The acceptance profile
is deliberately named NOT_RELEASE and contains no assertion of release readiness.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile

MANIFEST_NAME = 'PACKAGE_MANIFEST.json'
ROOT_RE = re.compile(r'^[A-Za-z0-9_\-]+$')
# Every shipped production module is mandatory, including lazy worker imports.
REQUIRED_FILES = frozenset({'artifact_staging.py','completion_ledger.py','file_effect_outbox.py','dialogue_runtime.py','dialogue_execution.py','task_semantics.py','voice_input.py','diagnostic_report.py', 'CHECK_REVIEWERS.cmd', 'review_qualification.py', 'review_arbitration.py', 'AGENTS.md', 'artifact_revision.py', 'evidence_provenance.py', 'live_gui_workflow_acceptance.py', 'random_acceptance_tasks.py', 'real_workflow_acceptance.py', 'requirement_grounding.py', 'windows_acceptance.py', 'model_protocol.py', 'artifact_review.py', 'workflow_recovery.py', 'evidence_segments.py', 'claim_interpretation.py', 'text_workflow.py', 'text_task_contract.py', 'model_roles.py', 'acceptance_policy.py', 'project_knowledge.py', 'text_providers.py', 'factual_claim_audit.py', 'stage60_quality.py', 'editorial_integrity.py', 'task_artifacts.py', 'deliverable_contract.py', 'russian_quantity.py', 'stage57_core.py', 'component_manager.py', 'agent_runtime.py', 'PROJECT_VISION.md', 'editorial_evidence_matrix.py', 'editorial_claim_policy.py', 'semantic_brief.py', 'platform_registry.py', 'app.py', 'independent_acceptance_editor.py', 'semantic_editor.py', 'editorial_guard.py', 'task_routing.py', 'compound_retry.py', 'production_pipeline.py', 'document_recovery.py', 'task_context.py', 'durable_json.py', 'content_quality_engine.py', 'release_guard.py', 'calendar_planner.py', 'reference_policy.py', 'candidate_identity.py', 'task_orchestration.py', 'strategy_recovery.py', 'publication_writer.py', 'publication_schema.py', 'modern_local_editor.py', 'professional_content.py', 'completion_gate.py', 'project_control.py', 'version_info.py', 'independent_quality_gate.py', 'diagnostic_monitor.py', 'compound_tasks.py', 'publication_schedule.py', 'strategy_integrity.py', 'content_requirements.py', 'hardware_profile.py', 'plan_governance.py', 'requirements.txt', 'evidence_context.py', 'assertion_scope.py', 'claim_support.py', 'model_download.py', 'ollama_resilience.py', 'generic_text_review.py', 'quality_review_model_pool.json', 'project_deletion.py', 'cloud_credentials.py', 'task_registry.py', 'question_registry.py', 'project_operation_guard.py', 'project_file_boundary.py', 'reviewer_outcomes.py', 'qt_worker_lifecycle.py', 'provider_errors.py', 'task_checkpoint.py', 'media_result_ownership.py', 'task_intake.py', 'workflow_authority.py', 'execution_environment.py'})

RELEASE_CHECKS = (
    'architecture_review',
    'regression_suite',
    'windows_gui',
    'ollama_live',
    'full_gui_workflow',
    'projects_isolation',
    'checkpoint_resume',
    'persistence_restart',
    'reference_safety',
    'media_operations',
    'independent_content_quality',
    'migration_safety',
)
# User files, virtual environments, and developer-machine secrets must never be shipped.
SKIP_DIRS = frozenset({'.venv', '__pycache__', '.git', '.pytest_cache', '.mypy_cache', 'VALIDATION_RESULTS'})
BLOCKED_NAMES = frozenset({'.env', 'id_rsa', 'id_ed25519', 'VALIDATION_RESULTS.zip'})
BLOCKED_SUFFIXES = frozenset({'.pem', '.p12', '.pfx', '.key', '.sqlite', '.sqlite3', '.db', '.pyc'})

class PackagingBlocked(RuntimeError):
    pass


def _fail(reason: str) -> None:
    raise PackagingBlocked(reason)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _safe_relpath(path: str) -> None:
    if not path or '\\' in path or '\x00' in path or path.startswith('/'):
        _fail(f'Небезопасный путь в ZIP: {path!r}')
    segments = path.rstrip('/').split('/')
    if any(x in ('', '.', '..') for x in segments):
        _fail(f'Некорректные сегменты в ZIP: {path!r}')
    if any(':' in x for x in segments):
        _fail(f'Недопустимое имя Windows: {path!r}')
    if any(x.endswith(' ') or x.endswith('.') for x in segments):
        _fail(f'Недопустимый путь Windows: {path!r}')
    if any(re.search(r'[<>"|?*]', x) for x in segments):
        _fail(f'Недопустимое имя Windows: {path!r}')
    if any(x.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL',
                   'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
                   'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'} for x in segments):
        _fail(f'Резервное имя Windows: {path!r}')


def _check_file_policy(relative: str) -> None:
    pp = PurePosixPath(relative)
    if any(part in SKIP_DIRS for part in pp.parts):
        _fail(f'Запрещён служебный каталог: {relative}')
    if pp.name in BLOCKED_NAMES or pp.suffix.lower() in BLOCKED_SUFFIXES:
        _fail(f'Не допускается пользовательский/секретный файл: {relative}')
    if pp.name.lower().endswith('.zip') and pp.name.lower().startswith('validation_results'):
        _fail(f'Запрещён отчёт с данными локального теста: {relative}')


def _inventory(folder: Path) -> dict[str, bytes]:
    if folder.is_symlink() or not folder.is_dir():
        _fail('Исходная папка отсутствует или является ссылкой')
    result = {}
    for p in sorted(folder.rglob('*')):
        if p.is_symlink():
            _fail(f'Символическая ссылка недопустима: {p}')
        if p.is_dir():
            continue
        if not p.is_file():
            _fail(f'Необычный элемент ФС: {p}')
        rel = p.relative_to(folder).as_posix()
        _safe_relpath(rel)
        _check_file_policy(rel)
        if rel == MANIFEST_NAME:
            _fail('Манифест создаётся сборщиком, а не копируется из исходников')
        result[rel] = p.read_bytes()
    missing = REQUIRED_FILES - set(result)
    if missing:
        _fail(f'Отсутствуют обязательные файлы: {sorted(missing)}')
    if len({k.casefold() for k in result}) != len(result):
        _fail('В папке есть имена, совпадающие без учёта регистра Windows')
    return result


def _content_fingerprint(files: dict[str, bytes]) -> str:
    return _sha(_json_bytes({k: _sha(v) for k,v in sorted(files.items())}))


def _validate_windows_launchers(files: dict[str, bytes]) -> None:
    """CMD batch files must be ASCII with Windows CRLF (never Unix LF)."""
    for name, payload in files.items():
        if not name.lower().endswith(('.bat', '.cmd')):
            continue
        if (not payload or any(c > 127 or (c < 32 and c not in (9, 10, 13)) for c in payload)
                or b'\r\n' not in payload or b'\n' in payload.replace(b'\r\n', b'')
                or b'\r' in payload.replace(b'\r\n', b'')
                or not payload.endswith(b'\r\n')):
            _fail(f'Windows batch file must be ASCII with CRLF: {name}')


def _validate_release_evidence(evidence: dict, expected_fingerprint: str) -> dict:
    if not isinstance(evidence, dict):
        _fail('Подтверждение релиза должно быть объектом JSON')
    if evidence.get('source_fingerprint') != expected_fingerprint:
        _fail('Подтверждение тестов относится к другому набору исходников')
    try:
        stamp = datetime.fromisoformat(str(evidence['tested_at']).replace('Z', '+00:00'))
    except (ValueError, KeyError, TypeError):
        _fail('Нет корректной даты интеграционной проверки')
    if stamp.tzinfo is None:
        _fail('Дата тестирования должна иметь часовую зону')
    now = datetime.now(timezone.utc)
    if not (now - timedelta(days=7) <= stamp.astimezone(timezone.utc) <= now + timedelta(minutes=5)):
        _fail('Результаты интеграционной проверки устарели или датированы будущим')
    checks = evidence.get('checks')
    if not isinstance(checks, dict):
        _fail('Отсутствуют проверяемые результаты полного интеграционного теста')
    missing = [name for name in RELEASE_CHECKS if not isinstance(checks.get(name), dict)
               or checks[name].get('status') != 'PASS'
               or not isinstance(checks[name].get('evidence_file'), str)
               or not checks[name]['evidence_file'].strip()]
    if missing:
        _fail(f'Не подтверждены обязательные проверки: {", ".join(missing)}')
    # A caller cannot mark PASS with empty descriptions or without evidence files.
    return {'source_fingerprint': expected_fingerprint, 'tested_at': stamp.isoformat(), 'checks': {name: checks[name] for name in RELEASE_CHECKS}}


def _zipinfo(name: str) -> zipfile.ZipInfo:
    # Reproducible fixed ZIP timestamps, no caller-specific paths or permission bits.
    info=zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
    info.compress_type=zipfile.ZIP_DEFLATED
    info.external_attr=(0o100644 << 16)
    return info


def package(folder: Path, target: Path, mode: str, evidence_file: Path | None = None) -> dict:
    if mode not in ('acceptance', 'release'):
        _fail('Неверный профиль архива')
    if not ROOT_RE.fullmatch(folder.name):
        _fail('Корневая папка должна иметь безопасное, стабильное имя')
    src=folder.resolve()
    out=target.resolve()
    if out == src or src in out.parents:
        _fail('Нельзя сохранять ZIP внутри упаковываемой папки')
    if mode == 'release' and 'NOT_RELEASE' in out.name.upper():
        _fail('Профиль release не может называться NOT_RELEASE')
    if mode == 'acceptance' and not any(marker in out.name.upper() for marker in ('NOT_RELEASE', 'NEEDS_REAL_TEST')):
        _fail('Архив приемочных испытаний обязан содержать NOT_RELEASE или NEEDS_REAL_TEST в имени')
    files=_inventory(folder)
    if mode=='acceptance' and folder.name.startswith('AI_SMM_Work_Architecture_'):
        if 'windows_acceptance.py' not in files:
            _fail('Кандидату Windows-проверки нужен windows_acceptance.py')
        if '_tests_source.zip' in files or any(name.split('/')[0] in {'test','tests'} or (name.endswith('.py') and '/' not in name and name.startswith('test_')) for name in files):
            _fail('В установочном кандидате запрещены внутренние тесты; они находятся в комплекте разработчика')
    _validate_windows_launchers(files)
    fingerprint=_content_fingerprint(files)
    release_evidence = None
    if mode == 'release':
        if evidence_file is None or not evidence_file.is_file():
            _fail('RELEASE BLOCKED: для релиза нужны реальные результаты Windows/Ollama/GUI/качества')
        try:
            release_evidence = _validate_release_evidence(json.loads(evidence_file.read_text(encoding='utf-8')),fingerprint)
        except (UnicodeError, json.JSONDecodeError):
            _fail('RELEASE BLOCKED: файл результатов повреждён')
        # Every declared evidence file must be present in the exact source snapshot.
        for name, check in release_evidence['checks'].items():
            rel = check['evidence_file']
            _safe_relpath(rel)
            if rel not in files or not rel.startswith('QA_EVIDENCE/'):
                _fail(f'RELEASE BLOCKED: не найден журнал {name}: {rel}')
            if len(files[rel]) < 20:
                _fail(f'RELEASE BLOCKED: пустой журнал проверки {name}')
        # Evidence must be part of the artifact, so the user can audit the verdict.
        if 'RELEASE_EVIDENCE.json' in files:
            _fail('RELEASE_EVIDENCE.json записывает только сборщик')
        files['RELEASE_EVIDENCE.json'] = _json_bytes(release_evidence)
    manifest={
        'format': 1,
        'product': 'AI SMM Manager',
        'root': folder.name,
        'profile': mode,
        'release_status': 'RELEASE_APPROVED' if mode=='release' else 'NOT_RELEASE',
        'source_fingerprint': fingerprint,
        'required_release_checks': list(RELEASE_CHECKS),
        'files': {name: {'sha256': _sha(data), 'size': len(data)} for name,data in sorted(files.items())},
    }
    files[MANIFEST_NAME]=_json_bytes(manifest)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd, tempname=tempfile.mkstemp(suffix='.zip',dir=target.parent,prefix='.gate-')
    os.close(fd)
    try:
        with zipfile.ZipFile(tempname,'w') as z:
            for name,data in sorted(files.items()):
                z.writestr(_zipinfo(f'{folder.name}/{name}'),data)
        verdict=verify(Path(tempname))
        if verdict['source_fingerprint'] != fingerprint or verdict['profile'] != mode:
            _fail('Повторная верификация ZIP не совпала с исходниками')
        os.replace(tempname,target)
        # Verify the very bytes destined for delivery, not only the temp artifact.
        verify(target)
        return verdict
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)


def verify(path: Path) -> dict:
    if not path.is_file():
        _fail(f'ZIP не существует: {path}')
    try:
        with zipfile.ZipFile(path, 'r') as z:
            if z.testzip() is not None:
                _fail('ZIP повреждён: контрольная сумма CRC не совпадает')
            infos=z.infolist()
            if not infos:
                _fail('Пустой ZIP')
            names=[i.filename for i in infos]
            if len(names) != len(set(names)):
                _fail('ZIP содержит дублирующиеся пути')
            if len(names) != len({x.casefold() for x in names}):
                _fail('ZIP содержит дублирующиеся пути для Windows')
            if any(i.flag_bits & 0x1 for i in infos):
                _fail('ZIP содержит шифрованный файл')
            roots=set()
            by_rel={}
            for info in infos:
                name=info.filename
                _safe_relpath(name)
                p=name.rstrip('/').split('/')
                root=p[0]
                roots.add(root)
                if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    _fail(f'Символическая ссылка внутри ZIP: {name}')
                if info.is_dir() and len(p) == 1:
                    continue  # A single explicit root directory entry is permitted.
                if len(p) < 2:
                    _fail(f'Обнаружен файл или каталог вне единственной корневой папки: {name}')
                if info.is_dir():
                    continue
                relative='/'.join(p[1:])
                _check_file_policy(relative)
                by_rel[relative]=z.read(info)
            if len(roots) != 1:
                _fail(f'В ZIP должно быть строго одно корневое имя, найдено: {sorted(roots)}')
            root=next(iter(roots))
            if not ROOT_RE.fullmatch(root):
                _fail('Неверное имя корневой папки')
            _validate_windows_launchers(by_rel)
            if 'DISTRIBUTION_MANIFEST.json' in by_rel:
                return _verify_kit_inventory(path, root, by_rel)
            missing=REQUIRED_FILES-set(by_rel)
            if missing:
                _fail(f'В ZIP отсутствуют обязательные файлы: {sorted(missing)}')
            if MANIFEST_NAME not in by_rel:
                _fail('Отсутствует обязательный контрольный манифест')
            try:
                manifest=json.loads(by_rel.pop(MANIFEST_NAME).decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                _fail('Повреждён манифест')
            if not isinstance(manifest,dict) or manifest.get('format')!=1 or manifest.get('root')!=root:
                _fail('Манифест не соответствует корню и формату')
            manifest_files=manifest.get('files')
            if not isinstance(manifest_files,dict) or set(manifest_files)!=set(by_rel):
                _fail('Состав ZIP не совпадает с контрольным манифестом')
            for name,data in by_rel.items():
                entry=manifest_files.get(name)
                if not isinstance(entry,dict) or entry.get('sha256')!=_sha(data) or entry.get('size')!=len(data):
                    _fail(f'Нарушена целостность: {name}')
            mode=manifest.get('profile')
            if mode == 'acceptance':
                if manifest.get('release_status')!='NOT_RELEASE':
                    _fail('Нельзя маркировать испытательный архив как релиз')
                if not any(x in path.name.upper() for x in ('NOT_RELEASE','NEEDS_REAL_TEST','.GATE-')):
                    _fail('Испытательный архив неправильно назван')
                if 'RELEASE_EVIDENCE.json' in by_rel:
                    _fail('Испытательный архив не может содержать свидетельство одобренного релиза')
            elif mode == 'release':
                if manifest.get('release_status')!='RELEASE_APPROVED':
                    _fail('Статус выпуска не подтверждён')
                evidence=by_rel.get('RELEASE_EVIDENCE.json')
                if not evidence:
                    _fail('Нет подтверждения реальных интеграционных тестов')
                try:
                    validated=_validate_release_evidence(json.loads(evidence.decode('utf-8')),manifest.get('source_fingerprint'))
                    for check_name, check in validated['checks'].items():
                        rel=check['evidence_file']
                        _safe_relpath(rel)
                        if rel not in by_rel or not rel.startswith('QA_EVIDENCE/') or len(by_rel[rel])<20:
                            _fail(f'Отсутствует доказательство реального испытания {check_name}')
                except (UnicodeDecodeError, json.JSONDecodeError):
                    _fail('Повреждено подтверждение релиза')
                if 'NOT_RELEASE' in path.name.upper() or 'NEEDS_REAL_TEST' in path.name.upper():
                    _fail('Релиз не должен называться испытательной сборкой')
            else:
                _fail('Неизвестный профиль архива')
            fingerprint_files={k:v for k,v in by_rel.items() if k!='RELEASE_EVIDENCE.json'}
            if _content_fingerprint(fingerprint_files)!=manifest.get('source_fingerprint'):
                _fail('Контрольная сумма исходного набора не совпадает')
            return {'status':'PASS', 'root':root, 'profile':mode,
                    'release_status':manifest['release_status'], 'file_count':len(by_rel),
                    'source_fingerprint': manifest['source_fingerprint'],
                    'zip_sha256': _sha(path.read_bytes())}
    except (zipfile.BadZipFile, EOFError, OSError) as e:
        _fail(f'ZIP не читается: {e}')


def _verify_kit_inventory(path: Path, root: str, files: dict[str, bytes]) -> dict:
    """Verify every kit byte and the contained program with its own manifest."""
    try:
        manifest=json.loads(files['DISTRIBUTION_MANIFEST.json'].decode('utf-8'))
    except (KeyError, UnicodeError, json.JSONDecodeError):
        _fail('Повреждён манифест комплекта')
    if (not isinstance(manifest,dict) or manifest.get('format')!=1
            or manifest.get('root')!=root or manifest.get('profile')!='acceptance-kit'
            or manifest.get('release_status')!='NOT_RELEASE'):
        _fail('Неверный формат или статус комплекта')
    if not any(x in path.name.upper() for x in ('NOT_RELEASE','NEEDS_REAL_TEST','.GATE-')):
        _fail('Испытательный комплект неправильно назван')
    entries=manifest.get('files')
    actual={k:v for k,v in files.items() if k!='DISTRIBUTION_MANIFEST.json'}
    if not isinstance(entries,dict) or set(entries)!=set(actual):
        _fail('Состав комплекта не совпадает с манифестом')
    for name,data in actual.items():
        entry=entries[name]
        if not isinstance(entry,dict) or entry.get('sha256')!=_sha(data) or entry.get('size')!=len(data):
            _fail('Нарушена целостность комплекта: '+name)
    program='AI_SMM_Work_Architecture_A75'
    tests='AI_SMM_Developer_A75/tests'
    for launcher in ('RUN_AI_SMM_MANAGER.cmd','RUN_ARCHITECTURE_CHECK.cmd'):
        if program+'/'+launcher not in actual:
            _fail('В комплекте нет точки входа: '+launcher)
    if (tests+'/a2_recovery_fixture.json' not in actual
            or not any(n.startswith(tests+'/test_') and n.endswith('.py') for n in actual)):
        _fail('В комплекте отсутствует исходный набор проверок')
    for name,data in actual.items():
        if name.startswith(program+'/') and name.endswith('.py') and name.count('/')==1:
            if actual.get(tests+'/'+name.split('/')[-1])!=data:
                _fail('Исходники программы и проверок различаются: '+name)
    with tempfile.TemporaryDirectory(prefix='verify_kit_') as folder:
        base=Path(folder)
        for name,data in actual.items():
            # Common ZIP verification has already checked path and file policy.
            target=base/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
        result=verify_tree(base/program)
    if (result.get('profile')!='acceptance'
            or result['source_fingerprint']!=manifest.get('source_fingerprint')):
        _fail('Комплект и программа относятся к разным исходникам или профилям')
    return {'status':'PASS','root':root,'profile':'acceptance-kit','release_status':'NOT_RELEASE',
            'file_count':len(actual),'source_fingerprint':result['source_fingerprint'],
            'zip_sha256':_sha(path.read_bytes())}


def package_kit(folder: Path, target: Path) -> dict:
    """One guarded writer for a program plus adjacent developer verification kit."""
    if not ROOT_RE.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
        _fail('Некорректный корень комплекта')
    if folder.resolve() in target.resolve().parents or folder.resolve()==target.resolve():
        _fail('Нельзя сохранять ZIP внутри комплекта')
    if not any(x in target.name.upper() for x in ('NOT_RELEASE','NEEDS_REAL_TEST')):
        _fail('Испытательный комплект обязан содержать NOT_RELEASE в имени')
    files={}
    for p in sorted(folder.rglob('*')):
        if p.is_symlink():
            _fail('Ссылка в комплекте: '+str(p))
        if p.is_dir():
            continue
        if not p.is_file():
            _fail('Необычный элемент комплекта: '+str(p))
        rel=p.relative_to(folder).as_posix()
        _safe_relpath(rel);_check_file_policy(rel)
        if rel=='DISTRIBUTION_MANIFEST.json':
            _fail('Манифест комплекта создаёт только сборщик')
        files[rel]=p.read_bytes()
    if len(files)!=len({n.casefold() for n in files}):
        _fail('Дублирующиеся имена Windows в комплекте')
    _validate_windows_launchers(files)
    result=verify_tree(folder/'AI_SMM_Work_Architecture_A75')
    manifest={'format':1,'root':folder.name,'profile':'acceptance-kit','release_status':'NOT_RELEASE',
              'source_fingerprint':result['source_fingerprint'],
              'files':{n:{'sha256':_sha(data),'size':len(data)} for n,data in files.items()}}
    files['DISTRIBUTION_MANIFEST.json']=_json_bytes(manifest)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tempname=tempfile.mkstemp(suffix='.zip',prefix='.gate-',dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tempname,'w') as z:
            for name,data in sorted(files.items()):
                z.writestr(_zipinfo(folder.name+'/'+name),data)
        verify(Path(tempname))
        os.replace(tempname,target)
        return verify(target)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)



def verify_tree(folder: Path) -> dict:
    """Verify the extracted program tree against the exact shipped manifest.

    Untracked local runtime output (.venv, validation logs) is permitted, but
    altering or removing a shipped program file invalidates acceptance evidence.
    """
    folder=folder.resolve()
    path=folder/ MANIFEST_NAME
    if not path.is_file():
        _fail('Нет PACKAGE_MANIFEST.json в распакованной папке')
    try:
        manifest=json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, OSError, json.JSONDecodeError):
        _fail('Повреждён манифест распакованного пакета')
    if not isinstance(manifest,dict) or manifest.get('format')!=1 or manifest.get('root')!=folder.name:
        _fail('Распакованная папка не соответствует контрольному манифесту')
    entries=manifest.get('files')
    if not isinstance(entries,dict) or not REQUIRED_FILES.issubset(entries):
        _fail('Распакованный пакет не содержит обязательные модули')
    actual={}
    for rel,descriptor in entries.items():
        _safe_relpath(rel)
        _check_file_policy(rel)
        candidate=folder/rel
        if candidate.is_symlink() or not candidate.is_file():
            _fail(f'Обязательный файл отсутствует или заменён ссылкой: {rel}')
        try:
            data=candidate.read_bytes()
        except OSError:
            _fail(f'Не удалось прочесть обязательный файл: {rel}')
        if not isinstance(descriptor,dict) or descriptor.get('sha256')!=_sha(data) or descriptor.get('size')!=len(data):
            _fail(f'Файл изменён после упаковки: {rel}')
        actual[rel]=data
    _validate_windows_launchers(actual)
    # Executable leftovers from older folders are dangerous: import resolution
    # may load an untracked module while all shipped files still hash correctly.
    # This is especially important for Windows users unpacking multiple stages.
    for candidate in folder.rglob('*'):
        if candidate.is_dir():
            continue
        relative=candidate.relative_to(folder).as_posix()
        parts=set(candidate.relative_to(folder).parts)
        if parts & {'.venv', '__pycache__', 'VALIDATION_RESULTS', '.pytest_cache'}:
            continue
        if (relative not in entries and relative != MANIFEST_NAME
                and candidate.suffix.lower() in ('.py', '.pyw', '.pyd', '.dll', '.exe', '.bat', '.cmd')):
            _fail(f'Обнаружен сторонний исполняемый файл, возможно от другой версии: {relative}')
    profile=manifest.get('profile')
    if profile=='acceptance':
        if manifest.get('release_status')!='NOT_RELEASE':
            _fail('Испытательный пакет ошибочно утверждает статус релиза')
    elif profile=='release':
        if manifest.get('release_status')!='RELEASE_APPROVED':
            _fail('Статус релиза не подтверждён')
        try:
            evidence=json.loads(actual['RELEASE_EVIDENCE.json'])
        except (KeyError, UnicodeError, json.JSONDecodeError):
            _fail('Нет корректного подтверждения релиза')
        _validate_release_evidence(evidence,manifest.get('source_fingerprint'))
    else:
        _fail('Неизвестный профиль распакованного пакета')
    inputs={k:v for k,v in actual.items() if k!='RELEASE_EVIDENCE.json'}
    if _content_fingerprint(inputs)!=manifest.get('source_fingerprint'):
        _fail('Состав файлов не совпадает со свидетельством тестирования')
    return {'status':'PASS','root':folder.name,'profile':profile,
            'source_fingerprint':manifest['source_fingerprint'],'verified_files':len(actual)}


def main() -> int:
    parser=argparse.ArgumentParser(description='Fail-closed AI SMM package policy')
    sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('package')
    p.add_argument('folder',type=Path)
    p.add_argument('archive',type=Path)
    p.add_argument('--mode',required=True,choices=['acceptance','release'])
    p.add_argument('--evidence',type=Path)
    v=sub.add_parser('verify')
    v.add_argument('archive',type=Path)
    vt=sub.add_parser('verify-tree')
    vt.add_argument('folder',type=Path)
    kit=sub.add_parser('package-kit')
    kit.add_argument('folder',type=Path)
    kit.add_argument('archive',type=Path)
    args=parser.parse_args()
    try:
        result=(package(args.folder,args.archive,args.mode,args.evidence) if args.cmd=='package'
                else package_kit(args.folder,args.archive) if args.cmd=='package-kit'
                else verify(args.archive) if args.cmd=='verify' else verify_tree(args.folder))
        print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
        return 0
    except PackagingBlocked as e:
        print('BLOCKED: '+str(e),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
