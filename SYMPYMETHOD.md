이번에 발견된 **"python 펜스 우회"** 사건은 단순한 버그 수정으로 끝날 문제가 아닙니다.  
이건 **"모델이 만들어낸 코드를 신뢰할 수 있는가"** 에 대한 근본적인 설계 원칙을 다시 세워야 한다는 신호입니다.

지금까지의 접근은 이랬습니다:
- 모델이 코드를 제출한다.
- 우리가 그 코드를 실행한다.
- 실행 결과가 답안과 일치하면 통과.

하지만 이번 사건에서 드러난 것은 **"모델이 코드를 제출하지 않거나, 다른 형식으로 제출하면 검증을 우회할 수 있다"**는 것입니다.  
즉, "검증을 피해 가는 길"이 존재하면 모델은 그 길을 찾아갑니다.

---

## 최고의 전략: "실행 기반 검증(Execution-based Verification)을 유일한 통과 기준으로"

핵심 원칙은 단순합니다.

> **"계산이 필요한 문제는, 실행 가능한 코드를 제출하고, 그 코드의 출력이 답안과 정확히 일치하지 않으면 무조건 거부한다.  
> 코드가 없거나, 실행 오류가 나거나, 결과가 답과 다르면 LLM 판사에게 묻지도 말고 거부한다."**

이 원칙을 시스템에 박아 넣으면, 모델이 `python` 펜스를 쓰든, `sympy` 펜스를 쓰든, 심지어 `text`로 위장하든 **"실행 가능한 코드 + 정확한 출력"** 이 없으면 통과할 수 없습니다.

---

## 구체적인 전략 4가지

### 1. "코드 블록 존재"가 아니라 "코드 블록 실행 성공"만 인정

현재는 `no_sympy_block`이 advisory로 빠져서 LLM 판사가 "그럴듯하다"고 판단하면 통과했습니다.  
이제는 **블록이 없으면 그 자체로 하드 거부**입니다. (개념 설명 문제 제외)

```python
# orchestrator gate (의사코드)
if problem_requires_computation:
    blocks = extract_all_code_blocks(problem)  # python, sympy 모두
    if not blocks:
        return REJECT("missing_code_block")  # advisory 아님, 무조건 거부
    for block in blocks:
        result = run_block(block)
        if not result.ok:
            return REJECT("execution_error")  # 무조건 거부
        if not result.value_in_solution:
            return REJECT("value_mismatch")   # 무조건 거부
```

> **"개념 설명 문제"** 는 계산이 필요 없으므로 코드 블록을 요구하지 않도록, 문제 생성 단계에서 `requires_computation` 라벨을 명시하게 합니다.

---

### 2. LLM 판사는 "보조"가 아니라 "집행"만

지금까지는 LLM 판사가 "코드가 실행될 것 같다"고 추측했습니다.  
이제는 **실행 결과를 판사에게 주입**하고, 판사는 그 결과와 답안을 비교하는 역할만 합니다.

```python
judge_input = {
    "question": question,
    "reference": reference,
    "candidate_answer": candidate_answer,
    "execution_results": [
        {"block_id": 1, "output": "32.0", "status": "success"},
        {"block_id": 2, "output": "1/(2*n**2)", "status": "success"},
    ]
}
```

판사는 더 이상 "코드가 맞는지" 추측하지 않습니다.  
**이미 실행된 결과**를 보고, "답안의 숫자가 32.0과 일치하는가"만 판단합니다.

---

### 3. 모델에게 "코드 없이 숫자를 쓰면 무조건 실패"라고 못 박기

문제 생성 프롬프트에 다음과 같은 규칙을 추가합니다.

```yaml
  problems: >
    ...
    ABSOLUTE RULES:
    ...
    10. MANDATORY EXECUTABLE VERIFICATION:
       a) ANY problem whose Solution key contains a numeric value, definite
          integral, derivative, root, limit, or algebraic result MUST include
          an executable code block (sympy or python) that computes that exact
          value.
       b) The code block MUST be complete, import all needed symbols, and
          assign the final result to a variable named `result`.
       c) The textual Solution key MUST state the value as `<<RESULT>>` and
          the system will replace it with the executed output.
       d) If you cannot provide such a code block, the problem is INVALID.
          Do not output it.
       e) If you write a number that did not come from an executed code block,
          the entire problem set will be REJECTED.
```

이제 모델은 **"코드 없이 숫자를 쓰는 행위"** 자체가 금지됩니다.

---

### 4. 회귀 테스트를 통한 지속적 방어

이번에 `python` 펜스 우회를 잡았지만, 다음에는 `text` 블록이나 다른 형식으로 우회할 수 있습니다.  
따라서 **실행 기반 검증을 우회하려는 모든 시도를 테스트 케이스로** 만들어야 합니다.

```python
def test_reject_python_fence_bypass():
    problem = """
    Solution key: The answer is 32.
    ```python
    from sympy import symbols, integrate, oo
    n = symbols('n')
    integrate(1/x**3, (x, n, oo))  # NameError
    ```
    """
    verdict = run_problems_gate(problem)
    assert verdict.ok == False
    assert "execution_error" in verdict.error_codes
```

이런 테스트를 계속 추가하면, 새로운 우회가 발견될 때마다 시스템이 스스로 방어할 수 있습니다.

---

## 결론: "실행 없이는 통과 없다"

이번 사건이 준 교훈은 분명합니다.

> **LLM은 말로는 그럴듯한 코드를 만들 수 있지만, 실제로 실행되지 않는 코드를 만들 수도 있다.  
> 따라서 "실행"이 유일한 검증 수단이 되어야 한다.**

최고의 전략은:

1. **코드 블록 없음 → 무조건 거부**
2. **코드 실행 오류 → 무조건 거부**
3. **코드 출력과 답안 불일치 → 무조건 거부**
4. **LLM 판사는 실행 결과를 받아 "일치 여부"만 확인**

이 4가지를 시스템의 핵심 원칙으로 삼으면, 모델이 어떤 방식으로든 "계산 안 하고 그럴듯한 숫자"를 쓰는 것을 원천 차단할 수 있습니다.  

원하시면, 이 전략을 반영한 `orchestrator.py`와 `judge.problems` 프롬프트의 구체적인 수정안을 바로 만들어 드리겠습니다.