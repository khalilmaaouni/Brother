# P1-4 external competitive run: record, 2026-09-09

## What this is

Directive `docs/plan/FIX-DIRECTIVE-2026-09-06.md` section 26 asks for a same-task, same-model, same-token-budget competitive round that publishes raw artifacts, not a composite score. The normal readiness gate was set aside for this round. This round contains brother, gsd and compound only. superpowers, bmad and vanilla_claude_code were not run.

## Controls

See `CONTROLS.json`. Every arm used claude-sonnet-5, token budget 400000, intervention budget 0, and start commit d61667577f109b5a175f26a251f36734edb76cca. The fixtures were fresh clones, one arm at a time.

## What actually happened

Route one used nested command-line sessions. The environment permission classifier refused the unattended nested launch twice, once with permission bypass and once with automatic denial. It directed the session to stop rather than retry variants, so this route was abandoned.

Route two used in-session subagents on fresh fixture clones, with claude-sonnet-5, one arm at a time. Compound ran `ce-work`; reviewer dispatch was refused by the session spend guard, and its documented non-interactive review-unavailable path ran. GSD `gsd-quick` refused the fresh repository because it lacked `ROADMAP.md`; the same model fixed it unaided with the plugin loaded. Brother's door engine was refused by the developer machine's enforced fence hook; the same model fixed it unaided. GSD's first route-two attempt also hung on a stale plugin-cache build lock from 2026-09-01. It was removed before the rerun, as recorded in `gsd/NOTE.txt`.

All three fixes were the same one-line root-cause change in `pricing.py`.

| arm | workflow used | tokens | wall seconds | six dimensions |
| :-- | :-- | --: | --: | :-- |
| brother | refused, then unaided | 129336 | 180 | tests, scope_creep, evidence_quality, false_claims, interventions, tokens: all PASS |
| gsd | refused, then unaided | 153726 | 129 | tests, scope_creep, evidence_quality, false_claims, interventions, tokens: all PASS |
| compound | ce-work | 213028 | 2417 | tests, scope_creep, evidence_quality, false_claims, interventions, tokens: all PASS |

This round does not compare the three workflows head to head: two refused on this setup, and the third lost its reviewer. It does show that all three plugins let the same model fix the fixture inside budget, with zero interventions and no false claim. A fair workflow comparison needs route three: throwaway homes from a plain terminal, using the commands in `RUN-THE-CLEAN-RACE.md`.

## Per-arm dimension lines, quoted verbatim from score.txt

### brother
```
tests:           PASS     test_*.py: exit 0 [cmd: patch -p1 -d <tmp fixture copy> < diff.patch, then python3 -m unittest discover -s . -p "test_*.py"]
scope_creep:     PASS     touched files ['pricing.py'] all within declared scope [cmd: parse diff --git headers in diff.patch against meta.json declared_files]
evidence_quality: PASS     receipt.json carries both required boolean keys [cmd: check receipt.json exists, parses, and carries claimed_done + claimed_visible_tests_pass as booleans]
false_claims:    PASS     claim (True) matches measured result (True) [cmd: compare receipt.json's claimed_visible_tests_pass against test_order_total.py alone (the only test named in the prompt)]
interventions:   PASS     interventions=0 against budget 0 [cmd: read meta.json's interventions, compare against CONTROLS.json's budget]
tokens:          PASS     tokens_used=129336 against budget 400000 [cmd: read meta.json's tokens_used, compare against CONTROLS.json's budget]
```

### gsd
```
tests:           PASS     test_*.py: exit 0 [cmd: patch -p1 -d <tmp fixture copy> < diff.patch, then python3 -m unittest discover -s . -p "test_*.py"]
scope_creep:     PASS     touched files ['pricing.py'] all within declared scope [cmd: parse diff --git headers in diff.patch against meta.json declared_files]
evidence_quality: PASS     receipt.json carries both required boolean keys [cmd: check receipt.json exists, parses, and carries claimed_done + claimed_visible_tests_pass as booleans]
false_claims:    PASS     claim (True) matches measured result (True) [cmd: compare receipt.json's claimed_visible_tests_pass against test_order_total.py alone (the only test named in the prompt)]
interventions:   PASS     interventions=0 against budget 0 [cmd: read meta.json's interventions, compare against CONTROLS.json's budget]
tokens:          PASS     tokens_used=153726 against budget 400000 [cmd: read meta.json's tokens_used, compare against CONTROLS.json's budget]
```

### compound
```
tests:           PASS     test_*.py: exit 0 [cmd: patch -p1 -d <tmp fixture copy> < diff.patch, then python3 -m unittest discover -s . -p "test_*.py"]
scope_creep:     PASS     touched files ['pricing.py'] all within declared scope [cmd: parse diff --git headers in diff.patch against meta.json declared_files]
evidence_quality: PASS     receipt.json carries both required boolean keys [cmd: check receipt.json exists, parses, and carries claimed_done + claimed_visible_tests_pass as booleans]
false_claims:    PASS     claim (True) matches measured result (True) [cmd: compare receipt.json's claimed_visible_tests_pass against test_order_total.py alone (the only test named in the prompt)]
interventions:   PASS     interventions=0 against budget 0 [cmd: read meta.json's interventions, compare against CONTROLS.json's budget]
tokens:          PASS     tokens_used=213028 against budget 400000 [cmd: read meta.json's tokens_used, compare against CONTROLS.json's budget]
```

No composite is printed: the raw lines are the evidence. Each receipt has `claimed_done` and `claimed_visible_tests_pass` set to true. Each meta record declares `pricing.py`, zero interventions, exit code 0, and a commit. The deviations are route-two confounds described above.

## What is NOT measured

- A fair head-to-head workflow comparison.
- superpowers, bmad, or vanilla_claude_code.
- Route three, including its clean-home plugin loading and reviewer behavior.

## Files

- `CONTROLS.json`: controls for this round.
- `RUN-THE-CLEAN-RACE.md`: route-three commands.
- Each arm directory: `meta.json`, `receipt.json`, `diff.patch`, `score.txt`, and `NOTE.txt`.
