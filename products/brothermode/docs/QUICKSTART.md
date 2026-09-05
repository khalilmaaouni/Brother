# Quick start

Two ways in. Path 1 is the boring one: two plain commands pasted into a
terminal once, proven end to end by the release smoke on every release.
Path 2 is the pinned, step-by-step one, written for technical users and
auditors who want an immutable tagged snapshot instead of the plugin
manager's moving line. Pick one; do not do both.

Both paths install into Claude Code. If you drive a different runtime (OpenAI
Codex CLI, GitHub Copilot, Qwen Code, iFlow, Antigravity), read "Other
runtimes" at the bottom of this page before you start. The engine runs there,
the enforcement does not, and putting the instruction file in place is a copy
you make by hand.

## Path 1: install as a plugin (the simple way)

This path is proven, not promised: `scripts/release-smoke-install.sh` runs
it end to end inside a throwaway configuration on every release
(marketplace add, install, the installed version matched against `VERSION`,
every hook group registered, then a clean uninstall), and a release whose
smoke run does not print PASSED does not ship (docs/RELEASE.md, step 4b).
The first recorded cycle is in
docs/evidence/2026-07-31-first-plugin-install.md and the smoke has carried
the proof on every release candidate since 2026-08-07.

No file editing of any kind happens on this path. You will not touch
`settings.json`, you will not edit JSON, and you will not run Python by
hand. The plugin brings its own automatic wiring with it.

```bash
claude plugin marketplace add khalilmaaouni/Brother
```

```bash
claude plugin install brothermode@brother
```

REPOINTED 2026-09-03: this used to name the standalone `BrotherModeUp`
repository, pinned to its own last tag. That repository is now private and
archived; BrotherMode ships from the Brother hub, and these two lines are
byte identical to [`README.md`](../README.md)'s own `## Install` section on
purpose, so the command never has two independently-typed copies that can
drift apart (`tools/test_bm_docs.py` checks the two pages agree). The hub
carries several products, so its marketplace name is `brother`, not this
product's own name.

Already running v2? Uninstall it first (`claude plugin uninstall
brotherme`). The plugin identity changed at v3.0.0, so the old and new ids
are different plugins to Claude Code and installing both leaves two hook
chains wired at once.

One surface note, corrected against vendor documentation rather than
assumed: `/plugin` itself opens an interactive panel in the terminal CLI,
not the desktop app (https://code.claude.com/docs/en/discover-plugins),
which is why the paste above happens in a terminal once, and
founder-reproduced 2026-08-06 confirms the same for the desktop app
directly. That is narrower than "the desktop app cannot install plugins":
once this marketplace has been added, the desktop app's own **+** button,
then **Plugins**, then **Add plugin**, opens a plugin browser over your
configured marketplaces and installs from there with no terminal
(https://code.claude.com/docs/en/desktop, "Install plugins"); this
project's own proof, `scripts/release-smoke-install.sh`, exercises the
terminal path above, not that browser. Inside the terminal client the
interactive forms (`/plugin marketplace add ...`, `/plugin install ...`)
do the same thing as the two commands above. Then, in any Claude Code
session, type:

```
/brothermode:help
```

That command explains what you have and what to do next in plain language.
When you are ready to try it on something real, type `/brothermode:start` and
describe what you want in your own words; it will guide you from there, one
decision at a time. The first time you start a project it asks where your
private project memory should live before writing anything there; if that
question is ever skipped, the automatic session records fall back to a folder
called `BrotherModeVault` in your home folder, and you can ask to move it.

To remove it later: `claude plugin uninstall brothermode` in a terminal, or `/plugin uninstall brothermode` inside the terminal client.

To update later: type `/brothermode:update` and it walks you through it, or run
the two lines it wraps yourself: `/plugin marketplace update
brother`, then `/plugin update brothermode`, then restart Claude
Code. That is the whole path. The rest of this page is Path 2 and applies only
if you skipped Path 1.

## Path 2: install by git clone (the verified way)

A literal, ten-minute path from nothing installed to seeing this thing do
something real. Every command below was run, as written, in a scratch copy of
this repository before this page was published. Where a step takes longer than
a few seconds, the real timing is stated so you know whether to wait or worry.

If a command's output does not match what is described here, stop and compare
carefully before continuing: a mismatch usually means a path is wrong, not that
something is broken.

## 1. Install the skill

The public default clones an immutable, tagged release, not a moving branch:
the tag is generated from the same release fact every other page reads
(`python3 tools/bm_project_facts.py --field install_target_tag`), the last
tag actually cut and known to resolve, never typed by hand, and
`tools/test_bm_docs.py` fails this page if it ever disagrees. The
development tree carries whatever development identity `cat VERSION` prints
in your checkout, a `.dev` identity rather than a tagged release, and this
page deliberately does not type that identity by hand: a typed version goes
stale the day after it is written; `docs/RELEASE.md` explains why.

REPOINTED 2026-09-03: the repository cloned below is now the Brother hub,
which ships several products, and BrotherMode lives inside it at
`products/brothermode`. A plain clone of the whole hub does not put
`SKILL.md` at its own top level, so the clone lands in a source checkout,
not the skill directory itself, and the second line below moves into this
product's own subdirectory before anything else runs.

```bash
git clone --branch v1.0.7 --depth 1 https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-src
cd ~/.claude/skills/brothermode-src/products/brothermode
```

Expected: git prints a few lines ending in something like `Resolving deltas:
100% (N/N), done.` This clone is step 1 of 3, not the finished install:
nothing is wired yet, and nothing has been copied into
`~/.claude/skills/brothermode` yet either. Step 3 below (`scripts/install.py`)
is the step that copies the tree there, wires the hooks, and stamps the
result with the commit it was installed from; skip it and you have a folder
of files Claude Code never reads. One thing no step on this path ever does:
register with Claude Code's own plugin manager (the list `claude plugin
list` shows). That registry is written only by `claude plugin install`
(Path 1 above). A clone install's health is checked by this project's own
`scripts/doctor.py` instead, in step 3. Verify the clone landed:

```bash
ls SKILL.md
```

Expected: `SKILL.md` printed back (you are already in
`products/brothermode` after the `cd` above). If you get "No such file or
directory", the clone did not finish or landed somewhere else.

Same dated fact as Path 1 above: `v3.0.0` predates the night rename, so an
old checkout at that tag carries the old flat `commands/brotherme-*.md`
surface and the single `skills/brotherme/SKILL.md` conductor, not the nine
`/brothermode:*` skills this project ships today; the tag pinned above is
newer than that and does not have this problem. The engine underneath
(`tools/bm_*.py`, `scripts/install.py`, `scripts/doctor.py`) is the same
either way; only the command and skill names differ. If you want today's
tree, use the development clone command below instead.

Working on BrotherMode's own code instead of just using it? Use the separate
development command, which tracks the moving `main` branch on purpose and
installs into its own directory so the two can never be confused:

```bash
# Development branch (changes over time)
git clone --branch main https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-dev-src
cd ~/.claude/skills/brothermode-dev-src/products/brothermode
```

Its own step 3 passes `--target ~/.claude/skills/brothermode-dev` to
`scripts/install.py` below, so the two checkouts can never land in the same
place.

## 2. Run the gate, to prove it works on your machine

Still in `products/brothermode` from step 1 (`pwd` should end in
`brothermode-src/products/brothermode`):

```bash
python3 tools/test_all.py
```

Expected: a line per suite, then a closing line reading `ALL GREEN`, and exit
code 0. Run it and expect ALL GREEN; that verdict is the check, not any
particular number of tests. It runs each suite in its own process, one at a
time, which is why it takes several minutes rather than seconds. That is the
cost of the isolation, not a hang.

A couple of skips are normal and are not failures: one is a check for a
shell-script autosave version this project no longer ships, the other needs a
filesystem that supports making a file read-only, which not every sandbox does.
A skip is reported as a skip; the gate still ends ALL GREEN.

No test count appears on this page on purpose. Counts move every time a test
lands, so a page that pins one teaches you to distrust the page instead of the
tree. If you want to know what the gate covers, `python3
tools/bm_project_facts.py` prints the suite list straight out of
`tools/test_all.py`. Dated counts, tied to the commit they were true of, live in
`../CHANGELOG.md`.

If you see any line starting `FAIL` or `ERROR`, or a closing line that is not
`ALL GREEN`, stop here: something about your Python or platform does not match
what this project expects, and installing the rest is not worth doing until that
is understood.

A single suite still runs on its own, and that is worth knowing while you are
working on one of them:

```bash
python3 tools/test_bm.py
```

One suite passing is not the gate, though. `test_all.py` runs the suites
serially so that two runs sharing one checkout do not interleave. HISTORICAL
NOTE, kept because an older copy of this page described it as current: the
suites once renamed a module aside mid-run, which could corrupt two
concurrent runs; the P9 fix round removed that rename, and the header of
`tools/test_all.py` records both the old hazard and its removal.

## 3. Wire the hooks

This step makes the parts that must never be forgotten (telemetry, the
pre-compaction safety snapshot) run automatically instead of depending on the
model remembering to run them. It also does the copy step 1's note mentioned:
still in `products/brothermode`, this copies the tree into
`~/.claude/skills/brothermode` because that is a different directory from the
source checkout you cloned into. One command does it:

```bash
python3 scripts/install.py --dry-run
python3 scripts/install.py
```

Run the `--dry-run` first. It prints every change and writes nothing, so you
see what is about to happen to your `settings.json` (and what would be
copied) before it happens.

Expected from the real run: a list of six hooks (`SessionStart`, `SessionEnd`,
`Stop`, `PreCompact`, `PreToolUse`, `PostToolUse`), a line naming the backup of your previous
settings, and a closing line reading `smoke: the fence hook ran end to end and
exited 0`. That last line is the point. The installer re-reads what it wrote
and actually executes the one hook that can refuse a write, so "installed"
means checked rather than attempted.

Six, not the five an earlier version of this page listed. `PreToolUse` is the
fence hook (`docs/HOOKS.md`), the one hook that can refuse a write, and it now
carries a second entry beside it, matched on `Bash`: `bm_bash_audit.py pre`
records the size, mtime and sha256 of every fenced file before a shell command
runs. `PostToolUse` is the other half of that pair, `bm_bash_audit.py post`,
which re-hashes those same files afterwards and raises one alert when a shell
write changed a file another session's fence covers. Detection, not prevention,
on purpose: by the time the alert exists, the write already happened.

What the installer will NOT do: overwrite an existing BrotherMode installation
(it refuses and tells you to pass `--upgrade`), rewrite a `settings.json` that
is not valid JSON (it refuses and points at the parse error rather than
throwing away what you were editing), or remove a hook of your own. An entry
counts as BrotherMode's only when every command in it names this
installation's own `tools/bm_*` files.

Check the result is valid JSON. The installer already did this and refuses to
report success otherwise, but run it once yourself so you know the command:

```bash
python3 -m json.tool ~/.claude/settings.json
```

Expected: the file prints back, reformatted, with no error.

Then prove the fence is not just wired but LIVE, and check the rest of the
install at the same time:

```bash
python3 ~/.claude/skills/brothermode/scripts/doctor.py
```

Doctor runs eleven checks, each printing PASS, FAIL with a one-sentence fix, or
SKIP with the reason nothing could be checked yet (SKIP is not a failure).
Add `--json` instead of reading the plain text if a script needs to consume
the result.

| # | Check | A FAIL means, in plain words |
|---|-------|-------------------------------|
| 1 | Fence hook wired and live | The blocked-write simulation below: builds a throwaway project, claims one file under one session, then asks the hook you actually wired to approve an edit of that file from a different session. A healthy fence refuses, then allows the same write when the owner asks; a hook that denies everything would pass only the first half and would be a brick, not a fence. Nothing outside a temporary directory is touched. A FAIL names which way it is dead: no `PreToolUse` entry, an entry pointing at a file that is not there, a matcher that leaves some write tools ungated, or a hook that runs and refuses nothing. |
| 2 | VERSION matches the plugin manifest | `VERSION` and `.claude-plugin/plugin.json` disagree about which release this is. |
| 3 | python3 3.9+ and git on PATH | One of those two is missing from this machine. |
| 4 | Setup has been completed | Run `python3 scripts/setup.py`; nothing below this line can be checked before that. |
| 5 | Vault path exists and is writable | Create it (`cp -R vault-template <your vault path>`) or fix its permissions. |
| 6 | Only one install method is wired | Both the plugin and a clone install are wiring hooks in the same `settings.json`, so every hook fires twice. Remove one: `/plugin uninstall <name>` or `python3 scripts/uninstall.py`. |
| 7 | Project store health | A `.brothermode/store.sqlite3` under the current directory failed its own `verify`; SKIP, not FAIL, when there is no store here yet. |
| 8 | Hook wiring matches installation_mode | The consent config says `plugin` or `clone` and the hooks actually wired disagree with it. |
| 9 | CHECKSUMS.sha256 self-check | A shipped file does not match the release manifest, the signature of an update that did not finish. |
| 10 | settings.json is valid JSON | Claude Code silently ignores a broken settings file, so every hook, not only the fence, is off. |

Checks 4, 5 and 8 SKIP until setup has run (`python3 scripts/setup.py`); that
is expected on a machine that just finished Step 3 above and has not yet
created a vault. Every other check applies from the moment the hooks are
wired.

To remove the wiring later:

```bash
python3 ~/.claude/skills/brothermode/scripts/uninstall.py
```

It removes only the entries it installed, leaves the files in place unless you
pass `--remove-files`, and never touches your vault.

### If you would rather wire it by hand

The installer writes the equivalent of the block below. All six events are
here, fence included, and so is the `Bash` audit pair: an earlier version of
this page stopped at four, which meant anyone wiring by hand ended up with the
one-writer-per-file promise switched off and nothing saying so. Merge it into
any hooks you already have.
Use the absolute path to your checkout rather than `~`: the installer writes
absolute, shell-quoted paths precisely because a home directory containing a
space breaks the unquoted form.

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/bm_sessionstart.py", "timeout": 30 } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/bm_telemetry.py outcomes-append", "timeout": 30 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/bm_hookchain.py stop", "timeout": 30 } ] }
    ],
    "PreCompact": [
      { "hooks": [ { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/bm_hookchain.py precompact", "timeout": 60 } ] }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/bm_fence_hook.py", "timeout": 10 },
          { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/vault_recall_hook.py check", "timeout": 10 }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [ { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/bm_bash_audit.py pre", "timeout": 10 } ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [ { "type": "command", "command": "python3 ~/.claude/skills/brothermode/tools/bm_bash_audit.py post", "timeout": 15 } ]
      }
    ]
  }
}
```

The `matcher` on the fence entry is the list of write tools the fence gates. Drop
a tool from it and writes through that tool are ungated, which is one of the
failure modes `scripts/doctor.py` looks for.

A `json.decoder.JSONDecodeError` from the check above means a comma or brace is
wrong; fix it before starting Claude Code, or Claude Code will simply ignore
the broken hooks block and every hook is off with nothing saying so. That
silent failure is the whole reason the installer exists. See `docs/SETUP.md`
for what each hook actually costs you when it fails.

## 4. Point the vault somewhere

The vault is a plain folder of markdown and JSONL files that holds this
project's memory: session logs, telemetry, and (if you use it) the founder
model. Nothing here talks to a server; it is just files on your disk.

```bash
cp -R ~/.claude/skills/brothermode/vault-template ~/BrotherModeVault
export BROTHERMODE_VAULT="$HOME/BrotherModeVault"
```

Add the `export` line to your shell profile (`~/.zshrc` or `~/.bashrc`) so it
survives a restart, or set it in the `env` block of `~/.claude/settings.json`
instead. Verify the copy landed:

```bash
ls ~/BrotherModeVault/Home.md
```

Expected: that path printed back.

That folder existing is not the same as setup being complete. Doctor's check
4, "Setup has been completed," still reads FAIL until `scripts/setup.py` runs
and records the consent that names this folder as your vault; check 4's own
remediation text names this exact command. Run it now, non-interactively,
with the same path you just created:

```bash
python3 ~/.claude/skills/brothermode/scripts/setup.py --vault ~/BrotherModeVault --mode clone --accept-notice
```

Expected: a line reading `setup: config written to ~/.brotherme/config.json`,
the vault path and installation mode you just gave it printed back, then
doctor's own output printed inline (checks 4, 5 and 8 should now read PASS),
and a closing line naming the next action. This is the one command doctor's
remediation text points at directly; skipping it is why check 4 fails on an
otherwise-correct install.

## 5. Verify the installation

```bash
python3 ~/.claude/skills/brothermode/tools/bm_score.py
```

Expected on a fresh vault: ten checks, most saying `NO-DATA` (correct: you
have no history yet, and this tool refuses to invent one), a few saying
`PASS`, and a closing line reading `10 checks: N PASS, N FAIL, N NO-DATA. LLM
judge scores only the residue.` One check, `budget-vs-tier`, can show a `FAIL`
that names `STATE.md`: that specific failure is about THIS repository's own
internal working file (the one its authors use to build it), not about
anything you have done, and it does not affect the vault, the hooks, or your
project. Also run the session-start hook by hand once, to see what a new
session will actually be shown:

```bash
python3 ~/.claude/skills/brothermode/tools/bm_sessionstart.py
```

Expected: the digest text (about twelve lines summarizing the active laws),
followed by a line saying the weekly review has never run. That nag is a
to-do, not an error: you have not had a first week yet.

## 6. Invoke it once, on one real task

Open Claude Code in any project, and type:

```
/brothermode read this project's README and tell me the three biggest risks
```

Pick a real, small task like this one: it needs to read at least one file and
think for more than one turn, because the telemetry hook only records a
session that did some real work (fewer than 5 model turns or zero tool calls
and nothing is written, on purpose, so the ledger cannot be padded with
trivial sessions). Let the session run to a natural end.

## 7. See the evidence

```bash
tail -1 "$BROTHERMODE_VAULT/99-System/telemetry/outcomes.jsonl"
```

Expected: one line of JSON ending in the session's token counts, tool call
count, and model name, for example a `tool_calls` field of 1 or more and a
`models` field naming what you used. That line is written by the `SessionEnd`
hook, not by the model narrating that it happened, which is the whole point:
this is proof the mechanism ran, not a claim that it did. If the file does not
exist or the line is missing, the most common cause is a session too short to
clear the activity floor in step 6, or the hooks block from step 3 not having
been picked up (Claude Code reads hook configuration at startup, so a session
already running when you edited `settings.json` will not have it).

## What you have now

The six hooks running automatically, a vault of your own, and one proof that
the telemetry mechanism works end to end. What you do NOT yet have from this
alone: a history (that takes real weeks of use), a felt-outcome rating trend,
or a weekly review (`tools/WEEKLY-REVIEW.md`, run it once your first week of
real sessions has landed). Read `../README.md`'s status section and
`KNOWN-LIMITS.md` before deciding how much to lean on anything described here
as more proven than it is.

## Other runtimes (Codex, Copilot, Qwen, iFlow, Antigravity)

Everything above installs into Claude Code. The engine itself is standard
library Python driven from a shell, so it runs anywhere a shell runs. What does
not travel is the enforcement: the pre-write hook that refuses an edit to a file
somebody else owns is verified in Claude Code and nowhere else.

There is no installer for another runtime. What ships is a generated instruction
file per runtime, already committed, that you copy into place yourself:

```bash
ls docs/runtimes/
# codex.AGENTS.md  generic.AGENTS.md  copilot.copilot-instructions.md
# qwen.QWEN.md  iflow.IFLOW.md  antigravity.brothermode.md
```

For OpenAI Codex CLI, the copy is one of these, and it is yours to make:

```bash
cp docs/runtimes/codex.AGENTS.md /path/to/your/project/AGENTS.md   # one project
cp docs/runtimes/codex.AGENTS.md "${CODEX_HOME:-$HOME/.codex}/AGENTS.md"  # every project
```

If your project already has an `AGENTS.md`, merge by hand. The generator
deliberately refuses to write into either destination, because an `AGENTS.md` at
a repository root usually already has content in it.

Three things the file itself will tell you, and that are worth knowing before
you decide:

- Call the tools by the ABSOLUTE path to this checkout. `python3
  tools/bm_store.py` only resolves when your working directory is this
  repository; in your own project it fails with `[Errno 2] No such file or
  directory`.
- Run `bm_store.py init` in a project before anything else, and start Codex with
  a writable sandbox (`-s workspace-write`). On its default read-only sandbox the
  store cannot create itself.
- Do NOT wire BrotherMode's hooks into Codex. Measured on 2026-08-05 against
  codex-cli 0.146.0: Codex reports every file write as a Bash call running
  `apply_patch`, so BrotherMode's matcher never fires and the fence hook exits
  quietly without deciding anything. In Codex the law is advisory. The store
  still refuses an overlapping claim; nothing refuses a write.

`RUNTIMES.md` is the full page: which file each runtime reads, which vendor page
that came from, what each runtime needs first, and what was measured rather than
assumed.
