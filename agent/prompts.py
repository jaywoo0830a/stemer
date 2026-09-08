"""prompts — 프롬프트 단일 소스를 config(prompts/config.yaml)에서 읽어 조립한다.

설계(프로토콜 분리 — PROMPT-PROTOCOL.md):
- **프롬프트 텍스트 ≠ 코드**. 텍스트는 `agent/prompts/config.yaml` 에 있고, 여기는
  그 데이터를 조립해 (role system / user / context / 판사 / parser) 문자열을 만든다.
- config 를 고치면 코드 수정 없이 다음 요청부터 반영(재빌드 필요). 파일이 없으면
  아래 `_FALLBACK`(동일 내용)으로 동작해 외부 없어도 오프라인 테스트가 안 깨진다.
- 결정론: source 는 **무조건 정본** (config.source_is_authority.preamble). worker 가
  source 를 '틀렸다 교정' 하거나 자기 산술로 이기려 하지 못하게 한다.

공개 API(다른 모듈이 쓰는 것):
    system_prompt(role, task_id, n_context, action, target)
    user_prompt(...), context_block(...), merge_results(...)
    PARSER_SYSTEM, JUDGE_SYSTEM   (planner.py / verify.py 가 import)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

from .rag import Chunk

_CFG_PATH = Path(__file__).resolve().parent / "prompts" / "config.yaml"

# ---- fallback (파일 없을 때 / 테스트 무망) — config.yaml 과 동일 뜻 —--------------
_FALLBACK = {
    "roles.worker": "You are a precise strictly source-faithful explanatory worker "
                    "for STEM study. Answer the exact question using ONLY what the "
                    "REFERENCE CONTEXT shows. Do not add a proof or a derived claim "
                    "that was not requested, and never introduce numbers/bounds/"
                    "exceptions not printed in the source. Math in LaTeX.",
    "roles.coder": "You are a strictly source-faithful code worker. Base claims and "
                   "patches only on symbols/files in REFERENCE CONTEXT; never invent "
                   "APIs/paths/behavior. If absent from context, say 'not present in "
                   "the supplied source'. Deliver (1) root cause (2) code in ```.",
    "roles.reasoner": "You are a rigorous reasoner bounded by the source. Derive step "
                      "by step only from REFERENCE CONTEXT or labeled axioms; never "
                      "override a value/statement the source prints; on conflict stop "
                      "and report it. Mark unsupportable steps UNVERIFIED.",
    "common.absolute":
        "ABSOLUTE RULES (must obey):\n"
        "1. Answer the EXACT question, only what it asks; no unsolicited proof.\n"
        "2. REPRODUCE source statements VERBATIM.\n"
        "3. SOURCE WINS OVER YOUR OWN ARITHMETIC: when the source prints a worked "
        "numeric answer and the question asks for that quantity, your number MUST "
        "equal the source's; on difference present the source value as the answer.\n"
        "4. If not covered by context, say '(not covered in the supplied source)'.\n"
        "5. Do not invent citations/bounds/formulas/data.\n"
        "6. Show steps; 7. Be concise, LaTeX math, markdown.",
    "scope.non_proof":
        "SCOPE: EXPLAIN/QUOTE/STATE/APPLY task. Report the source's fact/theorem/"
        "bound/worked value EXACTLY and answer the asked numeric/statement. Do NOT "
        "over-produce (no proof, no added cases, no numbers the source does not "
        "print). If a number is asked, keep your numeric answer equal to the source's "
        "printed value.",
    "scope.proof":
        "SCOPE: a proof/derivation was requested. Derive only from premises in "
        "REFERENCE CONTEXT or universal axioms you label; never decorate the source "
        "with invented proofs or external results.",
    "judge.system":
        'You are a strict verification judge. Judge the CANDIDATE ANSWER against the '
        'REFERENCE CONTEXT on: (a) grounding/drift, (b) source-conflict — if the '
        'reference prints a fact/formula/bound or a worked NUMERIC result, the answer '
        'must AGREE; overriding or contradicting the source is an ERROR, and '
        '(c) added exceptions/errors not asked. Source is always authoritative over '
        'the candidate; never let the candidate "correct" the source. '
        'Return ONLY a strict JSON object (no prose): '
        '{"ok": bool, "grounded": bool, "errors":[string], "exceptions":[string], '
        '"reason":string}',
    "parser.system":
        'You convert a study/coding plan into strict worker tickets. A user label '
        '[Task 1: explain] is AUTHORITATIVE for role (explain/summary/search/quote/'
        'state->worker; code/fix/review->coder; proof/derive/deep->reasoner). Never '
        'upgrade explain to proof because the body has "theorem". Keep the question '
        'verbatim in input. Return ONLY a JSON object: '
        '{"tasks":[{"id":int,"action":str,"target":str,"input":str,'
        '"role":"worker|coder|reasoner"}]}',
}


def _load_cfg() -> dict:
    """config.yaml(type safe) / fallback. 파일 우선, 없으면 fallback."""
    data = dict(_FALLBACK)
    path = os.environ.get("AGENT_PROMPTS_FILE") or _CFG_PATH
    try:
        import yaml  # 선택: 파일 있을 때만
        if Path(path).is_file():
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            roles = (raw.get("roles") or {})
            for r in ("worker", "coder", "reasoner"):
                if roles.get(r):
                    data[f"roles.{r}"] = str(roles[r])
            common = raw.get("common") or {}
            if common.get("absolute"):
                data["common.absolute"] = str(common["absolute"])
            scope = raw.get("scope") or {}
            if scope.get("non_proof"):
                data["scope.non_proof"] = str(scope["non_proof"])
            if (scope.get("proof") or {}).get("proof_or_derive"):
                data["scope.proof"] = str(scope["proof"]["proof_or_derive"])
            judge = raw.get("judge") or {}
            if judge.get("system"):
                data["judge.system"] = str(judge["system"])
            parser = raw.get("parser") or {}
            if parser.get("system"):
                data["parser.system"] = str(parser["system"])
    except Exception:  # noqa: BLE001 — 파일이 있어도 읽기 실패 시 fallback 유지
        pass
    return data


def _cfg(_k: str) -> str:
    return str(_load_cfg().get(_k, _FALLBACK.get(_k, ""))).strip()


# 판사/파서 시스템 (verify.py / planner.py 가 import)
JUDGE_SYSTEM = _cfg("judge.system")
PARSER_SYSTEM = _cfg("parser.system")


def system_prompt(role: str, task_id: int, n_context: int,
                  action: str = "", target: str = "") -> str:
    role_style = _cfg(f"roles.{role}") or _cfg("roles.worker")
    a = (action or "").lower()
    scope = _cfg("scope.proof" if a in ("proof", "derive", "deep")
                 else "scope.non_proof")
    meta = [f"ticket #{task_id}", f"assigned role: {role}"]
    if action:
        meta.append(f"action: {action}")
    if target:
        meta.append(f"target: {target}")
    head = "You are handling " + ", ".join(meta) + "."
    return (head + "\n\n" + role_style
            + f"\n\nYou will see {n_context} reference chunk(s) below if any.\n\n"
            + scope + "\n\n" + _cfg("common.absolute"))


def _sym_condition_note(question: str) -> str:
    notes: list[str] = []
    low = question.lower()
    if "symmetr" in low:
        notes.append("interval is SYMMETRIC about the origin — respect even/odd structure")
    if "zero" in low and ("integral" in low or "∫" in question or "integrate" in low):
        notes.append("the claim says the integral equals ZERO — explain/prove WHY, "
                     "not a bare antiderivative")
    if "orthogon" in low:
        notes.append("intended concept is ORTHOGONALITY of basis functions")
    return ("\nConditions/claims in the question you MUST honor and address:\n"
            + "\n".join(f"- {n}" for n in notes)) if notes else ""


def context_block(chunks: Sequence[Chunk], max_chars: int = 6000) -> str:
    if not chunks:
        return ("SOURCE: (NO-SOURCE — no reference chunks were supplied; no study "
                "context has been loaded for this question)")
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
    return (f"YOUR TASK (label={label}): answer the question below EXACTLY as given.\n\n"
            f"QUESTON VERBATIM:\n{question}\n{cond}\n\n"
            f"REFERENCE CONTEXT:\n{context_block(chunks)}\n\n"
            "Read the SOURCE AUTHORITY paragraph in the system prompt. Begin by "
            "restating the exact question in one line, then answer inside the source's "
            "given facts and printed values.")


def merge_results(results: Sequence[object]) -> str:
    lines: list[str] = []
    for r in results:
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


# 여전히 planner/기타가 참조 가능하도록 유지 (import 유틸)
def fetch_text(key: str, default: Optional[str] = None) -> str:
    return _cfg(key) or (default or "")
