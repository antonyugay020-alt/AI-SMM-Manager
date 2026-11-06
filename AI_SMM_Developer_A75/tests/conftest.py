"""Load canonical production modules and legacy flat-name test fixtures.

Pytest importlib collection prevents collisions between root acceptance tests
and mirrored tests. The historical tests/ fixture imports still resolve here.
No production sys.path mutation is required.
"""
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (str(TESTS), str(SOURCE)):
    if path not in sys.path:
        sys.path.append(path)
# Canonical production modules always win over test mirror modules.
src = str(SOURCE)
if src in sys.path:
    sys.path.remove(src)
sys.path.insert(0, src)
