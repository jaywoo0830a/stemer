"""prompts — 역할별 시스템 프롬프트 + 근거 주입 규약 (NEW-METHOD §환각차단).

공통: 모델은 "입력 근거(reference context)"에만 의존해 답해야 하며, 없는 지식은
창작하지 말아야 한다. 답변 형식은 마크다운 (코더는 ``` 코드 블록 필수).
"""
from __future__ import annotations

from typing import Sequence

from .rag import Chunk


ROLE_STYLE: dict[str, str] = {
    "worker": "You are a precise explanatory worker for STEM study. "
              "Give short, source-grounded explanations in Korean-friendly markdown; "
              "use LaTeX ($...$) for math. Never invent facts absent from context.",
    "coder": "You are a code worker. Read the provided code context, then explain "
             "root cause and/or return corrected minimal code in a ``` fenced block. "
             "Anchor every claim in the shown symbols/files; never guess APIs absent "
             "from context.",
    "reasoner": "You are a rigorous reasoner. Produce step-by-step derivation or "
                "proof from the provided context only. Mark any step you cannot "
                "support from context as UNVERIFIED rather than fabricating.",
}


def system_prompt(role: str, task_id: int, n_context: int) -> str:
    base = ROLE_STYLE.get(role, ROLE_STYLE["worker"])
    return (
        f"{base}\n\nYou are handling ticket #{task_id}. Above all: answer ONLY from "
        f"the {n_context} reference chunks supplied below. If the context is empty, "
        "say so and ask for the source instead of making things up."
    )


def context_block(chunks: Sequence[Chunk], max_chars: int = 6000) -> str:
    """근거 청크를 모델 프롬프트 안쪽 인용 형식으로 직렬화 (↔ 컨텍스트 한도)."""
    if not chunks:
        return "(no reference context found — do not fabricate; note this to the planner)"
    parts: list[str] = []
    used = 0
    for c in chunks:
        snippet = c.text.strip()
        if used + len(snippet) > max_chars:
            break
        parts.append(f"SOURCE {c.source}: {snippet}")
        used += len(snippet) + len(c.source)
    return "\n\n---\n\n".join(parts)


def user_prompt(task_input: str, chunks: Sequence[Chunk]) -> str:
    return (
        f"QUESTION / TICKET INPUT:\n{task_input.strip()}\n\n"
        f"REFERENCE CONTEXT (grounding only):\n{context_block(chunks)}"
    )


def merge_results(results: Sequence[object]) -> str:
    """여러 WorkerResult 를 하나의 조직화된 마크다운으로 취합 (§5.2-5)."""
    lines: list[str] = []
    for r in results:  # object: has .task/.role/.output/.error
        head = getattr(r, "task", None)
        role = getattr(r, "role", "")
        out = getattr(r, "output", "")
        err = getattr(r, "error", None)
        tag = f"Task {head} · {role}" if head else role
        lines.append(f"## {tag}\n")
        if err:
            lines.append(f"> ⚠️ worker failed: {err}\n")
        elif out:
            lines.append(out.rstrip() + "\n")
    return "\n".join(lines)
