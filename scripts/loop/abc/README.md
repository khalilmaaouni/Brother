# The A/B/C harness (brother.loop against native Claude Code, 2026-09-23)

A reusable, measured comparison of delivery arms on the same work. Written on the owner's order to compare cost, speed
and reliability of Brother.loop model templates against a person working in Claude Code.

- Sample: `pick_sample.py` (first open sub unit per unit, nearest the median size) then `validate_sample.py` (the spec's
  own done check must exist and be RED on the base commit).
- Arms: one worktree and hub branch per arm from one base commit (`setup_arms.sh`); loop arms start from
  `start_loop_arm.sh` and end with `end_loop_arm.sh`; the native arm is one continuous Claude Code session
  (`c_driver.py`, `start_arm_c.sh`, `stop_arm_c.sh`). Sequential equal slots, seeded order, no two arms sharing the CPU.
- Endpoint, blind: `score_arms.py` judges each arm at its last commit inside its slot, spec done check on python3 and
  /usr/bin/python3 in a clean clone with an empty HOME, plus a mutation sweep of the modules the sub unit's own commits
  changed.
- Money: `or_meter.py` reads the OpenRouter account's own usage (killed calls included); `claude_window.py` sums Claude
  transcripts by model and type, killed calls estimated apart; `prices.json` carries the validated list prices.
- Table and statistics: `analyze_abc.py` (cost per delivered sub unit with a bootstrap interval, exact McNemar per arm
  pair, human pace sensitivity for the native arm).
- `DATA-PLAN.md` records every meter, its validation, the contamination log and each decision taken while measuring.

Run data (streams, meters, results) lives outside the repository under ~/.claude/evidence/loop-run-2026-09-23/ab.
