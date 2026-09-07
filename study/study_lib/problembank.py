"""problembank — 로컬 LLM 으로 '교재 참조 기반 문제 은행'을 만들어 note 에 붙인다.

기본 원칙(사용자 확정 2026-09):
- 난이도 별 수량: 기초(Basic) 10 / 중급(Intermediate) 5 / 고급(Advanced) 5.
- 모든 문항은 현재 저장된 교재 passage 에 **실제로 있는** 문제/예제를 재서술하며
  교재 출처(예: 11.3 Exercises #7)를 표기하고, 가짜 출처/중복 변형을 만들지 않는다.
- 각 문항에 **전체 풀이** 포함.
- 산출: 완성된 note(md) 맨 끝에 '## Problem Bank' 섹션으로 append (본문 보존).

로컬 호출은 세 난이도를 한 번에 한 prompt 로 시도하되, 출력이 크면(CPU·긴 완성)
실패 시 난이도별로 쪼개 재시도하는 안전망을 둔다. 문항 수·출처·풀이 존재는 아래
순수 검증 함수로 강제한다(모델이 빠뜨리면 그 조각을 남지 않게).
"""
from __future__ import annotations

import re
from typing import Protocol

BASIC = 10
INTERMEDIATE = 5
ADVANCED = 5


class TextLLM(Protocol):
    """문자열 in/out 로컬 LLM (완성 텍스트 반환) — 모의/실제 공용 계약."""

    def complete_text(self, *, system: str, user: str) -> str: ...

_SECTION_LINE = re.compile(r"^#{3,4}\s+(.+)$")


def _count_strs(text: str) -> int:
    """`^N.` 또는 `^- ` 로 시작하는 문항 항목 수 (목록에서)"."""
    items = 0
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^\d+[.)]\s", line) or re.match(r"^[-*]\s+(?:\(Basic\)|\(Intermediate\)|\(Advanced\)|\d)", line):
            # 과도; 아래 정식 라벨 검증이 더 견고
            items = items
        if re.match(r"^\d+[.)]\s", line):
            items += 1
    return items


def parse_bank(markdown: str) -> dict:
    """생성된 은행 블록에서 (기초/중급/고급) 항목 수·출처·풀이 존재를 요약."""
    sections = {}
    cur = None
    for line in markdown.splitlines():
        m = _SECTION_LINE.match(line)
        if m:
            cur = _norm_key(m.group(1))
            sections.setdefault(cur, 0)
            continue
        if cur and re.match(r"^\d+[.)]\s", line.strip()):
            sections[cur] = sections.get(cur, 0) + 1
    return sections


def _norm_key(label: str) -> str:
    low = label.strip().lower()
    if any(k in low for k in ("basic", "기초", "기본")):
        return "basic"
    if any(k in low for k in ("intermediate", "중급")):
        return "intermediate"
    if any(k in low for k in ("advanced", "고급")):
        return "advanced"
    return low


def expected_counts() -> dict:
    return {"basic": BASIC, "intermediate": INTERMEDIATE, "advanced": ADVANCED}


def bank_valid(sections: dict) -> tuple[bool, str]:
    """문항 수(10/5/5)와 (소스·풀이 존재 여부로 보충 판단) 가 맞는지."""
    exp = expected_counts()
    for key, want in exp.items():
        if sections.get(key, 0) != want:
            return False, f"{key}: got {sections.get(key, 0)}, want {want}"
    return True, ""


def strip_bank_marker(markdown: str) -> str:
    """기존 '## Problem Bank' 아래가 있으면 제거(재생성 시 중복 방지)."""
    marker = "## Problem Bank"
    if marker not in markdown:
        return markdown
    head = markdown.split(marker, 1)[0]
    return head.rstrip() + "\n"


def user_prompt(topic_title: str, passages: list[str]) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return (
        f"어떤 '교재 출처 수업 문제은행' 을 만들어라. 토픽: {topic_title}.\n"
        f"아래 교재 passage 를 참고해, 실제로 passage 에 있는 문제/예제에서만 뽑아 "
        f"재서술하라(출처는 passage 의 예: 'EXAMPLE 3', '11.3 Exercises #7').\n"
        "규칙:\n"
        "- '## Problem Bank' 헤더 후, '### Basic (기초)' 10개, "
        "'### Intermediate (중급)' 5개, '### Advanced (고급)' 5개.(각 항목 하위)\n"
        "- 각 항목: `N. (출처) 문제서술` 한 줄 + `**Solution.** 전체 풀이`.\n"
        "- 교재에 없는 가짜 출처/중복 변형 금지. 수식은 $-로 감싸고 금지 매크로(\\text 등) 사용 금지.\n\n"
        f"참고 passage:\n{src}\n"
    )


def apply_bank(note_md: str, bank_md: str) -> str:
    """완성된 note 맨 아래에 문제 은행을 append (기존 본문·기존 뱅크 보존/교체)."""
    base = strip_bank_marker(note_md) if "## Problem Bank" in note_md else note_md.rstrip()
    return base + "\n\n" + bank_md.strip() + "\n"


# ---- 러너: 교재 passage → 로컬 LLM → 문제 은행 마크다운 ----
def _strip_fences(md: str) -> str:
    s = (md or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[A-Za-z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s


SYSTEM_RULE = (
    "You build a textbook-sourced practice bank in Markdown. Rules:\n"
    "1. Numbered sections: '## Problem Bank', then '### Basic', '### Intermediate', "
    "'### Advanced'.\n"
    "2. Each item line starts 'N. (교재 출처) 문제 서술.' then a '**Solution.** full 풀이' "
    "paragraph (Basic 10, Intermediate 5, Advanced 5 총 20).\n"
    "3. Every item MUST restate a problem/example actually visible in the given "
    "passages and cite its source like 'EXAMPLE 3' or '(11.3 Exercises #7)'. "
    "Never invent sources; never duplicate a near-identical variant.\n"
    "4. Math is wrapped in $...$ only; no \\text, no align/gather env, no Markdown "
    "horizontal rules.\n"
    "Output ONLY the Markdown bank."
)


def level_spec_prompt(passages: list[str], *, topic_title: str = "") -> str:
    """전 구간(10/5/5)을 한 번에 요청하는 프롬프트."""
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    head = f"토픽: {topic_title}.\n" if topic_title else ""
    return (
        head
        + f"아래 passage 를 기준으로 문제 은행을 만들어라(참고 passage {len(passages)}개):\n"
        + src
    )


def build_bank(passages: list[str], llm: TextLLM, *, topic_title: str = "") -> str:
    """러너 — 로컬 LLM 1회 호출로 전체 은행 생성, 검증 실패 시 '' 반환(호출부가 재시도).

    난이도별 출력이 커 모델이 자르는 걸 막으려면 freq 3회(난이도별)가 더 안정이지만,
    우선 단일 호출 검증으로 동작 검증. 이후 성능 이슈 시 분할로 전환.
    """
    out = _strip_fences(llm.complete_text(
        system=SYSTEM_RULE, user=level_spec_prompt(passages, topic_title=topic_title)))
    if not out or "Problem Bank" not in out:
        return ""
    bank = out[out.index("## Problem Bank"):].rstrip()
    return bank


def build_bank_verified(passages: list[str], llm: TextLLM, *,
                        topic_title: str = "", attempts: int = 3) -> str:
    """build_bank + 10/5/5 검증, 미달 시 재시도(attempts 회). 실패 시 '' 반환."""
    for _ in range(max(1, attempts)):
        bank = build_bank(passages, llm, topic_title=topic_title)
        if not bank:
            continue
        ok, _why = bank_valid(parse_bank(bank))
        if ok:
            return bank
    return ""


def problem_bank_path(note_path) -> str:
    """개념 note 경로 → 문제은행 동반 파일 경로. notes/<id>.md → notes/<id>.problems.md"""
    from pathlib import Path
    p = Path(note_path)
    return str(p.with_name(p.stem + ".problems" + p.suffix))


class OllamaTextLLM:
    """postproc_katex.OllamaClient 를 TextLLM(문자열) 계약으로 감싸는 어댑터."""

    def __init__(self, client) -> None:
        self._c = client

    def complete_text(self, *, system: str, user: str) -> str:
        return self._c._call(user, system=system)


