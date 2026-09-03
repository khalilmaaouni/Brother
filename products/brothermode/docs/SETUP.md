# Setup

Ten minutes, three steps: install the skill, wire the hooks, create the vault. Nothing here needs admin rights or third-party packages.

For a version of this with copy-pasteable commands, expected output at every
step, and one concrete piece of evidence at the end, see `docs/QUICKSTART.md`.
This page is the reference: the full hook explanation and the first-week
checklist that QUICKSTART.md points back to.

This whole page describes a Claude Code install. If you drive another runtime,
read "Other runtimes" near the bottom: the store and the retrieval commands work
there, the hooks do not, and the install is a file you copy by hand.

## Prerequisites

- Claude Code (CLI or desktop app) with skills enabled
- Python 3 on your PATH (`python3 --version`)
- git

## Step 1: install the skill

The public default clones an immutable, tagged release, not a moving branch:
the tag is generated from the same release fact every other page reads
(`python3 tools/bm_project_facts.py --field install_target_tag`), the last
tag actually cut and known to resolve, never typed by hand, and
`tools/test_bm_docs.py` fails this page if it ever disagrees. The
development tree carries whatever development identity `cat VERSION` prints
in your checkout, a `.dev` identity rather than a tagged release, and this
page deliberately does not type that identity by hand; `docs/RELEASE.md`
explains why.

THE BORING INSTALL, the default: two plain shell commands through Claude
Code's own plugin manager, proven end to end by
`scripts/release-smoke-install.sh` on every release. Paste once in any
terminal.

```bash
claude plugin marketplace add khalilmaaouni/BrotherModeUp@v3.4.2
claude plugin install brothermode@brothermode-marketplace
```

Already running v2? Uninstall it first (`claude plugin uninstall
brotherme`). The plugin identity changed at v3.0.0, so the old and new ids
are different plugins to Claude Code and installing both leaves two hook
chains wired at once.

`@v3.3.1` pins the marketplace add itself to the released tag rather than
the repository's moving default branch, generated from the same fact the
pinned clone below reads (`python3 tools/bm_project_facts.py --field
install_target_tag`); `docs/RELEASE.md` step 2b makes re-pinning it an
explicit release step, and `tools/test_bm_docs.py` fails this page if the
pin ever disagrees.

Three different things share the word "install" here. Adding this
repository as a marketplace for the first time is what the command above
does, and vendor documentation shows that done only from `/plugin
marketplace add` or the shell command above, both terminal-backed; it does
not document a desktop-app GUI path for registering a brand new
marketplace source (https://code.claude.com/docs/en/discover-plugins,
"Add marketplaces"). Once that marketplace is added, installing FROM it
can happen either in the terminal (the second command above, or the
interactive `/plugin install ...` form, since `/plugin` itself opens only
in the terminal CLI per that same page) or inside the desktop app with no
terminal at all, through its own **+** button, then **Plugins**, then
**Add plugin**, which opens a plugin browser over configured marketplaces
(https://code.claude.com/docs/en/desktop, "Install plugins").
Founder-reproduced 2026-08-06 confirms the narrow fact behind this: the
`/plugin` slash form does not run inside the desktop app, not that the
desktop app cannot install plugins at all. `scripts/release-smoke-install.sh`
proves the terminal path above end to end; it does not exercise the
desktop browser, which this project has not separately verified.

THE PINNED CLONE, for auditors and immutable-snapshot installs:

```bash
git clone --branch v3.4.2 --depth 1 https://github.com/khalilmaaouni/BrotherModeUp.git ~/.claude/skills/brothermode
```

The path matters: Claude Code discovers skills under `~/.claude/skills/`, and the session-start script resolves its own location, so the clone is the installation. Verify:

```bash
ls ~/.claude/skills/brothermode/SKILL.md
```

Same dated fact as the boring install above: `v3.0.0` predates the night
rename, so this checkout carries the old flat `commands/brotherme-*.md`
surface and the single `skills/brotherme/SKILL.md` conductor, not the nine
`/brothermode:*` skills this project ships today. The engine underneath
(`tools/bm_*.py`, `scripts/install.py`, `scripts/doctor.py`) is the same
either way; only the command and skill names differ. If you want today's
tree, use the development clone below instead.

Working on BrotherMode's own code instead of just using it? Use the separate
development command, which tracks the moving `main` branch on purpose and
installs into its own directory so the two can never be confused:

```bash
# Development branch (changes over time)
git clone --branch main https://github.com/khalilmaaouni/BrotherModeUp.git ~/.claude/skills/brothermode-dev
```

Then register the trigger in your global `~/.claude/CLAUDE.md` so every session knows the skill exists:

```markdown
# brothermode
When the user types /brothermode (any casing), read and follow
~/.claude/skills/brothermode/SKILL.md before doing anything else.
```

## Step 2: wire the six hooks

Hooks make the learning loop mechanical: the model cannot forget to write
telemetry, because the model is not the one writing it. Run the installer:

```bash
python3 ~/.claude/skills/brothermode/scripts/install.py --dry-run
python3 ~/.claude/skills/brothermode/scripts/install.py
```

`--dry-run` writes nothing and prints every change it would make. Run it first.

Six hooks, not the five earlier versions of this page listed, and not the four
the version before that listed. The fifth is `PreToolUse`, the fence hook
(`docs/HOOKS.md`): the only hook that can actually refuse a write across
another session's claim. It was documented for weeks and was in no install
instruction, which meant the project's headline promise, one writer per file,
was off by default on every installation that followed this page. The sixth is
`PostToolUse`, the second half of the `Bash` audit pair
(`tools/bm_bash_audit.py`): `PreToolUse` also carries that pair's first half,
which records the size, mtime and sha256 of every fenced file before a shell
command runs, and `PostToolUse` re-hashes those same files afterwards and
raises one alert when a shell write changed a file another session's fence
covers. That pair detects, it never refuses.

Useful flags:

- `--upgrade` is required before the installer will touch an installation that
  already exists. Without it, a second run refuses and changes nothing.
- `--target DIR` and `--settings FILE` install somewhere other than
  `~/.claude/skills/brothermode` and `~/.claude/settings.json`.
- `--no-hooks` copies the files and leaves your settings alone.

What it guarantees, and these are tested rather than asserted
(`tools/test_install.py`, run by `python3 tools/test_all.py`):

- It never removes a hook entry it did not write. An entry is BrotherMode's
  only when every command inside it names this installation's own `tools/bm_*`
  files, so a group you have added your own hook to is left completely alone.
- It refuses a `settings.json` that is not valid JSON instead of rewriting it,
  and reports the parser's own line and column. Rewriting would throw away
  whatever you were halfway through editing.
- It backs up `settings.json` before every write, to
  `settings.json.brothermode-backup-<timestamp>`.
- It re-reads and re-parses what it wrote, then runs the fence hook end to end
  from a throwaway directory and requires exit 0, before printing success.
- Re-running with `--upgrade` is idempotent: no duplicated hook entries.

Windows: the installer refuses, with a message naming the reason. Two of the
seven hook commands are POSIX shell (seven commands across six events, because
`PreToolUse` carries two), so on cmd.exe or PowerShell they would be wired and
silently dead. Install inside WSL, or wire the five python3-only hook commands
by hand and accept that `SessionStart` and `PreCompact` are off.

An upgrade adds and overwrites files; it never deletes. A file removed upstream
since your last install stays behind, and `scripts/verify-install.sh` reports
exactly those as `EXTRA`.

### Wiring by hand instead

The installer writes the equivalent of the block below, with absolute and
shell-quoted paths. All six events are here, fence and `Bash` audit pair
included: earlier versions of this page listed four and left the `PreToolUse`
fence to a cross-reference, which in practice meant a hand-wired install ran
with the one-writer-per-file promise switched off. Add to
`~/.claude/settings.json` (create the file if it does not exist, or merge into
your existing `hooks` block):

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

What each hook does, and honestly, what a FAILED hook costs you. The six
hooks do not all cost the same thing when they break, and treating them as
equivalent would be dishonest: losing a data point is not the same class of
loss as losing your ability to recover from a crash.

- **SessionStart** injects `DIGEST.md` (the 12-line law summary) plus any overdue-review nag into every new session's context.
  If this hook fails: the new session simply starts without the digest and
  nag text. Nothing is lost, nothing is recorded wrong; the session just
  begins less informed than it should.
- **SessionEnd** parses the finished session's transcript and appends one telemetry line to the ledger: tokens, tool calls, agents spawned, duration, models used. It also scans your short messages for correction candidates.
  If this hook fails: that one session's telemetry line and correction scan
  never happen. This costs you telemetry, one data point in the learning
  loop, nothing more. Your work, your files, and your vault notes are
  unaffected.
- **Stop** warns (never blocks) when a substantial session ends the day without a vault session log.
  If this hook fails: you simply do not get reminded to write a session log.
  It is advisory only; it writes nothing and protects nothing.
- **PreCompact** snapshots your working tree to a private git ref right before Claude Code compacts context (the token-death moment), so progress survives. Local git only, never pushes; recover with `python3 tools/bm_autosave.py recover`. It also writes a resume brief distilling the dying transcript (the last instruction, recent decisions, recent commands) so a resumed session recovers the thread, not just the files. Before a long or risky action you can log `python3 tools/bm_telemetry.py intent "next: X, because Y"` so a death leaves a forward-looking record. Optional: also add a `PostToolUse` hook running `bm_autosave.py tick` and set `BROTHERMODE_AUTOSAVE=1` for continuous autosave between compactions (costs a hook per tool call).
  If this hook fails: this is the one that can actually cost you something
  real. Your committed and already-saved files are never at risk either way;
  what a failed PreCompact hook costs is the EXTRA safety net: no snapshot of
  your uncommitted and untracked changes at the exact moment context gets
  compacted, and no resume brief telling the next session what you were in
  the middle of. That is recovery information, not telemetry, and it is not
  the same kind of loss: a missing telemetry line is a gap in a chart; a
  missing autosave snapshot or resume brief, hit at the wrong moment, can
  mean redoing work or re-explaining context you would otherwise have gotten
  back for free.
- **PreToolUse** is the fence hook (`docs/HOOKS.md`). It runs in front of every
  `Edit`, `Write`, `MultiEdit` and `NotebookEdit` and denies the write when the
  target is covered by an active claim another session owns. It is the only
  hook here that can refuse anything.
  If this hook fails: it fails OPEN, deliberately, and prints a line starting
  `bm_fence_hook: FAILING OPEN` to stderr. Nothing is blocked and nothing is
  lost, but the fence is back to being a ledger rather than a boundary for as
  long as it stays broken. The cost is not a lost data point, it is a
  guarantee quietly downgraded to a convention.
- **PostToolUse** is the second half of the `Bash` audit pair
  (`tools/bm_bash_audit.py`, described in `docs/HOOKS.md`). `PreToolUse` runs
  the pair's `pre` phase on every `Bash` call and records the size, mtime and
  sha256 of every file an active claim covers; `PostToolUse` runs the `post`
  phase once the shell command finishes, re-hashes those same paths, and raises
  one high-severity alert naming the path when a file another session's fence
  covers came back changed or gone. It answers a narrower question than the
  fence does, and it answers it late: a shell write cannot be refused, only
  reported after the fact.
  If this hook fails: nothing is blocked and nothing you wrote is lost, because
  this hook never had a decision to make. What you lose is the audit trail. A
  shell write across somebody else's fence goes back to being invisible, which
  is the state every version before this one shipped in. Both entrypoints exit
  0 whatever happens and print only to stderr.

Every hook is built to fail silent and exit 0, so a broken hook never blocks
a session from continuing. But "never blocks" is not the same claim as "never
costs you anything": a broken `SessionEnd` costs a telemetry line; a broken
`PreCompact` can cost you recovery information at the one moment you needed
it most. Treat the two claims separately, because they are not the same
thing.

## Doctor: check the whole install

```bash
python3 ~/.claude/skills/brothermode/scripts/doctor.py
```

Run this any time you are unsure whether an install, an update, or a hand
edit to `settings.json` left something broken. It runs eleven checks, each
printing `PASS`, `FAIL` with a one-sentence fix a non-engineer can follow, or
`SKIP` with the reason nothing could be checked yet (`SKIP` is not a
failure). Exit code 0 only when every check is `PASS` or `SKIP`. Add
`--json` instead of reading the plain text if a script needs to consume the
result.

| # | Check | A FAIL means, in plain words |
|---|-------|-------------------------------|
| 1 | The write-protection check (fence hook) wired and live | A blocked-write simulation (builds a throwaway project, claims one file under one session, then asks the wired hook to approve an edit of that file from a different session; a healthy fence refuses, then allows the same write when the owner asks) found the fence dead: not wired, wired at a path that does not exist, a matcher that leaves a write tool ungated, or a hook that runs but refuses nothing. |
| 2 | VERSION matches the plugin manifest | `VERSION` and `.claude-plugin/plugin.json` disagree about which release this install is. |
| 3 | python3 3.9+ and git on PATH | One of those two is missing from this machine. |
| 4 | Setup has been completed | Run `python3 scripts/setup.py`; nothing below this line can be checked before that. |
| 5 | Vault path exists and is writable | Create it (`cp -R vault-template <your vault path>`, Step 3 below) or fix its permissions. |
| 6 | Only one install method is wired | Both a plugin install and a clone install are wiring hooks into the same `settings.json`, so every hook fires twice. Remove one: `/plugin uninstall <name>` or `python3 scripts/uninstall.py`. |
| 7 | Project store health | A `.brothermode/store.sqlite3` under the current directory failed its own `verify`; this is a SKIP, not a FAIL, when there is no store here yet. |
| 8 | Hook wiring matches installation_mode | The consent config recorded by `scripts/setup.py` says `plugin` or `clone`, and the hooks actually wired in `settings.json` disagree with it. |
| 9 | CHECKSUMS.sha256 self-check | A shipped file does not match the checked-in release manifest, the signature of an update that did not finish. |
| 10 | settings.json is valid JSON | Claude Code silently ignores a broken settings file, so every hook, not only the fence, is off with nothing saying so. |

Checks 4, 5 and 8 read `SKIP` until setup has run (`python3 scripts/setup.py`
sets up the consent config those three checks read); that is the expected,
honest state right after Step 2 below, before Step 3 has created a vault.
Every other check applies from the moment the hooks are wired.

## Step 3: create the vault

The vault is a plain folder of markdown notes, and the repo ships a ready-made
template with the full layout, a Home dashboard, and a one-page constitution
(AGENTS.md) that tells every session how to read and write memory:

```bash
cp -R ~/.claude/skills/brothermode/vault-template ~/BrotherModeVault
```

Open `~/BrotherModeVault` as a vault in [Obsidian](https://obsidian.md) (free).
The notes link to each other with [[wiki-links]], so Obsidian's graph view shows
how projects, failures, and decisions connect; any plain editor works too, the
links just stay as text.

To put it somewhere else, set the environment variable where Claude Code can see it (your shell profile, or the `env` block of `~/.claude/settings.json`):

```bash
export BROTHERMODE_VAULT="$HOME/path/to/your/vault"
```

Optional: if you keep per-project `STATE.md` fence registries, tell the weekly checks where they live:

```bash
export BROTHERMODE_REGISTRIES="$HOME/work/*/STATE.md"
```

Unset, those checks report NO-DATA instead of guessing.

## Verify the installation

```bash
python3 ~/.claude/skills/brothermode/tools/bm_score.py
```

Expected on a fresh machine: 10 checks, mostly NO-DATA, and the closing line
"LLM judge scores only the residue." NO-DATA is correct for a system with no
history: the tools never invent numbers. You may also see one FAIL named
`budget-vs-tier` that points at `STATE.md`: this checks the skill repository's
OWN internal working file (the one its authors use to track building it), not
anything in your vault or your project, and a FAIL there does not mean your
install is broken. It is a real, checked-in inconsistency (that file's fence
lines are not all written in the single-line, tier-tagged format the checker
looks for), reported here rather than hidden.

```bash
python3 ~/.claude/skills/brothermode/tools/bm_sessionstart.py
```

Expected: the 12-line digest, plus a nag that the weekly review has never run. That nag is your first to-do, not an error.

Then start a real session, type `/brothermode`, and give it a task. After the session ends, check that telemetry landed:

```bash
tail -1 ~/BrotherModeVault/99-System/telemetry/outcomes.jsonl
```

## Your first week

1. Copy `STATE.template.md` into your main project as `STATE.md`.
2. Read `RUBRIC.md` with whoever plays the founder role, adjust the baselines to your reality, then freeze it. A rubric that drifts cannot measure drift.
3. Work normally for a week. Let the ledger fill.
4. Run the weekly review (`tools/WEEKLY-REVIEW.md`). Your first review will be mostly NO-DATA. The second one is where the loop starts to pay.

## Other runtimes

BrotherMode's engine is standard library Python driven from a shell, so it runs
in any runtime that can run a shell command: OpenAI Codex CLI, GitHub Copilot,
Qwen Code, iFlow, Antigravity, or a plain terminal with no agent at all. The
hooks are the part that does not travel. They parse Claude Code's hook JSON
contract, and Claude Code is the only runtime where BrotherMode's own hooks are
verified to run.

What ships for the others is a generated instruction file per runtime, committed
under `docs/runtimes/`, plus the generator that produced them:

```bash
python3 tools/bm_runtimes.py list                    # what is supported, and how far
python3 tools/bm_runtimes.py emit --runtime codex    # regenerate one adapter
```

`emit` stages the file and prints where it goes. It does not install it. The
copy is yours to make, because the destination (`AGENTS.md` at a repository
root, for example) usually already has content in it and a generator that
overwrote it would be a data loss bug:

```bash
cp docs/runtimes/codex.AGENTS.md /path/to/your/project/AGENTS.md
cp docs/runtimes/codex.AGENTS.md "${CODEX_HOME:-$HOME/.codex}/AGENTS.md"
```

Two things to know before you rely on it, both measured on 2026-08-05 against
codex-cli 0.146.0:

- The commands need the ABSOLUTE path to this checkout when your project is not
  this repository, and Codex needs a writable sandbox (`-s workspace-write`) for
  the store to be created at all. Run `bm_store.py init` in the project first.
- The one-writer fence does not transfer. Codex reports file writes as Bash
  commands running `apply_patch`, so the matcher that guards writes in Claude
  Code never fires. The enforcement primitive exists there (a PreToolUse deny
  really does block a command), but BrotherMode does not ship a Codex hook
  adapter and you should not hand wire one: a fence that fails open while
  looking installed is worse than no fence.

`RUNTIMES.md` carries the full capability table, the vendor documentation URL
behind every claim, and the measured findings per runtime.

### Cursor compatibility mode

Cursor has a full lifecycle of its own, separate from the instruction-file
path above: install, manage, uninstall, and a local harness so Claude Code
(Fable or Opus) can dispatch execution into Cursor.

```bash
python3 scripts/install_cursor.py
python3 ~/.cursor/brothermode/tools/bm_cursor.py doctor
python3 scripts/uninstall_cursor.py
```

Fence enforcement under Cursor stays ADVISORY until a live canary is
recorded. The harness uses git worktrees for isolation. Full page:
`docs/CURSOR-COMPAT.md`.

## Sharing with a teammate

Working mostly alone but need to hand a project to someone occasionally? `python3 tools/bm_telemetry.py handoff <project>` assembles one shareable markdown (overview, open items, latest session, recent outcomes) from your vault, secret-redacted, so you can send context without sending your whole vault. Review it before sharing; redaction is best-effort.

## Uninstall

```bash
python3 ~/.claude/skills/brothermode/scripts/uninstall.py --dry-run
python3 ~/.claude/skills/brothermode/scripts/uninstall.py
```

It removes the six hook entries it installed and the install record, and
nothing else. Every other hook and every other key in `settings.json` stays
where it was, in order. Add `--remove-files` to also delete the skill
directory, which it refuses to do unless the directory really looks like a
BrotherMode checkout.

Your vault is never deleted, with or without a flag. There is no code path in
the uninstaller that removes one; it prints the path and leaves the decision to
you.

Doing it by hand instead: remove the six hook entries from
`~/.claude/settings.json` and delete
`~/.claude/skills/brothermode`. Either way, that removes the skill but not what it wrote
inside each project you used it in: a per-project sqlite store, thread
files, `STATE.md` and its backups, local autosave git refs, and three lines
in `.git/info/exclude`. `../README.md`'s Uninstall section lists exactly
what is left and the commands to remove it, measured 2026-07-26 by actually
doing it in a scratch project. Your vault is yours either way; nothing
inside this repository ever writes to it except the files it holds.
