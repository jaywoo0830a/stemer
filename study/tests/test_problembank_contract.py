"""problembank 계약 — 교재 기반 문제 은행(10/5/5)의 순수 구성/검증 규칙.

사용자 명세(2026-09):
- Basic 10 / Intermediate 5 / Advanced 5, 각 항목 교재 출처 표기 + 전체 풀이.
- 만들어진 은행 블록을 완성 note 맨 끝에 append(본문 보존), 재생성 시 기존 뱅크 교체.
여기선 모의 LLM 마크다운으로 구조 규칙(개수/마커/append/보존)을 고정한다.
"""
from study_lib.problembank import (
    apply_bank,
    bank_valid,
    parse_bank,
    strip_bank_marker,
)

NOTE = "# T\n\nSome body.\n"

GOOD = """## Problem Bank

### Basic (기초)
1. (11.3 Exercises #7) Test series. 

**Solution.** Let f. 
2. (11.3 Exercises #9) p-series.

**Solution.** compare p.
10. (…)

### Intermediate (중급)
1. (…) a

**Solution.** …

### Advanced (고급)
1. (…)

**Solution.** …
"""


def _sample(section: str, n: int, marker: str) -> str:
    body = [f"### {marker}"]
    for i in range(1, n + 1):
        body.append(f"{i}. (11.3 Exercises #{i*3}) Problem {i}.\\n\\n**Solution.** 풀이 {i}.")
    return "\n".join(body)


def test_parse_bank_counts_sections():
    md = ("## Problem Bank\n\n### Basic\n1. (A) p\n\n**Solution.** s\n2. (B) q\n"
          "### Intermediate\n1. c\n### Advanced\n1. d\n")
    s = parse_bank(md)
    assert s["basic"] == 2 and s["intermediate"] == 1 and s["advanced"] == 1


def test_bank_valid_requires_10_5_5():
    ok, why = bank_valid({"basic": 10, "intermediate": 5, "advanced": 5})
    assert ok
    ok2, _ = bank_valid({"basic": 3, "intermediate": 5, "advanced": 5})
    assert not ok2


def test_apply_bank_appends_after_body():
    out = apply_bank(NOTE, "# section content")
    assert "# T\n\nSome body.\n\n# section content\n" == out


def test_apply_bank_replaces_existing_bank():
    first = apply_bank(NOTE, "OLD BANK")
    replaced = first.replace("OLD BANK", "# New\nonly")
    assert "# New" in replaced


def test_strip_bank_marker_removes_tail():
    md = "# body\n\n## Problem Bank\ntail stuff\n"
    assert "tail stuff" not in strip_bank_marker(md)
    assert strip_bank_marker(md).strip() == "# body"


# ---- 러너 ----
from study_lib.problembank import build_bank


class _Fake:
    def __init__(self, out):
        self.out = out

    def complete_text(self, *, system, user):
        return self.out


BANK = """## Problem Bank

### Basic
1. (11.3 Exercises #7) a

**Solution.** x
2. (…) b

**Solution.** y
### Intermediate
1. (…) c

**Solution.** z
### Advanced
1. (…) d

**Solution.** w
"""


def test_build_bank_returns_bank_only_from_response():
    out = build_bank(["passage A"], _Fake("intro text\n\n" + BANK))
    assert out.startswith("## Problem Bank")
    assert "intro text" not in out
    assert "### Basic" in out


def test_build_bank_empty_when_no_bank_marker_or_blank():
    assert build_bank([], _Fake("nothing here")) == ""
    assert build_bank([], _Fake("")) == ""

def test_build_bank_strips_fences():
    wrapped = "```markdown\n" + BANK + "\n```"
    assert build_bank(["p"], _Fake(wrapped)).rstrip() == BANK.rstrip()


def _mk_full_bank() -> str:
    rows = ["## Problem Bank"]
    for marker, n in (("### Basic", 10), ("### Intermediate", 5), ("### Advanced", 5)):
        rows.append(marker)
        for i in range(1, n + 1):
            rows.append(f"{i}. (11.3 Exercises #{i}) Q{i}.\n\n**Solution.** S{i}.")
    return "\n".join(rows)


def test_build_bank_verified_accepts_full_bank():
    from study_lib.problembank import build_bank_verified
    full = _mk_full_bank()
    assert build_bank_verified(["p"], _Fake(full)) == full.rstrip()


def test_build_bank_verified_rejects_and_retries():
    from study_lib.problembank import build_bank_verified
    # 개수가 안 맞는 BANK 를 계속 주면 attempts 내에도 실패 → ""
    assert build_bank_verified(["p"], _Fake(BANK), attempts=2) == ""


def test_problem_bank_path_derives_sibling_file():
    from study_lib.problembank import problem_bank_path
    assert problem_bank_path("notes/미적분-11-3.md") == "notes/미적분-11-3.problems.md"

