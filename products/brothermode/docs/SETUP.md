# Setup reference

Use [QUICKSTART.md](QUICKSTART.md) for the first-run sequence. This page
explains the installed files, configuration, hooks, diagnostics, and removal.
All unqualified paths and commands are relative to `products/brothermode`.

## Requirements and install choices

The core package requires Python 3.9 or later and declares no mandatory Python
dependencies in `pyproject.toml`. Git is needed for the clone route and local
recovery. Hook installation also requires a supported host with `python3` on
its process PATH. Optional Vault integrations have their own requirements;
installing the core does not configure those integrations.

The Claude installer refuses native Windows because its Python hook chain has
not been verified on a Windows machine. Its former POSIX shell wrappers have
been replaced. Use WSL for the documented install path; see
[WINDOWS-CHECK.md](WINDOWS-CHECK.md) for the native verification protocol.

The public umbrella route is [Brother's install](../../../README.md#start-in-sixty-seconds).
For the standalone product, run:

```bash
claude plugin marketplace add khalilmaaouni/Brother
claude plugin install brothermode@brother
```

The hub marketplace registers the product from `products/brothermode` at its
configured ref. The standalone product's local marketplace is a different
compatibility surface. `scripts/release-smoke-install.sh` defaults to testing
that local product marketplace in a temporary configuration; it is not proof
that today's hub install has run. Do not use its legacy `--github` source as
the hub install instruction.

For a pinned clone, use the sequence derived from
`tools/bm_project_facts.py::facts()`:

```bash
git clone --branch v1.0.19 --depth 1 https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-src
cd ~/.claude/skills/brothermode-src/products/brothermode
python3 scripts/install.py
```

Run the installer with `--dry-run` before its real invocation. A clone places
source files on disk; `scripts/install.py` makes the installed copy and edits
hook settings. It does not register the product with the plugin manager.

A separate exported-source development copy changes over time:

```bash
# Development branch (changes over time)
git clone --branch main https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-dev-src
cd ~/.claude/skills/brothermode-dev-src/products/brothermode
python3 scripts/install.py --target ~/.claude/skills/brothermode-dev
```

Use `--settings` as well as `--target` to isolate host settings. Different
source or target directories alone do not prevent duplicate hook chains.
Private hub development follows [PROJECT.md](../../../PROJECT.md).

Read identity rather than assuming a development suffix or equating a hub tag
with the product version:

```bash
cat VERSION
cat .claude-plugin/plugin.json
python3 tools/bm_project_facts.py
```

For a clone, merge the trigger below into `~/.claude/CLAUDE.md`. The installer
does not edit that file. Adjust the path for a custom target:

```markdown
When the user types /brothermode (any casing), read and follow
~/.claude/skills/brothermode/SKILL.md before doing anything else.
```

For the standalone plugin, use `/brothermode:help` and `/brothermode:start` in
a fresh Claude Code session. The umbrella uses `/brother`. These surfaces are
not interchangeable with a manually copied instruction file.

## Clone installer

```bash
python3 scripts/install.py --dry-run
python3 scripts/install.py
```

| Option | Effect |
|---|---|
| `--target DIR` | Changes the installed product directory from `~/.claude/skills/brothermode`. |
| `--settings FILE` | Changes the host settings file from `~/.claude/settings.json`. The install record and scope marker sit beside it. |
| `--upgrade` | Allows replacement of an existing install and owned hook entries. Use with `--dry-run` to inspect an upgrade. |
| `--no-hooks` | Leaves settings untouched while copying files. Identity, install-record, and scope handling still run. |
| `--repo PATH` | Opts a repository in by creating `.brother/config` if absent; repeat for multiple repositories. Existing files are preserved. |
| `--hooks-everywhere` | Removes the scope marker so hooks are not restricted to opted-in repositories. |

The installer refuses invalid settings JSON, preserves hook groups it does
not own, and backs up existing settings before writing. Ownership is checked
against the installation path and an explicit tool allowlist. A group mixing
an owned tool with an unrelated command is preserved. The installer records
`INSTALLED-FROM` and `brothermode-install.json`, rereads settings, checks the
hook tools exist, and invokes the fence on a harmless Read payload.

That smoke invocation proves execution, not write refusal. Doctor's separate
fence simulation checks a conflicting writer and the permitted owner. An
upgrade adds and overwrites files but never prunes old files; the verifier can
report those leftovers as `EXTRA`. Restart the host after wiring changes.

## Repository scope

`tools/bm_repo_scope.py` reads two separate controls:

| Control | Meaning |
|---|---|
| `brother-hook-scope` beside the host configuration, containing `scope: repositories` | The install is scoped. A repository without `.brother/config` is inactive, including its write guards. |
| Repository `.brother/config` | Presence opts in under a scoped install. `hooks: on` is the documented content. An explicit `hooks: off` suppresses ordinary hooks, while write guards stay active in that opted-in repository. |

A default clone installation with no `--repo` options reports
`hooks: active in 0 repositories (none yet)`. To opt in later, create
`.brother/config` in the target Git repository with `hooks: on`. Read an
existing file before editing it; an existing opt-out is a deliberate choice.
Without the machine scope marker, absent repository configuration means hooks
are active. A plugin install does not create the marker, but a previous scoped
installation can leave one that its hooks will honor.

An unreadable configuration is diagnosed rather than silently treated as a
successful opt-out. Scope is resolved from the target repository's Git root,
not from an arbitrary store location. The write-guard exemption means
`hooks: off` is not a universal emergency switch for every hook.

## Vault and consent configuration

The vault is a directory of local notes and records. Any text editor can read
it. The template supplies a home page, project areas, and its own instructions.
For a **new** destination on a default clone install:

```bash
cp -R ~/.claude/skills/brothermode/vault-template ~/BrotherModeVault
export BROTHERMODE_VAULT="$HOME/BrotherModeVault"
python3 ~/.claude/skills/brothermode/scripts/setup.py
```

For a plugin install, run setup from the actual installed product path. Reuse
an existing vault only after inspecting it; do not copy a template over it.
Interactive setup shows the notice and asks for the path and installation
mode. Its mode detection is heuristic: select `clone` for this custom
installer, `plugin` for the plugin manager, or `cursor` for Cursor.

After accepting the notice, automation can use explicit arguments:

```bash
python3 scripts/setup.py --vault "$BROTHERMODE_VAULT" --mode clone --accept-notice
python3 scripts/setup.py --show
```

Setup creates the consent config only after acceptance and requires
`--reconfigure` to replace an already consented configuration. Without a TTY,
the full `--vault`, `--mode`, `--accept-notice` trio is required. Setup records
a path; it does not create, move, or delete the vault. It runs doctor afterward,
but a doctor failure is reported separately from successful consent setup.

| Setting or file | Reader and purpose |
|---|---|
| `~/.brotherme/config.json` | `scripts/setup.py` owns consent: `setup_complete`, `vault_path`, `privacy_notice_version`, `installation_mode`, and `security_mode`. `standard` is the supported security mode. |
| `BROTHERME_CONFIG` | Overrides the consent config path, useful for isolated configurations. |
| `BROTHERMODE_VAULT` | Telemetry's vault location; without it telemetry uses `~/BrotherModeVault`. Recording a different path in consent does not export this variable. |
| `BM_VAULT_ROOT` | Vault retrieval's higher-priority environment override. Align it with the telemetry path if both are set. |
| `bm_vault.json` in the resolved host config directory | Retrieval's `vault` setting, used after `BM_VAULT_ROOT` and `BROTHERMODE_VAULT`. No configured retrieval root means NO-DATA. |
| `BROTHERMODE_REGISTRIES` | Optional globs for older `STATE.md` registry checks. The transactional store remains the authority for active ownership. |

Export variables in the environment that launches the host, or configure its
settings environment. A shell profile used by one terminal may not reach a
desktop-launched process. `tools/brother_paths.py` resolves host directories;
`tools/bm_telemetry.py` and `tools/bm_vault.py` have distinct vault readers, so
a consent config alone is not evidence that all memory tools use one path.

## Hook events and install differences

The clone installer and the product plugin share event names, but their actual
command lists differ in this tree. The sources are
`scripts/install.py::hook_groups()`, `tools/bm_hookchain.py::CHAINS`, and
`hooks/hooks.json`.

| Event | Clone wiring | Additional product-plugin wiring |
|---|---|---|
| SessionStart | `bm_sessionstart.py`: consent-gated context, digest, recovery hints, and checks. | `bm_vault.py refresh` |
| SessionEnd | `bm_telemetry.py outcomes-append`: consent-gated session ledger and correction-candidate capture. | None |
| Stop | `bm_hookchain.py stop`: telemetry reminder, lead watchdog, view refresh, and alert tick. | None |
| PreCompact | `bm_hookchain.py precompact`: local autosave and resume brief. | None |
| PreToolUse | Fence and Vault recall on `Edit|Write|MultiEdit|NotebookEdit|Bash`; Bash audit pre-phase in a separate Bash group. | `bm_session_cap.py` in the Bash group |
| PostToolUse | `bm_bash_audit.py post` for Bash-write detection. | `attempt_hook.py` in the Bash group |

The umbrella has its own generated runtime wiring. Inspect that package rather
than assuming this standalone-product table describes it.

The fence checks supported tool writes and readable `apply_patch` envelopes
against the store. It communicates a denial through hook JSON; exit 0 alone
does not mean a write was allowed. With default advisory failure handling, an
uncheckable write produces a `FAILING OPEN` diagnostic. Setting
`BM_FENCE_MODE=enforced` makes covered failures refuse the write;
`BM_FENCE_STRICT` separately controls whether otherwise unclaimed paths are
refused. Inspect `tools/bm_fence_hook.py` for the decision branches and
[HOOKS.md](HOOKS.md) for the payload contract. Hook trust and host dispatch
still determine whether any of this code runs.

The Bash audit detects changes to actively claimed files after the command;
it cannot contain arbitrary shell writes. Vault recall supplies context, not
permission to alter policy. A missing SessionEnd invocation loses a session
record, while a missing PreCompact invocation loses a recovery snapshot and
brief. Stop is a chain that can refresh records and views, not merely a silent
reminder. It is inaccurate to describe every hook as write-free or incapable
of refusal.

### Manual clone wiring

Prefer the installer, which writes absolute shell-quoted paths, backups, an
identity stamp, and an install record. If wiring manually, merge this object
with existing settings. Substitute your absolute install path for each `~`
path and quote it for the shell if it contains spaces. This block describes the
clone route; it deliberately does not add the plugin-only commands above.
The documentation suite compares the block against the installer.

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

Validate settings JSON after a manual edit:

```bash
python3 -m json.tool ~/.claude/settings.json
```

Then complete scope and consent setup, restart the host, and run doctor.
Manual wiring does not create the install record or `INSTALLED-FROM` stamp.

## Doctor and troubleshooting

Run the installed product's doctor from the target project so its store check
examines the intended working directory:

```bash
python3 ~/.claude/skills/brothermode/scripts/doctor.py
```

Use the actual product path for a plugin or custom target. `--settings FILE`
selects alternate Claude settings, `--json` emits structured results, and
`--strict` treats SKIP as nonzero. `--status` is a separate read-only host
status view and cannot be combined with `--json` or `--strict`.

`run_all_checks()` currently covers the following concerns, without promising
that every check can obtain evidence on every installation:

| Concern | What doctor examines |
|---|---|
| Fence | Wiring, matchers, and a temporary conflicting-writer/owner simulation. |
| Version and runtime | `VERSION` against the product manifest, Python, Git, and Windows hook limitations. |
| Consent and vault | Whether setup is complete and the recorded vault is usable. |
| Install shape | Duplicate installs, complete wiring, and agreement with the recorded install mode. |
| Project store | `.brothermode/store.sqlite3` under the current directory, using store verification when present. |
| Files and settings | Checksums and valid settings JSON. |
| Provenance and location | `INSTALLED-FROM`, stranded installations, and retained data locations. |

PASS means the named check supports its claim. FAIL names a problem to fix.
SKIP is the tool's spelling for NO-DATA, such as a missing project store or an
unavailable source identity; the default exit code permits it, `--strict`
does not. Neither the installer smoke nor doctor proves a signed-in host
invoked its hooks in a real session.

| Symptom | First inspection |
|---|---|
| Hooks appear installed but do nothing | Check the scope marker, target `.brother/config`, consent, host restart, and the Python executable visible to the host. |
| No telemetry row | Check consent, transcript availability, and activity floor: at least five API messages and one tool call. Then verify the host actually invoked SessionEnd. |
| Notes land in an unexpected vault | Compare `BROTHERMODE_VAULT`, `BM_VAULT_ROOT`, consent's path, and `bm_vault.json`. |
| Repeated hook output | Check for simultaneous bundle, standalone, and clone installations. Use the removal path belonging to the unwanted install. |
| Fence allows an unchecked write | Read the diagnostic, confirm a project store and active claims exist, and inspect the effective fence mode and host trust. |
| Checksum mismatch | Compare with the shipped manifest and intended revision. Do not overwrite the manifest to hide the mismatch. |

For the checksum comparison itself, run `bash scripts/verify-install.sh` from
the product. A match establishes agreement with the supplied manifest, not
its authenticity. For source checks, run `python3 tools/test_bm_docs.py` or
`python3 tools/test_all.py` and report their actual output, including skips.

## Other runtimes

Codex has a repository-level installer and managed hook configuration:
[scripts/brother_install.py](../../../scripts/brother_install.py) and
[scripts/codex_hooks_install.py](../../../scripts/codex_hooks_install.py).
Follow [the Codex guide](../../../docs/how-to/install-codex.md), then verify
installed skill discovery, hook trust, and the active host's behavior. The
older instruction adapter is not a substitute for that lifecycle.

Cursor has an independent product install, management tool, and uninstall:

```bash
python3 scripts/install_cursor.py --dry-run
python3 scripts/install_cursor.py
python3 ~/.cursor/brothermode/tools/bm_cursor.py doctor
python3 scripts/uninstall_cursor.py
```

These are lifecycle alternatives, not steps to run on an existing Claude
installation. Cursor dispatch uses Git worktrees, but fence enforcement remains
advisory until a live signed-in canary demonstrates a deny. See
[CURSOR-COMPAT.md](CURSOR-COMPAT.md).

For other instruction-file adapters:

```bash
python3 tools/bm_runtimes.py list
python3 tools/bm_runtimes.py emit --runtime codex
```

`emit` writes a generated adapter under `docs/runtimes/`; it does not install
it into a project or stage it in Git. Merge instructions into an existing host
file instead of overwriting it. In another project, invoke tools by absolute
path and run `bm_store.py init` there before relying on its store. See
[RUNTIMES.md](RUNTIMES.md) for the adapter boundaries.

## Ongoing use and handover

Keep the deciding command with each completed change. Run the weekly review
in [tools/WEEKLY-REVIEW.md](../tools/WEEKLY-REVIEW.md) once there is real history
to inspect; absent history stays NO-DATA. The local store owns file claims;
copying a `STATE.md` template alone does not establish transactional ownership.

`python3 tools/bm_telemetry.py handoff <project>` assembles a local handoff from
vault notes and outcomes. Review it before sharing: redaction is best effort.
A handoff file, the project store, and an engine delivery receipt are different
artifacts and should be named accurately when handing work to a reviewer.

## Uninstall and retained data

For a standalone plugin, use `claude plugin uninstall brothermode`. For the
umbrella, remove the `brother` registration using its own host lifecycle.
For a clone, inspect then remove its owned wiring:

```bash
python3 ~/.claude/skills/brothermode/scripts/uninstall.py --dry-run
python3 ~/.claude/skills/brothermode/scripts/uninstall.py
```

Use the original `--settings` and `--target` for a custom install. The
uninstaller preserves unrelated hook groups, removes its install record, and
attempts to remove the scope marker. Because that marker is shared, removing
it can change the scoping of another Brother installation on the same host.
`--remove-files` also removes a recognized product directory;
`--remove-consent` removes the consent config. Neither deletes the vault.

The tool prints the data it leaves behind. This can include the vault,
per-project `.brothermode/` stores, `threads/`, `STATE.md` and backups, local
`refs/brothermode` autosave refs, and exclusions in `.git/info/exclude`.
Review and back up needed data before deliberately removing any of it.
For retained-data reporting, see `scripts/uninstall.py::data_locations()`.
