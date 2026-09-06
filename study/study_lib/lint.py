"""KaTeX 린트 — 렌더된 마크다운에서 금지 환경/매크로를 찾는다 (무료 로컬 게이트).

클라이언트 관점:
    issues = lint_katex(markdown)   # [] 이면 통과
    # LintIssue(line, message) — 금지 env: align/equation/gather/split/proof/...
    #                          금지 macro: \\bm, \\mathds
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FORBIDDEN_ENVS = (
    "align", "align*", "equation", "equation*", "eqnarray", "gather",
    "split", "proof", "theorem", "lemma", "corollary", "definition",
)
FORBIDDEN_MACROS = ("\\bm", "\\mathds")

_ENV_RE = re.compile(r"\\begin\{(" + "|".join(FORBIDDEN_ENVS) + r")\}")


@dataclass(frozen=True)
class LintIssue:
    line: int
    message: str


def lint_katex(markdown: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for lineno, line in enumerate(markdown.splitlines(), 1):
        m = _ENV_RE.search(line)
        if m:
            issues.append(LintIssue(lineno, f"forbidden env '{{{m.group(1)}}}'"))
        for macro in FORBIDDEN_MACROS:
            if macro in line:
                issues.append(LintIssue(lineno, f"forbidden macro '{macro}'"))
    return issues
