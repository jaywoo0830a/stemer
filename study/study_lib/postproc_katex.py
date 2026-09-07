"""POST-PROCESS — 로컬 LLM(Ollama) KaTeX 후처리 파이프라인.

생성(payload→render)된 note.md 의 수식이 "인라인 $..$ 만이라 단조롭다"는 문제를
해소하기 위한 최종 후처리 단계:
  - 렌더 완료 마크다운의 **문장·출처·구조는 그대로** 두고,
  - 수식 **표현만** 로컬 LLM 이 재구성한다:
      (a) 핵심 정의/정리/방정식    → 단독 라인 display  $$...$$
      (b) 여러 줄로 이어지는 유도    →  \\begin{aligned}...\\end{aligned} 블록
      (c) 짧은 심볼/문맥 수식        →  인라인  $...$  유지
  - 안전 가드 통과 시에만 원본 note 를 덮어쓴다(실패/거절 시 원본 유지).

가드(원문 보존 우선 — "완성된 결과물은 건드리지 말 것"):
  1) 수식 밖 텍스트(공백·개행 무시)가 원문과 **완전히 동일** → 문장/출처/번호 불변.
  2) KaTeX 벨런스 + 금지 매크로/환경 미사용(lint_katex 통과).
  3) 빈 $$ 나 수식이 통째로 사라진 경우 없음.
주의: 로컬 LLM 은 안정적이지 않을 수 있으므로(또는 꺼져 있으면) 그냥 원본을 돌려준다.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .lint import lint_katex

# `$$...$$` (DOTALL) 먼저, 그 다음 인라인 `$...$` (escape 허용)
_DISPLAY_RE = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
_INLINE_RE = re.compile(r"\$(?:\\.|[^$\\\n])*\$(?!\$)")


def _strip_math(text: str) -> str:
    """문서에서 모든 수식($/$$)을 제거해 남은 평문(비수식)을 돌려준다."""
    out = _DISPLAY_RE.sub("", text)
    out = _INLINE_RE.sub("", out)
    return out


_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def word_signature(markdown: str) -> list:
    """수식 밖 텍스트에서 단어(한글/영문/숫자) 토큰만 순서대로 추출(lower).

    display 승격/개행 리플로우로 문장부호·공백·줄바꿈이 바뀌어도, 단어 내용·순서가
    같으면 '결과물(문장 내용/출처/번호)은 보존'된 것으로 본다.
    """
    return [t.lower() for t in _WORD_RE.findall(_strip_math(markdown))]


def text_preserved(original: str, candidate: str) -> bool:
    """후보가 원문과 문장·출처·번호의 '단어 내용·순서'를 보존하는가."""
    return word_signature(original) == word_signature(candidate)


def _empty_math(text: str) -> bool:
    """새 결과에 내용 없는 수식(빈 $$, 비어있는 aligned)이 섞였는가."""
    for m in _DISPLAY_RE.findall(text):
        if not m.strip() or m.strip() in ("\\begin{aligned}", "\\end{aligned}"):
            return True
    for m in _INLINE_RE.findall(text):
        if not m.strip():
            return True
    return False


def guard_ok(original: str, candidate: str) -> tuple[bool, str]:
    """후보를 반영해도 되는가. (ok, 사유) — lint + 문장 보존 + 빈수식 검사."""
    if not text_preserved(original, candidate):
        return False, "non-math text changed"
    issues = lint_katex(candidate)
    if issues:
        return False, f"katex lint: {issues[0].message}"
    if _empty_math(candidate):
        return False, "empty math produced"
    return True, ""


class OllamaClient:
    """Ollama /api/generate 경량 클라이언트 (표준 urllib — 추가 의존성 없음)."""

    def __init__(self, *, base_url: str = "http://host.docker.internal:11434",
                 model: str = "qwen2.5:3b", timeout: float = 1200.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _call(self, prompt: str, *, system: str = "") -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system or None,
            "stream": False,
            "options": {"temperature": 0.0, "num_ctx": 8192, "num_predict": 8000},
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "")


REFORMAT_SYSTEM = (
    "You reflow LaTeX display math inside an existing Markdown study note. "
    "You MUST NOT change, add, remove, or reword ANY text, heading, citation, "
    "number, or list outside of math delimiters. Only change HOW math is "
    "typeset, and only when it improves readability:\n"
    "- Keep short inline symbols inside one-line $...$ (x=2, n\\to\\infty, "
    "p>1, etc).\n"
    "- Promote a KEY definition/theorem/result equation or a 'Formula' line to "
    "its own display block on its own line: $$...$$ .\n"
    "- If a worked solution shows a chain of equalities/derivations, typeset "
    "that chain as a display \\begin{aligned} ... \\end{aligned} block (each "
    "row ends with \\\\\\). Align at = or \\le.\n"
    "- Never introduce environments other than aligned, never use \\text or "
    "\\tag, never fabricate content.\n"
    "Output the ENTIRE Markdown note with only those layout edits applied."
)

REFORMAT_USER = (
    "Rewrite the math typesetting (keep every non-math word, heading, and "
    "citation identical). Output the full note:\n\n" "{markdown}"
)


def refine_math(markdown: str, client: OllamaClient) -> str:
    """Ollama 로 수식 표현만 다양화. 검증/장애 시 원본을 그대로 돌려준다."""
    try:
        candidate = client._call(
            REFORMAT_USER.format(markdown=markdown), system=REFORMAT_SYSTEM
        ).strip()
    except Exception:
        return markdown
    if not candidate:
        return markdown
    ok, _reason = guard_ok(markdown, candidate)
    return candidate if ok else markdown


def refine_math_detailed(markdown: str, client: OllamaClient,
                         *, verbose: bool = False) -> tuple[str, str]:
    """진단용 — 후처리 후 (결과 또는 원본, reason). verbose 시 candidate 도 사유에 포함.

    reason 예: 'ok' | 'llm-error: ...' | 'empty' | 'guard: <사유>'.
    """
    try:
        candidate = client._call(
            REFORMAT_USER.format(markdown=markdown), system=REFORMAT_SYSTEM
        ).strip()
    except Exception as exc:
        return markdown, f"llm-error: {exc}"
    if not candidate:
        return markdown, "empty"
    ok, why = guard_ok(markdown, candidate)
    if ok:
        return candidate, "ok"
    if verbose:
        # 사유 + (이상하면) 후보 미리보기 일부
        return markdown, f"guard: {why}; candidate(first 300): {candidate[:300]}"
    return markdown, f"guard: {why}"



###############################################################################
# 테스트용 순수 오케스트레이션 (Ollama 프로토콜 추상) — 서버 연동 전 로컬 TDD
###############################################################################


class LocalLLM(Protocol):
    def complete_text(self, *, system: str, user: str) -> str: ...


def refine_with(llm: LocalLLM, markdown: str) -> str:
    """실파이프라인에서 쓸: 인자 LLM 로 후처리. 가드 통과 시 candidate, 아니면 원본."""
    try:
        system = REFORMAT_SYSTEM
        user = REFORMAT_USER.format(markdown=markdown)
        candidate = llm.complete_text(system=system, user=user) or ""
    except Exception:
        return markdown
    if not candidate.strip():
        return markdown
    ok, _reason = guard_ok(markdown, candidate)
    return candidate if ok else markdown


def default_ollama_client() -> OllamaClient:
    """env(OLLAMA_HOST/OLLAMA_MODEL)로 받은 기본 Ollama 클라이언트."""
    import os
    host = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    return OllamaClient(base_url=host, model=model)


def postkatex_if_enabled(path, *, force: bool = False) -> str:
    """저장 직후 훅 — POSTKATEX=1(또는 force)일 때 note 파일 후처리.

    반환: 새 내용(변경 시 갱신 후) 또는 원문. Ollama 오류/가드 거부 시 원문 유지.
    """
    import os
    from pathlib import Path

    p = Path(path)
    if not (force or os.environ.get("POSTKATEX", "0") == "1"):
        return ""
    md = p.read_text(encoding="utf-8")
    new = refine_math(md, default_ollama_client())
    if new != md:
        p.write_text(new, encoding="utf-8")
    return new

