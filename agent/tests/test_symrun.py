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
