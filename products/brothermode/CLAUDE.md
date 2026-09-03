# BrotherModeUp: repo working rules

Proven recipes from docs/handover/2026-08-11-morning/03-RULES-AND-PROCESS-
FIXES.md, PO-1 to PO-6, plus standing repo policy. Read PROJECT.md first for
identity and links; this file is process, not identity.

## PO-1: the gate, foreground detach plus sentinel (nohup dies, this does not)

`python3 tools/test_all.py` runs 8 to 13 minutes (516.3s measured 2026-08-11);
backgrounding it as a harness task dies at exit 144 with no verdict
(reproduced twice 2026-08-08). Detach and poll instead:

```
rm -f "$TMPDIR/gate.exit"
nohup bash -c 'python3 tools/test_all.py > "$TMPDIR/gate.log" 2>&1; echo $? > "$TMPDIR/gate.exit"' > /dev/null 2>&1 &
for i in $(seq 1 80); do pgrep -f tools/test_all.py > /dev/null || break; sleep 10; done
cat "$TMPDIR/gate.log"; cat "$TMPDIR/gate.exit"
```

THE FIRST LINE IS LOAD-BEARING AND WAS MISSING UNTIL 2026-08-11 night.
`$TMPDIR` survives between sessions, so the previous run's `gate.exit` is
already on disk when the poll starts. Without clearing it the loop breaks on
the first iteration and the session reads a stale exit code as its own
verdict. Observed that night: the poll returned `gate exit: 0` after ten
seconds while the log still read `running test_bm_docs.py`, and the real run
finished nine minutes later at exit 1 with two failed suites. It fails toward
green, which is the worst direction, so the sentinel is cleared before the
launch and the wait is on the process rather than on the file.

Never stop a running gate with `pkill -f` (can match another session's
prompt). Kill by PID after printing the target.

## PO-2, PO-3, PO-4, PO-6: proven process shapes

- PO-4: run `pgrep -f tools/test_all.py` before every board refresh. A
  refresh that read mid-gate output once poisoned the board.
- PO-2: cross-family audits run one file per call, with a ceiling. A single
  multi-file call returned NO-DATA; one-file-per-call found 12 real issues.
- PO-3: worktree agents return deltas, never a direct commit. The
  orchestrator copies the delta, re-runs the gate, commits. Collision-free.
- PO-5: review briefs for safety seams are falsification-only: the reviewer
  runs attacks and reports what broke or states COULD NOT BREAK; reasoning
  without an executed attack is NO-DATA.
- PO-6: before adding an entry to any registry (SUITES in tools/test_all.py,
  tools/write_sites.json, a workflow step), open the file that READS that
  registry first and confirm the shape it expects.

## Standing rules

- No em dashes, no en dashes, anywhere: code, comments, docs, commit
  messages, output.
- No `Co-Authored-By:` trailer, ever, in any commit
  (~/.claude/skills/github-desktop-push/repos.md, founder decision
  2026-08-10, after a force-rewrite removed one). Enforced at commit time by
  `scripts/bm_commit_msg_hook.py`.
- Touching tracked files: `git add` new ones first, regenerate the manifest
  LAST, then STAGE THE MANIFEST:
  `git add -A && sh scripts/checksums.sh CHECKSUMS.sha256 && git add CHECKSUMS.sha256`.
  The trailing add is load bearing: the script rewrites the file on disk AFTER
  the first add, so a plain `git commit` captures the index and ships the STALE
  manifest under a message claiming it was regenerated. Reproduced 2026-08-23 in
  two sessions independently; one of them was correct only by the accident of
  splitting the command across two calls, which is why this is written down.
  Skipping the regeneration entirely leaves `scripts/doctor.py` check 9 FAILing,
  and no test catches it.
- Push policy: direct to main. Every gate (secret scan, dash scan, green
  tests, command verification) stays mandatory.
- Two-host law (founder order 2026-08-16, amended the same day when the
  target was constrained to GitHub or Bitbucket): every development,
  past and forward, works on GitHub AND Bitbucket Cloud by default.
  GitHub is the main target and canonical home; Bitbucket carries the
  adopter team. Concretely: the engine speaks plain git only (never gh,
  never a host API, in tools/ or hooks/); every host-facing feature
  ships both legs or labels the missing leg UNVERIFIED by name
  (docs/BITBUCKET.md is the pattern); CI parity is
  bitbucket-pipelines.yml beside .github/workflows/, honest about
  runner limits. A change that only works on one host is not done until
  the other is covered or its gap is named in the same change. Azure
  Repos: removed from the target by the same founder direction
  (PRODUCT-DIRECTION.md amendment 2026-08-16); docs/AZURE-REPOS.md and
  azure-pipelines.yml stay in the tree as dormant and no Azure work is
  scheduled; the flip condition is the founder naming a client on Azure
  again.
- Merge-to-main default (founder order 2026-08-15): every session merges
  its finished work to main before it ends; worktree agent deltas are
  folded by the orchestrator, re-gated on the committed tree, and pushed.
  Work that cannot merge yet is NAMED in the session's handover pack close
  report at the exact step it stopped, never left silently in a worktree.

## The baton ceremony, both halves (ratified 2026-08-11)

Every session in this repository opens and closes with it. Full rule in the
founder's global CLAUDE.md; the paper version anyone can follow with nothing
installed is docs/HANDOVER-BY-HAND.md.

OPEN, before new work:

    python3 tools/bm_handover.py detect

Read what it reports: the newest pack and its age, unacknowledged handovers,
and records whose owning session is dead, each with its clearing command. Then
read the newest pack in docs/handover/ in its stated order, adopt or park what
the predecessor left, and say what you adopted before starting.

CLOSE, before the session ends:

    python3 tools/bm_handover.py skeleton --slot <name>
    # fill every FILL-BY-HAND slot by hand, then
    python3 tools/bm_handover.py zip --pack docs/handover/<date>-<name>
    python3 tools/bm_handover.py verify-close --pack docs/handover/<date>-<name>

The close report's first line is FINISHED or UNFINISHED, one of two words,
never a percentage. verify-close refuses a hollow pack, a missing status line,
a stale zip, and any session still holding unparked records, naming them by id.

skeleton also generates 07-NEXT-SESSION-PROMPT.md by default: a paste-ready
prompt for whoever opens next, with four FILL-BY-HAND slots (goal, decisions
already made, ordered work list, frozen or blocked). verify-close refuses an
unfilled slot, naming exactly which one.

IT WILL REFUSE YOU AND IT IS USUALLY RIGHT. On its first real close it refused
its own author twice: once for a missing zip, once for three live claims. Park
what it names and run it again rather than working around it.

NOT ENFORCED, stated plainly: the opening half is discipline. Nothing refuses a
session that skips detect, because wiring it needs hooks/hooks.json and
tools/bm_sessionstart.py, which are under another lane's claims. The closing
half is enforced by verify-close only when somebody runs it.

## Key commands (from PROJECT.md)

- Full gate: `BROTHERMODE_SESSION_CAP=99 python3 tools/test_all.py`
- Manifest, after `git add`, and stage it again afterwards:
  `git add -A && sh scripts/checksums.sh CHECKSUMS.sha256 && git add CHECKSUMS.sha256`
- Install check: `bash scripts/verify-install.sh`
