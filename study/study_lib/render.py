"""렌더 — 검증된 GEN-PROTOCOL payload(JSON) → 최종 마크다운.

클라이언트 관점:
    md = render_guide(payload, title="...", subject="chem",
                      kind="exam", section="3.5", book_id="chembook")

원칙(GEN-PROTOCOL §5): 구조·헤더·라벨·번호는 **전부 템플릿(서버) 소유** —
payload 는 내용만 담는다. 주제 팩 키(eq/cond 등)는 존재 시 라벨 섹션으로 렌더.
"""
from __future__ import annotations

# kind 별 핵심 슬롯 렌더 순서
CORE_ORDER: dict[str, tuple[str, ...]] = {
    "exam": ("cs", "r", "as", "ex", "pr"),
    "note": ("cs", "r", "as"),
    "problems": ("pr",),
}

# 주제 팩(확장) 키 라벨 — 렌더 순서도 이 순서를 따른다
EXT_LABELS: dict[str, str] = {
    "th": "Theorems",
    "prf": "Proofs",
    "cd": "Conditions",
    "cx": "Counterexamples",
    "law": "Laws",
    "var": "Key quantities",
    "case": "Important cases",
    "setup": "Setup",
    "sign": "Sign conventions",
    "eq": "Key equations",
    "cond": "Reaction conditions",
    "mech": "Mechanism",
    "spec": "Species",
    "trend": "Trends",
    "ox": "Oxidation states",
    "path": "Pathways",
    "cmp": "Comparisons",
    "cyc": "Cycles",
    "tree": "Relationships",
    "org": "Structures",
    "exp": "Experimental design",
}

_LVL = {"b": "Basic", "a": "Advanced"}


def _concept_block(concept: dict) -> str:
    lines = [f"### {concept.get('c', '')}".rstrip()]
    if concept.get("d"):
        lines += ["", f"**Definition.** {concept['d']}"]
    if concept.get("f"):
        lines += ["", f"**Formula.** {concept['f']}"]
    if concept.get("k"):
        lines += ["", f"**Intuition.** {concept['k']}"]
    if concept.get("m"):
        lines += ["", f"**Common mistake.** {concept['m']}"]
    return "\n".join(lines)


def _render_solved_problems(items: list[dict]) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        lvl = _LVL.get(item.get("lvl", ""), "").strip()
        head = f"### {lvl} Problem {i}".replace("  ", " ").strip()
        parts.append(f"{head}\n\n{item.get('p', '')}\n\n**Solution.** {item.get('s', '')}")
    return "## Problems\n\n" + "\n\n".join(parts)


def _render_core(key: str, value: object, kind: str) -> str:
    if key == "cs" and isinstance(value, list):
        blocks = [_concept_block(c) for c in value if isinstance(c, dict)]
        return "## Concepts\n\n" + "\n\n".join(blocks) if blocks else ""
    if key == "r" and isinstance(value, str):
        return f"## Worked recipe\n\n{value}"
    if key == "as" and isinstance(value, list):
        items = [f"- {a}" for a in value if a]
        return "## Applications\n\n" + "\n".join(items) if items else ""
    if key == "ex" and isinstance(value, list):
        parts = []
        for i, e in enumerate(value, 1):
            if not isinstance(e, dict):
                continue
            parts.append(f"### Worked example {i}\n\n{e.get('p', '')}\n\n"
                         f"**Solution.** {e.get('s', '')}")
        return "## Worked examples\n\n" + "\n\n".join(parts) if parts else ""
    if key == "pr" and isinstance(value, list):
        if value and isinstance(value[0], dict):   # problems kind: {lvl,p,s}
            return _render_solved_problems(value)
        items = [f"{i}. {p}" for i, p in enumerate(value, 1) if p]
        return "## Practice problems\n\n" + "\n".join(items) if items else ""
    return ""


def _render_extension(key: str, value: object) -> str:
    label = EXT_LABELS.get(key, key.replace("_", " ").title())
    if isinstance(value, str):
        return f"## {label}\n\n{value}"
    if isinstance(value, list):
        items = [f"- {item}" for item in value if item]
        return f"## {label}\n\n" + "\n".join(items) if items else ""
    return ""


def render_guide(payload: dict, *, title: str, subject: str, kind: str,
                 section: str | None = None, book_id: str | None = None,
                 source: str | None = None) -> str:
    """클라이언트가 쓰는 진입점 — 검증된 payload → 최종 마크다운."""
    meta = {"title": title, "subject": subject, "kind": kind}
    if book_id:
        meta["book"] = book_id
    if section:
        meta["section"] = section
    if source:
        meta["source"] = source

    out = ["---"]
    out += [f"{k}: {v}" for k, v in meta.items()]
    out += ["---", "", f"# {title}", ""]

    order = CORE_ORDER.get(kind, ())
    for key in order:
        if key in payload:
            section_md = _render_core(key, payload[key], kind)
            if section_md:
                out.append(section_md)

    for key in EXT_LABELS:                     # 주제 팩 확장 (라벨 순서)
        if key in payload and key not in order:
            section_md = _render_extension(key, payload[key])
            if section_md:
                out.append(section_md)

    return "\n".join(out) + "\n"
