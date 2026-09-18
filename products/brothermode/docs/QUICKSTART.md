# Quick start

Use this page to install BrotherMode, configure its local memory, and check a
first session. [SETUP.md](SETUP.md) explains the settings and failure modes.
Commands below run from `products/brothermode` unless a different directory is
shown. Python 3.9 or later and Git must be on PATH; the Claude Code routes also
need the `claude` CLI. The core tools use the Python standard library.

```bash
python3 --version
git --version
claude --version
```

The usual Brother entry point is the umbrella plugin described in the
[root install guide](../../../README.md#start-in-sixty-seconds). The routes below install
BrotherMode on its own. Pick a plugin install or a clone install for a given
host configuration, so the same hooks do not run twice. For Codex or Cursor,
see [Other hosts](#other-hosts) before following Claude Code setup.

## 1. Choose an installation

### Standalone plugin

Run in a terminal:

```bash
claude plugin marketplace add khalilmaaouni/Brother
claude plugin install brothermode@brother
```

The root marketplace names `brothermode` and its product source ref. After
installation, start a fresh Claude Code session and use `/brothermode:help`,
then `/brothermode:start` with a small outcome. The guided start carries the
setup conversation. Installing the plugin alone is not consent to record
session telemetry.

For the manual setup commands below, set `BM_PRODUCT_ROOT` to the actual
installed **product** directory reported by your plugin manager. Verify that
it contains `scripts/setup.py` and `vault-template/Home.md`; do not guess a
cache path or use the umbrella bundle directory as though it were the product.

```bash
BM_PRODUCT_ROOT="/absolute/path/to/installed/brothermode"
test -f "$BM_PRODUCT_ROOT/scripts/setup.py"
test -f "$BM_PRODUCT_ROOT/vault-template/Home.md"
```

Continue at step 2. Do not run `scripts/install.py` on top of a plugin install.
If migrating an old registration, remove it through the plugin manager before
adding a replacement; the old `brotherme` identity and `brothermode` are
separate registrations.

### Pinned clone

The pinned clone ref comes from
`python3 tools/bm_project_facts.py --field install_target_tag`. It is a hub
release ref, independent of the product's `VERSION`:

```bash
git clone --branch v1.0.20 --depth 1 https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-src
cd ~/.claude/skills/brothermode-src/products/brothermode
ls SKILL.md
cat VERSION
bash scripts/verify-install.sh
```

`ls` should find the product skill. The verifier reports whether the source
bytes agree with the supplied checksum manifest. Investigate a mismatch;
regenerating the manifest would replace the evidence you are checking.

Then inspect and perform the copy and hook installation:

```bash
python3 scripts/install.py --dry-run
python3 scripts/install.py
BM_PRODUCT_ROOT="$HOME/.claude/skills/brothermode"
```

The dry run writes nothing. The real installer copies the product, backs up
existing settings before writing, records its source identity, and runs a
smoke check. The line `smoke: the fence hook ran end to end and exited 0`
proves the hook executes; doctor separately checks that a conflicting write
is refused. An existing install needs `--upgrade`; malformed settings are
refused rather than repaired. See [installer options](SETUP.md#clone-installer).

A clone install is absent from the plugin manager's registry. Add this trigger
to your existing `~/.claude/CLAUDE.md`, preserving its other instructions:

```markdown
When the user types /brothermode (any casing), read and follow
~/.claude/skills/brothermode/SKILL.md before doing anything else.
```

That is an instruction-file route, not registration of the standalone plugin's
namespaced commands.

### Separate development copy

For an exported-source development copy, use a separate target:

```bash
# Development branch (changes over time)
git clone --branch main https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-dev-src
cd ~/.claude/skills/brothermode-dev-src/products/brothermode
python3 scripts/install.py --target ~/.claude/skills/brothermode-dev
```

Inspect that install with `--dry-run` first. Use
`BM_PRODUCT_ROOT="$HOME/.claude/skills/brothermode-dev"` for the remaining
commands and adjust the trigger path. The different target does not separate
settings; use `--settings` for an isolated host configuration. Private hub
contributors follow [PROJECT.md](../../../PROJECT.md) instead of treating the
public export as the development remote.

## 2. Enable the target repository

Run these from the repository where you intend to work:

```bash
mkdir -p .brother
```

If `.brother/config` already exists, read it and preserve its existing content
and choices. For a new file only:

```bash
printf 'hooks: on\n' > .brother/config
```

The clone installer creates a machine-level scope marker; without any opted-in
repository it reports `hooks: active in 0 repositories (none yet)`. A plugin
install does not create that marker but honors one left by a scoped install.
`--repo /absolute/project/path` can opt in during clone installation.
`--hooks-everywhere` removes the marker instead. A repository's `hooks: off`
line suppresses ordinary hooks but does not disable write guards in an opted-in
repository. [Scope details](SETUP.md#repository-scope) explain the distinction.

## 3. Choose memory storage and record consent

For a new vault at the example path, copy the template once:

```bash
cp -R "$BM_PRODUCT_ROOT/vault-template" "$HOME/BrotherModeVault"
export BROTHERMODE_VAULT="$HOME/BrotherModeVault"
ls "$BROTHERMODE_VAULT/Home.md"
```

If the destination already exists, inspect it and reuse it or choose a new
path; do not copy the template over an existing vault. Keep the export visible
to the host process, through its launch environment or settings. A terminal
export affects only processes started from that environment.

Read the privacy notice with interactive setup:

```bash
python3 "$BM_PRODUCT_ROOT/scripts/setup.py"
```

Confirm the actual vault path and installation mode: `clone` for the custom
installer, `plugin` for the plugin manager. Setup's automatic mode detection is
a path heuristic, so verify the answer. For automation after reading and
accepting the notice, the explicit clone form is:

```bash
python3 "$BM_PRODUCT_ROOT/scripts/setup.py" --vault "$BROTHERMODE_VAULT" --mode clone --accept-notice
```

For a plugin install, use `--mode plugin`. Setup writes
`~/.brotherme/config.json` (or `BROTHERME_CONFIG`) and runs doctor. It does not
create or move the vault, and it does not export `BROTHERMODE_VAULT`. That
variable matters: telemetry reads it, while Vault retrieval also supports
`BM_VAULT_ROOT` and its own config. Keep those paths aligned.

## 4. Verify from the project you will use

Restart Claude Code after changing its hook configuration. From the target
project directory:

```bash
python3 "$BM_PRODUCT_ROOT/scripts/doctor.py"
python3 "$BM_PRODUCT_ROOT/scripts/setup.py" --show
```

Doctor reports each check's actual PASS, FAIL, or SKIP result. Treat SKIP as
NO-DATA, including an absent project store before the first task. Fix FAIL
results using their explanations. Do not assume setup's successful exit means
its embedded doctor run passed. Use `--strict` when a skipped check must also
make doctor exit nonzero.

For source validation, run from the product source directory:

```bash
python3 tools/test_bm_docs.py
python3 tools/test_all.py
```

The full gate runs suites serially with timeouts and prints `ALL GREEN` only
when no suite failed. Read NO-DATA and skip lines as well: an exported clone
can lack internal evidence intentionally. No fixed test count or runtime is
promised here. A single documentation suite checks consistency; it does not
prove the host invoked a hook.

## 5. Try one small task and inspect the evidence

In the standalone plugin, use `/brothermode:start` with a specific outcome and
a deciding check. On the clone route, use the `/brothermode` trigger configured
above. Start with a small change whose files and expected behavior you can
inspect. Ask for status, review, and the delivery evidence before accepting it.

After a qualifying session ends, inspect the local telemetry ledger:

```bash
tail -1 "$BROTHERMODE_VAULT/99-System/telemetry/outcomes.jsonl"
```

`tools/bm_telemetry.py` records only sessions with at least five API messages
and one tool call, after consent and with a readable transcript. A row carries
fields such as `session_id`, `tool_calls`, `models`, and `token_basis`.
`token_basis: as-flushed` means the transcript may lag the final turn. A missing
row may mean no qualifying session, no consent, a scoped-out repository, or
hook wiring that was not loaded. It is NO-DATA until diagnosed, not proof of a
successful hook run. Telemetry also does not replace the checks proving the
change itself.

## Other hosts

- **Codex:** this repository ships a lifecycle installer and managed hook
  tooling. Follow [the Codex guide](../../../docs/how-to/install-codex.md).
  Verify host wiring and trust; a legacy instruction-file adapter alone does
  not enforce file ownership.
- **Cursor:** `scripts/install_cursor.py`, `tools/bm_cursor.py`, and
  `scripts/uninstall_cursor.py` provide a separate lifecycle. See
  [Cursor compatibility](CURSOR-COMPAT.md). Enforcement remains advisory until
  a signed-in canary demonstrates a deny.
- **Other instruction-file hosts:** `python3 tools/bm_runtimes.py list` shows
  the adapters, and [RUNTIMES.md](RUNTIMES.md) describes their limits. Merge
  generated instructions into an existing host instruction file; do not
  overwrite it. Use absolute paths to the tools and a writable project store.

For upgrades, removal, retained data, and the hook behavior table, continue to
[SETUP.md](SETUP.md).

## Appendix: clone hook wiring

Prefer the installer. This reference block matches its `hook_groups()` event,
matcher, command, and timeout structure, which `tools/test_bm_docs.py` checks.
It is the clone wiring, not the larger product-plugin manifest. Merge with
existing settings rather than replacing them. Replace each `~` path with the
actual absolute, shell-quoted install path when wiring manually, especially
when the path contains spaces. Manual wiring also needs the scope and consent
steps above.

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
