"""prompts — 역할별 시스템/유저 프롬프트 (NEW-METHOD §환각차단, 엄격판).

원칙(엄격):
1. **정확한 질문 수행** — 사용자가 물은 것(그대로)에만 답한다. 연관된 "다른" 문제로
   대체해 풀지 않는다. (예: “대칭 구간 적분 = 0 인 이유” ↔ “부정적분 공식” 구분.)
2. **근거 우선(grounding)** — REFERENCE CONTEXT 가 있으면 그것에 근거한다.
   - 컨텍스트가 비어 있거나 질문과 관련이 없으면, 일반 지식 유도임을 **명시**하고
     교재(문헌) 근거처럼 가장하지 않는다.
3. **추론은 보이게** — 답만 내놓지 말고 단계/조건을 밝힌다. 질문의 조건/클레임(예:
   “symmetric interval”, “=0”)을 누락하지 않는다.
4. **형식** — 마크다운 + LaTeX `$…$`. (코더는 ``` 코드 블록 필수)
5. **모르면 솔직히** — 지어내지 않는다.
"""
from __future__ import annotations

from typing import Optional, Sequence

from .rag import Chunk

# --------------------------------------------------------------------------- #
# 역할 정의 — 각 역할 경계
# --------------------------------------------------------------------------- #
ROLE_STYLE: dict[str, str] = {
    "worker": (
        "You are a precise explanatory worker for STEM study.\n"
        "Grounding: explain ONLY from REFERENCE CONTEXT when relevant; answer the "
        "exact question asked.\n"
        "Anti-drift: do NOT silently swap the question for a related formula or a "
        "different exercise. A conceptual 'why is it zero / why / when' must be "
        "answered as a concept — use formulas only to support it.\n"
        "Math in LaTeX ($…$ / $$…$$)."
    ),
    "coder": (
        "You are a code worker.\n"
        "Grounding: base every claim and patch on the shown symbols/files in "
        "REFERENCE CONTEXT. Never invent APIs, signatures, or paths absent from "
        "context; name any guess as a guess.\n"
        "Deliverable: (1) short root-cause, (2) minimal corrected code in ONE ``` "
        "fenced block."
    ),
    "reasoner": (
        "You are a rigorous reasoner.\n"
        "Derive step by step. Every step must be justified from either REFERENCE "
        "CONTEXT or universal axioms you label as such; mark unsupportable steps "
        "UNVERIFIED.\n"
        "Prove the exact claim in the question — if it asserts a specific fact "
        "(e.g. an integral over a symmetric interval equals 0), prove THAT fact "
        "under its conditions, not a generic nearby result."
    ),
}

# 공통 제약(모든 역할) — 질문 치환 방지 + 근거 정직성
_COMMON = (
    "ABSOLUTE RULES (must obey):\n"
    "1. Answer the EXACT question asked, and ONLY what it asks. Never replace it "
    "with a different but similar problem, never give a bare antiderivative when "
    "a symmetric-zero property is asked, and never volunteer a proof or extra "
    "topic unless the question explicitly asks to 'prove' / 'derive'.\n"
    "2. Treat REFERENCE CONTEXT as your primary source. When a reference chunk "
    "states an explicit theorem/formula/bound/NUMBER, REPRODUCE it VERBATIM "
    "(symbols, limits, conditions, and values exactly as printed). Do not "
    "paraphrase, simplify, re-derive, or write a different version of what the "
    "source states, and do not add material that is not shown in the source.\n"
    "3. NUMERIC/HAND-WORK: if the source shows a worked example whose result is "
    "a number (e.g. 'need 32 terms', '= 1/200'), and the question is about that "
    "kind of quantity, your answer must AGREE with the source's printed value. "
    "Redo every substitution/inequality carefully in steps; if your self-computed "
    "number differs from the source's printed one, recompute, and if it still "
    "differs, present the source value as authoritative and flag the discrepancy.\n"
    "4. When the context is absent or irrelevant, explicitly say '(general "
    "derivation — no study source)' — never claim textbook provenance you do not have.\n"
    "5. Do not invent citations, theorem/page numbers, data, bounds, or numbers.\n"
    "6. Show reasoning step by step; state uncertainty.\n"
    "7. Respond in markdown; math in LaTeX; concise but complete.\n"
)


def _scope_rule(action: str) -> str:
    """action 이 '증명/유도' 가 아니면 과잉 증명·확장을 금지(문제#1 회귀 가드)."""
    a = (action or "").lower()
    if a in ("proof", "derive", "deep"):
        # 증명 요청이면 그래도 source 밖으로 확장 금지 메시지만 강조
        return ("SCOPE: a proof/derivation is requested — still derive ONLY from "
                "what the source states; do not add external theorems as if from the source.")
    return ("SCOPE: this is an EXPLAIN/QUOTE/STATE task. Report the fact, theorem, "
            "bound, or worked value EXACTLY as the source presents it. Do NOT "
            "over-produce: do not append a proof, a general derivation, extra "
            "examples, or added cases that the source does not show and the "
            "question does not ask for. Keep the answer inside the question's scope.")


def system_prompt(role: str, task_id: int, n_context: int,
                  action: str = "", target: str = "") -> str:
    meta: list[str] = [f"ticket #{task_id}", f"assigned role: {role}"]
    if action:
        meta.append(f"action: {action}")
    if target:
        meta.append(f"target: {target}")
    head = "You are handling " + ", ".join(meta) + "."
    return (head + "\n\n" + ROLE_STYLE.get(role, ROLE_STYLE["worker"])
            + f"\n\nYou will see {n_context} reference chunk(s) below if any.\n\n"
            + _scope_rule(action) + "\n\n" + _COMMON)


def _sym_condition_note(question: str) -> str:
    """질문에서 지켜야 할 조건/클레임을 뽑아 재강조한다."""
    notes: list[str] = []
    low = question.lower()
    if "symmetr" in low:
        notes.append(
            "interval is SYMMETRIC about the origin — respect even/odd structure")
    if "zero" in low and ("integral" in low or "∫" in question
                          or "integrate" in low):
        notes.append(
            "the claim says the integral equals ZERO — you must explain/prove WHY "
            "it is zero (symmetry/orthogonality), not merely give an antiderivative")
    if "orthogon" in low:
        notes.append("intended concept is ORTHOGONALITY of basis functions")
    if "fourier" in low:
        notes.append("Fourier basis is the subject")
    return ("\nConditions/claims in the question you MUST honor and address:\n"
            + "\n".join(f"- {n}" for n in notes)) if notes else ""


def context_block(chunks: Sequence[Chunk], max_chars: int = 6000) -> str:
    """근거 청크 → 인용 블록. 비어 있으면 NO-SOURCE 정책 문구."""
    if not chunks:
        return ("SOURCE: (NO-SOURCE — no reference chunks were supplied; "
                "no study context has been loaded for this question)")
    parts: list[str] = []
    used = 0
    for c in chunks:
        snippet = c.text.strip()
        if used + len(snippet) > max_chars:
            break
        parts.append(f"SOURCE [{c.source}] ({c.section or 'sec?'}):\n{snippet}")
        used += len(snippet) + len(c.source) + len(c.section)
    return "\n\n---\n\n".join(parts)


def user_prompt(task_input: str, chunks: Sequence[Chunk],
                *, task_action: str = "", task_target: str = "") -> str:
    question = (task_input or "").strip()
    cond = _sym_condition_note(question)
    label = task_action or task_target or "question"
    return (
        f"YOUR TASK (label={label}): answer the question below EXACTLY as given.\n\n"
        f"QUESTION VERBATIM:\n{question}\n"
        f"{cond}\n\nREFERENCE CONTEXT:\n{context_block(chunks)}\n\n"
        "Begin by restating the exact question in one line, then answer it."
    )


# --------------------------------------------------------------------------- #
# parser(티켓화) — 엄격 스키마 강제. (planner.PlanParser.SCHEMA_HINT 를 대체)
# --------------------------------------------------------------------------- #
PARSER_SYSTEM = (
    "You convert a study/coding plan into strict worker tickets.\n"
    "Rules:\n"
    "- A user label like '[Task 1: explain]' is AUTHORITATIVE for role mapping "
    "(explain/summary/search→worker; code/fix/review→coder; proof/derive/deep→reasoner). "
    "Never upgrade 'explain' to 'proof' unless the body itself demands a proof.\n"
    "- Keep the FULL question text in 'input' verbatim — do not summarize it away.\n"
    "- Preserve explicit file/target exactly.\n"
    "- Split only on real task boundaries; do not invent tasks.\n"
    "- Return ONLY a strict JSON object — no prose, no code fence.\n"
    '  Schema: {"tasks":[{"id":int,"action":"explain|proof|code|fix|review|search|summary|deep",'
    '"target":string,"input":string,"role":"worker|coder|reasoner"}]}\n'
    '- If unsure, return {"tasks":[]}.'
)


def merge_results(results: Sequence[object]) -> str:
    """여러 WorkerResult 를 하나의 조직화된 마크다운으로 취합 (§5.2-5)."""
    lines: list[str] = []
    for r in results:  # object: has .task/.role/.output/.error
        head = getattr(r, "task", None)
        role = getattr(r, "role", "")
        out = getattr(r, "output", "")
        err = getattr(r, "error", None)
        grounded = getattr(r, "grounded", True)
        gnote = getattr(r, "grounding_note", "")
        tag = f"Task {head} · {role}" if head is not None else (role or "agent")
        lines.append(f"## {tag}\n")
        if err:
            lines.append(f"> ⚠️ worker failed: {err}\n")
        elif not grounded and out:
            lines.append(f"> 🔴 UNGROUNDED — {gnote or 'did not reproduce source.\n'}")
            lines.append(out.rstrip() + "\n")
        elif out:
            lines.append(out.rstrip() + "\n")
    return "\n".join(lines)
