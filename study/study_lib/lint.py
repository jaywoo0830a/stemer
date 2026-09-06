"""KaTeX 린트 — 렌더된 마크다운의 수식이 오류 없이 렌더되는지 검증 (무료 로컬 게이트).

MATH-PROTOCOL 규칙 (출력 토큰 최소화 + KaTeX 오류 방지):
1. 수식은 `$...$` (인라인) 로 감싼다 — $ 짝이 맞아야 한다.
2. 내용은 **유니코드 수학 문자 우선** (KaTeX 가 직접 렌더: ∫∑√∂≤≥≠∈∀→±×÷).
3. 유니코드로 못 그리는 복합 구조만 최소 명령 허용:
   \\frac \\sqrt \\sum \\int \\lim \\to \\infty \\cdot \\times \\left \\right 등.
4. 금지: 다중행 환경(align/equation/gather/...), \\bm \\mathds 등 위험 매크로.

클라이언트 관점:
    issues = lint_katex(markdown)   # [] 이면 통과
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FORBIDDEN_ENVS = (
    "align", "align*", "equation", "equation*", "eqnarray", "gather",
    "split", "proof", "theorem", "lemma", "corollary", "definition",
    "matrix", "pmatrix", "bmatrix", "cases", "array",
)
FORBIDDEN_MACROS = ("\\bm", "\\mathds", "\\text", "\\tag", "\\def", "\\color")

# 유니코드로 안 그려지는 복합 구조에만 허용하는 최소 명령 (백슬래시 포함 토큰)
ALLOWED_MACROS = (
    "frac", "dfrac", "tfrac", "sqrt", "sum", "prod", "int", "iint", "iiint",
    "lim", "limsup", "liminf", "to", "mapsto", "infty", "cdot", "times",
    "left", "right", "left(", "right)", "left[", "right]", "big", "Big",
    "bigg", "Bigg", "partial", "nabla", "binom", "approx", "equiv", "le",
    "ge", "ne", "pm", "mp", "div", "ldots", "cdots", "dots", "pi", "alpha",
    "beta", "gamma", "delta", "epsilon", "varepsilon", "theta", "lambda",
    "mu", "sigma", "phi", "varphi", "omega", "Gamma", "Delta", "Theta",
    "Lambda", "Sigma", "Phi", "Omega", "pi", "ln", "log", "exp", "sin",
    "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "arcsin",
    "arccos", "arctan", "max", "min", "sup", "inf", "det", "ker", "dim",
)
_ALLOWED_RE = re.compile(r"\\(?:" + "|".join(ALLOWED_MACROS) + r")(?![a-zA-Z])")
_ANY_MACRO_RE = re.compile(r"\\[a-zA-Z]+")
_ENV_RE = re.compile(r"\\begin\{(" + "|".join(FORBIDDEN_ENVS) + r")\}")


@dataclass(frozen=True)
class LintIssue:
    line: int
    message: str


def _count_dollar_spans(line: str) -> int:
    """줄에서 수식 래퍼로 쓰이는 $ 개수 (이스케이프 \\$ 제외)."""
    return len(re.findall(r"(?<!\\)\$", line))


def _dollar_balanced(markdown: str) -> tuple[bool, int]:
    """전체 문서에서 $ 짝이 맞는지. (ok, 첫 불균형 라인)."""
    balance = 0
    for lineno, line in enumerate(markdown.splitlines(), 1):
        n = _count_dollar_spans(line)
        # 한 줄에 $$...$$ (디스플레이) 처리는 짝수면 OK
        balance += n
        if balance % 2 != 0 and "$" not in line.replace("\\$", ""):
            # 줄 내부에서만 열리고 닫힌 게 아니라면 상태 추적
            pass
    return (balance % 2 == 0), balance


def lint_katex(markdown: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for lineno, line in enumerate(markdown.splitlines(), 1):
        # ① 금지 환경
        m = _ENV_RE.search(line)
        if m:
            issues.append(LintIssue(lineno, f"forbidden env '{{{m.group(1)}}}'"))
        # ② 금지 매크로
        for macro in FORBIDDEN_MACROS:
            if macro in line:
                issues.append(LintIssue(lineno, f"forbidden macro '{macro}'"))
        # ③ 허용 목록 밖의 매크로 (백슬래시 명령) → KaTeX 오류/렌더 실패 유발 가능
        for macro in _ANY_MACRO_RE.findall(line):
            name = macro[1:]
            if name in ("begin", "end"):     # env 는 ①에서 이미 처리
                continue
            if not _ALLOWED_RE.search(macro):
                # 이미 금지 목록에서 걸렸으면 중복 보고 생략
                if any(f in macro for f in FORBIDDEN_MACROS):
                    continue
                issues.append(LintIssue(lineno, f"macro not in allowed set: '{macro}'"))
        # ④ 인라인 $ 홀수 개 (한 줄에 열고 닫음이 안 맞음)
        n = _count_dollar_spans(line)
        if n % 2 != 0:
            issues.append(LintIssue(lineno, "odd number of '$' on line (unbalanced math)"))
    # ⑤ 문서 전체 $ 균형
    ok, balance = _dollar_balanced(markdown)
    if not ok:
        issues.append(LintIssue(0, f"unbalanced '$' across document (net {balance})"))
    return issues

