# Prepare a release decision

A locally integrated or accepted delivery is not automatically a release.

For Brother itself, use the release note for the exact tag and its supported reproducibility/export verification from a clean checkout.

In Claude Code, ask in plain words, for example "is 1.0.19 ready to cut?" or "cut 1.0.19". The session runs `python3 scripts/cut.py --check` and then the cut, and asks you one question before the tag is pushed. You never run a command. Read it alongside the manual chain in `docs/plan/RELEASE-POLICY.md` until it has a real release cut behind it.

List material FAIL/NO-DATA; do not bury expected failures in a green aggregate. For deployable systems, name rollback/roll-forward handling, compatibility, monitoring, and the signal that stops/reverses rollout.

Record who made the release decision and what known risks were accepted. Brother prepares evidence; it does not make organizational release authority.

## Verify the result

After release, observe reality. Pre-release evidence cannot prove production behavior that had not yet occurred.
