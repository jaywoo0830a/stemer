"""렌더 — 검증된 GEN-PROTOCOL payload(JSON) → 최종 마크다운.

클라이언트 관점:
    md = render_guide(payload, title="...", subject="chem",
                      kind="exam", section="3.5", book_id="chembook")

원칙(GEN-PROTOCOL §5): 구조·헤더·라벨·번호는 **전부 템플릿(서버) 소유** —
payload 는 내용만 담는다. 주제 팩 키(eq/cond 등)는 존재 시 라벨 섹션으로 렌더.
"""
from __future__ import annotations

import re

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


# ---- 자동 수식 래핑 (MATH-PROTOCOL 하이브리드 보정) ----
# 모델이 $ 를 안 쓴 수식 조각을 감지해 $...$ 로 감싼다 (KaTeX 가 렌더).
# 이미 $ 로 감싼 것/문장 전체는 건드리지 않는다.
_MATH_CHARS = re.compile(
    r"[∫∑∏√∞∂∇≤≥≠≃≈≡∈∉⊂⊃⊆⊇∧∨∀∃⇒⇔→←↦±×÷·∙∘−⁻²³⁰¹⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ₙₐₑ"  # noqa
    r"⟨⟩⟦⟧∥⊥∠]\|√½¾¼θαβγδεζηικλμνξπρστυφχψωΔΘΛΞΠΣΦΨΩ"
)
_SCRIPT = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ₙ_^]")
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")
_MATHISH = re.compile(
    r"(?:[A-Za-z]\s*[=<>≈≃≡]\s*[A-Za-z0-9√π]|"   # a = b
    r"\d\s*[=<>]\s*[A-Za-z0-9]|"                  # 수치 등식
    r"[A-Za-z]\([A-Za-z0-9]\)|"                   # f(x)
    r"[A-Za-z]\s*['′]\s*[=]|"                     # y' =
    r"\\frac|\\sqrt|\\sum|\\int|\\lim|\\to|\\infty)"
)


def _wrap_math(text: str) -> str:
    """텍스트에서 수학 조각을 찾아 $...$ 로 감싼다 (멱등, 오탐 방지)."""
    # 이미 $ 로 감싼 곳은 보존 — $ 밖의 지역만 처리
    segments = re.split(r"(\$[^$]*\$)", text)
    out: list[str] = []
    for seg in segments:
        if seg.startswith("$"):
            out.append(seg)                 # 이미 래핑됨 → 유지
            continue
        # 이 세그먼트에서 수학 조각 후보만 $ 로 감싼다
        # 단순화: 세그먼트 전체가 "수학처럼 보이고" 충분히 짧으면 그대로 감싼다
        if _math_segment(seg):
            out.append(f"${_strip_embrace(seg)}$")
        else:
            out.append(seg)
    return "".join(out)


def _math_segment(seg: str) -> bool:
    """이 텍스트 조각을 수식으로 감싸도 안전한가 (짧고 수학 문자 포함)."""
    s = seg.strip()
    if not s or len(s) > 60:
        return False
    # 일반 문장(마침표/쉼표+공백 다수, 언어적 단어)은 제외 — 오탐 방지
    words = re.findall(r"[A-Za-z]{3,}", s)
    if s.endswith((".", "!", "?")) or (len(words) >= 3 and " " in s):
        # "find the limit" 처럼 언어적 어미가 붙으면 수식 전체 래핑 부적절
        if not _LATEX_CMD.search(s):
            return False
    # 수학 문자 / 첨자 / LaTeX 명령 / 수학 패턴 하나라도 있으면 감싼다
    if _MATH_CHARS.search(s) or _SCRIPT.search(s) or _LATEX_CMD.search(s):
        return True
    return bool(_MATHISH.search(s))


def _strip_embrace(s: str) -> str:
    return s.strip()


def _wrap_all_math(text: str) -> str:
    return _wrap_math(text)


def _concept_block(concept: dict) -> str:
    lines = [f"### {concept.get('c', '')}".rstrip()]
    if concept.get("d"):
        lines += ["", f"**Definition.** {_wrap_all_math(concept['d'])}"]
    if concept.get("f"):
        lines += ["", f"**Formula.** {_wrap_all_math(concept['f'])}"]
    if concept.get("k"):
        lines += ["", f"**Intuition.** {_wrap_all_math(concept['k'])}"]
    if concept.get("m"):
        lines += ["", f"**Common mistake.** {_wrap_all_math(concept['m'])}"]
    return "\n".join(lines)


def _render_solved_problems(items: list[dict]) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        lvl = _LVL.get(item.get("lvl", ""), "").strip()
        head = f"### {lvl} Problem {i}".replace("  ", " ").strip()
        parts.append(f"{head}\n\n{_wrap_all_math(item.get('p', ''))}\n\n"
                     f"**Solution.** {_wrap_all_math(item.get('s', ''))}")
    return "## Problems\n\n" + "\n\n".join(parts)


def _render_core(key: str, value: object, kind: str) -> str:
    if key == "cs" and isinstance(value, list):
        blocks = [_concept_block(c) for c in value if isinstance(c, dict)]
        return "## Concepts\n\n" + "\n\n".join(blocks) if blocks else ""
    if key == "r" and isinstance(value, str):
        return f"## Worked recipe\n\n{_wrap_all_math(value)}"
    if key == "as" and isinstance(value, list):
        items = [f"- {_wrap_all_math(a)}" for a in value if a]
        return "## Applications\n\n" + "\n".join(items) if items else ""
    if key == "ex" and isinstance(value, list):
        parts = []
        for i, e in enumerate(value, 1):
            if not isinstance(e, dict):
                continue
            parts.append(f"### Worked example {i}\n\n{_wrap_all_math(e.get('p', ''))}\n\n"
                         f"**Solution.** {_wrap_all_math(e.get('s', ''))}")
        return "## Worked examples\n\n" + "\n\n".join(parts) if parts else ""
    if key == "pr" and isinstance(value, list):
        if value and isinstance(value[0], dict):   # problems kind: {lvl,p,s}
            return _render_solved_problems(value)
        items = [f"{i}. {_wrap_all_math(p)}" for i, p in enumerate(value, 1) if p]
        return "## Practice problems\n\n" + "\n".join(items) if items else ""
    return ""


def _render_extension(key: str, value: object) -> str:
    label = EXT_LABELS.get(key, key.replace("_", " ").title())
    if isinstance(value, str):
        return f"## {label}\n\n{_wrap_all_math(value)}"
    if isinstance(value, list):
        items = [f"- {_wrap_all_math(item)}" for item in value if item]
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
