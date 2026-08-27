# Study note conventions (canonical)

Purpose: warm-up notes read BEFORE the original textbook. The textbook is the source of truth.

## Language & scope

- English only (US English).
- One topic per note, about one page (~400-900 words). No deep proofs.

## Textbook-first rule

- Every definition, theorem and notation must match the textbook exactly.
- Do not derive results beyond the textbook. Always link to the textbook
  chapter/section (e.g. "see §3.5").
- Section numbers must be accurate. Take them from the provided textbook
  excerpts only — never guess.

## Math rendering (KaTeX only)

- Allowed environments: aligned, cases, matrix, pmatrix, bmatrix, vmatrix,
  array, alignedat, smallmatrix.
- Forbidden: align, equation, eqnarray, gather, split, proof, theorem, and any
  other numbered/proof environment — never use them.
- Use \operatorname and \boldsymbol (NOT \bm), \mathrm{d} for differentials;
  never \mathds.
- Display math only inside $$ ... $$. Inline math inside $ ... $.

## Figures (optional)

- Follow study/figures/plot_style.py conventions (Okabe-Ito palette, CM
  mathtext, SVG+PNG via save_fig(), fixed rng seed).

## Status lifecycle

draft -> review -> done. "done" requires human verification against the textbook.

## Format

Follow templates/warmup.md.
