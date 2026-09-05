# The clean-install Codex smoke, and the runbook that closes its other half

Ship gate 7 of the Codex workstream (board row C7). Two halves, and this page
is honest about which one is done.

- THE CREDENTIAL-FREE HALF IS DONE, on this machine, on 2026-09-04, against
  the app-bundled Codex CLI `/Applications/ChatGPT.app/Contents/Resources/codex`,
  `codex-cli 0.153.0-alpha.5`. Every command, exit code and decisive line
  below was captured after the last edit by `python3 scripts/codex_smoke.py`,
  which is the automation of exactly these steps.
- THE CREDENTIALLED HALF IS NOT DONE, and its blocker is stated rather than
  worked around: an isolated Codex home holds no credentials, so a real
  `codex exec` turn there is refused by OpenAI. The runbook at the end of this
  page is what the founder runs in his own signed-in Codex to close it.

Nothing here touched `~/.codex`. Every Codex invocation ran with `CODEX_HOME`
and `HOME` pointed inside a throwaway directory, and the isolation was
MEASURED, not asserted: see "Isolation, measured" below.

## The automation

    python3 scripts/codex_smoke.py

It drives steps 1 to 5 below, prints PASS, FAIL or NO-DATA, and keeps every
step's whole output under `<work>/logs/` rather than a tail (a trim applied at
capture time always looks reasonable while it is being typed). It reports
NO-DATA and exits 2 when no Codex binary is present, which is never a pass.
Registered in `scripts/check_all.sh` as `codex-smoke`.

Flags: `--codex-bin` (defaults to the app-bundled binary, because the PATH
`codex` on this machine is an older npm build), `--work` (the throwaway
directory, default `~/.claude/evidence/codex-smoke`), `--keep`.

## What is real and what is stubbed

Real: the Codex binary, its marketplace and plugin install, its hooks
machinery, Brother's own 18 hook registrations, `scripts/brother_run.py`, the
claim store, the integration and the receipt.

Stubbed, because an isolated home has no credentials: Codex's own model (a
local HTTP provider `codex_smoke.py` serves on 127.0.0.1, the technique the C3
lane used) and Brother's model worker (the documented `DOOR_MODEL_CMD` and
`MODEL_WORKER_CMD` seam in `scripts/model_worker.py`, the same seam
`scripts/product_acceptance.py` uses).

NOT stubbed, since 2026-09-05: the sandbox. The stub turn runs at the same
`-s workspace-write` the runbook documents, rather than switching sandboxing
off with `danger-full-access`. That earlier shape is why this automation
stayed green while the documented command was broken: the leg that could
write had no sandbox at all, and the leg carrying Codex's real default never
reached a write, being refused at the login first.

So this proves the PLUMBING end to end. It does not prove a real model's
behaviour. That is the half the runbook closes.

## The four install commands, captured

Step 1, the marketplace. A LOCAL PATH is a valid marketplace source, and that
is what the smoke uses so it tests the tree it is run from:

    $ /Applications/ChatGPT.app/Contents/Resources/codex plugin marketplace add <repo root>
      Added marketplace `brother` from <repo root>.
      Installed marketplace root: <repo root>
      exit 0

For the PUBLIC repository the same command takes the HTTPS Git URL, which is
the form the README already documents and the form the founder will use:

    codex plugin marketplace add https://github.com/khalilmaaouni/Brother --ref <the release you are testing>
    # worked example: --ref v1.0.2

Step 2, the plugin:

    $ /Applications/ChatGPT.app/Contents/Resources/codex plugin add brother@brother --json
      "name": "brother",
      "marketplaceName": "brother",
      "version": "1.0.1",
      "installedPath": "<CODEX_HOME>/plugins/cache/brother/brother/1.0.1",
      "authPolicy": "ON_INSTALL"
      exit 0

Step 3, the confirmation. `pluginId` is `brother@brother`, and its `source`
resolves `./bundle` against the marketplace root:

    $ /Applications/ChatGPT.app/Contents/Resources/codex plugin list --available --json
      "pluginId": "brother@brother",
      "source": { "source": "local", "path": "<repo root>/bundle" }
      exit 0

Step 4, the hooks. A Codex plugin install delivers NO hooks (Codex removed
`plugin_hooks`; see HOOKS-MAPPING.md), so this second step is what gives a
Codex user the fence:

    $ python3 scripts/codex_hooks_install.py --codex-home <CODEX_HOME> --trust --cwd <toy repo>
      codex_hooks_install: wrote <CODEX_HOME>/hooks.json: 18 command(s) across
        PostToolUse, PreCompact, PreToolUse, SessionEnd, SessionStart, Stop
      codex_hooks_install: codex hooks/list reports 18 hook(s) from <CODEX_HOME>/hooks.json
      codex_hooks_install: PASS: codex reports all 18 hook(s) trusted and enabled
      exit 0

## Step 5a: the real task, and the refusal that blocks it

The smoke attempts the real thing FIRST, with no stub, so the blocker is
captured rather than predicted. The toy repository is the README example's
shape: a git repository holding `mathlib.py` (a bare `add`) and a
`test_mathlib.py` written as a `unittest.TestCase`, so nothing has to be
installed for its tests to run. Step 5 below lays down the same two files,
from the same constants this script uses.

    $ /Applications/ChatGPT.app/Contents/Resources/codex exec \
        -s workspace-write -c 'sandbox_workspace_write.writable_roots=["<toy repo>/.git"]' \
        -C <toy repo> \
        "use the Brother plugin to make add() refuse non-numeric input and cover it with a test, tests run with python3 -m unittest"
      ERROR: unexpected status 401 Unauthorized: Missing bearer or basic
        authentication in header, url: https://api.openai.com/v1/responses
      exit 1

That is the blocker for the credentialled half, verbatim. Note what still ran
before it: Codex's `hook: SessionStart` lines fire in that same transcript, so
the plugin and the hooks are live even in a turn the model call refuses. The
missing thing is a credential, and a credential is the founder's to supply.

## Step 5b: the same turn through the stub provider

The identical `codex exec` turn, with the model pointed at the local stub, so
the plugin, the hooks, the engine and the receipt path all run for real:

    $ ... codex exec --skip-git-repo-check -c model_provider="c7stub" \
        -s workspace-write -c 'sandbox_workspace_write.writable_roots=["<toy repo>/.git"]' ... \
        -C <toy repo> \
        "use the Brother plugin to make add() refuse non-numeric input and cover it with a test, tests run with python3 -m unittest"
      hook: SessionStart ... hook: PreToolUse ... hook: PostToolUse ... hook: Stop
      exit 0

Its one tool call runs the Brother engine, and the engine's own report inside
that turn reads:

      files changed (1): mathlib.py
      verdicts: 1 PASS, 0 FAIL, 0 NO-DATA
      brother_run: receipt: <run dir>/receipt/receipt.json

## What a passing receipt looks like

The receipt is a file, at `<run dir>/receipt/receipt.json`. Its per-file check
lines, read out of the receipt the engine wrote (its `scope.changed`, which is
`receipt_door.per_file_checks`' own output, never retyped):

    mathlib.py (unit U1): state verified, check python3 -c "import mathlib;
      mathlib.add(1,2)" && ! python3 -c "import mathlib; mathlib.add('a','b')"
      2>/dev/null exited 0

Read it as three claims, and each is separately checkable. The FILE that
changed. The exact CHECK COMMAND a stranger can re-run. Its EXIT CODE. A
receipt whose state is `verified` also carries `check_passed_before`, the
before-and-after discrimination: the same check was run before the work and
failed, which is what stops a green command from claiming more than it proved.
A unit with no such measurement reads `no-data`, never a pass.

## Isolation, measured

`CODEX_HOME` is Codex's own isolation variable (named in its help text:
"--ignore-user-config  Do not load $CODEX_HOME/config.toml; auth still uses
CODEX_HOME"). `HOME` is pointed inside the same throwaway directory.

The smoke does not assert that `~/.codex` was untouched, it measures it. A
witness is hashed before and after over exactly the parts a plugin or hook
install would change: `config.toml`, `AGENTS.md`, whether a `hooks.json` or a
`hooks/` directory exists, and the full listing of the `plugins/` tree. The
sqlite databases and caches in that directory are deliberately EXCLUDED,
because the ChatGPT desktop app rewrites them on its own and a hash that
drifts by itself is evidence of nothing.

    founder ~/.codex witness before: f524095855164eec1bb6efc0f98855634eb2f105940b2978ffcd0cad67c0ba9f
      (config.toml, AGENTS.md, hooks.json present=False, hooks present=False, plugins tree: 3532 entries)
    founder ~/.codex witness after:  f524095855164eec1bb6efc0f98855634eb2f105940b2978ffcd0cad67c0ba9f
    isolation holds: the witness is byte-identical before and after.

A witness that moved is a FAIL, not a warning.

## THE RUNBOOK: closing the credentialled half

For the founder, in his own signed-in Codex. Six steps, about ten minutes.
Nothing here needs this repository's session or this machine's evidence
directory. Every step names what a pass looks like.

Step 0, a throwaway home so your own `~/.codex` is not changed. Everything
below assumes this is exported in the shell you are using:

    export CODEX_HOME=~/codex-smoke-home
    mkdir -p "$CODEX_HOME"

If your throwaway home instead lands under `/tmp` or `/var` (both are
symlinks into `/private/tmp` and `/private/var` on macOS), note that Codex
canonicalizes `CODEX_HOME` before reading anything back, so
`scripts/codex_hooks_install.py` resolves the same path with
`os.path.realpath` before it writes; before that fix, step 4 below wrote a
hooks file under the unresolved spelling and Codex read it back under the
resolved one, matching nothing. `~/codex-smoke-home` above is not a symlinked
path, so this only matters if you choose `mktemp -d` or a literal `/tmp/...`
instead.

Your credentials still resolve through `CODEX_HOME`, so if this home has no
`auth.json` you will hit the same 401 above. Two ways round it, pick one:
copy your own `~/.codex/auth.json` into it (`cp ~/.codex/auth.json
"$CODEX_HOME"/`), or skip step 0 entirely and use your real home, accepting
that steps 1, 2 and 4 then write into it (step 4 needs `--allow-default-home`
in that case). The uninstall route at the end of this runbook undoes it;
see that section rather than deleting `hooks.json` by hand, which can hold
hooks that are not Brother's.

Step 1, the marketplace. A public tag has carried the Codex package since
v1.0.2, so the public form works for a released version; point Codex at a
checkout of the hub's main instead when testing work that has not shipped in
a tag yet:

    codex plugin marketplace add https://github.com/khalilmaaouni/Brother --ref <the release you are testing>
    # worked example: codex plugin marketplace add https://github.com/khalilmaaouni/Brother --ref v1.0.2
    # testing unreleased work: codex plugin marketplace add ~/brother-hub

  PASS: "Added marketplace `brother`", exit 0.

  Running this a second time in the same home at a different ref, which is
  what an upgrade looks like, is refused: "Error: marketplace 'brother' is
  already added from a different source; remove it before adding this
  source", exit 1. Remove the configured marketplace first, then add it at
  the new ref and add the plugin again, as README.md's upgrade block spells
  out. `codex plugin marketplace upgrade brother` is not that route: it
  exits 0 and leaves the installed version where it was, because it only
  refreshes the snapshot at the ref already configured.

Step 2, the plugin:

    codex plugin add brother@brother --json

  PASS: JSON naming `"pluginId": "brother@brother"` and a version, exit 0.

Step 3, the confirmation:

    codex plugin list --available --json

  PASS: the installed list carries `brother@brother`, exit 0.

Step 4, the hooks, which the plugin install does NOT deliver. The installer is
a maintainer script and does NOT ship inside `bundle/`, so it is run from a
checkout of this repository, not from the installed plugin:

    # the same checkout as step 1 (~/brother-hub here, or a public clone at your tag)
    python3 ~/brother-hub/scripts/codex_hooks_install.py \
        --codex-home "$CODEX_HOME" --trust --cwd <your repo>

  PASS: "PASS: codex reports all 18 hook(s) trusted and enabled", exit 0.
  Anything less is NO-DATA: a hooks file Codex has read but not trusted does
  not run, and a Codex install without it has skills and no fence.

Step 5, the toy. In a throwaway directory, not a real project:

    mkdir toy && cd toy && git init -q .
    printf 'def add(a, b):\n    return a + b\n' > mathlib.py
    printf 'import unittest\n\nfrom mathlib import add\n\n\nclass AddTest(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n\nif __name__ == "__main__":\n    unittest.main()\n' > test_mathlib.py
    git add -A && git commit -q -m toy

  The test file is a `unittest.TestCase`, not a bare assert, and the task
  sentence in step 6 names `python3 -m unittest` for the same reason: on
  2026-09-04 the model read the old bare-assert file, wrote `import pytest`,
  ran `pytest -q` and got "pytest: command not found". `unittest` is in the
  standard library, so this toy needs nothing installed.

Step 6, the task:

    codex exec -s workspace-write -c "sandbox_workspace_write.writable_roots=[\"$PWD/.git\"]" -C "$PWD" "use the Brother plugin to make add() refuse non-numeric input and cover it with a test, tests run with python3 -m unittest"

  Both flags are load bearing, and each one is a failure this step already
  had.

  `-s workspace-write` is the first. Codex's default sandbox is `read-only`,
  so a turn without the flag refuses every patch the model writes, verbatim:
  "patch rejected: writing is blocked by read-only sandbox". A turn that ends
  by telling you "the repository is mounted read-only" is this missing flag,
  not a Brother failure. `codex exec --help` prints it as "-s, --sandbox
  <SANDBOX_MODE>" with possible values "read-only, workspace-write,
  danger-full-access"; `workspace-write` lets the model write inside the
  workspace it was given and nothing wider.

  The `-c` grant is the second, and it is the difference between reaching the
  engine and reaching a receipt. A workspace-write turn prints its own roots
  in its header, "sandbox: workspace-write [workdir, /tmp, $TMPDIR]", and
  still refuses `<workdir>/.git`. Brother isolates each unit with `git
  worktree add`, which writes `.git/worktrees/`, so without the grant every
  unit is refused with "isolation could not be established" and the receipt
  records a FAIL. Measured both ways on 2026-09-05: the same turn with the
  grant creates the worktree at exit 0. Keep the grant to the toy's own
  `.git` and nothing wider: on a real project it also lets the model rewrite
  that repository's history, which is a deliberate act rather than a default.

  WHAT THE ENGINE'S OWN MODEL CALLS DO INSIDE THIS SANDBOX, and why there is
  no third flag. Brother's engine spawns its decomposer and its per-unit
  worker as CHILD PROCESSES of this turn, so the sandbox governs them, and
  three things were measured on 2026-09-05 rather than assumed.

  1. `workspace-write` blocks EVERY socket a command in the turn opens,
     loopback included. From a stub-provider probe, so no credential and no
     spend was involved:

         BLOCKED 1.1.1.1   PermissionError [Errno 1] Operation not permitted
         BLOCKED 127.0.0.1 PermissionError [Errno 1] Operation not permitted

     A model CLI in that position does not report a network problem. It
     reports itself not logged in and exits 1, and the door then refuses
     three attempts running with "door: refused after 3 attempt(s), store
     untouched". That is exactly the FAIL this gate returned on both
     binaries before any of this was understood.

  2. `-c sandbox_workspace_write.network_access=true` does lift it (the same
     probe then prints `REACHED 1.1.1.1`, and a nested `claude -p` answers at
     exit 0 inside the turn). It is NOT in the command above on purpose, and
     point 3 is why.

  3. A nested `codex exec`, which is what the door falls back to on a Codex
     host, cannot start inside a codex turn AT ALL, with or without the
     network. Verbatim from the signed-in run:

         Error: failed to initialize in-process app-server client:
                Operation not permitted (os error 1)

     So the seam route below is the documented one, and it is documented
     because it needs no whole-turn widening and no second vendor
     credential. The network-plus-`claude` route in point 2 is measured, not
     chosen: `scripts/model_worker.py` exposes `BROTHER_MODEL_CLIENT` to
     select the `claude` adapter over the `codex` one, so with the network
     grant a nested `claude -p` DOES answer inside the turn and the engine's
     own worker can run that way, but only by widening the whole turn's
     network access and by holding a second vendor's credential, which is a
     deliberate trade rather than a default. `danger-full-access` against a
     nested `codex exec` has not been measured at all, so it stays
     unmeasured, not impossible.

  THE ROUTE THAT DOES WORK, and the one the skill tells the model to take:
  hand the engine the units through its documented seam, which makes no model
  call at all.

      DOOR_MODEL_CMD="cat plan.json" python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" --cwd "$PWD" --runs-root "$TMPDIR/brother-runs"

  `plan.json` is a JSON list of units, each with `id`, `objective`,
  `done_check`, `writes` and `deps`. The engine still isolates every unit,
  still runs every `done_check`, and still writes the receipt; only the
  decomposition came from the agent driving the turn rather than from a
  nested model. `writes` must name EVERY file the unit will change or
  create, both `mathlib.py` and `test_mathlib.py` for the toy task; a file
  changed outside `writes` fails the scope audit and the whole unit reads
  QUARANTINE, never integrated. The per-unit worker takes the same
  treatment through `MODEL_WORKER_CMD`, and that worker script must exit 0
  once it is done editing: a non-zero exit, whether a stray traceback or a
  `set -e` trip firing after the writes already landed, makes
  `model_worker.py`'s `main()` return 3 before `collect_artifacts` or
  `commit_changes` ever run, so the edit is lost before anything is
  committed and the unit's receipt reads NO-DATA.

  `--runs-root` is the third thing this step needs, and its absence is the
  2026-09-05 signed-in failure. The engine keeps its records OUTSIDE the
  repository it integrates into, and the default is its own tree: under a
  plugin install that tree is read only, so the first engine call died with
  `PermissionError: [Errno 1] Operation not permitted` on the installed
  plugin's own `docs` path. `$TMPDIR` is one of the roots a workspace-write
  turn already grants. Never point it at a directory inside the toy: the records
  then sit untracked inside the very tree integration checks for
  cleanliness, and a run pointed there once spun 11 rounds of live worker
  calls before it was killed by hand.

  NOTHING ELSE IS WRITTEN INTO THE TOY. The turn goes straight to the engine:
  no STATE.md, no `.sbe/`, no fence file, no task registry. Those are what
  the 2026-09-05 run left behind, and they made the tree dirty before the
  first unit ran, so all three worker attempts passed their own done checks
  and integration then refused every one of them.

  A write outside every granted root is dropped SILENTLY, with no error line
  and exit 0. So if condition 3 below fails while conditions 1 and 2 pass,
  the thing to look for is a path the sandbox never granted.

  PASS, and this is the whole point of the gate, is FOUR things together:
  1. the turn ends at exit 0 with no 401,
  2. `hook: SessionStart` and `hook: PreToolUse` appear in its transcript,
     which is Brother's hooks running inside a real Codex turn,
  3. the engine prints `brother_run: receipt: <path>`, and that file exists,
  4. that receipt's per-file lines name `mathlib.py`, the exact check command
     that decided it, and an exit code, in the shape shown above.

  A turn's exit 0 proves NOTHING about writes, so it is never on that list:
  a write outside a granted root is dropped silently and the turn still ends
  green, which means only the receipt this gate reads and the toy's own `git
  status --porcelain` say whether anything landed.

  A run that changes the files but leaves no receipt is a FAIL of this gate,
  not a partial pass: the receipt is the deliverable.

  THE 2026-09-05 18:02 JST SIGNED-IN FAILURE, and the three rules it produced.
  The founder's own signed-in Codex turn ran this exact step 6 command against
  Brother v1.0.6 (row X8). It correctly wrote no STATE.md and no `.sbe/`, used
  `--runs-root /tmp/brother-add-runs`, and reran with `DOOR_MODEL_CMD='cat
  /tmp/brother-add-plan.json'` after the expected door refusal. The one unit's
  `done_check` was `python3 -m unittest`, which already passed on the
  untouched toy before any work happened. The engine's intent screen printed
  its own warning and proceeded on the recorded default, and the per-unit
  worker ran with no `MODEL_WORKER_CMD` set, so it fell back to its own
  default argv, a nested `codex exec`, which cannot start inside a Codex turn
  and finished in about a second having changed nothing. Because the check
  was already green, the unit read integrated anyway. Verbatim from that run's
  own receipt and report:

      files changed (0): none
      the check already passed before the work began, so it cannot prove the work

  Codex then ended the turn with a question instead of a verdict, verbatim:

      May I retry Brother with a check that requires the new rejection test?

  THE MASKING LESSON, found while writing the fix: `scripts/test_codex_smoke.py`'s
  own `TheDocumentedCommand` sets `MODEL_WORKER_CMD` in its own test
  environment before ever invoking the engine, so it proved a seam the
  documented turn never used. The founder's real turn named no worker command
  at all, which is exactly the gap that produced the 18:02 failure. The
  regression this fix adds, `TheSignedInShape` in the same file, runs the
  exact signed-in shape: no `MODEL_WORKER_CMD` override in one case, and both
  seams set from the start in the other, so the harness can no longer be
  green on a seam nobody in the documented command actually sets.

  THE THREE RULES, now also carried in `bundle/skills/using-brother/SKILL.md`:

  1. Each unit's `done_check` must fail BEFORE any work happens. Run it
     yourself against the untouched repository first; a check already exiting
     0 is what produced "files changed (0): none" above, and no worker can
     fix a check that was never able to fail. Never write a bare-path check
     either: a done check must be runnable on the untouched tree and judged
     on its RESULT, never on a missing file. `python3 test_add_rejects.py`
     for a file that does not exist yet just prints "No such file or
     directory", which the engine treats as a broken check and refuses
     before any worker starts, and under `cat plan.json` there is no
     planner left to hand back a replacement check, so the unit is pulled
     out instead. Use `python3 -m unittest` plus the import based one-liner
     instead, the shape shown at "What a passing receipt looks like" above.
  2. Set both `DOOR_MODEL_CMD` and `MODEL_WORKER_CMD` from the first attempt,
     never only after a door refusal. Under Codex neither seam has a live
     model to fall back to (a nested `codex exec` cannot start inside a Codex
     turn), so an unset `MODEL_WORKER_CMD` is not a smaller failure than an
     unset `DOOR_MODEL_CMD`: it is the same failure, one step later, and it is
     the one this run actually hit.
  3. A NO-DATA receipt (the check already passed before the work began, or
     the files changed read none) means the agent's own check or script was
     wrong. It is never a stated-versus-observed contradiction and never a
     forcing condition under any injected law, L6 included: the agent
     rewrites the check, fixes the script, and reruns the engine in the
     same turn, without asking anyone. Ending a turn with a question instead
     of a receipt, the way this run did, is the failure these rules close.

When that passes, row C7's credentialled half closes, and the evidence to file
is the transcript of step 6 plus the receipt file it names.

## Uninstalling what the runbook wired

In the same shell (same `CODEX_HOME` if you used the throwaway one from step
0):

    python3 ~/brother-hub/scripts/codex_hooks_install.py --codex-home "$CODEX_HOME" --uninstall
    codex plugin remove brother@brother
    codex plugin marketplace remove brother

  PASS: the first line names what it removed, for example "removed 18 Brother
  hook command(s) across PostToolUse, PreCompact, PreToolUse, SessionEnd,
  SessionStart, Stop from <path>" and "removed Brother's trust section from
  <path>"; a second run answers "NO-DATA: nothing of Brother's to remove"
  rather than an error, at exit 0. `--uninstall` strips only the hook
  commands this installer wrote and only its own marked trust section in
  `config.toml`; any other hook already in that file survives. Do not delete
  `$CODEX_HOME/hooks.json` by hand instead: on a real, non-throwaway home it
  can hold hooks that are not Brother's, and `--uninstall` is what tells them
  apart. Write `brother@brother` in the plugin removal: `codex plugin remove
  brother` is refused as ambiguous. If you used step 0's real-home option,
  pass `--codex-home ~/.codex --allow-default-home --uninstall` here to
  match.

## What this gate still does not prove

1. A REAL MODEL'S TOOL SHAPES. The stub turn exercises one tool call. What a
   real model does across many, and how Brother's hooks read tool names other
   than the `Bash`-plus-`apply_patch` shape Codex normalises into, is unproven
   until the runbook above is run.
2. THE OTHER GATES ARE THE OTHER ROWS. Ship gate 7's own wording also asks for
   the existing Claude, macOS, Windows, release-invariant and public-export
   gates to be re-run beside this one. That is the battery, not this script:
   `sh scripts/required_fast.sh` and `sh scripts/check_all.sh`.
3. NOTHING IS WIRED BY AN INSTALL. Step 4 is a deliberate second act. Until a
   Codex user runs it, they have skills and no fence.
4. THE UNINSTALL ROUTE ABOVE IS UNIT-TESTED, NOT RUN THROUGH THIS RUNBOOK.
   `scripts/test_codex_hooks_install.py`'s `TestUninstall` cases and a live
   audit run drove `--uninstall`, including the foreign-hook-survives and
   second-run-NO-DATA cases; nobody has yet driven it inside this runbook's
   own signed-in Codex flow end to end.
