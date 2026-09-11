# Persona corpus tools

Tools from the 2026-09-07 persona dogfood, moved into the tree so a
promotion run is reproducible from a pinned checkout, not a hand-run loop.

- `scenarios.json`, `persona-roster.json`: 8 personas, 40 scenarios
  (archetype fixtures, not real clients: "drinks-marketplace",
  "bottler-data-platform").
- `ACTOR-BRIEF-TEMPLATE-v2.md` (current), `ACTOR-BRIEF-TEMPLATE.md` (v1,
  kept for history): the brief shape `gen_briefs.py` fills in.
- `gen_briefs.py ROUND TREE [--all]`: one actor brief per persona.
  Default: only scenarios whose latest verdict is not PASS. `--all`: every
  scenario, so a promotion run reruns the passing ones too, no regression.
- `judge.py`: the headless Claude CLI (`claude -p`, sonnet with haiku as
  fallback) judges each new transcript as the persona, scrubbing the two
  client terms out first. An answer without a PASS, FAIL or NO-DATA
  verdict is recorded as NO-DATA and counted as a failed call.
- `tally.py [--installed]`: pass rate from the latest result per scenario,
  exit 0 at 36 of 40. `--installed` also prints the pinned tree
  (`PINNED-TREE` in the evidence dir) and flags a REGRESSION when a
  scenario PASSed earlier but not in its latest round.

All four scripts read scenarios.json and templates from this directory;
they read/write `transcripts/`, `judgments.jsonl` and `brief-*.md` under
`--evidence-dir` (default `$PERSONA_EVIDENCE_DIR`, else the 2026-09-07
evidence dir).

## Running a round

1. `git worktree add --detach <TREE> <commit-or-branch>`, pin it.
2. Write `<evidence-dir>/PINNED-TREE`: line 1 the tree path, line 2 commit.
3. `python3 gen_briefs.py <round> <TREE> --all --evidence-dir <dir>`
4. Dispatch one actor per brief file against `<TREE>`.
5. `python3 judge.py --evidence-dir <dir>` then `tally.py --installed --evidence-dir <dir>`.

## Privacy

Fixtures are archetypes only, never a real client's tree or data. `judge.py`
scrubs the two client terms before a transcript leaves this machine. Never
feed this tool a real client transcript.
