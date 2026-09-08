"""symrun — "계산은 모델이 아니라 sympy"(SYMPYMETHOD) 실행 계층.

원칙(개정 — '실행 없이는 통과 없다'):
  - 문제 생성 에이전트가 숫자를 암산 금지; 각 수치 해답은 실행 가능한 코드
    블록(```sympy```/```python``` **둘 다**, 텍스트 위장 불가)이 계산해 내야 한다.
  - 이 모듈이 그 코드를 실제로 로컬 실행해,
       1) 코드 없음(계산 문제)        -> missing_code_block  (하드 거부)
       2) 실행 오류/시간초과/금지토큰  -> execution_error     (하드 거부)
       3) 실행 수치와 Solution key 불일치 -> value_mismatch   (하드 거부)
      를 LLM 판사 여부와 무관하게 결정론으로 낸다(run_gate).
  - 순수 개념 문제(계산 결과를 요구하지 않는 문제)만 코드 블록을 면제한다
    (함수가 *반환*하는 숫자 리터럴은 요구 안 함 — 오탐 방지).
  - 수용 시, Solution key 의 <<RESULT>> 자리에 실제 실행 값을 채워(시스템이 직접
    계산) 사용자에게 확정 숫자만 보여준다(substitute_results).

안전한 실행:
  - 시간 제한(timeout), 축소 내장, 오로지 sympy(+print) 허용 전역으로 exec.
    LLM 이 만든 임의 코드 실행 위험을 최소화(운영: Docker 샌드박스 권장).
sympy 미설치: run 은 unavailable → 호출 측이 no_sympy 로 처리(수용 불가 방향).
"""
from __future__ import annotations

import math
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
    r"(?<![\w.-])(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\w.])")
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


def extract_code_blocks(text: str) -> List[str]:
    """검증 대상 코드 블록: ```sympy``` 와 ```python``` **둘 다** 뽑는다.

    출제 모델이 종종 ```sympy``` 대신 ```python``` 펜스로 계산 블록을 내므로,
    검증 게이트(손계산 드리프트 탐지)는 python 펜스를 외면해 broken 코드/가짜
    수치가 통과하는 우회를 막기 위해 두 펜스를 모두 실행 후보로 본다. (그렇지
    않으면 'python 펜스로 써서 실행 안 됨' → no_sympy_block advisory → LLM 판사
    가 코드를 실제 실행 없이 통과시켜버림.)"""
    return [m.group(1).strip() for m in _FENCE_BOTH.finditer(text or "")]


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
    """문제셋 본문 전체에서 모든 코드 블록(```sympy```/```python```)을 실행해
    리스트로 반환."""
    return [run_block(c) for c in extract_code_blocks(text)]


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
        code_res = [run_block(c) for c in extract_code_blocks(blk)]
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


# ---------------------------------------------------------------------------
# SYMPYMETHOD 개정판 — "실행 없이는 통과 없다" (python/sympy 펜스, text 위장 모두 차단)
# ---------------------------------------------------------------------------
# doctrine:
#   1) 계산이 필요한 문제(수치 답 존재/코드 존재/<<RESULT>> 사용)에 코드가 없으면
#      advisory 가 아닌 **hard 거부 (missing_code_block)**.
#   2) 실행 오류(execution_error), 코드 결과와 답 불일치(value_mismatch) → hard 거부.
#   3) 순수 개념 문제(숫자·코드·플레이스홀더 전부 없음)만 코드 요구를 면제.
#   4) LLM 판사에게는 "실행 결과"를 구조적으로 주입(추측 방지).
_PLACEHOLDER = re.compile(r"<<RESULT>>|<<result>>")
# '답으로 주장된 수치' 휴리스틱(발주 취지 — 자유 텍스트 숫자 전부가 답은 아님:
#  지수/라벨 같은 부수 숫자는 제외하고, 등호/≈/boxed/answer 뒤의 값만 답으로 본다.)
_ANSWER_LITERAL = re.compile(
    r"(?:=\s*|≈\s*|answer\s*(?:is|:)?\s*|results?\s*:|boxed\{|so\s+)"
    r"(\d+(?:\.\d+)?(?:e[+-]?\d+)?|<<RESULT>>)", re.IGNORECASE)
# 목표 수치를 요구하는 '계산 명령' 어휘 (find/compute/... for a number)
_TARGET_IMPERATIVE = re.compile(
    r"\b(find|compute|evaluate|determine|calculate|solve for|which n|smallest|"
    r"least|how many terms|how many|first \d+\s*terms|find the (?:sum|value|limit|"
    r"root|integral|partial sum)|remainder\s*[<≤]|error\s*[<≤]|"
    r"within|accurate to|to three decimals)\b", re.IGNORECASE)
_digits = re.compile(r"\d")


# --- COUNTER-CONTRADICTION: 논리/수치 일관성 보조 (결정론) --------------------
# 하드 의존성 없이(순수 float) 판단 가능한 '수학 모순'을 잡는 primitives.
# 고급(조건 충족 가능성/SMT)은 LLM 판사(mat. consistency 축)와 선택적 sympy
# 솔버 계층에 위임하되, 실행 출력의 명백한 무효(invalid/EmptySet/non-finite/
# 답-자리 부정 등)는 여기서 즉시 하드 거부한다.


def _isfinite(v: Optional[float]) -> bool:
    """float 가 유한한 값인지(NaN/Inf 제외)."""
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _category(question: str, solution: str) -> str:
    """문제 맥락 분류(가벼움): index/count 답을 요구하는지 vs 참/부등식 결정."""
    t = f"{question or ''} {solution or ''}".lower()
    if re.search(r"\b(find|smallest|least|how many|which n|terms|fewest|"
                 r"index|first .* terms)\b", t):
        return "index"
    if re.search(r"\b(is .* (larger|smaller)|compare|which|true or false)\b", t):
        return "bool"
    return "value"



@dataclass
class BlockGate:
    """한 실행 코드 블록의 실행 결과."""
    ok: bool
    value: Optional[float] = None
    error: Optional[str] = None
    code: str = ""


@dataclass
class ProblemGate:
    """한 문제의 실행-기반 검증 요약."""
    idx: int
    piece: str = ""                       # 문제 단위 원문
    requires: bool = False                # 계산 필요(코드/수치/플레이스홀더)
    has_code: bool = False
    blocks: List[BlockGate] = field(default_factory=list)
    hard: Optional[str] = None            # 결정론 거부 사유(없으면 None)
    hard_code: Optional[str] = None       # execution_error|value_mismatch|missing_code
    placeholder_value: Optional[float] = None

    @property
    def block_text(self) -> str:
        return "\n".join(b.code for b in self.blocks)


def _has_declared_final(blk: str) -> bool:
    """문제가 '계산 결과'를 요구하는지(→ 실행 코드 블록 필수인지) 결정론 판정.

    판정 = 아래 중 하나라도 참이면 그 문제는 계산/수치 결과를 요구:
      - <<RESULT>> 플레이스홀더 사용,
      - 질문이 목표 수치를 요구하는 도치 명령(find n / error < eps / fewest terms …)
        을 숫자와 함께 사용.
    주의: 자유 텍스트의 '=1' 류(예: Σ_{n=1} 하한, 정의의 등식)는 '답'이 아니라
    부수 표기라 답으로 취급하지 않는다(전역 '= number' 휴리스틱은 여기 사용 안 함
    → 오탐 방지). 실제 '답으로서의 수치'는 프롬프트가 <<RESULT>> 또는 경우에 따라
    코드 출력 에코(기존 solution_has_value)로 강제한다.
    """
    if _PLACEHOLDER.search(blk):
        return True
    return bool(_TARGET_IMPERATIVE.search(blk) and _digits.search(blk))


def run_gate(problem_set: str) -> List[ProblemGate]:
    """도크트린 실행 게이트 — 문제 단위로 코드 실행 + 거부 판정을 결정론으로 낸다.

    반환의 각 ProblemGate.hard/hard_code 가 None 이면 그 문제는 '실행 기반으로는
    통과'로 간주(개념 문제 포함). orchestrator 는 이를 모아 재시도/거부를 결정한다.
    """
    blocks = split_problem_blocks(problem_set or "")
    out: List[ProblemGate] = []
    for idx, piece in enumerate(blocks):
        code_list = extract_code_blocks(piece)
        executed = [run_block(c) for c in code_list]
        gates = [BlockGate(ok=r.ok, value=r.value, error=r.error, code=c)
                 for r, c in zip(executed, code_list)]
        has_ph = bool(_PLACEHOLDER.search(piece))
        needs = bool(code_list) or has_ph or _has_declared_final(piece)
        g = ProblemGate(idx=idx, piece=piece, requires=needs,
                        has_code=bool(code_list), blocks=gates)

        if not needs:
            # 순수 개념: 코드 불요 → 실행 기준 통과.
            out.append(g)
            continue
        if not code_list:
            g.hard = ("missing_code_block: problem needs a numeric/algebraic "
                      "answer yet supplies no executable sympy/python block")
            g.hard_code = "missing_code_block"
            out.append(g)
            continue
        # 코드가 있음 → 각각 실행 결과 검증
        ok_runs = [b for b in gates if b.ok]
        for b in gates:
            if not b.ok:
                g.hard = (f"execution_error: an executable block failed "
                          f"({b.error}). No un-executed number may stand.")
                g.hard_code = "execution_error"
                break
        if g.hard:
            out.append(g)
            continue
        # 실행 성공. 두 모드로 나뉜다:
        #  A) <<RESULT>> placeholder 가 있으면 → 그 값을 시스템이 채우므로, 답 텍스트에
        #     수치를 되뇌이라는 에코 검증은 하지 않는다(placeholder 자체가 합법 자리).
        #     다만 실행이 실제 수치를 냈는지는 필수.
        #  B) placeholder 없이 저자가 숫자를 직접 적었다면 → 그 숫자가 실행 출력과
        #     일치해야 한다(수동 계산 drift 차단 = value_mismatch).
        if has_ph:
            vals = [b.value for b in ok_runs if b.value is not None]
            finite_vals = [v for v in vals if _isfinite(v)]
            if not finite_vals:
                g.hard = ("execution_error: <<RESULT>> used but no numeric value "
                          "was produced by the code block")
                g.hard_code = "execution_error"
            elif (_category(piece, "") == "index"
                  and not any(v > 0 for v in finite_vals)):
                g.hard = ("inconsistent_result: <<RESULT>> problem asks for an "
                          "index/count n, but block yields only non-positive values")
                g.hard_code = "inconsistent_result"
            else:
                g.placeholder_value = finite_vals[-1]
            out.append(g)
            continue
        # B 경로(숫자를 직접 적음): 코드가 실제 '수치'를 하나라도 내는지 확인.
        # 실행은 '성공'했는데 print 가 기호/EmptySet/정의되지 않은 값을 내면 value=None
        # → 그 문제는 손으로 답을 달 뿐 코드로 검증되지 않았다. (COUNTER-CONTRADICTION:
        #    '해 없음/미정의' 도 확정 수치 기만이므로 하드 거부)
        produced = [b.value for b in ok_runs if b.value is not None]
        finite = [v for v in produced if _isfinite(v)]
        if not finite:
            g.hard = ("inconsistent_result: executed block produced no finite "
                      "numeric value (undefined/EmptySet/empty solve) yet a "
                      "definite numeric answer is asserted")
            g.hard_code = "inconsistent_result"
            out.append(g)
            continue
        # COUNTER-CONTRADICTION: '가장 작은 n/몇 개/찾아라' 류는 양의(종종 정수)
        # index 가 답이어야 한다. 코드가 전부 0 이하(또는 비양수)만 내면 문제·풀이
        # 전제(remainder 나감, 올바른 부등식)와 모순 → 하드 거부.
        if _category(piece, "") == "index" and not any(v > 0 for v in finite):
            g.hard = ("inconsistent_result: question asks for an index/count n, "
                      "but the block produces only non-positive numbers "
                      "(wrong inequality/remainder premises)")
            g.hard_code = "inconsistent_result"
            out.append(g)
            continue
        for b in ok_runs:
            if b.value is not None and not _isfinite(b.value):
                continue
            if b.value is not None and not solution_has_value(piece, b.value):
                vfmt = (int(b.value) if abs(b.value - round(b.value)) < 1e-9
                        else f"{b.value:.10g}")
                g.hard = (f"value_mismatch: executed code outputs {vfmt}, which "
                          "the Solution key does not echo (hand-calc drift / "
                          "unexecuted number)")
                g.hard_code = "value_mismatch"
                break
        out.append(g)
    return out


def substitute_results(problem_set: str) -> str:
    """출력용: 실행 로직이 계산한 값으로 각 문제의 ```<<RESULT>>``` 를 실제
    숫자로 치환해 사용자에게 보여준다 (실패/미실행 문제는 이 단계에서 이미 hard
    거부되었으므로, 여기 남는 placeholder 는 정상 실행 문제의 결과만 채운다)."""
    blocks = split_problem_blocks(problem_set or "")
    gates = run_gate(problem_set or "")
    rebuilt = []
    for gi, piece in enumerate(blocks):
        gv = gates[gi].placeholder_value if gi < len(gates) else None
        if gv is not None:
            v = (int(gv) if abs(gv - round(gv)) < 1e-9 else f"{gv:.10g}")
            piece = _PLACEHOLDER.sub(str(v), piece)
        rebuilt.append(piece)
    return "\n\n".join(rebuilt)
