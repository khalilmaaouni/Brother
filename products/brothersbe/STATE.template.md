# BrotherSBE STATE (active session registry)

Copy this file into each project as STATE.md. It is the single source of truth for
in-flight work: any compaction, kill, or new session resumes from this file, never
from memory. Update it at every milestone, not at session end.

## Session YYYY-MM-DD: <one-line objective>

## Fence registry
One line per writer, written BEFORE the writer starts (fence then dispatch).
Close a fence only by appending its evidence block: the exact command run and its
last lines. Flip to LANDED in the landing commit itself, never later.

- agent: <orchestrator or agent id> (sole writer, session <id>) | tier T1 | TTL <date> |
  objective: <what this writer will accomplish, one line> |
  files: <the exact files it may write; anything else is out of bounds> |
  output: <what done looks like: a commit, a document, a passing gate> |
  boundaries: <what it must not touch or do> |
  termination: <the observable end state> |
  check: <a runnable command that proves the work landed> |

Example of a closed fence:

- agent: orchestrator (sole writer, session abc123) | tier T1 | TTL 2026-01-15 EOD |
  objective: fix the date parser for two-digit years |
  files: src/parser.py, tests/test_parser.py |
  output: one commit, tests green |
  boundaries: no changes outside the parser module |
  termination: test_two_digit_years passes |
  check: python3 -m pytest tests/test_parser.py -q |
  LANDED 2026-01-15, evidence (verbatim, run after last edit):
    tests/test_parser.py .......... 10 passed in 0.41s

## Decisions
Dated, one line each, newest first. A decision supersedes its predecessors; the
superseded line is noise from that moment.

## Never-forget
The lines below are exempt from every forgetting mechanism. Keep this list short
and absolute: safety invariants, human-only gates, live fences, unmerged work, open
operator asks.

- <your project's non-negotiables go here>
