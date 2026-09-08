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

import re
import sys
from pathlib import Path

from .llm import LLMError
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

# ---- 단일 파일 전체 교재 시스템 (run_free_one 용: 3개 부분을 한 호출에) ----
_FULL_SYSTEM = (
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
)


def _full_user(topic, passages) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return (
        "TOPIC: {topic}\n"
        "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
        "Textbook source passage (consult as needed):\n"
        "{passages}\n\n"
        "Write this topic's complete study note in English now (markdown body only, no YAML "
        "header): Reading the Topic, WORKED EXAMPLES (>=3) and PRACTICE with full solutions "
        "(>=5). Do not truncate -- finish every solution."
    ).format(
        topic=topic.title or topic.topic_id,
        book=topic.book_id, section=topic.section or "-",
        subject=topic.subject, passages=src)


def run_free_one(topic, llm, passages, notes_dir: str | Path) -> str:
    """topic 의 자유 md 학습자료를 notes_dir/<topic>.md 로 저장, 경로 반환(영어)."""
    system = _FULL_SYSTEM
    user = _full_user(topic, passages)
    guard_ctx("concept(single)", llm, system, user, 12000)
    try:
        res = llm.complete(system=system, user=user,
                           max_tokens=12000, json_object=False)
    except Exception as exc:  # noqa: BLE001
        raise exc
    body = res.content if isinstance(res.content, str) else str(res.content)
    body = normalize_math_delims((body or "").strip())
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

# ---- 공통 최상 품질 표준(현재 백엔드: Mistral-Small-24B-Instruct) ----
# 아래 _STD 는 모든 부분 앞에 붙는 공통 원칙. 출력 언어는 항상 영어(_EN_KICK).
# 목표: 기술적으로 틀림 없고 왜 그 단계인지 설명하며, 문장이 완전·자연스런 교재.
_STD = (
    "You are a rigorous university math tutor writing material that a motivated student "
    "can study WITHOUT the textbook open. Every claim must be checkable from your words "
    "and the cited passage; when a theorem is used you must state and verify its "
    "HYPOTHESES (e.g. 'f is continuous on [1,∞), positive, and decreasing' BEFORE applying "
    "the Integral Test).\n"
    "Language: write in COMPLETE, grammatical English sentences as in a polished published "
    "textbook -- full articles/connectives, natural spacing around math, no telegraphic "
    "fragments, no dropped operators or words. Never collapse into staccato lists in prose; "
    "reserve bullets only for genuinely separate checks."
    " Every logical step must be explicit and flows from the previous one.\n"
    "Math: use ONLY KaTeX delimiters -- $...$ for inline math and $$...$$ for display "
    "math. Never use \\(...\\), \\[...\\], or dollar-less \\begin{align}/\\begin{equation} "
    "layout; a bare math symbol outside dollars is an error. Use \\sum, \\int, \\frac, "
    "\\lim, \\sqrt properly.\n"
    "Completion & anti-loop: every sentence ends with a period, every worked item finishes "
    "with its **Answer.**, and you must COMPLETE the whole part -- never stop mid-sentence "
    "or mid-derivation, and never repeat/loop the same expression or sentence. Finish every "
    "single item you begin (a partial, stopped, or degenerate-looping answer is a failure).\n"
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

# English-only: 본문 해설/풀이는 항상 영어.
_EN_KICK = (
    "Present all prose, explanations, and solutions in English. Output exactly the "
    "requested part as clean markdown body only (no YAML header). Do not truncate -- "
    "finish every example and every solution completely before stopping."
)

# 각 부분의 per-request 생성 상한(Phi-4 ctx 131072 autoregressive).
# 하나의 complete() = 한 요청. 목록형은 _COUNTED/_SPEC 배치로 다회 누적해 항목을 채운다.
# 128k 여유라 요청당 길게 뽑아도 되지만 목표(examples 5 · practice 20)는 명시 수 유지.
# part 별 per-request 생성 상한. concept 는 서술 narrative(긴 문장 안정)라 크게,
# examples/practice 는 '한 항목'씩 짧게(1200~2500) 해 정확한 수식·proof 를 유지.
_PART_MAX = {"concept": 30000, "examples": 4000, "practice": 4000}

# --- 개수·난이도 목록형 부분을 '여러 요청'으로 쪼개 누적 생성 ---
# 단일 요청에서 R1 은 첫 마커 하나 만들고 완결한다(실측). ctx 가 작아 한 번에 N개
# 전부는 불가 → tier/배치로 쪼개 연속 호출로 누적. 각 part 별 목표 구성:
#   examples : Basic 5
#   practice : Basic 10 + Standard 5 + Challenge 5
_SPEC = {
    "examples": {
        "marker": "### Example",
        "heading": "## Worked examples",
        "kind": "examples",
        "tiers": [("Basic", 5)],
        "counted": (
            "You produce worked examples for this topic. Each item uses the format "
            "'### Example N': give **Problem.** then a complete step-by-step "
            "**Solution.** (name each rule, verify hypotheses) and a final **Answer.**. "
            "Write only the items the USER message requests.\n"),
    },
    "practice": {
        "marker": "### Problem",
        "heading": "## Practice problems",
        "kind": "problems",
        "tiers": [("Basic", 10), ("Standard", 5), ("Challenge", 5)],
        "counted": (
            "You produce practice problems for this topic (built-in full answer key). "
            "Each item uses '### Problem N'. Give **Problem.** (with difficulty/skill "
            "tag), a strict '**Work area.**' line, a full step-by-step **Solution.** and "
            "final **Answer.**. Write ONLY the difficulty tier and count the USER message "
            "asks for; number them continuously (do not restart numbering).\n"),
    },
}
_LIST_PER_SHOT = 1     # 요청당 항목 1 개 — 긴 단일스트림에서 수식 연산자 소실(decay) 방지


def _count_markers(body: str, marker: str) -> int:
    """body 에서 'marker ' 로 시작하는 마커 개수(제목 번호 항목)."""
    return max(0, body.count(marker + " ")) if body else 0


def _clean_list_chunk(chunk: str, marker: str, heading: str) -> str:
    """모델이 헤딩/앞 잡음을 반복 출력하면 떼어낸다. 실제 항목 마커까지 남긴다."""
    c = chunk.strip()
    lines = c.splitlines()
    while lines and lines[0].lstrip().startswith("#"):
        if lines[0].lstrip().startswith(marker):
            break
        lines.pop(0)
    return "\n".join(lines).strip()


def _tier_user(topic, sub_passages, spec, tier_label: str, goal: int,
               start: int) -> str:
    """목록형 부분의 'tier_label 난이도 goal 개, start 번호부터' 생성용 유저."""
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(sub_passages, 1))
    tier_guide = {
        "Basic": "routine, foundational computation directly on the key formulas.",
        "Standard": "typical exam-style; combines two techniques or has a small trap.",
        "Challenge": "harder: a proof, a counterexample, or a multi-step synthesis.",
    }.get(tier_label, tier_label.lower())
    return (
        "TOPIC: {topic}\n"
        "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
        "Textbook source passage (consult as needed):\n"
        "{passages}\n\n"
        "Produce {goal} '{label}' {kind} numbered {start}..{startp} ('{marker} N'). "
        "{label} means: {guide}. Continue numbering from {start} (do not restart). "
        "Output only these items; do not include the '{heading}' heading (it already "
        "exists).\n"
        "{counted}"
    ).format(
        topic=topic.title or topic.topic_id,
        book=topic.book_id, section=topic.section or "-",
        subject=topic.subject, passages=src,
        goal=goal, label=tier_label,
        kind="examples" if spec["marker"].startswith("### Example")
        else "practice problems",
        start=start, startp=start + goal - 1,
        marker=spec["marker"], heading=spec["heading"],
        guide=tier_guide, counted=spec["counted"])


def _part_user(topic, passages, part: str) -> str:
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
        subject=topic.subject, passages=src, kick=_EN_KICK)


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


def normalize_math_delims(text: str) -> str:
    """모델이 \\(...\\)/\\[...\\] 로 낸 수식을 KaTeX $...$/$$...$$ 로 통일한다.

    reasoning 모델(R1)은 달러($)보다 backslash-parenthesis( \\( , \\) ) 형식을 선호해
    KaTeX 렌더 호환을 깨는 경우가 많다(실측 Output). 페어 기반 단순 치환만 하므로
    이미 $ 인 부분은 건드리지 않는다.
    """
    if not text:
        return text
    # display 먼저: \[ ... \] → $$ ... $$
    text = re.sub(r"\\\[\s*(.*?)\s*\\\]", lambda m: "$$" + m.group(1) + "$$",
                  text, flags=re.S)
    # inline: \( ... \) → $ ... $
    text = re.sub(r"\\\(\s*(.*?)\s*\\\)", lambda m: "$" + m.group(1) + "$",
                  text, flags=re.S)
    return text


def _gen_part_body(topic, llm, part, sysp, passages) -> str:
    """부분의 마크다운 본문('##' 헤딩 포함) 생성.

    - concept/기타: 단일 호출(_part_system 로 full 지시).
    - examples/practice: tier 구성을 _SPEC 에 따라 난이도별 배치로 쪼개 연속 호출로
      누적. '## ...' 헤딩은 여기서 한 번만 붙이고, 항목 번호는 tier 를 가로지르며 연속.
    """
    spec = _SPEC.get(part)
    if spec is None:
        guard_ctx(part, llm, sysp, _part_user(topic, passages, part),
                  _PART_MAX.get(part, 8000))
        res = llm.complete(system=sysp, user=_part_user(topic, passages, part),
                           max_tokens=_PART_MAX.get(part, 8000),
                           json_object=False)
        raw = res.content if isinstance(res.content, str) else str(res.content)
        return normalize_math_delims((raw or "").strip())

    marker = spec["marker"]
    cap = _PART_MAX.get(part, 8000)
    counted_sys = _STD + "\n" + spec["counted"]

    chunks: list[str] = [spec["heading"]]
    idx = 0
    for tier, goal in spec["tiers"]:
        need = goal
        stall = 0
        while need > 0 and stall < 3:
            req = min(_LIST_PER_SHOT, need)
            start = idx + 1
            user = _tier_user(topic, passages, spec, tier, req, start)
            guard_ctx(f"{part}:{tier}@{start}", llm, counted_sys, user, cap)
            try:
                res = llm.complete(system=counted_sys, user=user,
                                   max_tokens=cap, json_object=False)
            except Exception as exc:  # noqa: BLE001
                print(f"[free:{part}] FAILED tier={tier} @{start}: {exc}",
                      file=sys.stderr, flush=True)
                stall = 3
                break
            raw = res.content if isinstance(res.content, str) else str(res.content)
            chunk = normalize_math_delims(raw or "").strip()
            got = _count_markers(chunk, marker)
            if got == 0:
                print(f"[free:{part}] tier={tier} @{start} produced 0 items; "
                      f"stop chunk ({len(chunk)} chars)", file=sys.stderr,
                      flush=True)
                stall += 1
                continue
            chunks.append(_clean_list_chunk(chunk, marker, spec["heading"]))
            idx += got
            need -= got
            stall = 0
        # need>0 인 채 나오면 다음 tier 로 넘어가도 되게 경고만
        if need > 0:
            print(f"[free:{part}] tier={tier} short by {need} items (got "
                  f"{goal - need}/{goal})", file=sys.stderr, flush=True)
    return "\n\n".join(chunks) + "\n"


def run_free_parts(topic, llm, passages, notes_dir: str | Path,
                   parts=PART_ORDER, *, return_combined: bool = True) -> str:
    """topic 에 대해 일부(기본 전체) 부분을 생성·저장하고 병합본을 쓴다.

    각 부분은 완료 즉시 자기 파일(<topic>.<part>.md)에 저장한다. 부분 하나가
    실패해도 이후 부분은 계속 진행(장시간 무인 실행용). realtime log 는 들을 수
    있도록 각 부분 완료/실패를 stdout 에 남긴다(log 인자).
    parts 에 없는 나머지는 디스크의 기존 <topic>.<part>.md 를 재사용해 병합에
    포함한다. 전부 실패(재사용할 것도 없음)면 마지막에 LLMError 를 던진다.
    병합본 notes/<topic>.md 경로를 반환한다.
    """
    import sys

    topic_vars = dict(title=topic.title or topic.topic_id, subject=topic.subject,
                      book=topic.book_id, section=topic.section or "-")
    base = Path(notes_dir)
    base.mkdir(parents=True, exist_ok=True)

    part_bodies: dict[str, str] = {}
    generated: list[str] = []
    # 새로 생성할 부분 (부분별로 독립 생성·저장 — 실패해도 나머지는 계속)
    for part in parts:
        sysp = _part_system(part)
        sub = passages_for_part(part, passages)   # 권고안 B: part 전용 passage
        rag_tok = sum(estimate_tokens(p) for p in sub)   # RAG 입력 토큰(추정, ~char/3)
        print(f"[free:{part}] generating {topic.topic_id} "
              f"(passages {len(passages)}→{len(sub)}, rag_in≈{rag_tok}t, "
              f"max_tokens={_PART_MAX.get(part, 8000)})...", flush=True)
        try:
            body = _gen_part_body(topic, llm, part, sysp, sub)
        except Exception as exc:  # noqa: BLE001
            print(f"[free:{part}] FAILED {topic.topic_id}: {exc}",
                  file=sys.stderr, flush=True)
            continue
        # 품질 가드: 헤딩("## ...") 없는 body(= think 누출)는 실패 처리
        if "## " not in body:
            print(f"[free:{part}] REJECTED {topic.topic_id}: body has no "
                  f"'## ' section heading ({len(body)} chars) — looks like "
                  f"reasoning-leak, not saved", file=sys.stderr, flush=True)
            continue
        part_bodies[part] = body
        generated.append(part)
        pf = Path(base) / f"{topic.topic_id}.{part}.md"
        pf.write_text(_HEADER.format(part=part, **topic_vars) + body + "\n",
                      encoding="utf-8")
        print(f"[free:{part}] done -> {pf.name} ({len(body)} chars, "
              f"{_count_markers(body, _SPEC[part]['marker']) if part in _SPEC else '-'} items)",
              flush=True)
    # 생성하지 않은 나머지 부분: 디스크에 있으면 병합에 재사용
    for part in PART_ORDER:
        if part not in part_bodies:
            existing = _read_part_file(base, topic.topic_id, part)
            if existing:
                part_bodies[part] = existing

    body_chunks = [part_bodies[p] for p in PART_ORDER if part_bodies.get(p)]
    if not body_chunks:
        raise LLMError(
            f"all requested parts failed for {topic.topic_id} and no part file "
            "exists on disk to reuse")
    combined = (base / f"{topic.topic_id}.md")
    combined.write_text(_FRONT.format(**topic_vars) + "\n\n".join(body_chunks)
                        + "\n", encoding="utf-8")
    ok_parts = [p for p in PART_ORDER if p in part_bodies]
    print(f"[free] merged {topic.topic_id}: ok_parts={ok_parts} "
          f"-> {combined.name}", flush=True)
    return str(combined)


# ---- 문맥 예산 (Phi-4-mini-reasoning / llama-server, ctx 131072=128k 운영) ----
# autoregressive. model native 최대 131072 까지이므로 큰 교재 생성 시 여백 크게.
# 한 요청은 prompt+생성 합 ≤ CTX_LIMIT. passage 예산 = CTX - 스캐폴드 - 생성예비.
CTX_LIMIT = 131072
MAX_INPUT = CTX_LIMIT
HARD_CTX = MAX_INPUT
_SCAFFOLD_EST = 1536           # 시스템 헤더+토픽
_MIN_GEN_RESERVE = 20000       # 최소 생성 예비 (긴 해설용 — 128k 여유 기준)
DEFAULT_INPUT_TOKENS = 24000   # passage 기본(필요 시 env LOCAL_FREE_INPUT_TOKENS 상향)


def input_budget() -> int:
    """한 요청에 실을 passage 입력 예산.

    autoregressive llama: prompt + 생성 합이 CTX_LIMIT(131072) 안이어야 하므로
    passage 상한 = CTX_LIMIT - _SCAFFOLD_EST - _MIN_GEN_RESERVE. 기본/기본env 는
    DEFAULT_INPUT_TOKENS 이며 넘지 않는 선에서 LOCAL_FREE_INPUT_TOKENS 로 조정.
    """
    import os
    try:
        want = int(os.environ.get("LOCAL_FREE_INPUT_TOKENS",
                                  DEFAULT_INPUT_TOKENS))
    except ValueError:
        want = DEFAULT_INPUT_TOKENS
    cap = MAX_INPUT - _SCAFFOLD_EST - _MIN_GEN_RESERVE
    return max(200, min(want, cap))


def guard_ctx(part: str, llm, system: str, user: str, outmax: int) -> None:
    """권고안 P6: 시스템+user+생성분 합이 CTX_LIMIT 넘으면 요청 전에 거부.

    llm 에 count_tokens 가 없으면(테스트 더미 등) 건너뛴다."""
    counter = getattr(llm, "count_tokens", None)
    if counter is None:
        return
    total = counter(system) + counter(user) + outmax
    if total > CTX_LIMIT:
        raise LLMError(
            f"ctx overrun part={part}: sys+user+{outmax} ~= {total} > "
            f"{CTX_LIMIT}; raise LOCAL_FREE_INPUT_TOKENS later won't help — "
            "split further or shrink passage/output.")


def estimate_tokens(text: str) -> int:
    """토크나이저 없을 때의 문자 근사(한글 혼합 3자 당 ~1토큰)."""
    return max(1, (len(text) + 2) // 3)


def pack_passages(passages, max_tokens: int | None = None,
                  *, count_tokens=None) -> list[str]:
    """관련성 우선의 passages 를 입력 토큰 예산 내 최장 접두부로 자른다.

    - max_tokens: 주어지면 이 값 사용, 아니면 input_budget().
    - count_tokens: (str)->int; 없으면 estimate_tokens 근사.
    """
    if max_tokens is None:
        max_tokens = input_budget()
    if count_tokens is None:
        count_tokens = estimate_tokens
    # 긴 passage 하나가 예산을 넘는 최악을 위해 시스템/유저 고정 오버헤드는
    # 여기서 여유로 보지 않고 estimate 의 안전폭으로 다룬다.
    used = 0
    packed: list[str] = []
    for p in passages:
        n = count_tokens(p)
        if used + n > max_tokens:
            if not packed:          # 첫 passage 조차 넘침 → 강제 1개 포함
                packed.append(p)
            break
        packed.append(p)
        used += n
    return packed


# ---- 부분별 passage 전문화(권고안 B) ---------------------------------------
# 세 부분이 같은 passages 전체를 그대로 반복해 보내지 않도록, 각 part 에 어울리는
# passage 를 우선 뽑아 그 부분에만 쓴다(전체 예산보다 작게). passage 텍스트에
# 예/연습/정의 등의 표식이 있으면 그 표식에 맞는 조각을 높은 우선순위로 선택하고,
# 부족하면 앞쪽(primary) passage 로 채워 grounding 을 유지한다.
_PART_KEYWORDS = {
    "concept": ["definition", "define", "theorem", "정의", "정리", "introduction",
                "continuous", "increasing", "decreasing", "property"],
    "examples": ["example", "sample", "solution", "예제", "예 ", "worked"],
    "practice": ["exercise", "problem", "practice", "연습", "문제", "#7", "#24"],
}
_DEFAULT_PART_TOKENS = 10000     # 각 part passage 예산 (128k 대비)


def _part_score(part: str, text: str) -> int:
    low = text.lower()
    return sum(low.count(k.lower()) for k in _PART_KEYWORDS.get(part, []))


def part_token_cap() -> int:
    """각 part 의 passage 예산(기본 2048, env LOCAL_FREE_PART_TOKENS 로 조정). 전역
    input_budget() 을 초과하지 않도록 상한도 함께 적용한다."""
    import os
    try:
        want = int(os.environ.get("LOCAL_FREE_PART_TOKENS",
                                  _DEFAULT_PART_TOKENS))
    except ValueError:
        want = _DEFAULT_PART_TOKENS
    return max(256, min(want, input_budget()))


def passages_for_part(part: str, passages, *, max_tokens: int | None = None,
                      count_tokens=None):
    """part 에 어울리는 passage 부분집합 (예산 내, 최소한의 grounding 유지)."""
    if not passages:
        return []
    if max_tokens is None:
        max_tokens = part_token_cap()
    if count_tokens is None:
        count_tokens = estimate_tokens
    # 맨 앞 'primary' 일부는 항상 유지 (모든 part 의 공통 근거)
    core_n = max(1, min(len(passages), max(2, len(passages) // 5)))
    core = passages[:core_n]
    # 나머지를 part 키워드 점수에 따라 내림차순 정렬 (동률은 원순서)
    rest = list(enumerate(passages[core_n:], start=core_n))
    rest_sorted = sorted(rest, key=lambda it: (-_part_score(part, it[1]), it[0]))
    ordered = list(core) + [p for _, p in rest_sorted]
    return pack_passages(ordered, max_tokens, count_tokens=count_tokens)

