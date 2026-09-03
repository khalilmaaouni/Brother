# Module: release

LOAD WHEN: a version is being cut, an update is being offered to an existing install, or the distributable bytes are being changed.

(The release controls. The routing table in `references/modules.md` names when to load
it.)

## What is switched on: nothing, and that is the honest answer

This module is DECLARED BUT NOT ENFORCED. Switching it on or off changes nothing a
machine can observe: every surface below enforces itself and reads no profile. The
module is a grouping and a load-when trigger for a reader, not a switch.

What is explicitly OUT of this module's scope: the session-start update notice.
`tools/sbe_sessionstart.py` runs `tools/sbe_telemetry.py check-update` UNCONDITIONALLY,
at every profile. A draft of the profile lane gated it here, and the effect was that an
existing install, upgraded to this version, silently stopped printing

```
BROTHERSBE: the skill changed since your last session (<old> -> <new>). Read the diff before relying on it:
  git -C <install> log --oneline <old>..<new>
```

That is not a release convenience. It is the notice that the rules a session is about
to follow are not the rules the user last read, and a user who is not told will trust
an install they have not checked. It cost 236 bytes of startup context to gate, measured
in `references/modules.md`, and no size target is worth buying with it. If you genuinely
want it off, `SECURITY.md` says how: delete the `check-update` line from the hook, which
is a visible edit in your own tree rather than a silent consequence of a profile.

## The surfaces

- `tools/sbe_release_invariant.py`: distributable bytes cannot move without VERSION
  moving. The distributable set is `src/`, `tools/`, `bin/`, `skills/`, `hooks/`,
  `agents/`, `scripts/`, `install.sh` and `.claude-plugin/`. Absent evidence is
  NO-DATA, never a pass: a docs-only change, or two refs with nothing between them,
  reads NO-DATA and blocks nothing.
- `src/brothersbe/versionbump.py`: one command moves every declaration site (VERSION,
  the plugin manifest, the marketplace listing, the digest header) so they cannot
  disagree.
- `scripts/checksums.sh` and `CHECKSUMS.sha256`: the deterministic manifest of every
  shipped file, and `scripts/verify-install.sh`, the user-side half that re-hashes an
  installed copy against it.
- `PUBLISH-CHECKLIST.md` and `docs/RELEASE.md`: the human steps around all of it.

## The honest limit

None of this proves a manifest is authentic. That half is where the manifest came
from, and no check in this repository can answer it.
