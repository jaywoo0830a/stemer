# Chemistry — Study Guide Conventions (Subject Guide B · fixed)

This is a **fixed constant** (prompt prefix A+B, cached). It governs how payload
content is written for **chemistry** topics. Retrieved textbook chunks are the
source of truth — never contradict them, never invent reaction data or constants.

## 1. Voice & scope

- US English, concise, one topic per note; a 1-page warm-up read before the chapter.
- Always state the **phase / solution context** (aqueous, gas, temperature) a
  reaction or law refers to.
- Textbook-first: point to the source section; keep derivations out.

## 2. Chemistry notation (payload-safe)

- Formulas: `H2O`, `CO2`, `NH3`; ions with superscript charge `Ca2+`, `Cl−`,
  `SO4 2−`; use Unicode − and superscript forms when practical.
- Reaction arrows: `→` (complete), `⇌` (equilibrium). Phases: `(s)(l)(g)(aq)`.
- Conditions: use °C, atm/kPa, catalyst names in words.
- Keep payload as readable plain text with Unicode; avoid heavy LaTeX.

## 3. Slot guidance

Allowed keys = core (cs/c/d/f/k/m/r/as/ex/pr) + chemistry keys
(eq/cond/mech/spec/trend/ox).

### cs — concept blocks (≤3)

- `c`: short concept name (≤12 tokens).
- `d`: definition with the species/state context (e.g., "acid = proton donor in
  aqueous solution").
- `f`: equation or quantitative law + the conditions under which it holds (T, P,
  ideal assumptions).
- `k`: one crisp intuition ("equilibrium is dynamic, not static").
- `m`: the most common student mistake — wrong stoichiometry, missing phase,
  forgetting units (state the *false* habit).

### chem-only keys (top-level)

- `eq`: **balanced reaction equations** (atoms and charge), one per item, phases
  optional but consistent.
- `cond`: reaction conditions in one line — catalyst, temperature, pressure, solvent.
- `mech`: mechanism as ordered steps; name intermediates; describe electron flow in
  words (no drawn arrows in payload).
- `spec`: chemical species of interest with formula and role (reactant/product/
  catalyst/intermediate).
- `trend`: periodic trend rules (across / down) with a one-line why.
- `ox`: oxidation states per element or the redox pairs involved.

### r — worked recipe

- A numbered decision order, e.g. `1) Balance 2) Convert to moles 3) Find limiting
  reagent 4) Convert to asked quantity`.

### as — applications

- One line each, industrial/environmental/lab mapping.

### ex / pr

- Worked examples carry balanced equations and units; practice problems are solvable
  by the recipe and give a balanced-equation starting point when needed.

## 4. Rigor rules

- Every reaction equation must be **balanced in atoms and charge**.
- Distinguish `→` from `⇌`; state whether equilibrium is implied.
- Never invent ΔH, K, or numeric data — use retrieved-chunk values; prefer SI and
  consistent units.
- State temperature/pressure when they affect the outcome.

## 5. Budget discipline

- Respect per-field token budgets from the schema.
- When tight, keep equations exact and drop prose; never drop phases silently.
