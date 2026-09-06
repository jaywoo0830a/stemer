---
title: Exact ODEs. Integrating Factors
subject: math
kind: exam
book: 공학수학
section: 1.4
---

# Exact ODEs. Integrating Factors

## Concepts

### Exact ODE

**Definition.** M(x,y)dx + N(x,y)dy = 0 is exact if there exists u(x,y) with ∂u/∂x = M, ∂u/∂y = N. If M,N have continuous first partials in a simply connected region, exactness ⇔ ∂M/∂y = ∂N/∂x.

**Formula.** ∂M/∂y = ∂N/∂x (necessary and sufficient under continuity).

**Intuition.** The ODE is a total differential of some u; solution is u = c.

**Common mistake.** Checking only ∂M/∂y = ∂N/∂x without verifying continuity, or forgetting the constant when integrating.

### Integrating factor

**Definition.** If M dx + N dy = 0 is not exact, a function F(x,y) such that FM dx + FN dy = 0 is exact is an integrating factor.

**Formula.** If R = (∂M/∂y − ∂N/∂x)/N depends only on x, then F(x) = exp(∫R dx). If R* = (∂N/∂x − ∂M/∂y)/M depends only on y, then F*(y) = exp(∫R* dy).

**Intuition.** Multiply to force exactness; then solve as exact.

**Common mistake.** Using the wrong formula for R or R*, or applying Theorem 1 when R depends on y.
## Worked recipe

1) Write ODE as M dx + N dy = 0. 2) Check exactness: ∂M/∂y = ∂N/∂x? If yes, go to step 5. 3) If not, compute R = (∂M/∂y − ∂N/∂x)/N. If R depends only on x, find F(x) = exp(∫R dx). 4) Else compute R* = (∂N/∂x − ∂M/∂y)/M. If R* depends only on y, find F*(y) = exp(∫R* dy). Multiply ODE by F. 5) Find u by integrating M dx (or N dy) and adjusting with a function of the other variable. 6) General solution: u(x,y) = c. 7) If initial condition given, solve for c.
## Applications

- Fluid flow: exact differentials represent potential functions; solution curves are streamlines.
- Thermodynamics: integrating factors convert inexact differentials (heat) to exact (entropy).
- Economics: exact equations model conservation laws; integrating factors restore balance.
## Worked examples

### Worked example 1

Solve (cos y sinh x + 1) dx − sin y cosh x dy = 0, y(1) = 2.

**Solution.** M = cos y sinh x + 1, N = −sin y cosh x. ∂M/∂y = −sin y sinh x, ∂N/∂x = −sin y sinh x ⇒ exact. Integrate M w.r.t. x: u = cos y cosh x + x + g(y). ∂u/∂y = −sin y cosh x + g'(y) = N ⇒ g'(y)=0 ⇒ g=c. General: cos y cosh x + x = c. y(1)=2 ⇒ cos 2 cosh 1 + 1 = c ≈ 0.358. Answer: cos y cosh x + x = 0.358.

### Worked example 2

Solve (eˣ⁺ʸ + y eʸ) dx + (x eʸ − 1) dy = 0, y(0) = −1.

**Solution.** M = eˣ⁺ʸ + y eʸ, N = x eʸ − 1. ∂M/∂y = eˣ⁺ʸ + eʸ + y eʸ, ∂N/∂x = eʸ ⇒ not exact. R* = (∂N/∂x − ∂M/∂y)/M = (eʸ − eˣ⁺ʸ − eʸ − y eʸ)/(eˣ⁺ʸ + y eʸ) = −1. F*(y) = exp(∫−1 dy) = e⁻ʸ. Multiply: (eˣ + y) dx + (x − e⁻ʸ) dy = 0. Now exact. Integrate M: u = eˣ + xy + g(y). ∂u/∂y = x + g'(y) = x − e⁻ʸ ⇒ g' = −e⁻ʸ ⇒ g = e⁻ʸ. General: eˣ + xy + e⁻ʸ = c. y(0)=−1 ⇒ 1 + 0 + e = c ⇒ c = 1+e. Answer: eˣ + xy + e⁻ʸ = 1+e.
## Practice problems

1. Solve (2xy + y²) dx + (x² + 2xy) dy = 0.
2. Find an integrating factor and solve (3x²y) dx + x³ dy = 0.
3. Solve (eˣ sin y + tan y) dx + (eˣ cos y + x sec² y) dy = 0.
4. Solve (y² + 2xy) dx − x² dy = 0 using an integrating factor.
## Theorems

Theorem 1 (Integrating Factor F(x)): If R = (∂M/∂y − ∂N/∂x)/N depends only on x, then F(x) = exp(∫R dx) is an integrating factor. Theorem 2 (Integrating Factor F*(y)): If R* = (∂N/∂x − ∂M/∂y)/M depends only on y, then F*(y) = exp(∫R* dy) is an integrating factor.
## Proofs

For F(x), require ∂(FM)/∂y = ∂(FN)/∂x. This gives F ∂M/∂y = F' N + F ∂N/∂x, so F'/F = (∂M/∂y − ∂N/∂x)/N = R. Integrate. Similarly for F*(y).
## Conditions

If neither R nor R* depends only on one variable, no simple integrating factor of that form exists; other methods (separation, linear) may be needed. Exactness test requires continuous partial derivatives.
## Counterexamples

ODE (y) dx + (x) dy = 0 is not exact (∂M/∂y=1, ∂N/∂x=1? Actually M=y, N=x ⇒ ∂M/∂y=1, ∂N/∂x=1, so it is exact. Use a non-exact: y dx + x² dy = 0, ∂M/∂y=1, ∂N/∂x=2x, not exact; R = (1−2x)/x² depends on x, so F(x) exists.