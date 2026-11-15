"""Reproduce unpacked-source/system-Python failure without Windows or downloads."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import windows_qa_environment as environment
import windows_qa_session as session


def test_reports_all_missing_dependencies_without_importing_them(monkeypatch):
    def missing(name):
        raise environment.PackageNotFoundError(name)
    monkeypatch.setattr(environment, 'version', missing)
    monkeypatch.setattr(environment.importlib.util, 'find_spec', lambda name: None)
    report = environment.inspect_environment()
    for name, _ in environment.DEPENDENCIES:
        assert any(name in issue for issue in report['issues'])
    assert report['status'] == 'BLOCKED'
    assert report['automatic_downloads'] is False
    assert report['release_eligible'] is False
    assert report['executable'] == sys.executable


def test_metadata_without_importable_package_does_not_pass(monkeypatch):
    monkeypatch.setattr(environment, 'version', lambda name: '1.0')
    monkeypatch.setattr(environment.importlib.util, 'find_spec', lambda name: None)
    assert len(environment.inspect_environment()['issues']) >= 6


@pytest.mark.parametrize('builtin', [False, True])
def test_both_supported_subtest_providers_require_discovery(monkeypatch, builtin):
    def installed(name):
        if builtin and name == 'pytest-subtests':
            raise environment.PackageNotFoundError(name)
        return '9.2.0' if builtin else '8.3.0'
    monkeypatch.setattr(environment, 'version', installed)
    monkeypatch.setattr(environment.importlib.util, 'find_spec',
                        lambda name: None if builtin and name == 'pytest_subtests' else object())
    monkeypatch.setattr(environment.sys, 'version_info', (3, 12))
    monkeypatch.setattr(environment.struct, 'calcsize', lambda name: 8)
    report = environment.inspect_environment()
    assert report['issues'] == []
    assert report['dependencies']['pytest-subtests']['provider'] == (
        'pytest (built-in subtests)' if builtin else 'pytest-subtests')


@pytest.mark.parametrize('python,bits', [((3, 11), 8), ((3, 12), 4)])
def test_wrong_python_or_bitness_is_blocked(monkeypatch, python, bits):
    monkeypatch.setattr(environment.sys, 'version_info', python)
    monkeypatch.setattr(environment.struct, 'calcsize', lambda name: bits)
    assert 'QA requires Python 3.12 x64' in environment.inspect_environment()['issues']


def test_blocked_preflight_precedes_wizard_config_and_models(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['windows_qa_session.py', '--role', 'preliminary_3080ti', '--output', str(tmp_path)])
    snapshot = {'fingerprint': 'fixture-source', 'files': {}}
    monkeypatch.setattr(session, 'source_snapshot', lambda root: snapshot)
    monkeypatch.setattr(session, 'bootstrap_inventory', lambda folder: None)
    monkeypatch.setattr(session, 'inspect_environment', lambda: {'issues': ['Missing PySide6'], 'remedy': 'Run SETUP_WINDOWS_QA.cmd'})
    def forbidden(*args):
        pytest.fail('A blocked Python must not open configuration, UI or models')
    monkeypatch.setattr(session, 'wizard', forbidden)
    monkeypatch.setattr(session, 'read_config', forbidden)
    monkeypatch.setattr(session, 'execute', forbidden)
    assert session.main() == 2
    with ZipFile(tmp_path / 'DIAGNOSTICS.zip') as archive:
        assert json.loads(archive.read('QA_SESSION/SOURCE_SNAPSHOT.json')) == snapshot
        status = json.loads(archive.read('QA_SESSION/STATUS.json'))
        assert status['status'] == 'BLOCKED'
        assert status['target_clean_consecutive'] == 0
        assert status['release_eligible'] is False


def test_actual_empty_venv_produces_complete_offline_blocked_archive(tmp_path):
    virtual = tmp_path / 'QA Python без пакетов'
    venv.EnvBuilder(with_pip=False).create(virtual)
    python = virtual / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    output = tmp_path / 'отчёт'
    output.mkdir()
    # Invalid/old config must survive, and missing dependencies must take priority.
    settings = output / 'settings.json'
    settings.write_bytes(b'USER_SETTINGS_UNCHANGED')
    env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
    result = subprocess.run([str(python), str(ROOT / 'windows_qa_session.py'),
                             '--role', 'preliminary_3080ti', '--output', str(output)],
                            env=env, capture_output=True, timeout=45)
    assert result.returncode == 2, result.stderr
    assert settings.read_bytes() == b'USER_SETTINGS_UNCHANGED'
    assert not list(output.glob('*/run_*'))
    with ZipFile(output / 'DIAGNOSTICS.zip') as archive:
        report = json.loads(archive.read('QA_SESSION/PYTHON_ENVIRONMENT.json'))
        assert report['virtual_environment'] is True
        assert all(item['version'] is None for item in report['dependencies'].values())
        assert len(report['issues']) >= 6
        snapshot = json.loads(archive.read('QA_SESSION/SOURCE_SNAPSHOT.json'))
        for name in ('windows_qa_session.py', 'windows_qa_environment.py', 'SETUP_WINDOWS_QA.cmd'):
            assert snapshot['files'][name] == hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        manifest = json.loads(archive.read('QA_SESSION/REPORT_MANIFEST.json'))
        for name, digest in manifest['files'].items():
            assert hashlib.sha256(archive.read('QA_SESSION/' + name)).hexdigest() == digest


def test_setup_is_explicit_and_never_installs_into_selected_model_python():
    setup = (ROOT / 'SETUP_WINDOWS_QA.cmd').read_text(encoding='utf-8')
    launcher = (ROOT / 'RUN_WINDOWS_QA.cmd').read_text(encoding='utf-8')
    assert '-m venv "%~dp0.venv"' in setup
    assert 'set "QA_SETUP_PYTHON=%~dp0.venv\\Scripts\\python.exe"' in setup
    assert '"%AI_SMM_QA_PYTHON%"' not in setup
    assert 'pip check' in setup
    assert 'pip ' not in launcher
    assert 'call SETUP_WINDOWS_QA' not in launcher
    assert 'ollama pull' not in setup
