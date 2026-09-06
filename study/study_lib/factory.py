"""factory — 토픽 1건 생성 오케스트레이션.

클라이언트 관점:
    res = generate_one(topic, library=lib, store=store, embedder=emb,
                       llm=flash, schema=schema, guide=SUBJECT_GUIDE,
                       notes_dir="notes")
    res.status == "draft"   # → notes/<topic_id>.md 저장, registry draft 전이

흐름(GEN-PROTOCOL §6):
retrieve → 프롬프트 조립(system = 캐시 고정부: 지침+가이드+스키마/예산) →
llm(json_object) → protocol 검증 → 실패 시 **슬롯 patch 재요청(≤ max_patch)** →
render → figures(교재 원본만) → 파일 저장 → registry `todo→draft`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .figures import attach_figures
from .lint import lint_katex
from .llm import LLMClient, Usage
from .protocol import Schema, validate
from .registry import DRAFT, Library
from .render import render_guide
from .retrieve import retrieve

SYSTEM_TEMPLATE = (
    "You generate concise textbook study-guide content for one topic.\n"
    "Respond with ONLY a JSON object conforming to the payload schema below.\n"
    "Keep every value within its token budget; be precise, not verbose.\n"
    "\n"
    "MATH RULE (hard requirement):\n"
    "1. The Formula field `f` and any Key equation/value with `=` must be wrapped "
    "$...$:  f: \"$y'+p(x)y=q(x)$\".\n"
    "2. In prose fields (Definition d, Intuition k, Mistake m, problem p, "
    "solution s, recipe r), put $ around each math phrase but NOT around the "
    "words:  d: \"A linear ODE is $y'+p(x)y=q(x)$ where p,q are continuous.\"\n"
    "3. Inside $, write math tersely and prefer ASCII/Unicode: "
    "$y'=f(x,y)$, $\\frac{dy}{dx}$, $e^{2x}$, $\\int_0^1 x\\,dx$, $x^2$, $y''+4y=0$. "
    "($\\frac, \\sqrt, \\int, \\sum, \\lim, \\cdot, \\to, \\infty, \\left(\\right)$ "
    "are the only commands allowed.)\n"
    "4. NEVER join prose words with $, never place a whole sentence inside $, and "
    "never use align/equation/gather/cases/matrix or \\bm, \\mathds, \\text, \\tag.\n"
    "\n"
    "Correct f values:\n"
    "  f: \"$y'-3y=0$\"\n"
    "  f: \"$g(y)\\,dy = \\,f(x)\\,dx$\"\n"
    "Incorrect f values:\n"
    "  f: \"y'-3y=0\"            (missing $)\n"
    "  f: \"$y'-3y=0$ and ...\"  (prose in f)\n"
)


@dataclass(frozen=True)
class GenerateResult:
    topic_id: str
    status: str                 # "draft" | "failed"
    note_path: str | None = None
    payload: dict | None = None
    attempts: int = 0           # 총 llm 호출 수 (1 + patch 재시도)
    retries: int = 0            # 실제 patch 재요청 횟수
    usage: Usage = field(default_factory=Usage)
    issues: tuple[str, ...] = ()
    lint_warnings: tuple[str, ...] = ()


def _describe_kind(schema: Schema, kind: str) -> str:
    slots = schema.kinds[kind].slots
    parts = []
    for key, slot in slots.items():
        if slot.type == "array":
            if slot.fields:
                fields = ", ".join(f"{f.key}≤{f.budget}" for f in slot.fields)
                parts.append(f"{key}: list≤{slot.max} of {{ {fields} }}")
            else:
                parts.append(f"{key}: list≤{slot.max} of strings, each ≤{slot.budget} tokens")
        else:
            parts.append(f"{key}: string ≤{slot.budget} tokens")
    return "; ".join(parts)


def _describe_subject_keys(schema: Schema, subject: str) -> str:
    keys = schema.subject_keys.get(subject, ())
    return ", ".join(keys) if keys else "(none)"


def build_system(schema: Schema, subject: str, kind: str, guide: str) -> str:
    """캐시 고정부 — subject/kind 별 상수. (guide 는 과목 개념 가이드 B)."""
    return "\n".join([
        SYSTEM_TEMPLATE,
        guide,
        f"Payload schema ({kind}): {_describe_kind(schema, kind)}",
        f"Optional subject keys for {subject}: {_describe_subject_keys(schema, subject)}",
    ])


def build_user(topic: object, texts: list[str], *, patch: str = "") -> str:
    """가변부 — 토픽 질문 + grounding 텍스트 (+ patch 지시)."""
    lines = [
        f"Topic: {topic.title or topic.topic_id}",
        f"Book: {topic.book_id}",
        f"Subject: {topic.subject}   Kind: {topic.kind}",
        f"Section: {topic.section or '-'}",
        "",
        "Return the JSON payload for this topic now.",
    ]
    if texts:
        lines += ["", "Grounded textbook passages:"]
        lines += [f"[{i}] {t}" for i, t in enumerate(texts, 1)]
    if patch:
        lines += ["", "The previous attempt was rejected. Fix these issues and "
                      "return the FULL payload again:", patch]
    return "\n".join(lines)


def _budget_ceiling(schema: Schema, kind: str) -> int:
    slots = schema.kinds[kind].slots
    total = 0
    for slot in slots.values():
        if slot.type == "array":
            per_item = slot.budget or sum(f.budget for f in slot.fields) or 100
            total += per_item * (slot.max or 1)
        else:
            total += slot.budget or 100
    return total


def _add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        cache_hit_tokens=a.cache_hit_tokens + b.cache_hit_tokens,
        cache_miss_tokens=a.cache_miss_tokens + b.cache_miss_tokens,
    )


def generate_one(topic: object, *, library: Library, store: object,
                 embedder: object, llm: LLMClient, schema: Schema, guide: str,
                 figures=None, notes_dir: str | Path = "notes",
                 reranker=None, n_crossref: int = 5,
                 max_patch: int = 2, lint: bool = True,
                 max_tokens: int | None = None) -> GenerateResult:
    """클라이언트 진입점 — 토픽 1건을 생성해 draft 노트로 저장한다."""
    subject = topic.subject
    kind = topic.kind
    system = build_system(schema, subject, kind, guide)
    ctx = retrieve(topic, store=store, embedder=embedder, reranker=reranker,
                   n_crossref=n_crossref)
    texts = [c.text for c in ctx.chunks]
    if max_tokens is None:
        # 추론형 모델(deepseek-v4-*)은 reasoning_content 에도 토큰을 쓴다.
        # 예산 + 200 만으론 reasoning 이 예산을 다 써서 content 가 빈 채로
        # finish_reason=length 가 된다 → reasoning 여유를 넉넉히 (기본 8000).
        max_tokens = max(8000, _budget_ceiling(schema, kind) + 4000)

    usage = Usage()
    attempts = 0
    payload: dict | None = None
    report = None

    def attempt(user_text: str) -> dict:
        nonlocal attempts, usage
        attempts += 1
        result = llm.complete(system=system, user=user_text, max_tokens=max_tokens)
        usage = _add_usage(usage, result.usage)
        return result.content

    payload = attempt(build_user(topic, texts))
    report = validate(schema, payload, subject, kind)

    retries = 0
    while not report.ok and retries < max_patch:
        retries += 1
        patch_note = "; ".join(report.issues[:8])
        payload = attempt(build_user(topic, texts, patch=patch_note))
        report = validate(schema, payload, subject, kind)

    if not report.ok:
        return GenerateResult(topic_id=topic.topic_id, status="failed",
                              attempts=attempts, retries=retries, usage=usage,
                              issues=tuple(report.issues))

    markdown = render_guide(payload, title=topic.title or topic.topic_id,
                            subject=subject, kind=kind, section=topic.section,
                            book_id=topic.book_id)
    if figures is not None:
        figs = figures.figures_for(topic.book_id, topic.section or "")
        markdown = attach_figures(markdown, figs)

    warnings: tuple[str, ...] = ()
    if lint:
        warnings = tuple(f"L{issue.line}: {issue.message}"
                         for issue in lint_katex(markdown))

    path = Path(notes_dir) / f"{topic.topic_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")

    library.set_status(topic.topic_id, DRAFT, note_path=str(path))
    library.save()

    return GenerateResult(topic_id=topic.topic_id, status="draft",
                          note_path=str(path), payload=payload,
                          attempts=attempts, retries=retries, usage=usage,
                          lint_warnings=warnings)
