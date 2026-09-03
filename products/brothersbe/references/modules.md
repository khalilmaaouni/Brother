# Optional modules: the surfaces the default profile does not load

LOAD WHEN: a module is being switched on or off, or the work needs a surface the default profile does not load: memory and vault write-back, telemetry, team coordination, policy evaluation, or release control.

The default profile is the safety floor (the ground map, one writer per file, fence
then dispatch, never claim done without a verifying command) plus design,
implementation boundaries and verification. That is the whole of it, and SKILL.md
routes to no file in the table below.

What this file does NOT claim, because an earlier draft did and it was false: it does
not claim that every surface below is off until a declaration switches it on. ONE of
the five modules has a mechanism that reads the declaration (`telemetry`, whose
startup nags are gated in `tools/sbe_sessionstart.py`). The other four are marked
DECLARED BUT NOT ENFORCED in the Enforced by column, and that phrase means exactly
what it says: nothing anywhere reads the declaration for them, so switching them on
or off changes nothing a machine can observe. They are a stated grouping of surfaces
and a place to put the enforcement point later, not a switch. `team` in particular
is loaded UNCONDITIONALLY today, by three shipped surfaces that consult no profile at
all, and the row says so.

`release` is the fourth of those four BY DECISION, and the decision is worth the
paragraph because a draft of this lane went the other way. That draft gated
`tools/sbe_telemetry.py check-update` behind the release module, which meant an
already-installed copy, upgraded to this version, silently STOPPED printing the
notice that the governance engine itself had changed under it. That notice is
safety-adjacent: a user who is not told the law changed will trust an install they
have not read, and turning it off as a side effect of a size optimisation is exactly
the quiet downgrade this project exists to refuse. `SECURITY.md` also states, and
still states, that the update check runs at session start and that the way to switch
it off is to delete the line from the hook, which the gated draft made false for
every default install. The notice is therefore UNCONDITIONAL, the release module's
own scope EXCLUDES it, and what gating it would have bought is measured at the foot
of this file rather than asserted. Everything else the module names (the release
invariant, the version bump, the checksum manifest) enforces itself and reads no
profile, so the module has no enforcement point left and says so.

`python3 tools/sbe_profile.py check --strict` FAILs any Enforced by cell that is
neither the exact phrase DECLARED BUT NOT ENFORCED nor a file that really does ask
this tool about that module, so a later edit cannot quietly promote a module to
enforced without an enforcement point.

No law lives in this file or in any file it routes to. Every one of the nineteen
laws stays reachable from SKILL.md's own routing table whatever the profile is,
because a law that disappears when a module is off is worse than a long law file.
What a module changes is which SURFACE the law runs against, and each file below
says so in its own words.

## The declaration

`.brothersbe/profile.json`, at the root of the project being worked on:

```json
{
  "schemaVersion": "1.0",
  "profile": "default",
  "modules": []
}
```

`profile` is `default` (no modules) or `full` (every module). `modules` adds to
whatever the named profile already includes, so `{"profile": "default", "modules":
["team"]}` is the default plus team coordination. A module id this file does not
list is refused BY NAME rather than ignored, because a typo that silently loads
nothing looks exactly like a module that is switched on.

The declaration is found by walking UP from the working directory: the first
directory holding `.brothersbe/profile.json` answers, and the walk stops at a
directory holding `.git` (the project boundary) or at the filesystem root. When the
walk finds nothing, the SHIPPED INSTALL's own declaration answers, and every verdict
line says so by name and prints the absolute path of the file that was opened. A
session started three directories deep in a declared project therefore reads that
project's declaration, not the install's, and when it does read the install's it is
told.

Two environment overrides, for a session that wants a different profile without
editing the file: `SBE_PROFILE` names the profile, `SBE_PROFILE_MODULES` is a
comma-separated list added to it. Both are read by `tools/sbe_profile.py`, which is
also what the SessionStart hook asks before it runs any module's startup emitter.

Resolve the active profile, with the file each enabled module routes to and its
enforcement point:

```
python3 tools/sbe_profile.py resolve
```

Check the profile's own invariants (no module leaked into the default, every law
still reachable, every LOAD WHEN line agreeing with its routing row, every
enforcement claim standing up):

```
python3 tools/sbe_profile.py check --strict
```

## The modules

| Module | Load when this is true | Read | Holds | Enforced by |
|---|---|---|---|---|
| `vault` | memory is being read at the start of a run, or written back at a milestone, at a fence close, or at session end. | `references/module-vault.md` | the memory surface L17's close runs against | DECLARED BUT NOT ENFORCED. No loader, hook, tool or check reads the declaration for this module. SKILL.md's step 2 tells the reader to consult it, which is instruction to a reader, not a control. |
| `telemetry` | a session-start nag, a spend line, a scorecard or an outcome rating is being read or written, or the telemetry ledger is being reasoned about. | `references/module-telemetry.md` | the ledger, the hooks that write it, and the checks fed by it | `tools/sbe_sessionstart.py`, which runs `startup-nags` only inside an `enabled telemetry` guard, so the nag is absent from the injected context at the default profile and present at `full`. |
| `team` | `/brothersbe:work` is resolving ready tasks, dispatching an implementation-worker against a worktree, or a human is taking over a task another writer has claimed; or `/brothersbe:handover` is preparing, showing, or resolving an explicit human handover of a whole change. | `references/team-execution.md` | the eight execution laws of team work | DECLARED BUT NOT ENFORCED, and not even optional today: `skills/work/SKILL.md`, `skills/handover/SKILL.md` and `agents/implementation-worker.md` load `references/team-execution.md` unconditionally and consult no profile. |
| `policy` | a registered check is being bound to a command, a policy rule is being evaluated, or the control plane is being changed. | `references/module-policy.md` | check severity, `.sbe/policy.yml`, `.sbe/checks.yml` | DECLARED BUT NOT ENFORCED. `tools/sbe_checks.py` and the control-plane files enforce themselves, and none of them reads the profile. |
| `release` | a version is being cut, an update is being offered to an existing install, or the distributable bytes are being changed. | `references/module-release.md` | the release invariant, the version bump, the checksum manifest | DECLARED BUT NOT ENFORCED. The release controls enforce themselves and read no profile, and the one surface that COULD have been gated, the session-start update notice, is unconditional by decision (see the paragraph above): a user must be told the law changed under them whatever profile they run. |

## What this actually saved, and what it cost, measured in this tree

Every number here was pasted from a command run in this working tree against
`1722a03` (1.0.0-rc.23). None is carried over from an earlier baseline, and the
earlier baseline is why the rule exists: a previous draft of this section quoted
figures taken on 1.0.0-rc.21, and two of them (the injected byte counts and a claimed
78-byte shrink in `DIGEST.md`) did not survive being re-measured here.

WHAT GOT SMALLER, and it is the only thing that did. The bytes
`tools/sbe_sessionstart.py` injects into a session: 2786 at the default profile
against 2869 at `SBE_PROFILE=full`, so 83 bytes fewer, 2.9 percent. Measured by
running the shipped hook twice against a fresh empty vault whose version marker holds
a different sha, which is the condition that makes both the telemetry nag and the
update notice print at all. The whole of that 83 bytes is one line, the weekly-review
nag, which is the entire mechanical effect the profile has on a session's startup
context today.

WHAT GATING THE UPDATE NOTICE WOULD HAVE BOUGHT, since the trade has to be visible
rather than asserted. With `check-update` behind an `enabled release` guard the
default profile injected 2550 bytes; unconditional, it injects 2786. So the gate was
worth 236 bytes, 8.2 percent of the full-profile injection and 2.4 percent of the
hook's own 10000-character cap. Of the two lines it removed, the first is 111 bytes
and the second is 124, of which 84 bytes are the install's own absolute path, so on a
shorter install path the saving is smaller again. 236 bytes is what an upgraded
install was being charged to stop being told that its governance engine had changed.
That is the trade, and it was refused.

WHAT GOT BIGGER. `SKILL.md` went from 15148 bytes to 17010, so 1862 bytes MORE,
because the profile section and its honesty caveats cost more than the severity
paragraph that moved out. `DIGEST.md` went from 2362 bytes to 2549, so 187 bytes
MORE, not less: the header now names the module index and says how many of the five
modules are actually gated, and that sentence is worth more than the bytes it costs.
Four module files are new on disk (`references/module-vault.md` 2491,
`references/module-telemetry.md` 2069, `references/module-policy.md` 2507,
`references/module-release.md` 1676, so 8743 bytes), and so is this index at 7663.
Nothing was deleted. Across every markdown file this lane touches, documentation
surface GREW by 18455 bytes.

WHAT DID NOT MOVE AT ALL. `references/team-execution.md` is 13308 bytes and is loaded
UNCONDITIONALLY by `skills/work/SKILL.md`, `skills/handover/SKILL.md` and
`agents/implementation-worker.md`, none of which reads a profile. Not one byte of it
became optional. Any sentence describing those bytes as "surface made optional" is
false, which is why no sentence here says it.
