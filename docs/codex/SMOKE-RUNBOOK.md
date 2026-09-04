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
captured rather than predicted. The toy repository is the README's own: a git
repository holding `mathlib.py` (a bare `add`) and `test_mathlib.py`.

    $ /Applications/ChatGPT.app/Contents/Resources/codex exec -C <toy repo> \
        "use the Brother plugin to make add() refuse non-numeric input and cover it with a test"
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

    $ ... codex exec --skip-git-repo-check -c model_provider="c7stub" ... -C <toy repo> \
        "use the Brother plugin to make add() refuse non-numeric input"
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
    printf 'from mathlib import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n' > test_mathlib.py
    git add -A && git commit -q -m toy

Step 6, the task:

    codex exec -C "$PWD" "use the Brother plugin to make add() refuse non-numeric input and cover it with a test"

  PASS, and this is the whole point of the gate, is FOUR things together:
  1. the turn ends at exit 0 with no 401,
  2. `hook: SessionStart` and `hook: PreToolUse` appear in its transcript,
     which is Brother's hooks running inside a real Codex turn,
  3. the engine prints `brother_run: receipt: <path>`, and that file exists,
  4. that receipt's per-file lines name `mathlib.py`, the exact check command
     that decided it, and an exit code, in the shape shown above.

  A run that changes the files but leaves no receipt is a FAIL of this gate,
  not a partial pass: the receipt is the deliverable.

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
