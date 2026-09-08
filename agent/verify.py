"""verify — LLM 판사(Tier-2)로 답을 검증 (참/거짓·오류·예외 판별).

사용자 요청: "논리적인 모델을 판사로 써서 참인지/거짓인지/오류·예외가 있는지
검증" → worker(또는 coder/reasoner)가 만든 답을 **다른(더 엄격한) 로컬 모델**이
재판하도록 한다.

- 다른 모델 선택: 생산자(producer)와 **같지 않은** 역할로 판사를 고른다
  (self-confirmation 방지). 기본: producer 가 worker/coder = reasoner(8088,
  DeepSeek-R1-Distill), producer 가 reasoner = coder1(8086). AGENT_JUDGE 로 재정의.
- 판사는 참조(source)만을 근거로 답이 일치하는지/오류·예외를 지어내지 않았는지
  검토해 구조화 JSON {ok, grounded, errors[], exceptions[], reason} 를 돌려준다.

설계: Verifier 는 Protocol — 이름/망치 대체로 주입가능(테스트: StubVerifier).
LlmVerifier 는 OpenAI 호환 llama-server 의 chat_json 을 쓴다(네트워크 여기서만).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence

from .gateway import Gateway, GatewayError
from .rag import Chunk

try:  # 필수 아님 — JSON 파싱 힐퍼
    from .gateway import _find_json_object
    HAVE_JSON = True
except Exception:  # noqa: BLE001
    HAVE_JSON = False


@dataclass(frozen=True)
class Verdict:
    ok: bool                # 수락 여부 (표면 충실도 + 판사 허용)
    grounded: bool          # 근거 기반 & 참  (판사가 grounded 이상)
    reason: str = ""
    errors: List[str] = field(default_factory=list)      # 판사가 잡은 오류(사람-판독)
    exceptions: List[str] = field(default_factory=list)  # 답이 만든 '예외/경우' 검증
    error_codes: List[str] = field(default_factory=list)  # 판사 분류 코드(stable)
    judge_role: str = ""
    source: str = "llm"     # llm | stub | deferred | tier1

    @property
    def human(self) -> str:
        parts = [self.reason] + [f"err: {e}" for e in self.errors]
        if self.error_codes:
            parts.append("codes: " + ",".join(self.error_codes))
        if self.exceptions:
            parts.append("unchecked-exceptions: " + ", ".join(self.exceptions))
        return " | ".join(p for p in parts if p)


class Verifier(Protocol):
    def verify(self, question: str, chunks: Sequence[Chunk],
               answer: str) -> Verdict: ...


# 판사 프롬프트는 prompts(config.yaml) 단일 소스에서 — 여기는 재-export.
from . import prompts as _prompts  # noqa: E402

JUDGE_SYSTEM = _prompts.JUDGE_SYSTEM

# 출제(문제 만들기) 결과를 심판하는 전용 시스템 — 새 문제 숫자는 허용하되,
# 각 문제가 concretely solvable & source-기반이며, 문맥 밖 일반정리를 안 쓰는지.
JUDGE_PROBLEMS = _prompts.fetch_text("judge.problems", default=(
    "You are a STRICT PROBLEM SET QUALITY judge. You are given the REQUEST, "
    "the REFERENCE CONTEXT (trusted source), and a CANDIDATE problem set authored "
    "by another model.\n"
    "You must act as a deterministic gatekeeper. Source is absolute authority. "
    "Reject anything that is not perfectly grounded in the source or is "
    "internally ill-posed.\n\n"
    "Judge the set on the following axes. If ANY check fails, set ok=false.\n"
    "1) SOURCE CONFINEMENT (Critical): Does every problem AND its solution key "
    "use ONLY theorems, definitions, and methods explicitly present in the "
    "REFERENCE CONTEXT? If a problem drags in an outside theorem (e.g., uses "
    "'absolute convergence' when the source never mentions it), classify it as "
    "'extra_claim'.\n"
    "2) CONCRETENESS: Is each problem a concrete, well-posed question with a "
    "numeric/symbolic target? Reject meta-questions like 'Formulate a problem "
    "where...' or problems with no explicit computational goal.\n"
    "3) DEFINITENESS OF SOLUTION KEY: Does each Solution key end with an "
    "explicit final answer (number, condition, or boxed result)? Reject any "
    "solution that ends with 'the student will finish', 'the reader computes', "
    "or leaves the final answer open.\n"
    "4) MATHEMATICAL SOUNDNESS (Internal logic): Scrutinize the logic of the "
    "Solution key. Reject immediately if you find:\n"
    "   - A two-sided bound where LHS and RHS have collapsed into the same "
    "     limit when the source method requires distinct indices (e.g., tail at "
    "     k vs tail at k+1).\n"
    "   - Inverted or nonsensical inequalities (e.g., 'f_{n+1} <= f_n <= q' "
    "     garbage where the logic is broken).\n"
    "   - Arithmetic or algebraic errors in the derivation.\n"
    "5) STRUCTURAL COMPLIANCE: Does each problem have a 'PROBLEM N' header, "
    "'Question', 'Solution key', 'Difficulty', and 'Source anchor'? "
    "Missing anchors or solution keys are failures.\n"
    "6) SANITY OF NEW NUMBERS: New numbers invented by the author are ALLOWED, "
    "but only if the solution key produces a correct definite answer using the "
    "source's method. Do NOT reject simply because numbers differ from a printed "
    "example; reject only if the new numbers break the solvability or logic.\n\n"
    "Return ONLY a strict JSON object (no prose, no markdown fence) with keys: "
    "{\"ok\": bool, \"grounded\": bool, \"errors\":[string], "
    "\"error_codes\":[string], \"exceptions\":[string], \"reason\":string}.\n"
    "error_codes MUST be one or more of: source_violation, meta_question, "
    "indefinite_solution, bound_collapse, logic_error, structural_missing, other. "
    "Set ok=false on ANY violation."
))

def pick_judge_server(producer_role: str, registry) -> str:
    """생산자와 다른 역할의 판사 주소. env AGENT_JUDGE 가 있으면 그것 우선."""
    import os
    ov = os.environ.get("AGENT_JUDGE")
    if ov:
        return ov.rstrip("/")
    r = registry
    if producer_role == "reasoner":
        try:
            return r.role("coder").urls[0]
        except Exception:  # noqa: BLE001 — fallthrough
            pass
    # 기본: worker/coder 답을 reasoner(DeepSeek-R1)가 판사
    try:
        return r.role("reasoner").urls[0]
    except Exception:  # noqa: BLE001
        # 마지막 폴백: coder
        try:
            return r.role("coder").urls[0]
        except Exception:  # noqa: BLE001
            raise ValueError("no verifier role available (need reasoner or coder)") from None


class LlmVerifier:
    """llama-server(Gateway) 에게 판사를 맡긴다. chat_json 사용(구조화)."""

    def __init__(self, gateway: Gateway, *, judge_role: str = "reasoner",
                 system: Optional[str] = None) -> None:
        self._gw = gateway
        self.judge_role = judge_role
        self._system = system

    # Verifier 규약
    def verify(self, question: str, chunks: Sequence[Chunk],
               answer: str) -> Verdict:
        user = self._build_message(question, chunks, answer)
        try:
            data = self._gw.chat_json(system=self._system or JUDGE_SYSTEM,
                                      user=user, max_tokens=300)
        except GatewayError:
            # 판사 서버 장애/응답불가 → '미판정(deferred)' 으로 명시(하드 실패 대신).
            return Verdict(ok=True, grounded=True,
                           reason="judge unreachable; verdict deferred",
                           judge_role=self.judge_role, source="deferred")
        return self._parse(data)

    def _build_message(self, question, chunks, answer) -> str:
        ref = "\n\n---\n\n".join(
            f"[{c.source} / {c.section}]\n{c.text}" for c in chunks) if chunks else "(no reference supplied)"
        return (
            f"QUESTION:\n{question.strip()}\n\n"
            f"REFERENCE CONTEXT:\n{ref}\n\n"
            f"CANDIDATE ANSWER TO VERIFY:\n{answer.strip()}"
        )

    def _parse(self, data) -> Verdict:
        if isinstance(data, dict):
            ok = bool(data.get("ok", False)) if "ok" in data else bool(data.get("grounded", False))
            grounded = bool(data.get("grounded", ok))
            errs = data.get("errors") or []
            excs = data.get("exceptions") or []
            codes = data.get("error_codes") or []
            reason = str(data.get("reason", "") or "")
            return Verdict(ok=ok, grounded=grounded, reason=reason,
                           errors=_as_list(errs), exceptions=_as_list(excs),
                           error_codes=_as_list(codes), judge_role=self.judge_role)
        return Verdict(ok=False, grounded=False, reason="judge reply not a dict",
                       judge_role=self.judge_role)


class ProblemsVerifier(LlmVerifier):
    """문제-셋 품질 판사: LlmVerifier + JUDGE_PROBLEMS 시스템."""

    def __init__(self, gateway: Gateway, *, judge_role: str = "reasoner") -> None:
        super().__init__(gateway, judge_role=judge_role, system=JUDGE_PROBLEMS)


def _as_list(x) -> list:
    if isinstance(x, list):
        return [str(v) for v in x]
    if isinstance(x, (str, int, float)):
        return [str(x)]
    return []


class StubVerifier:
    """테스트용 — 미리 정해진 평결을 준다 (결정론)."""

    def __init__(self, verdict: Optional[Verdict] = None,
                 trace: Optional[list] = None) -> None:
        base = Verdict(ok=True, grounded=True, reason="stub accept",
                       judge_role="stub", source="stub")
        if verdict is not None:
            base = Verdict(ok=verdict.ok, grounded=verdict.grounded,
                           reason=verdict.reason, errors=list(verdict.errors),
                           exceptions=list(verdict.exceptions),
                           error_codes=list(verdict.error_codes),
                           judge_role=verdict.judge_role or "stub",
                           source="stub")
        self._v = base
        self.trace = trace if trace is not None else []

    def verify(self, question, chunks, answer) -> Verdict:
        self.trace.append(
            {"q": question, "n": len(chunks), "answer_head": (answer or "")[:40]})
        return self._v
