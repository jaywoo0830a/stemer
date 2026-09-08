"""planner — 사용자 계획서(Plan, 마크다운) → 작업 티켓(Task) 목록.

두 경로를 제공한다:
1. plan_parser.parse(plan, gateway=parser_gw)  — parser(Qwen2.5-7B)가 자유 텍스트를
   JSON 티켓({tasks:[...]})으로 정규화 (NEW-METHOD §5.2-2). 서버 필요.
2. split_plan(plan)                            — 모델 없이도 "[Task N]" 블록을 로컬로
   분해(결정적). 오케스트레이터 테스트/가벼운 사용은 이걸 쓴다.

Task 스키마 (§5.3):
    {"id": int, "action": "explain|proof|code|fix|search|deep", "target": str,
     "input": str, "role": "worker|coder|reasoner"|None}
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .gateway import Gateway, GatewayError


@dataclass(frozen=True)
class Task:
    id: int
    action: str = "explain"          # 작업 종류 → 기본 역할 매핑 단서
    target: str = ""                 # 대상 (파일/책/섹션/심볼)
    input: str = ""                  # 구체 지시
    role: Optional[str] = None       # 명시 역할 (worker/coder/reasoner)

    @property
    def desc(self) -> str:
        parts = [self.input or self.action]
        if self.target:
            parts.insert(0, f"[{self.target}]")
        return " ".join(parts)


ROLE_BY_ACTION = {
    "explain": "worker",
    "summary": "worker",
    "search": "worker",
    "proof": "reasoner",
    "derive": "reasoner",
    "deep": "reasoner",
    "code": "coder",
    "fix": "coder",
    "review": "coder",
}

_TASK_HDR = re.compile(r"\[Task[^\]]*\]", re.IGNORECASE)


def _default_role(action: str) -> str:
    return ROLE_BY_ACTION.get(action, "worker")


def task_from_dict(d: dict, *, default_role: str = "worker") -> Task:
    """모델/설정 dict → Task (필드 결손 허용, role 미지정 시 action→role)."""
    action = str(d.get("action", "explain")).strip().lower() or "explain"
    raw_role = d.get("role")
    role = str(raw_role).strip().lower() if raw_role else None
    return Task(
        id=int(d.get("id", 0)),
        action=action,
        target=str(d.get("target", "") or ""),
        input=str(d.get("input") or d.get("description") or "").strip(),
        role=role or _default_role(action),
    )


def split_plan(plan: str) -> list[Task]:
    """결정적 로컬 분해 — "[Task N: Label]" 또는 "## Task N" 헤더 구간을 자른다.

    각 [Task] 헤더의 표제/본문에서 action·역할(worker/coder/reasoner)을 추론한다.
    태그/키워드 못 잡으면 worker 로 둔다 (기본 논리 일꾼).
    """
    if not plan or not plan.strip():
        return []
    body = plan.strip()
    headers = list(re.finditer(r"\[Task[^\]]*\]", body, re.IGNORECASE)) or \
        list(re.finditer(r"^##\s+Task\b", body, re.MULTILINE))
    tasks: list[Task] = []
    for idx, m in enumerate(headers):
        start = m.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(body)
        heading = m.group(0)
        section = body[start:end].strip()
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        content = "\n".join(lines) if lines else ""
        action, role = _classify(heading, content)
        tasks.append(Task(
            id=len(tasks) + 1,
            action=action,
            target=_guess_target(content) or "",
            input=content,
            role=role,
        ))
    return tasks


def _classify(heading: str, content: str) -> tuple[str, str]:
    """헤더+본문 키워드로 (action, role) 결정 (기본 explain/worker)."""
    blob = (heading + " " + content).lower()
    rules = [
        (("proof", "derive", "prove", "theorem", "derive"), "proof", "reasoner"),
        (("deep dive", "deep-dive", "verify", "rigorous"), "deep", "reasoner"),
        (("fix", "bug", "bugfix", "patch", "error", "indexerror"), "fix", "coder"),
        (("code", "implement", "write", "function", "refactor"), "code", "coder"),
        (("review", "optimize", "profile"), "review", "coder"),
        (("concept", "explain", "why", "intuition"), "explain", "worker"),
        (("search", "find", "locate", "similar", "equivalent"), "search", "worker"),
        (("summar", "summary", "summarize"), "summary", "worker"),
    ]
    for kws, action, role in rules:
        if any(k in blob for k in kws):
            return action, role
    return "explain", "worker"


def _guess_target(text: str) -> str | None:
    """'File: X' / 'function `f`' 같은 대상 추정 (있으면)."""
    if not text:
        return None
    fm = re.search(r"File:\s*([^\s,]+)", text)
    if fm:
        return fm.group(1)
    fn = re.search(r"function\s+[`']?([A-Za-z_][\w]*)", text)
    if fn:
        return fn.group(1)
    return None


def tasks_to_dicts(tasks: Sequence[Task]) -> list[dict]:
    return [
        {"id": t.id, "action": t.action, "target": t.target,
         "input": t.input, "role": t.role}
        for t in tasks
    ]


class PlanParser:
    """parser 서버로 자유 텍스트 → Task 목록 (NEW-METHOD §5.2-2).

    gateway 가 실제 parser(Qwen2.5-7B)를 쓴다. 실패/응답 없으면 fallback 으로
    로컬 split_plan 에 위임해도 되도록 raisePlanParseError 는 안 하고, 결과가 없으면
    split_plan 결과를 돌려주는 관용적 동작을 선택한다(주입한 `allow_fallback`).
    """

    SCHEMA_HINT = (
        'Return ONLY a JSON object: {"tasks":[{"id":int,"action":str,'
        '"target":str,"input":str,"role":"worker|coder|reasoner"}]} '
        "with one item per requested task."
    )

    def __init__(self, gateway: Gateway, *, allow_fallback: bool = True) -> None:
        self.gateway = gateway
        self.allow_fallback = allow_fallback

    def parse(self, plan: str) -> list[Task]:
        system = (
            "You split a study/coding plan into discrete worker tickets. "
            + self.SCHEMA_HINT
        )
        # decision flow 의 안전대책: 모델이 본문 그대로 되돌려도 실패 안 하게
        # 예측 불가 응답은 로컬 split_plan 으로 대체.
        try:
            obj = self.gateway.chat_json(system=system, user=plan, max_tokens=1200)
        except GatewayError:
            if self.allow_fallback:
                return split_plan(plan)
            raise
        raw = obj.get("tasks") if isinstance(obj, dict) else None
        if isinstance(raw, list) and raw:
            return [task_from_dict(t) for t in raw]
        if self.allow_fallback:
            return split_plan(plan)
        raise GatewayError("parser returned no tasks in response")
