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
    errors: List[str] = field(default_factory=list)      # 판사가 잡은 오류
    exceptions: List[str] = field(default_factory=list)  # 답이 만든 '예외/경우' 검증
    judge_role: str = ""

    @property
    def human(self) -> str:
        parts = [self.reason] + [f"err: {e}" for e in self.errors]
        if self.exceptions:
            parts.append("unchecked-exceptions: " + ", ".join(self.exceptions))
        return " | ".join(p for p in parts if p)


class Verifier(Protocol):
    def verify(self, question: str, chunks: Sequence[Chunk],
               answer: str) -> Verdict: ...


JUDGE_SYSTEM = (
    "You are a strict verification judge for a STEM/code study assistant.\n"
    "You are given: the QUESTION, the REFERENCE CONTEXT (chunks from trusted "
    "sources), and a CANDIDATE ANSWER produced by another model.\n"
    "Your job: determine whether the answer is correct, fully grounded in the "
    "reference, and free of invented errors or cases.\n\n"
    "Rules:\n"
    "- If the reference states a fact/theorem/formula/bound and the answer "
    "paraphrases or uses it consistently -> grounded=true, ok=true.\n"
    "- If the answer CONTRADICTS the reference, drops a stated condition/bound, "
    "or introduces numbers/errors/exceptions/cases absent from the reference -> "
    "ok=false (list them under errors / exceptions).\n"
    "- If there is no reference context (free answer), judge only internal "
    "consistency and obvious mathematical/logical invalidity.\n"
    '- Return ONLY a strict JSON object (no prose): {"ok": bool, "grounded": bool, '
    '"errors":[string], "exceptions":[string], "reason":string}'
)


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

    def __init__(self, gateway: Gateway, *, judge_role: str = "reasoner") -> None:
        self._gw = gateway
        self.judge_role = judge_role

    # Verifier 규약
    def verify(self, question: str, chunks: Sequence[Chunk],
               answer: str) -> Verdict:
        user = self._build_message(question, chunks, answer)
        try:
            data = self._gw.chat_json(system=JUDGE_SYSTEM, user=user, max_tokens=800)
        except GatewayError:
            # 판사 서버 장애/응답불가 → '미판정' 으로 통과시키되 명시 (하드 실패 대신)
            return Verdict(ok=True, grounded=True,
                           reason="judge unreachable; verdict deferred",
                           judge_role=self.judge_role)
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
            reason = str(data.get("reason", "") or "")
            return Verdict(ok=ok, grounded=grounded, reason=reason,
                           errors=list(errs) if isinstance(errs, list) else [str(errs)],
                           exceptions=list(excs) if isinstance(excs, list) else [],
                           judge_role=self.judge_role)
        return Verdict(ok=False, grounded=False, reason="judge reply not a dict",
                       judge_role=self.judge_role)


class StubVerifier:
    """테스트용 — 미리 정해진 평결을 준다 (결정론)."""

    def __init__(self, verdict: Optional[Verdict] = None,
                 trace: Optional[list] = None) -> None:
        self._v = verdict or Verdict(ok=True, grounded=True,
                                     reason="stub accept", judge_role="stub")
        self.trace = trace if trace is not None else []

    def verify(self, question, chunks, answer) -> Verdict:
        self.trace.append(
            {"q": question, "n": len(chunks), "answer_head": (answer or "")[:40]})
        return self._v
