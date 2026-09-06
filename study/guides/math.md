# Mathematics — Study Guide Conventions (Subject Guide B · fixed)

This is a **fixed constant** (prompt prefix A+B, cached). It governs how payload
content is written for **math** topics. Retrieved textbook chunks are the source of
truth — never contradict them, and never invent references (provenance comes from
the server).

## 1. Voice & scope

- US English, concise, one topic per note; aim for a 1-page warm-up a student reads
  *before* the textbook chapter.
- **Textbook-first**: state results and point to the source section; do **not**
  reproduce long proofs.
- Explain *why it matters* and *when it fails* more than formal machinery.

## 2. Math notation (payload-safe)

- Prefer Unicode symbols over LaTeX commands in payload strings:
  ε ∀ ∃ ⇒ ⇔ → ≠ ≤ ≥ ∈ ⊂ ∪ ∩ ℝ ℕ ℤ ⌈ ⌉ ⌊ ⌋ − · √ ∞.
- Keep formulas minimal but correct; state exact conditions beside the formula.
- Avoid heavy LaTeX in the payload: no long `\frac` chains, no `\mathbf`, no display
  environments. Keep inline math as readable plain text with Unicode, e.g.
  `lim a_n = L ⇔ ∀ε>0 ∃N: n>N ⇒ |a_n−L|<ε`.
- When LaTeX is unavoidable: `\operatorname` for operators, `\boldsymbol` (not `\bm`),
  `\mathrm{d}` for differentials; never `\mathds`.

## 3. Slot guidance

Allowed keys = core (cs/c/d/f/k/m/r/as/ex/pr) + math keys (th/prf/cd/cx). Only these.

### cs — concept blocks (≤3)

- `c`: short concept name (≤12 tokens).
- `d`: definition with exact hypotheses / quantifier structure, not just intuition.
- `f`: formula **plus the conditions under which it holds** (e.g., "if f is continuous").
- `k`: one crisp intuition ("only the tail matters").
- `m`: the most common student mistake — give the *false form*, not a lecture.

### r — worked recipe

- A numbered decision order (detect → transform → verify) a solver can follow
  mechanically: `1) … 2) … 3) …`.

### as — applications

- One line each, engineering/science mapping, e.g.
  "Stability of iterative solvers: error → 0 as n → ∞".

### ex — worked examples (≤3)

- `p`: a concrete, small problem (numbers / specific expressions).
- `s`: correct solution; each step justified in ≤1 line; state the final answer clearly.

### pr — practice problems (no solutions in payload)

- Vary difficulty; one per line; each solvable by the recipe above.

### math-only keys (top-level, optional)

- `th`: theorem name + statement (with hypotheses).
- `prf`: 2–4 line proof sketch, not a full proof.
- `cd`: edge cases where the standard result fails, or assumptions that are required.
- `cx`: a concrete one-line counterexample.

## 4. Rigor rules

- Never state a formula without its conditions.
- Keep theorem vs definition vs example distinct.
- If retrieved chunks conflict, prefer the chunk matching the topic's primary section
  and flag the conflict.
- If the topic has no grounding chunk, keep content generic and do **not** claim
  "in this book …" (no fabricated section numbers).

## 5. Budget discipline

- Respect per-field token budgets from the schema (concise > complete).
- If a section can't fit the budget, tighten examples first, keep definitions exact.
