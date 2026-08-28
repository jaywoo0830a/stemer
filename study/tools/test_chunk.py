#!/usr/bin/env python3
"""Standalone tests for the textbook chunking heuristics (rag/chunk.py).

Run from anywhere:  python3 study/tools/test_chunk.py
No external dependencies (stdlib + rag.chunk only). Exit code 0 = all pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag import chunk  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _text(chunks) -> str:
    return " ".join(c.text for c in chunks)


def _sections(chunks) -> list[str]:
    return [c.section for c in chunks]


def test_stewart_number_at_end() -> None:
    print("Stewart style — section number at END of heading:")
    md = """## 1 Functions and Models
TOC noise
## Four Ways to Represent a Function 1.1
section 1.1 body
## FIGURE 1
caption
## ■ Functions
concept block text
## 1.1 Exercises
exercises text
## Mathematical Models 1.2
section 1.2 body
"""
    c = chunk.split_markdown(md, "calc", min_chars=20)
    check("TOC dropped", not any("TOC" in x.text for x in c))
    check("1.1 section kept", any(s.startswith("1.1") for s in _sections(c)))
    check("1.2 section kept", any(s.startswith("1.2") for s in _sections(c)))
    check("captions stay body text", "caption" in _text(c))
    check("exercises stay body text", "exercises text" in _text(c))


def test_number_at_start_and_other_forms() -> None:
    print("Number at start / 'Section X.Y' / bare number:")
    md = """## Contents
contents noise
## 12.3 The Derivative
derivative body text
## Definition of the Derivative
definition block text
## Proof
proof text
## 12.3 Exercises
exercises for 12.3
## Section 13.1 Vector Fields
vector body text
## Why you should learn it
objectives noise text
## Applied Project The Calculus of Rainbows
project text
## 13.2
bare number body text
"""
    c = chunk.split_markdown(md, "bio", min_chars=20)
    secs = _sections(c)
    check("front matter dropped", not any("contents noise" in x.text for x in c))
    check("12.3 section", any(s.startswith("12.3 The Derivative") for s in secs))
    check("Section 13.1 form", any(s.startswith("13.1 Vector Fields") for s in secs))
    check("bare 13.2 section", any(s.startswith("13.2") for s in secs))
    check("exercise block is not a section", not any("Exercises" in s for s in secs))
    check("chapter derived from number", any(cx.chapter == "Chapter 12" for cx in c))
    t = _text(c)
    for frag in ("definition block text", "proof text", "project text",
                 "objectives noise text", "exercises for 12.3"):
        check(f"noise kept as body: {frag!r}", frag in t)


def test_front_matter_and_in_section_noise() -> None:
    print("Front matter drop + noise inside sections:")
    md = """## Contents
contents noise
## Copyright 2021 Cengage
copyright text
## Learning Objectives
objectives text
## 2 Limits and Derivatives
chapter body opener
## 2.1 The Limit of a Function
limit body
## Theorem
theorem text
## Checkpoint
checkpoint text
## Key Terms
key terms text
## Summary
summary text
## Answers to Odd-Numbered Exercises
answers text
## 2.2 Calculating Limits
next section body
"""
    c = chunk.split_markdown(md, "chem", min_chars=20)
    check("front matter dropped",
          not any(any(w in x.text for w in ("contents noise", "copyright text",
                                            "objectives text", "chapter body opener")) for x in c))
    t = _text(c)
    for frag in ("theorem text", "checkpoint text", "key terms text",
                 "summary text", "answers text"):
        check(f"in-section noise kept: {frag!r}", frag in t)
    check("numbered chapter title kept",
          any(x.chapter == "2 Limits and Derivatives" for x in c))
    check("sections 2.1/2.2 kept",
          any(s.startswith("2.1") for s in _sections(c)) and any(s.startswith("2.2") for s in _sections(c)))


def test_short_and_symbol_noise() -> None:
    print("Short titles / single letters / symbols:")
    md = """## 3 Applications of Differentiation
## 3.1 Maximum and Minimum Values
body one
## D
single letter noise
## ;
symbol noise
## n Conceptual Exercises
n-prefix noise
"""
    c = chunk.split_markdown(md, "math", min_chars=20)
    check("no single-letter chunk", not any("D" == x.chapter for x in c))
    check("no symbol chapter", not any(";" == x.chapter for x in c))
    check("noise text present", all(w in _text(c) for w in ("single letter noise", "symbol noise", "n-prefix noise")))


def test_size_limits() -> None:
    print("Chunk size cap + overlap:")
    long_para = "word " * 400  # ~2000 chars paragraph
    md = f"## 4 Integrals\n## 4.1 Areas and Distances\n{long_para}\n\nsecond paragraph"
    c = chunk.split_markdown(md, "size", min_chars=50, max_chars=1000, overlap=100)
    check("oversized text split", all(len(x.text) <= 1000 + 200 for x in c))
    check("all pieces carry section", all(x.section.startswith("4.1") for x in c))


def main() -> None:
    print("== chunking heuristics tests ==")
    test_stewart_number_at_end()
    test_number_at_start_and_other_forms()
    test_front_matter_and_in_section_noise()
    test_short_and_symbol_noise()
    test_size_limits()
    print(f"== {_PASS} passed, {_FAIL} failed ==")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
