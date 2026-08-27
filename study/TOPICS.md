# TOPICS — textbook register & topic -> section map

This file drives the RAG pipeline. Rows with status `todo` are generated
automatically (overnight watcher). Replace the example rows with real entries.

## Format

| topic | book | section | status | note |
|---|---|---|---|---|
| Topic name (US English) | book_id from the register below | primary textbook section, e.g. `3.5` or `3.5, 3.6` | todo / draft / review / done | path to the note file |

- `section` is the FIRST-PRIORITY retrieval candidate — the pipeline always
  includes those sections verbatim.
- The pipeline adds cross-references automatically via hybrid search; put the
  main section(s) here only.
- Status values: `todo` (generate), `draft` (generated, needs human review),
  `review` (in review), `done` (human-verified against the textbook).

## Books

| book_id | title | author |
|---|---|---|
| prob | (example) Introduction to Probability | — |

## Topics

| topic | book | section | status | note |
|---|---|---|---|---|
| Normal distribution | prob | 3.5 | done | notes/01-normal-distribution.md |
