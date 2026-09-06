# Keep current

`scripts/keep_current.py --tag vX.Y.Z [--public-url URL] [--evidence-dir DIR] [--install]`

Updates a Codex install only after every one of seven links reads PASS, in
order, stopping at the first FAIL or NO-DATA:

1. **signature**: `git tag -v <tag>` in a clone (mirrors
   `scripts/release_closeout.py`'s `tag_signature_verified`). PASS on a
   verified signature, NO-DATA on an unsigned tag, FAIL on a bad one.
2. **reproduction**: `scripts/reproduce_export.py --verify-tree --tag <tag>`
   run inside the clone; PASS only on its own PASS line.
3. **manifest**: `docs/releases/<version>.export-manifest.txt` exists in the
   clone and its sha256 equals the digest the release note states.
4. **conformance**: the newest
   `~/.claude/evidence/adapter-conformance/codex/summary.txt` names
   `provider=codex ... verdict=PASS` and is not older than the tag's commit.
5. **closeout**: the X1 to X7 evidence `release_closeout.py` writes for this
   version, under its own evidence directory default.
6. **virgin-ci**: a recorded GitHub Actions virgin-install run id for this
   tag, read from the closeout evidence. Never dispatches anything.
7. **smoke**: `~/.claude/evidence/codex-battery/SUMMARY.txt`, first line
   `tag=<tag>`, with B6, B8 and B9 all PASS.

Only with all seven PASS, and `--install`, does it run
`scripts/brother_install.py upgrade --ref <tag>`. Without `--install` it is
a pure report. A missing `brother_install.py` prints NO-DATA and installs
nothing.

Tests: `scripts/test_keep_current.py` drives every link's classifier from
canned strings, the stop-at-first-non-PASS rule, and the install gate
through a fake install function, so no test needs a network clone.
