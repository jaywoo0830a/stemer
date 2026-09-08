"""symrun — '계산은 모델이 아니라 sympy' 실행 계층 (SYMPYMETHOD.md).

원칙:
  - 문제 생성 에이전트는 숫자를 암산 금지. 각 수치 해답 근처에 실행 가능한
    ```sympy``` 블록을 내고, 그 블록의 출력이 Solution key 의 최종 답이어야 함.
  - 이 모듈이 그 블록을 실제로 로컬 실행해
      1) 문법/실행 오류 여부(작성 실패 = 신뢰 불가 → 거부),
      2) 최종 수치 값 추출(sympy) 후,
      3) Solution key 에 적힌 답과 일치하는지(손계산 드리프트 탐지) 비교하는
    결정론적 게이트를 제공한다.

안전한 실행:
  - 시간 제한(timeout), 내장 임포트 차단, 순수 sympy + print 만 허용하는
    축소된 전역에서 exec. LLM 이 만든 임의 코드를 exec 하는 위험을 최소화.
    (운영에서 완전한 샌드박스는 Docker 컨테이너에서 구동 권장.)
sympy 미설치 시: run 은 unavailable → 게이트는 false판정 방향이 아닌
'동작 불가(no_sympy)' 플래그로 호출자에게 알림(설치 안 된 환경에선 건너뜀).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import sympy  # noqa: F401  (import 확인용)
    HAS_SYMPY = True
except Exception:  # noqa: BLE001
    HAS_SYMPY = False

# ```sympy (또는 ```python) 펜스 블록 추출
_FENCE = re.compile(r"```sympy\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FENCE_BOTH = re.compile(
    r"```(?:sympy|python)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_ALLOWED_BUILTINS = {
    "abs", "round", "int", "float", "bool", "min", "max", "print", "range",
    "list", "str", "len", "sum", "sorted", "tuple", "type", "complex",
    "True", "False", "None", "isinstance",
}
_FORBIDDEN_SUBSTR = (
    "__import__", "eval(", "exec(", "open(", "os.", "sys.", "subprocess",
    "globals()", "locals()", "compile(", "type(",
)
_NUM_TOKEN = re.compile(
    r"(?<![\d._a-zA-Z])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\d._a-zA-Z])")
# 유리수 'a/b' 출력(예: Rational 을 print 시 '5/4') 을 한 값으로 잡는 토큰
_FRAC_TOKEN = re.compile(r"(?<!\d)(\d+)\s*/\s*(\d+)(?![\d.])")
# 출력에서 최종 수치 값 후보로 읽을 이름들 (마지막 할당 우선)
_RESULT_NAMES = ("answer", "result", "ans", "n", "N", "value", "x")


@dataclass
class BlockResult:
    """한 ```sympy``` 블록 실행 결과."""
    ok: bool
    error: Optional[str] = None          # 실행 불가 사유
    raw_output: str = ""                 # exec 속 print 들을 모은 stdout
    value: Optional[float] = None        # 추출된 최종 수치(sympy -> float)
    values: List[float] = field(default_factory=list)  # 후보 수치들
    value_note: str = ""                 # 값 추출 경로(none 일 때 사유)

    def has_number(self) -> bool:
        return self.value is not None or bool(self.values)


def extract_blocks(text: str, *, sympy_only: bool = True) -> List[str]:
    """본문에서 펜스 블록 코드 추출. sympy_only=True 면 ```sympy``` 만, False 는
    python 펜스도 허용(교정 시 유연)."""
    pat = _FENCE if sympy_only else _FENCE_BOTH
    return [m.group(1).strip() for m in pat.finditer(text or "")]


DEFAULT_TIMEOUT = 8.0  # 초


def run_block(code: str, *, timeout: float = DEFAULT_TIMEOUT) -> BlockResult:
    """생성된 sympy 코드를 안전 실행. 예외/금지패턴/시간초과 → ok=False."""
    code = (code or "").strip()
    if not code:
        return BlockResult(ok=False, error="empty sympy block")
    if not HAS_SYMPY:
        return BlockResult(ok=False, error="no_sympy module is not installed")
    low = code.lower()
    for frag in _FORBIDDEN_SUBSTR:
        if frag in low:
            return BlockResult(ok=False,
                               error=f"forbidden token '{frag}' in sympy block")
    if "__builtins__" in code or "import os" in low or "import sys" in low:
        return BlockResult(ok=False, error="block redefines/escapes stdlib")

    import builtins as _b
    captured: List[str] = []

    def _print(*args, **kwargs):  # noqa: A001
        sep = kwargs.get("sep", " ")
        captured.append(sep.join(str(a) for a in args))

    # 실행 전역: 축소된 내장 + sympy 이름 전체 노출(from sympy import * 근사).
    safe_builtins = {k: getattr(_b, k) for k in _ALLOWED_BUILTINS}
    safe_builtins["print"] = _print

    def _guard_import(name, *_, **__):
        # exec 안에서 import 는 오로지 sympy(+그 하위모듈) 만 허용.
        if name == "sympy" or name.startswith("sympy."):
            return sympy if name == "sympy" else getattr(sympy, name.rsplit(".")[-1])
        raise ImportError(f"import of '{name}' is blocked in sympy sandbox")

    safe_builtins["__import__"] = _guard_import
    g = {"__builtins__": safe_builtins, "sympy": sympy}
    for attr in dir(sympy):
        if not attr.startswith("_"):
            g[attr] = getattr(sympy, attr)

    import threading

    outcome: dict = {}

    def _worker():
        try:
            ns2 = dict(g)
            ns2["print"] = _print
            exec(compile(code, "<sympy>", "exec"), ns2, ns2)  # noqa: S102
            flat = {k: v for k, v in ns2.items() if not k.startswith("__")}
            outcome["ns"] = flat
        except Exception as exc:  # noqa: BLE001
            outcome["exc"] = exc

    thr = threading.Thread(target=_worker, daemon=True)
    thr.start()
    thr.join(timeout)
    if thr.is_alive():
        return BlockResult(ok=False, error=f"sympy timeout > {timeout}s")

    exc = outcome.get("exc")
    if exc is not None:
        return BlockResult(ok=False, error=f"sympy execution error: {exc}")

    ns2 = outcome.get("ns", {})
    raw = "\n".join(captured).strip()

    # 값 추출 — 우선순위: (1) answer/result/… 명시 변수, (2) 출력 파싱.
    var_vals: List[float] = []
    for name in _RESULT_NAMES:
        v = ns2.get(name)
        if v is None:
            continue
        try:
            if hasattr(v, "evalf"):
                var_vals.append(float(sympy.N(v, 15)))
            elif isinstance(v, (int, float)):
                var_vals.append(float(v))
        except Exception:  # noqa: BLE001
            continue

    # 출력(raw)에서 순서대로 candidate 수치 추출: 유리수 'a/b' 는 한 값으로,
    # 소수·정수 순서로. 마지막 print 결과 = 보통 답.
    candidates: List[float] = []
    for frac in _FRAC_TOKEN.findall(raw):
        try:
            candidates.append(float(int(frac[0]) / int(frac[1])))
        except (ValueError, ZeroDivisionError):
            pass
    if not candidates:
        for tok in _NUM_TOKEN.findall(raw):
            try:
                candidates.append(float(tok))
            except ValueError:
                pass

    if var_vals:
        value = var_vals[-1]
    elif candidates:
        value = candidates[-1]
    else:
        value = None
    all_vals = [v for v in candidates if v not in var_vals] + var_vals
    if not all_vals and value is not None:
        all_vals = [value]
    note = "" if value is not None else "no numeric value printed/assigned"
    return BlockResult(ok=True, raw_output=raw, value=value,
                       values=all_vals, value_note=note)


def run_problems_blocks(text: str) -> List[BlockResult]:
    """문제셋 본문 전체에서 모든 ```sympy``` 블록을 실행해 리스트로 반환."""
    return [run_block(c) for c in extract_blocks(text)]


# --- 결정론 비교: 손계산 드리프트 탐지 ---------------------------------------
def solution_has_value(solution_text: str, value: float,
                       tol: float = 1e-6) -> bool:
    """solution_text 에 value 가 (근사/정수 포함) 쓰였는지."""
    text = (solution_text or "")
    if value is None:
        return False
    if abs(value - round(value)) < 1e-9:      # 정수 답
        iv = int(round(value))
        # 분모(1/32)·소수 속(.32·1.32) 의 정수는 해당 값 아님 → 그 앞의 '/','.' 배제
        return bool(re.search(rf"(?<![\d./])\s*{iv}(?!\d)", text))
    # 실수: 소수 토큰 근접
    for tok in _NUM_TOKEN.findall(text):
        try:
            if abs(float(tok) - value) <= tol:
                return True
        except ValueError:
            continue
    return False


# answer 본문을 PROBLEM 블록 단위로 분리(작은 로컬 버전 — grounding 과 독립)
_PROBLEM_SPLIT = re.compile(r"(?m)^\s*(?=(?:PROBLEM|문제)|(?:\d+\s*[.)]\s*(?:Question|문제)))")


def split_problem_blocks(text: str) -> List[str]:
    """'PROBLEM N'/‘문제 N’ 단위로 대략 분리(없으면 전체 1블록)."""
    text = (text or "").strip()
    starts = [m.start() for m in _PROBLEM_SPLIT.finditer(text)]
    if not starts:
        return [text] if text else []
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        piece = text[s:e].strip()
        if piece:
            out.append(piece)
    return out or ([text] if text else [])


def count_mismatches(problem_set: str) -> List[dict]:
    """각 블록 실행 후 블록 내부 수치가 같은 PROBLEM 의 Solution key 값과
    일치하는지. 반환: [{problem_idx, block_ok, computed, found_in_solution,
                       reason}]
    오류만 모으고자 할 땐 호출측에서 filter.
    """
    blocks = split_problem_blocks(problem_set)
    out = []
    for idx, blk in enumerate(blocks):
        code_res = [run_block(c) for c in extract_blocks(blk)]
        if not code_res:
            # 수치 문제인데 sympy 블록 없음 — 여기서는 '탐지 불가'(무해) 표기만,
            # 판사(LLM)와 결합해 missing_sympy 로 처리.
            out.append({"problem_idx": idx, "block_ok": False,
                        "reason": "no_sympy_block"})
            continue
        for br in code_res:
            if not br.ok:
                out.append({"problem_idx": idx, "block_ok": False,
                            "reason": br.error or "exec_error"})
                continue
            found = (br.value is not None
                     and solution_has_value(blk, br.value) )
            if not found and br.value is not None:
                vfmt = (int(br.value) if abs(br.value - round(br.value)) < 1e-9
                        else f"{br.value:.10g}")
                out.append({"problem_idx": idx, "block_ok": True,
                            "computed": vfmt, "found_in_solution": False,
                            "reason": (f"sympy computed {vfmt} but that value "
                                       "is absent from this problem's "
                                       "Solution key (possible hand-calc drift)")})
    return out
