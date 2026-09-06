# Physics — Study Guide Conventions (Subject Guide B · fixed)

This is a **fixed constant** (prompt prefix A+B, cached). It governs how payload
content is written for **physics** topics. Retrieved textbook chunks are the source
of truth — never contradict them, and never invent constants or references.

## 1. Voice & scope

- US English, concise, one topic per note; a 1-page warm-up read before the chapter.
- State **which physical situation** the model/equation applies to (object, frame,
  idealizations).
- Show the physics reasoning path (model → equation → solve → sanity-check).

## 2. Physics notation (payload-safe)

- Unicode: ⋅ × → ≈ ± ∞ Δ; Greek letters ε μ λ ν ρ ω θ φ as needed.
- SI units with symbols (m, s, kg, N, J, W, C, V, A, T). **Every number carries its
  unit.**
- Mark vector vs scalar quantities; state the chosen basis/axes/sign convention once.
- Prefer Unicode and plain text; avoid heavy LaTeX in payload strings.

## 3. Slot guidance

Allowed keys = core (cs/c/d/f/k/m/r/as/ex/pr) + physics keys (law/var/case/setup/sign).

### cs — concept blocks (≤3)

- `c`: short concept name (≤12 tokens).
- `d`: definition of the quantity / situation (with the model's idealizations).
- `f`: equation + the situation where it is valid (e.g., "constant acceleration").
- `k`: one crisp intuition ("energy is the capacity to do work").
- `m`: the most common mistake — sign errors, wrong frame, dropped unit, ignored
  condition (state the *false* habit).

### var — key quantities (top-level)

- Rows `[symbol, meaning, unit]` rendered server-side as a table. Keep symbols ≤3
  characters; SI units only.

### law — laws (top-level)

- `name + equation + when it applies` (one compact statement).

### case — limiting/approximate cases (top-level)

- Small-angle, low-speed, ideal gas, neglect of air resistance, etc. — each with the
  regime where it is valid.

### setup — physical scenario (top-level)

- One line describing the geometry/system (e.g., "mass m on an incline at angle θ").

### sign — sign convention (top-level)

- State the convention used so results are unambiguous (e.g., "upward is +y").

### r / as / ex / pr

- Same roles as math, but **numbers carry units** and each worked solution ends with
  a unit-check or order-of-magnitude sanity check.

## 4. Rigor rules

- **Dimensional consistency**: every equation must balance units; flag when it does not.
- Vector/scalar: label vector quantities; choose and state the axes once.
- State approximations plus their validity regime.
- Do not fabricate constants — use values from the retrieved textbook chunk; prefer SI.

## 5. Budget discipline

- Respect per-field token budgets from the schema.
- When tight, keep the equation and its conditions exact; shorten prose and examples.
