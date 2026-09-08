모델이 문제를 만들 때 **수를 직접 계산하다가 틀리는 문제**를 해결하려면, **"계산은 모델이 아니라 sympy가 한다"**는 원칙을 시스템에 강제로 박아 넣어야 합니다.  
즉, 문제 생성 에이전트가 답을 만들 때 **자신이 계산하지 않고 sympy 코드를 생성 → sympy가 실행 → 그 출력을 최종 답으로 사용**하는 파이프라인을 구축하는 것입니다.

---

## 핵심 아이디어: "계산은 도구가, 서술은 모델이"

1. **문제 생성 프롬프트**: 모델에게 "답을 계산하지 말고, 계산용 sympy 코드를 만들어라"고 지시.  
2. **실행 계층**: 생성된 sympy 코드를 로컬에서 실행하여 실제 수치/결과를 얻음.  
3. **조립**: 그 결과를 문제의 Solution key에 주입하거나, 모델이 결과를 참조하여 최종 답안을 작성.  
4. **판사**: 답안의 수치가 sympy 재계산 결과와 일치하는지 검증.

---

## 1. 문제 생성 프롬프트(`problems`)에 추가할 규칙

모델이 "손으로 계산한 값"을 답에 쓰지 못하게 하고, sympy 코드를 의무화합니다.

```yaml
  problems: >
    SCOPE: you are AUTHORING practice problems, not answering one. Role is
    PROBLEM-SETTER. You generate 2-4 DISTINCT, rigorously well-posed exercises
    derivable ONLY from the REFERENCE CONTEXT.

    ABSOLUTE RULES:
    ...
    [기존 규칙 유지]

    7. SYMPY-ONLY CALCULATION (Strict):
       a) For ANY Solution key that requires a numeric result, a definite
          integral, a root, a limit, a derivative evaluation, or solving a
          system, you MUST provide a complete, executable sympy snippet that
          computes the answer.
       b) You MUST NOT perform the calculation yourself in your head. You MUST
          NOT write a number that did not come from sympy. If you do, it is a
          critical error.
       c) The sympy code must be placed in a fenced block immediately after
          the Solution key, labeled exactly as:
          ```sympy
          from sympy import *
          ...
          ```
       d) The Solution key's final answer MUST be the output of that sympy code.
       e) If sympy cannot solve the problem, say 'sympy cannot compute this' and
          do NOT provide a guess or hand-derived number.

    [출력 구조에 sympy 코드 포함]
    5. STRUCTURE: For each problem, output EXACTLY:
         PROBLEM N — [concept tag]
           Question: <clear, self-contained text. LaTeX for math>
           Solution key: <deterministic steps matching source. Explicit final answer>
           Sympy verification: <executable sympy code that produces the final answer>
           Difficulty: easy|medium|hard
           Source anchor: <chapter/section/theorem used, verbatim or exact ref>
```

---

## 2. 오케스트레이터에 "sympy 실행 계층" 추가

파이프라인에서 문제 생성 후, `Sympy verification` 블록을 실제로 실행합니다.

```python
# agent/orchestrator.py (개념 코드)
import sympy as sp
import re
import json

def extract_sympy_code(problem_text: str) -> str:
    """문제 텍스트에서 ```sympy ... ``` 블록 추출"""
    match = re.search(r"```sympy\n(.*?)```", problem_text, re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()

def run_sympy(code: str):
    """sympy 코드를 안전하게 실행하고 결과를 문자열로 반환"""
    try:
        # sympy 실행은 신뢰할 수 있는 환경에서만 (샌드박스 권장)
        local_ns = {}
        exec(code, {"sympy": sp}, local_ns)
        # 결과는 보통 마지막 표현식 또는 변수에 저장됨
        # 사용자 코드에 따라 result 변수를 강제하도록 프롬프트에서 지정
        return local_ns.get("result", "No result variable found")
    except Exception as e:
        return f"Sympy execution failed: {e}"

def verify_problem_with_sympy(problem_text: str) -> bool:
    code = extract_sympy_code(problem_text)
    if code is None:
        return False  # sympy 코드가 없으면 실패
    output = run_sympy(code)
    # 모델이 제시한 답과 sympy output 비교 (문자열 또는 수치 비교)
    # 이 부분은 정규식으로 모델의 "Final answer:" 부분과 output을 비교
    return True  # 일치하면 통과
```

**프롬프트에서 강제할 사항**: sympy 코드는 반드시 `result = ...` 형태로 최종 결과를 `result` 변수에 저장하도록 지시해야 합니다. 그래야 실행 계층이 그 값을 가져올 수 있습니다.

---

## 3. 판사(`JUDGE_PROBLEMS`)에 sympy 검증 추가

판사는 이제 모델이 준 sympy 코드가 실제로 그 답을 내는지 확인합니다.  
또한 모델이 손으로 계산한 틀린 숫자를 적었을 때 잡아냅니다.

```python
JUDGE_PROBLEMS = _prompts.fetch_text("judge.problems", default=(
    "You are a STRICT PROBLEM SET QUALITY judge. You are given the REQUEST, "
    "the REFERENCE CONTEXT (trusted source), and a CANDIDATE problem set authored "
    "by another model.\n"
    "You must act as a deterministic gatekeeper. Source is absolute authority. "
    "Reject anything that is not perfectly grounded in the source or is "
    "internally ill-posed.\n\n"
    "Judge the set on the following axes. If ANY check fails, set ok=false.\n"
    "... [기존 1~6 규칙 유지] ...\n"
    "7) SYMPY VERIFICATION (Critical):\n"
    "   - Does every Solution key that involves a numeric result include an "
    "     executable sympy block? If not -> error_code: missing_sympy.\n"
    "   - The sympy block MUST be the source of the final numeric answer. "
    "     If the Solution key states a numeric value and that value did NOT come "
    "     from the sympy block (or differs from sympy output), classify it as "
    "     'hand_calculation_error' and reject.\n"
    "   - If sympy cannot compute the answer, the solution must say "
    "     'sympy cannot compute this'. A hand-derived guess is not acceptable.\n"
    "   - Re-run the sympy code mentally or note the expected structure: the "
    "     code must be complete and directly executable (import sympy, define "
    "     variables, compute result). Any syntax error or missing import is "
    "     a rejection.\n"
    "New numbers chosen by the author are ALLOWED only if sympy verifies them.\n"
    "Return ONLY a strict JSON object: "
    "{\"ok\": bool, \"grounded\": bool, \"errors\":[string], "
    "\"error_codes\":[string], \"exceptions\":[string], \"reason\":string}.\n"
    "error_codes MUST be one or more of: source_violation, meta_question, "
    "indefinite_solution, bound_collapse, logic_error, structural_missing, "
    "missing_sympy, hand_calculation_error, other. "
    "Set ok=false on ANY violation."
))
```

---

## 4. 전체 파이프라인 요약

```
[문제 생성 에이전트]
   ↓ (문제 초안 + sympy 코드 생성)
[심파이 실행 계층]  ← 실제 sympy로 계산
   ↓ (계산 결과 획득)
[문제 최종 조립]  (모델이 결과를 반영)
   ↓
[판사]  (sympy 코드 존재, 계산 일치, 소스 준수 검사)
   ↓
사용자에게 전달
```

이 구조가 되면, 모델이 **자기 머리로 암산한 잘못된 숫자를 답에 쓸 수 없습니다.**  
왜냐하면 답은 반드시 sympy 실행 결과와 일치해야 하기 때문입니다.

---

## 5. 주의할 점

- **sympy 실행 보안**: 생성된 코드를 그대로 `exec`로 실행하는 것은 위험할 수 있습니다.  
  개발 단계에서는 괜찮지만, 운영에서는 샌드박스(Docker 등)를 반드시 고려해야 합니다.
- **모델이 sympy 코드를 잘못 생성**할 수 있습니다. 이 경우 "sympy 실행 실패"가 되어 판사가 거부하게 됩니다.  
  이는 오히려 좋은 현상입니다. 잘못된 계산이 통과되지 않으니까요.
- **계산이 필요 없는 문제**(개념 설명 등)는 sympy를 요구하지 않도록 판사가 유연하게 판단해야 합니다.  
  프롬프트에서 "numeric result가 있을 때만"이라고 명시했으므로 괜찮습니다.

이렇게 하면 "수치 계산 오류"가 시스템에서 원천적으로 차단됩니다.