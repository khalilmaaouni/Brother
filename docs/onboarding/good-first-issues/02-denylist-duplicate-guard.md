EXPORT-DENYLIST.txt has no duplicate-entry guard

`scripts/test_export_public.py` carries `E120TheAllowlistNamesEveryPathExactlyOnce`,
a test that fails on any path listed twice in `docs/plan/EXPORT-ALLOWLIST.txt`,
because a duplicate line silently inflates the exporter's own entry count.
`docs/plan/EXPORT-DENYLIST.txt` sits right beside the allowlist, is read by
the same exporter (`load_denylist`), and has no equivalent guard today: a
duplicate line there would be just as silent.

This gap is not tracked as its own row on the readiness roadmap; it is a
sibling of row S31 (docs/plan/READINESS-ROADMAP-2026-08-29.json), first
drafted in docs/plan/GOOD-FIRST-ISSUES-2026-09-05.md (item 4) and
re-verified against today's tree before being carried into this file: the
allowlist's guard class still exists on 2026-09-06, the denylist's does
not.

Files: `scripts/test_export_public.py` only, a new small test class
mirroring E120's own (same duplicate-detection shape, pointed at
`docs/plan/EXPORT-DENYLIST.txt` instead of the allowlist).

Done check: a scratch copy of `EXPORT-DENYLIST.txt` with one path repeated
makes the new test fail, naming the path and its line numbers; the real,
checked-in file (which has no duplicates today) makes it pass. Quote both
runs of `python3 scripts/test_export_public.py -v` in the pull request.

label: good first issue
