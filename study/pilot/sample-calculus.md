# Chapter 3 Limits

## 3.1 The Limit of a Function

Informally, we say that a function f has limit L at a point a if we can make f(x)
as close to L as we like by taking x sufficiently close to a, but not equal to a.

The value f(a) itself plays no role in the limit. What matters is the behavior of
f near a, on both sides. A function has a limit at a only when the left and the
right behavior agree on the same value L.

One useful way to think about limits is through error control: for any allowed
error epsilon, there is a distance delta so that whenever x is within delta of a
(but x is not a), the value f(x) is within epsilon of L.

## 3.2 Computing Limits

When a function is built from continuous pieces, the fastest way to evaluate a
limit is often direct substitution: plug a into f and read off the value.

Direct substitution fails when plugging in produces an indeterminate form such as
0/0. In that case, rewrite the expression first: factor and cancel, rationalize a
numerator with a conjugate, or combine fractions over a common denominator.

A limit that equals infinity means the function grows without bound near the point
of interest. This is not a number, so we describe the behavior instead of writing
an equality with infinity.

## 3.5 The Limit of a Sequence

A sequence is an infinite list of numbers a_1, a_2, a_3, ... . We say the sequence
converges to L when its terms get and stay arbitrarily close to L as n grows.

Only the tail of the sequence matters. Changing or deleting finitely many early
terms never changes the limit.

Many limits of fractions reduce to a ratio of leading terms: divide top and bottom
by the highest power of n, then let n grow.

## 3.6 Monotone Convergence

A sequence is increasing if each term is at least the one before it, and decreasing
if each term is at most the one before it. Such sequences are called monotone.

The monotone convergence theorem says: if a sequence is monotone and bounded, then
it converges. An increasing sequence bounded above has a limit, and a decreasing
sequence bounded below has a limit.

This theorem is a workhorse for proving that a recursively defined sequence, such
as one built by repeatedly applying a function, actually settles down to a value
instead of wandering forever.
