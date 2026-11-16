"""Offline QA Python discovery; never imports ML, installs packages or runs QA."""
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import struct
import sys


DEPENDENCIES = (
    ('PySide6', 'PySide6'), ('Pillow', 'PIL'),
    ('gradio_client', 'gradio_client'), ('pytest', 'pytest'),
    ('pytest-subtests', 'pytest_subtests'), ('psutil', 'psutil'),
)


def inspect_environment():
    issues = []
    if sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
        issues.append('QA requires Python 3.12 x64')
    dependencies = {}
    for distribution, module in DEPENDENCIES:
        try:
            installed = version(distribution)
        except PackageNotFoundError:
            installed = None
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            available = False
        provider = distribution
        # pytest 9 includes the same subtests fixture in its core. Require the
        # capability, including on pytest 8 where the separate plugin is needed.
        if module == 'pytest_subtests' and (installed is None or not available):
            try:
                if importlib.util.find_spec('_pytest.subtests') is not None:
                    installed = version('pytest')
                    available = True
                    provider = 'pytest (built-in subtests)'
            except (ImportError, ValueError, PackageNotFoundError):
                pass
        dependencies[distribution] = {'version': installed, 'module_available': available, 'provider': provider}
        if installed is None or not available:
            issues.append('Missing or incomplete QA dependency: ' + distribution)
    return {
        'status': 'BLOCKED' if issues else 'READY_FOR_QA',
        'scope': 'Python dependency discovery only; not Qt/CUDA acceptance',
        'executable': sys.executable, 'python': sys.version,
        'virtual_environment': sys.prefix != sys.base_prefix,
        'dependencies': dependencies, 'issues': issues,
        'remedy': 'Run SETUP_WINDOWS_QA.cmd in this source folder, then RUN_WINDOWS_QA.cmd. '
                  'An explicit AI_SMM_QA_PYTHON overrides the source .venv; '
                  'clear it or select an already prepared Python 3.12 x64.',
        'automatic_downloads': False, 'release_eligible': False,
    }


if __name__ == '__main__':
    report = inspect_environment()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(2 if report['issues'] else 0)
