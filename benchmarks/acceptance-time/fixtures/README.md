# Fixtures (synthetic test data, never real trial data)

Every CSV in this directory is fabricated to exercise
`benchmarks/acceptance-time/run.py score`'s refusal and reporting paths.
None of it comes from a real reviewer, none of it should ever be read as a
real Acceptance Time result, and none of it is cited anywhere as evidence
about which condition (raw diff, ordinary summary, Brother receipt) a real
reviewer would find faster or more correct. `benchmarks/acceptance-time/test_run.py`, in the
parent directory, is the only thing that reads these files.

- `synthetic_zero_arms_present.csv`: header only, zero data rows. Zero of
  the three arms are present (and zero reviewers, so this also trips the
  five-reviewer floor).
- `synthetic_one_arm_present.csv`: five distinct fabricated reviewers, all
  under `raw_diff`. One of the three arms is present.
- `synthetic_two_arms_present.csv`: five fabricated reviewers under
  `raw_diff`, five under `ordinary_summary`. Two of the three arms are
  present; `brother_receipt` is missing.
- `synthetic_all_three_arms.csv`: five fabricated reviewers under each of
  the three conditions (15 rows), with fabricated seconds, decisions, and
  narrative text (`defects_found`, `lines_inspected`), so
  `run.py score` has a complete, honestly-synthetic input to compute a real
  comparison against. The word FABRICATED appears in every narrative cell
  on purpose, so nobody mistakes this for a real reviewer's words.
