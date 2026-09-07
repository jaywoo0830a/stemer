"""generate_free — 스키마/렌더 없이 로컬 LLM 이 곧장 마크다운 학습자료를 씀.

배경(실증): Qwen3-14B·R1 같은 '해설형/추론' 로컬 모델은 상세 구조화 payload
(스키마 JSON) 요구를 안 지키고 자연스러운 해설 md 를 쓴다 → 그 강점을 그대로,
"한 편의 markdown"을 직접 만들게 한다(LOCAL_LLM_FREE=1).

- 입력: topic(제목/책/섹션) + 교재 passage(store retrieve).
- 출력: notes/<topic>.md (앞에 YAML front matter 추가, 본문은 모델이 쓴 md 그대로).
- 시스템/유저 프롬프트가 "교재를 근거로 개념을 풀어 설명 + 예제/연습(n개) + 출처"를
  요구해 우리가 원하는 학습자료 형태를 유도한다. 슬롯별 hard-validation 은 없다.
- CLI: study_lib.cli 의 _generate-free 경로(env LOCAL_LLM_FREE=1)로 갈 때 사용.
  llm 은 'complete_text' 가 아니라 LLMClient(complete) 로, json_object=False 로 호출.
"""
from __future__ import annotations

from pathlib import Path

from .registry import DRAFT

_FRONT = (
    "---\n"
    "title: {title}\n"
    "subject: {subject}\n"
    "book: {book}\n"
    "section: {section}\n"
    "generator: local-free\n"
    "---\n\n"
)

import os

# 언어에 따른 프롬프트 조각. LOCAL_FREE_LANG=ko 이면 한글로, 기본은 영어.
_LANG = {
    "en": {
        "system": (
            "You are an expert math tutor writing a self-contained study note for ONE topic.\n"
            "The note must actually TEACH: a reader should be able to redo every step.\n\n"
            "STRUCTURE (in this order):\n"
            "1. ## Reading the Topic -- a flowing, readable explanation: what the idea is for, "
            "   the intuition, definitions, why each key formula holds, and the one common mistake "
            "   students make. Ground it in the textbook passages.\n"
            "2. ## Worked examples -- provide AT LEAST 3, preferably 4-5, of increasing difficulty:\n"
            "   one basic/template case, one typical exam-style case, and one application/word "
            "   problem (create a plausible extension if the passage has no word problem). "
            "   For EACH example include the FULL step-by-step solution headed **Solution.**: "
            "   explain each algebraic/calculus move line by line (what rule is applied and why), "
            "   not just the final answer. State the conclusion explicitly.\n"
            "3. ## Practice problems -- provide AT LEAST 5, ordered by rising difficulty, then "
            "   right below each give a fully worked **Solution**: (steps + final answer, as a "
            "   built-in answer key). Do not leave problems unresolved.\n\n"
            "QUALITY RULES:\n"
            "- Be mathematically correct. Never state a false step; if a step relies on a theorem, "
            "   say so (e.g. 'f is continuous/positive/decreasing on [1,oo), so Integral Test applies').\n"
            "- Cite the textbook source inline like (textbook EXAMPLE 3) or (11.3 Exercises #7); "
            "   create a labelled extension only when the passage lacks the needed item.\n"
            "- Markdown only. Wrap every math expression in $...$ (display: $$...$$). One short "
            "   symbol inline, never leave bare math outside $."
        ),
        "user": (
            "TOPIC: {topic}\n"
            "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
            "Textbook source passage (consult as needed):\n"
            "{passages}\n\n"
            "Write this topic's study note in English now (markdown body only, no YAML header). "
            "Keep the Reading concise but make the WORKED EXAMPLES (>=3) and PRACTICE with full "
            "solutions (>=5) the strongest part. Do not truncate -- finish every solution."
        ),
        "cite": "textbook EXAMPLE 3",
    },
    "ko": {
        "system": (
            "You are an expert math tutor writing a self-contained study note for ONE topic.\n"
            "The note must actually TEACH: a reader should be able to redo every step.\n\n"
            "STRUCTURE (in this order):\n"
            "1. ## Reading the Topic -- a flowing, readable explanation: what the idea is for, "
            "   the intuition, definitions, why each key formula holds, and the one common mistake "
            "   students make. Ground it in the textbook passages.\n"
            "2. ## Worked examples -- provide AT LEAST 3, preferably 4-5, of increasing difficulty:\n"
            "   one basic/template case, one typical exam-style case, and one application/word "
            "   problem (create a plausible extension if the passage has no word problem). "
            "   For EACH example include the FULL step-by-step solution headed **Solution.**: "
            "   explain each algebraic/calculus move line by line (what rule is applied and why), "
            "   not just the final answer. State the conclusion explicitly.\n"
            "3. ## Practice problems -- provide AT LEAST 5, ordered by rising difficulty, then "
            "   right below each give a fully worked **Solution**: (steps + final answer, as a "
            "   built-in answer key). Do not leave problems unresolved.\n\n"
            "QUALITY RULES:\n"
            "- Be mathematically correct. Never state a false step; if a step relies on a theorem, "
            "   say so (e.g. 'f is continuous/positive/decreasing on [1,oo), so Integral Test applies').\n"
            "- Cite the textbook source inline like (textbook EXAMPLE 3) or (11.3 Exercises #7); "
            "   create a labelled extension only when the passage lacks the needed item.\n"
            "- Markdown only. Wrap every math expression in $...$ (display: $$...$$). One short "
            "   symbol inline, never leave bare math outside $."
        ),
        "user": (
            "TOPIC: {topic}\n"
            "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
            "Textbook source passage (consult as needed):\n"
            "{passages}\n\n"
            "Write this topic's study note, presenting the body text in Korean. "
            "Keep Reading concise but make WORKED EXAMPLES (>=3) and PRACTICE with full "
            "solutions (>=5) the strongest part. Do not truncate -- finish every solution."
        ),
        "cite": "textbook EXAMPLE 3",
    },
}


def _free_lang() -> str:
    lang = os.environ.get("LOCAL_FREE_LANG", "en").strip().lower()
    return lang if lang in _LANG else "en"


def _prompts() -> dict:
    return _LANG[_free_lang()]


def system_prompt() -> str:
    return _prompts()["system"]


def build_user(topic, passages) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return _prompts()["user"].format(
        topic=topic.title or topic.topic_id,
        book=topic.book_id, section=topic.section or "-",
        subject=topic.subject, passages=src)


def run_free_one(topic, llm, passages, notes_dir: str | Path) -> str:
    """topic 의 자유 md 학습자료를 notes_dir/<topic>.md 로 저장, 경로 반환."""
    try:
        res = llm.complete(system=system_prompt(), user=build_user(topic, passages),
                           max_tokens=16000, json_object=False)
    except Exception as exc:  # noqa: BLE001
        raise exc
    body = res.content if isinstance(res.content, str) else str(res.content)
    front = _FRONT.format(title=topic.title or topic.topic_id, subject=topic.subject,
                          book=topic.book_id, section=topic.section or "-")
    p = Path(notes_dir) / f"{topic.topic_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(front + (body or "").strip() + "\n", encoding="utf-8")
    return str(p)


# ---- 부분(개념 / 예제 / 연습·풀이) 분할 생성 --------------------------------
# 단일 슬롯 llama-server 를 순차로 돌며, 커다란 한 파일이 토큰 한도(16000)에서
# 잘리는 것을 피한다. 각 부분을 자기 파일로 저장 후 병합된 notes/<topic>.md 를 만든다.
_HEADER = (
    "---\n"
    "title: {title}\n"
    "subject: {subject}\n"
    "book: {book}\n"
    "section: {section}\n"
    "part: {part}\n"
    "generator: local-free\n"
    "---\n\n"
)

# PART_ORDER 순서로 병합한다.
PART_ORDER = ("concept", "examples", "practice")

# ---- 공통 최상 품질 표준(DeepSeek-R1-Distill-Qwen-32B 등 강 추론 모델용) ----
# 아래 _STD 는 모든 부분 앞에 붙는 원칙이다. 본문 언어는 _LANGUAGE_KICK 으로 정한다
# (지시 자체는 영어로 써도 강 모델은 잘 따른다). 목표: 기술적으로 틀림 없고, 왜 그
# 단계인지 설명하며, 예제·문제가 '진짜 훈련'이 되는 약 14k 토큰급 교재.
_STD = (
    "You are a rigorous university math tutor writing material that a motivated student "
    "can study WITHOUT the textbook open. Every claim must be checkable from your words "
    "and the cited passage; when a theorem is used you must state and verify its "
    "HYPOTHESES (e.g. 'f is continuous on [1,∞), positive, and decreasing' BEFORE applying "
    "the Integral Test).\n"
    "Language: prose should flow like a good lecturer -- never a bullet dump -- yet every "
    "logical step must be explicit.\n"
    "Math: write ALL mathematics in LaTeX inside $...$ (inline) or $$...$$ (display). Do "
    "not leave a bare symbol outside dollars. Use \\sum, \\int, \\frac, \\lim, \\sqrt "
    "properly; no plain-text math.\n"
    "Notation must be introduced before use and reused consistently. State the result at "
    "the end of each worked item in a boxed/emphasised form (e.g. '**Answer.** $S=...$').\n"
    "Ground in the given passages: quote or paraphrase, and cite inline the way the "
    "passage labels items -- e.g. (textbook EXAMPLE 3), (11.3 Exercises #7) -- or mark "
    "genuinely new items as (extension).\n"
    "Never write anything mathematically false. If you are not fully certain of a numeric "
    "fact, do the arithmetic carefully in the working and show it.\n"
)

# 부분별 작업 지시. 평문은 부분 '만' 출력한다.
_PARTS = {
    "concept": (
        "Write ONLY the concept/lecture part of the study note: a section titled "
        "'## Reading the Topic'.\n"
        "Give a connected lecture (several paragraphs, not a box list) that:\n"
        "1. MOTIVATES: the concrete question the idea answers and why a student should care.\n"
        "2. BUILDS INTUITION first, then gives the precise definition(s) and theorem(s) with "
        "   the exact hypotheses and what each hypothesis is FOR.\n"
        "3. Justifies every key formula: either a short derivation or a clear reason it holds "
        "   (name the ingredient, e.g. 'this is just the limit definition of the integral').\n"
        "4. Shows one worked non-example or the single most common student misconception and "
        "   why it is wrong.\n"
        "5. Tells the reader 'what to check' before using the tool (a compact procedure box).\n"
        "No solved practice items go here; that is a separate part. End with a short "
        "'You are ready when...' self-check list."
    ),
    "examples": (
        "Write ONLY the worked-examples part: a section titled '## Worked examples'.\n"
        "Provide 4-6 examples of clear increasing difficulty and variety:\n"
        "  (i) one template/basic case, "
        "(ii) one typical exam-style case with a trap or a common wrong turn, "
        "(iii) one case requiring combining two techniques, and "
        "(iv) one application / word problem (invent a plausible labelled extension only if "
        "the passage provides no such problem).\n"
        "Format each example as:\n"
        "  ### Example N (source tag)\n"
        "  **Problem.** precise statement in one or two sentences.\n"
        "  **Solution.** a complete, line-by-line derivation: name each rule/step as you use "
        "it (e.g. 'substitute u=...', 'compare with p-series p=3/2>1'), verify any theorem "
        "hypotheses explicitly, do the algebra/integral/limit in displayed steps, then give "
        "the emphasised **Answer.** -- nothing left to the reader's imagination.\n"
        "Add ONE short 'why this step matters' remark to at least two examples to teach "
        "technique, not just answers."
    ),
    "practice": (
        "Write ONLY the practice-problems part: a section titled '## Practice problems'\n"
        "The problems are a graduated training set with a BUILT-IN FULL ANSWER KEY (this "
        "note doubles as the solution manual):\n"
        "Provide 6-10 problems ordered from routine to challenging. Across the set, cover "
        "every technique/formula introduced for this topic, and label each problem's "
        "difficulty '[basic]', '[standard]', '[challenge]' and its skill (e.g. computational / "
        "conceptual / proof / application).\n"
        "Format each as:\n"
        "  ### Problem N (difficulty · skill, source tag)\n"
        "  **Problem.** precise wording, no ambiguity about what is asked.\n"
        "  (leave a short blank '**Work area.**' line)\n"
        "  **Solution.** the complete worked answer: state the method, verify hypotheses, "
        "show every step in displayed math, and end with the final **Answer.**.\n"
        "Make the [challenge] items genuinely require care (a proof, a counterexample, or a "
        "multi-step synthesis) -- not just bigger numbers. Do not strand any problem without "
        "its solved answer."
    ),
}

# 유저 프롬프트 언어 지시: 본문 해설 언어만 결정 (en/ko).
_LANGUAGE_KICK = {
    "en": (
        "Present all prose, explanations, and solutions in English. Output exactly the "
        "requested part as clean markdown body only (no YAML header). Do not truncate -- "
        "finish every example and every solution completely before stopping."
    ),
    "ko": (
        "Present all prose, explanations, and solutions in Korean (mathematical symbols and "
        "LaTeX stay as-is). Output exactly the requested part as clean markdown body only "
        "(no YAML header). Do not truncate -- finish every example and every solution "
        "completely before stopping."
    ),
}

# 각 부분의 최대 생성 토큰. 세 부분 합계 ≈ 3000+5500+6500 ≈ 15k 토큰(강 모델·정성용).
_PART_MAX = {"concept": 4000, "examples": 7000, "practice": 8000}


def _part_user(topic, passages, lang: str, part: str) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return (
        "TOPIC: {topic}\n"
        "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
        "Textbook source passage (consult as needed):\n"
        "{passages}\n\n"
        "{kick}"
    ).format(
        topic=topic.title or topic.topic_id,
        book=topic.book_id, section=topic.section or "-",
        subject=topic.subject, passages=src, kick=_LANGUAGE_KICK[lang])


def _part_system(part: str) -> str:
    """부분 시스템 프롬프트 = 공통 품질 표준 + 해당 부분 작업 지시."""
    return _STD + "\n" + _PARTS[part]


def _read_part_file(base: Path, topic_id: str, part: str) -> str | None:
    pf = base / f"{topic_id}.{part}.md"
    try:
        txt = pf.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return txt.split("---\n\n", 2)[-1].rstrip()  # front matter 뒤 본문


def run_free_parts(topic, llm, passages, notes_dir: str | Path,
                   parts=PART_ORDER, *, return_combined: bool = True) -> str:
    """topic 에 대해 일부(기본 전체) 부분을 생성·저장하고 병합본을 쓴다.

    parts 에 없는 나머지 부분은 이미 디스크에 있는 <topic>.<part>.md 본문을
    재사용해 병합에 포함한다(있을 때만). 단일 슬롯 llama-server 를 순차 사용.
    병합본 notes/<topic>.md 경로를 반환한다.
    """
    lang = _free_lang()
    topic_vars = dict(title=topic.title or topic.topic_id, subject=topic.subject,
                      book=topic.book_id, section=topic.section or "-")
    base = Path(notes_dir)
    base.mkdir(parents=True, exist_ok=True)

    part_bodies: dict[str, str] = {}
    # 새로 생성할 부분
    for part in parts:
        sysp = _part_system(part)
        usrp = _part_user(topic, passages, lang, part)
        res = llm.complete(system=sysp, user=usrp,
                           max_tokens=_PART_MAX.get(part, 8000),
                           json_object=False)
        body = res.content if isinstance(res.content, str) else str(res.content)
        body = (body or "").strip()
        part_bodies[part] = body
        pf = Path(base) / f"{topic.topic_id}.{part}.md"
        pf.write_text(_HEADER.format(part=part, **topic_vars) + body + "\n",
                      encoding="utf-8")
    # 생성하지 않은 나머지 부분: 디스크에 있으면 병합에 재사용
    for part in PART_ORDER:
        if part not in part_bodies:
            existing = _read_part_file(base, topic.topic_id, part)
            if existing:
                part_bodies[part] = existing

    combined = (base / f"{topic.topic_id}.md")
    body_chunks = [part_bodies[p] for p in PART_ORDER if part_bodies.get(p)]
    combined.write_text(_FRONT.format(**topic_vars) + "\n\n".join(body_chunks)
                        + "\n", encoding="utf-8")
    return str(combined)

