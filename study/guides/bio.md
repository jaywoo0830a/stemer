# Biology — Study Guide Conventions (Subject Guide B · fixed)

This is a **fixed constant** (prompt prefix A+B, cached). It governs how payload
content is written for **biology** topics. Retrieved textbook chunks are the
source of truth — never invent processes, numbers, or structures.

## 1. Voice & scope

- US English, concise, one topic per note; a 1-page warm-up read before the chapter.
- For every structure or process state **where it occurs** (compartment, tissue,
  organism) and its **function**.
- Textbook-first: point to the source section; keep mechanisms at overview depth.

## 2. Biology notation (payload-safe)

- Plain names plus standard abbreviations on first use, e.g. `ATP (adenosine
  triphosphate)`, `NADH`, `DNA`.
- Consistently use either common or binomial names; give the binomial once if useful.
- Keep payload as readable plain text; avoid heavy LaTeX.

## 3. Slot guidance

Allowed keys = core (cs/c/d/f/k/m/r/as/ex/pr) + biology keys
(path/cmp/cyc/tree/org/exp).

### cs — concept blocks (≤3)

- `c`: short concept name (≤12 tokens).
- `d`: definition including location/scope (e.g., "cellular respiration = the
  ATP-yielding breakdown of glucose in the cytoplasm and mitochondria").
- `f`: use for a quantitative relationship or net equation (e.g., respiration
  summary equation); otherwise omit `f` rather than forcing a formula.
- `k`: one crisp intuition ("structure fits function").
- `m`: the most common student mistake — confusing sister concepts, wrong
  compartment, off-by-one in cycle steps.

### bio-only keys (top-level)

- `path`: ordered process/pathway steps (`1) … 2) …`) with compartment and the key
  enzyme/step where relevant.
- `cmp`: comparison rows `[feature, A, B]` — rendered server-side as a table.
- `cyc`: cyclic process (e.g., Krebs) as stages with what enters and leaves each.
- `tree`: clades/relationships one line each (e.g., "all mammals share a common
  ancestor; monotremes split first").
- `org`: structure/organ with location + function.
- `exp`: experimental design — hypothesis, control, variables, predicted outcome
  (concise).

### r — worked recipe

- A numbered decision order for solving the topic's typical task (identify →
  compare → conclude).

### as — applications

- One line each (medicine, ecology, biotech) mapping.

### ex / pr

- Worked examples state the organism/context and expected outcome; practice problems
  are answerable from the note's path/cmp content.

## 4. Rigor rules

- Keep **structure vs function** explicit.
- Don't invent quantitative values (rates, counts) — prefer retrieved-chunk values;
  otherwise stay qualitative and say so.
- Distinguish homologous vs analogous, and never conflate two pathways.
- If the topic lacks grounding, keep claims generic and do not fabricate specifics.

## 5. Budget discipline

- Respect per-field token budgets from the schema.
- When tight, keep the pathway/order exact and trim prose examples.
