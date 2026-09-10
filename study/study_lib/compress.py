"""compress — Docling(RAG store)에 누적된 공부 범위를 LLM 이 한 편의 공부 교재로 압축.

접근(사용자 확정, 단순화):
    store에 누적된 챕터/범위 청크의 원문 md 를 취합한 다음,
    Gemini 플래시 등 원격 LLM(OpenRouter) 호출로 "챕터를 통째로 공부 교재"
    마크다운을 만든다 → notes/<book>-<scope>.study.md 로 저장.

- 입력: 공부 범위의 교재 passage(청크 텍스트, store 에서 조회).
- 출력: 정의/원리/공식/예제/연습·풀이 구조의 한 편 markdown (YAML front matter 포함).
- 스키마 하드 검증 없음 — generate_free 와 같은 접근(LLMClient.complete json_object=False).
- 범위가 컨텍스트 예산을 넘으면 split_into_batches 배칭으로 자동 저하한다
  (기본: 통째로 한 번에).

책임 경계: 이 모듈은 순수 프롬프트/배칭/병합 규칙. 실제 네트워크는 주입된 LLM.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from .llm import LLMClient

_FRONT = (
    "---\n"
    "title: {title}\n"
    "book: {book}\n"
    "scope: {scope}\n"
    "generator: compress\n"
    "---\n\n"
)

SYSTEM_PROMPT = (
    "You are an expert STEM tutor. Condense the given textbook chapter/range into ONE "
    "self-contained study handout in Markdown.\n"
    "The handout must actually TEACH: a reader should be able to redo every step without "
    "opening the book.\n\n"
    "STRUCTURE (in this order) — sections headed with ## or ###:\n"
    "1. ## Key Concepts — definitions, intuition, why each result holds.\n"
    "2. ## Formulas & Theorems — a compact list, each with its precise statement and "
    "the condition under which it applies.\n"
    "3. ## Worked examples — at least 3, increasing difficulty, each with a FULL "
    "step-by-step solution headed **Solution.** (explain every algebraic/calculus move, "
    "and cite the source like (EXAMPLE 3) or (11.3 Exercises #7)).\n"
    "4. ## Practice problems — at least 5, ordered by rising difficulty, each with a "
    "fully worked **Solution.** right below it (built-in answer key).\n"
    "5. ## Summary — one short 'what to remember' box.\n\n"
    "QUALITY RULES:\n"
    "- Be mathematically correct. Never state a false step; if a step relies on a "
    "theorem, state it explicitly.\n"
    "- Ground every claim and example in the given textbook passage. Do NOT invent "
    "example numbers or problems unrelated to the passage. Reuse the book's wording so "
    "page numbers/symbols line up.\n"
    "- Markdown only. Wrap every math expression in $...$ (display: $$...$$). Never "
    "leave bare math outside $.\n"
    "- Output ONLY the Markdown handout body (no YAML front matter, no code fence). "
    "Do not truncate — finish every solution."
)

_DEFAULT_INPUT_TOKENS = 120_000   # 기본 1회 입력 예산 (Gemini flash 상당 컨텍스트)
_DEFAULT_OUTPUT_TOKENS = 8_000    # 공부 교재 목표 토큰 (한 편 md)


def estimate_tokens(text: str) -> int:
    """한글 혼합 3자당 ~1토큰 문자 근사 (토크나이저 없을 때)."""
    return max(1, (len(text) + 2) // 3)


def strip_fences(md: str) -> str:
    """```markdown ... ``` 펜스 제거 후 앞뒤 공백 정리 (모델이 종종 감쌈)."""
    s = (md or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[A-Za-z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def build_front_matter(*, title: str, book: str, scope: str) -> str:
    """공부 교재의 YAML front matter 블록."""
    return _FRONT.format(title=title, book=book, scope=scope)


def assemble_passages(passages: Sequence[str], *, section_label: str = "") -> str:
    """passage(청크) 목록 → 근거 원문 md, 각 조각에 `[n]` 표기를 붙인다."""
    parts: list[str] = []
    if section_label:
        parts.append(f"## {section_label}")
    for i, p in enumerate(passages, 1):
        text = getattr(p, "text", p) if not isinstance(p, str) else p
        parts.append(f"[{i}] {text}".strip())
    return "\n\n".join(p for p in parts if p)


def build_user(content: str, *, title: str, book: str, scope: str) -> str:
    return (
        f"TITLE: {title}\n"
        f"BOOK: {book}   SCOPE: {scope}\n\n"
        f"Textbook source passages (numbered, consult as needed):\n"
        f"{content}\n\n"
        f"Write this chapter's complete study handout as ONE Markdown document now "
        f"(body only, no YAML/code fence): Key Concepts, Formulas & Theorems, Worked "
        f"examples (>=3, full solutions), Practice problems (>=5, full solutions), "
        f"Summary. Do not truncate — finish every solution."
    )


def split_into_batches(passages: Sequence[str], max_tokens: int,
                       *, count_tokens=None) -> list[list[str]]:
    """passages 를 토큰 예산 내 순차 배치로 자른다 (단일 청크가 크면 강제 단독)."""
    ct = count_tokens or estimate_tokens
    batches: list[list[str]] = []
    cur: list[str] = []
    used = 0
    for p in passages:
        n = ct(p)
        if cur and used + n > max_tokens:
            batches.append(cur)
            cur, used = [], 0
        cur.append(p)
        used += n
    if cur:
        batches.append(cur)
    return [b for b in batches if b]


def compress_body(passages: Sequence[str] | str, llm: LLMClient, *,
                  title: str, book: str = "", scope: str = "",
                  in_budget: int | None = None, out_tokens: int = _DEFAULT_OUTPUT_TOKENS,
                  section_label: str = "") -> str:
    """passage 를 LLM 으로 공부 교재 본문 md 로 압축해 반환 (front matter 제외).

    passages 가 예산을 넘으면 split_into_batches 배칭, 각 배치를 이어붙여 한 편으로.
    LLM 은 LLMClient.complete(json_object=False, content는 md 문자열).
    """
    if isinstance(passages, str):
        passages = [passages]
    budget = in_budget or _DEFAULT_INPUT_TOKENS
    ct = getattr(llm, "count_tokens", None) or estimate_tokens
    batches = split_into_batches(list(passages), budget, count_tokens=ct)
    out_parts: list[str] = []
    total = len(batches)
    for bi, batch in enumerate(batches, 1):
        content = assemble_passages(batch, section_label=section_label)
        if total == 1:
            sys_p = SYSTEM_PROMPT
            user = build_user(content, title=title, book=book, scope=scope)
        else:
            sys_p = SYSTEM_PROMPT.replace(
                "into ONE self-contained study handout in Markdown.",
                "into the handout you already started (this is a continuation batch).")
            user = (f"Continuing chapter '{title}' (batch {bi}/{total}); "
                    f"same BOOK/SCOPE: {book} / {scope}.\n"
                    f"Below are the NEXT source passages. Extend the handout with their "
                    f"concepts/examples/practice — same headings, each with full "
                    f"solutions. Body only.\n\n{content}")
        res = llm.complete(system=sys_p, user=user, max_tokens=out_tokens,
                           json_object=False)
        raw = res.content if isinstance(res.content, str) else str(res.content)
        out_parts.append(strip_fences(raw))
    return "\n\n".join(p for p in out_parts if p)


def _slug(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "-", s).strip("-")
    return (s or "range")


def compress_chapter(
    passages: Sequence[str] | str, llm: LLMClient, *,
    title: str, book: str = "", scope: str = "",
    notes_dir: str | Path = "notes", in_budget: int | None = None,
    out_tokens: int = _DEFAULT_OUTPUT_TOKENS, section_label: str = "") -> str:
    """진입점 — passage → 공부 교재 .md 저장, 저장 경로 반환."""
    body = compress_body(passages, llm, title=title, book=book, scope=scope,
                         in_budget=in_budget, out_tokens=out_tokens,
                         section_label=section_label)
    nd = Path(notes_dir)
    nd.mkdir(parents=True, exist_ok=True)
    fname = f"{_slug(book or 'book')}-{_slug(scope or 'range')}.study.md"
    path = nd / fname
    path.write_text(build_front_matter(title=title, book=book, scope=scope)
                    + body + "\n", encoding="utf-8")
    return str(path)