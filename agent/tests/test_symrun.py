"""symrun(SYMPYMETHOD 실행 계층) 계약 테스트 — 결정론, 네트워크/LLM 없음."""
from __future__ import annotations

import pytest

from agent import symrun as S


def test_run_block_computes_integer_via_sympy():
    code = (
        "from sympy import *\n"
        "n = ceiling(sqrt(1/(2*Rational(1,2000))))\n"
        "print(n)"
    )
    r = S.run_block(code)
    assert r.ok is True
    assert r.value == 32
    assert r.raw_output == "32"


def test_run_block_extracts_assigned_result_variable():
    code = ("from sympy import *\n"
            "result = Rational(3,4) + Rational(1,2)\nprint(result)")
    r = S.run_block(code)
    assert r.ok
    assert abs(r.value - 1.25) < 1e-9


def test_run_block_forbids_escapes():
    for bad in ("import os", "import subprocess", "from os import system",
                "__import__('os')", "open('/etc/passwd')"):
        r = S.run_block(bad)
        assert r.ok is False, bad
        assert r.error


def test_run_block_syntax_error_reported():
    r = S.run_block("from sympy import *\n1/0")
    assert r.ok is False
    assert "error" in (r.error or "").lower() or "error" in str(r.error)


def test_extract_blocks_fences_only():
    text = ("before\n```sympy\nx=1\n```\nafter\n```python\ny=2\n```")
    assert S.extract_blocks(text) == ["x=1"]           # sympy_only 기본
    assert S.extract_blocks(text, sympy_only=False) == ["x=1", "y=2"]


def test_solution_has_value_integer_and_float():
    assert S.solution_has_value("so n = 32.", 32)
    assert S.solution_has_value("answer: 1/32", 32) is False   # 분모, 무시돼야
    assert S.solution_has_value("x ≈ 0.333", 1 / 3, tol=1e-3)
    assert not S.solution_has_value("so n = 45", 32)


def test_count_mismatches_clean():
    text = ("PROBLEM 1\nQuestion: find n so R_n < 0.0005\n"
            "Solution key: n=32\n```sympy\nfrom sympy import *\n"
            "print(ceiling(sqrt(1000)))\n```\nDifficulty: medium")
    assert S.count_mismatches(text) == []


def test_count_mismatches_drift_detected():
    text = ("PROBLEM 1\nQuestion: find n\nSolution key: n=45   # 손계산 오답\n"
            "```sympy\nfrom sympy import *\nprint(ceiling(sqrt(1000)))\n```")
    mm = S.count_mismatches(text)
    assert len(mm) == 1
    assert mm[0]["block_ok"] is True
    assert mm[0]["computed"] == 32
    assert mm[0]["found_in_solution"] is False
    assert "drift" in mm[0]["reason"] or "absent" in mm[0]["reason"]


def test_count_mismatches_no_block_and_broken_block():
    no_block = S.count_mismatches("PROBLEM 1\nQuestion: x\nSolution key: x=2")
    assert no_block and no_block[0]["reason"] == "no_sympy_block"

    broken = ("PROBLEM 1\nQuestion: find\nSolution key: 9\n"
              "```sympy\n1/0\n```")
    mm = S.count_mismatches(broken)
    assert mm and mm[0]["block_ok"] is False


def test_python_fence_block_is_executed_too():
    """모델이 ```python``` 펜스로 계산 블록을 낼 때도 실행해 broken 코드를 잡는다
    (```sympy``` 전용으로 뽑으면 'no_sympy_block' advisory 로 새어 LLM 판사가 통과시킴)."""
    live_model_output = (
        "PROBLEM 1 — [Remainder Estimate]\n"
        "Question: find smallest n so R_n of sum 1/n^3 < 0.0005\n"
        "Solution key: n=32\n"
        "```python\n"
        "from sympy import symbols, integrate, oo, Rational\n"
        "n = symbols('n', real=True, positive=True)\n"
        "integral_expr = integrate(1/x**3, (x, n, oo))   # x 미정의 -> NameError\n"
        "print(n)\n"
        "```\n"
        "Difficulty: medium")
    mm = S.count_mismatches(live_model_output)
    # 실행 시도했고(no_sympy_block 아님) 실행 오류 → block_ok False
    assert mm and mm[0]["block_ok"] is False
    assert mm[0]["reason"] != "no_sympy_block"
    assert "execution error" in mm[0]["reason"] or "forbidden" in mm[0]["reason"]


def test_python_fence_drift_detected():
    text = ("PROBLEM 1\nQuestion: compute n\nSolution key: n=45\n"
            "```python\nfrom sympy import *\n"
            "print(ceiling(sqrt(1000)))\n```")
    mm = S.count_mismatches(text)
    assert mm and mm[0]["block_ok"] is True
    assert mm[0]["computed"] == 32
    assert mm[0]["found_in_solution"] is False


def test_extract_code_blocks_covers_python_fence():
    text = "```sympy\nx=1\n``` then ```python\ny=2\n```"
    assert S.extract_code_blocks(text) == ["x=1", "y=2"]


# ---- run_gate(도크트린 "실행 없이는 통과 없다") 새 계약 ----
def _gate_of(text):
    return S.run_gate(text)[0]


def test_run_gate_concept_problem_needs_no_code():
    p = ("PROBLEM 1\nQuestion: state the comparison test hypothesis.\n"
         "Solution key: positive decreasing integrable => series behaves like integral.\n"
         "Difficulty: easy")
    g = _gate_of(p)
    assert g.requires is False and g.has_code is False and g.hard is None


def test_run_gate_concept_problem_with_casual_index_not_compute():
    # Σ_{n=1} 하한 같은 '부수 =숫자' 는 계산 답이 아니다 → 코드 불요
    p = ("PROBLEM 1\nQuestion: decide convergence of sum 1/sqrt(n).\n"
         "Solution key: by the p-test it diverges.\nDifficulty: easy")
    g = _gate_of(p)
    assert g.requires is False and g.hard is None


def test_run_gate_missing_code_block_is_hard():
    p = ("PROBLEM 1\nQuestion: find smallest n so R_n < 0.001.\n"
         "Solution key: n=23\nDifficulty: medium")
    g = _gate_of(p)
    assert g.requires is True and g.has_code is False
    assert g.hard_code == "missing_code_block"


def test_run_gate_exec_error_is_hard():
    p = ("PROBLEM 1\nQuestion: find n so error < 0.0005\nSolution key: n=32\n"
         "```python\nfrom sympy import *\n1/0\n```")
    g = _gate_of(p)
    assert g.requires and g.has_code
    assert g.hard_code == "execution_error"


def test_run_gate_value_mismatch_is_hard():
    p = ("PROBLEM 1\nQuestion: compute n\nSolution key: n=45\n"
         "```python\nfrom sympy import *\nprint(ceiling(sqrt(1000)))\n```")
    g = _gate_of(p)
    assert g.hard_code == "value_mismatch"


def test_run_gate_clean_ok():
    p = ("PROBLEM 1\nQuestion: find n so R_n < 0.0005\nSolution key: n=32\n"
         "```python\nfrom sympy import *\nprint(ceiling(sqrt(1000)))\n```")
    g = _gate_of(p)
    assert g.hard is None


def test_run_gate_placeholder_accepted_and_captured():
    p = ("PROBLEM 1\nQuestion: compute the value\n"
         "Solution key: final = <<RESULT>>\n"
         "```sympy\nfrom sympy import *\nprint(Rational(3,4))\n```")
    g = _gate_of(p)
    assert g.hard is None and abs(g.placeholder_value - 0.75) < 1e-9


def test_run_gate_placeholder_without_number_rejected():
    p = ("PROBLEM 1\nQuestion: compute the value\nSolution key: =<<RESULT>>\n"
         "```sympy\nfrom sympy import *\nprint(symbols('n'))\n```")
    g = _gate_of(p)
    assert g.hard_code == "execution_error"


def test_substitute_results_fills_placeholder():
    p = ("PROBLEM 1\nQuestion: value?\nSolution key: = <<RESULT>>\n"
         "```sympy\nfrom sympy import *\nprint(Rational(1,2))\n```")
    out = S.substitute_results(p)
    assert "<<RESULT>>" not in out
    # 정확히 실제 값(0.5)으로 채워짐 (Rational print 되면 유리수 표기가 쓰임)
    assert "final" in out or "0.5" in out


# ---- COUNTER-CONTRADICTION: 'runs but fails to give real finite indexed result' ----
def test_run_gate_rejects_run_without_numeric_value():
    """블록이 '실행 성공'했지만 Symbol/EmptySet 같은 수치 아닌 값을 내면
    손으로 답을 달은 것과 같으므로 inconsistent_result 하드 거부."""
    p = ("PROBLEM 1\nQuestion: Find smallest n so R_n < 0.0005\n"
         "Solution key: n=45\n"
         "```python\nfrom sympy import *\nn = symbols('n')\nprint(n)   # Symbol, no number\n```")
    g = S.run_gate(p)[0]
    assert g.hard_code == "inconsistent_result"


def test_run_gate_rejects_non_positive_index():
    """index/count 를 묻는데 출력이 ≤0 이면 전제 모순(remainder/부등식 오류)."""
    p = ("PROBLEM 1\nQuestion: Find the smallest n\nSolution key: n=-2\n"
         "```python\nfrom sympy import *\nprint(-2)\n```")
    g = S.run_gate(p)[0]
    assert g.hard_code == "inconsistent_result"


def test_run_gate_accepts_positive_value_non_index():
    """값 문제(부등식 결정 아님)에서 양의 유한 실수는 정상 통과."""
    p = ("PROBLEM 1\nQuestion: compute the sum value\nSolution key: sum=0.5\n"
         "```python\nfrom sympy import *\nprint(Rational(1,2))\n```")
    g = S.run_gate(p)[0]
    assert g.hard is None



