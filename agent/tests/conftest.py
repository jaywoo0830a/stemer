"""agent 패키지 테스트 — repo root 를 PYTHONPATH 에 올려 `import agent` 가능하게 한다.

사용:
    cd /home/rlawjddn/projects/stemer
    python -m pytest agent/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]  # agent/ 바로 위 = stemer/
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
