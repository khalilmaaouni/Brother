# BrotherMode

BrotherMode keeps long work recoverable and reviewable across sessions. It
records ownership, decisions, checks, and returned cost data on disk, then
presents the result for a person's acceptance.

Use the [quick start](docs/QUICKSTART.md) for a first install and task, or the
[setup reference](docs/SETUP.md) for hook wiring, configuration, diagnostics,
and removal. [docs/CONTINUITY.md](docs/CONTINUITY.md) explains the resume contract.

## How it works

| Piece | What it does | Implementation |
|---|---|---|
| Local store | Records work lifecycles and file claims transactionally; refuses overlapping claims and supports checkpoints and handovers. | `tools/bm_store.py`, `.brothermode/store.sqlite3` in the project |
| Project and controller | Turns recorded outcomes and plans into resumable work, with authorization and verification states. | `tools/bm_project.py`, `tools/bm_autonomy.py`, `tools/bm_controller.py` |
| File fence | Checks supported writes against active ownership; reports the reason for a refusal or an unchecked write. | `tools/bm_fence_hook.py` |
| Decisions and views | Reads the same store to produce status, catch-up briefs, handback records, handover packs, and `PROJECT-VIEW.html`. | `tools/bm_lead.py`, `tools/bm_view.py` |
| Recovery | Saves local Git snapshots before compaction and records a resume brief. | `tools/bm_autosave.py`, `tools/bm_telemetry.py` |
| Session records | Parses a qualifying session's transcript into a local JSONL ledger after setup consent. | `scripts/setup.py`, `tools/bm_telemetry.py` |
| Vault recall | Indexes configured memory and retrieves relevant notes, including recorded failures before an edit. Retrieved text remains untrusted context. | `tools/bm_vault.py`, `tools/vault_recall_hook.py` |

Within Brother, the umbrella router selects this execution capability or
BrotherSBE's assurance capability. The repository's `scripts/brother_run.py`
composes the execution and receipt path; the installed bundle carries its
generated runtime. BrotherMode's project store and the run receipt answer
different questions: the store tracks work, while the receipt records what a
particular run can prove. See the [shared charter](../../docs/CHARTER.md).

## Install through Brother

The [Brother install](../../README.md#start-in-sixty-seconds) is the public umbrella route:

```bash
claude plugin marketplace add khalilmaaouni/Brother
claude plugin install brother@brother
```

Open a fresh Claude Code session in the target repository and use `/brother`
with the outcome you want. A bare request follows the routing skill's
unfinished-work discovery before asking for a new outcome. For Codex, use
[the repository's install guide](../../docs/how-to/install-codex.md) and the
installed skill, not a Claude-style slash command.

### Standalone product or pinned clone

To install BrotherMode alone, choose this alternative to the umbrella:

```bash
claude plugin marketplace add khalilmaaouni/Brother
claude plugin install brothermode@brother
```

The root marketplace's `brothermode` entry names this product and pins its
source ref. It is distinct from the product-local compatibility marketplace.
The generated `install_command_plugin` fact still uses that local marketplace
identity; it is not the hub install command above.

For a pinned source copy, the clone sequence from `bm_project_facts.py` is:

```bash
git clone --branch v1.0.19 --depth 1 https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-src
cd ~/.claude/skills/brothermode-src/products/brothermode
python3 scripts/install.py
```

Before the install line, run `python3 scripts/install.py --dry-run` to inspect
the proposed copy and settings changes. A clone alone registers no plugin and
wires no hooks. The installer copies the product to
`~/.claude/skills/brothermode`; continue with repository opt-in, vault consent,
and verification in the quick start. Choose one installation method per host
configuration so duplicate hook chains do not run.

The separate development clone changes over time:

```bash
# Development branch (changes over time)
git clone --branch main https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-dev-src
cd ~/.claude/skills/brothermode-dev-src/products/brothermode
python3 scripts/install.py --target ~/.claude/skills/brothermode-dev
```

The public clone is useful for examining the exported source. Development in
the private hub follows [PROJECT.md](../../PROJECT.md). A separate target
directory does not isolate hook settings; use `--settings` as well when you
need an isolated install. Read the product identity with `cat VERSION` and
`python3 tools/bm_project_facts.py`, rather than assuming the checkout carries
a development suffix or that its product version equals the hub tag.

## Hooks, scope, and limits

Both product install routes cover SessionStart, SessionEnd, Stop, PreCompact,
PreToolUse, and PostToolUse. Their command lists differ: the plugin's
`hooks/hooks.json` additionally wires Vault refresh, a session cap, and attempt
tracking. The clone installer uses `scripts/install.py::hook_groups()`.
[Setup](docs/SETUP.md#hook-events-and-install-differences) lists both.

- The clone installer writes a scope marker. With no `--repo` arguments it
  reports `hooks: active in 0 repositories (none yet)`. Opt a repository in
  through `.brother/config`, or install with `--hooks-everywhere` to remove
  that machine-level scoping. A plugin install does not create this marker,
  but hooks honor one that already exists.
- `hooks: off` in `.brother/config` suppresses reporting and advisory hooks.
  It does not disable write guards in an opted-in repository. See
  `tools/bm_repo_scope.py::hooks_off` for that distinction.
- The fence covers supported write tools and readable `apply_patch` envelopes.
  The Bash audit detects changes to claimed files after a shell command;
  arbitrary shell and external writes are not contained by these hooks.
- Fence errors normally fail open with a diagnostic. `BM_FENCE_MODE=enforced`
  refuses covered writes when the fence cannot be checked. Host wiring and
  trust still decide whether a hook runs; package installation alone proves
  neither. Cursor enforcement remains advisory pending a live canary.
- Recovery snapshots share the repository's local Git storage. They are not
  an independent backup. Cost fields are only evidence when returned by the
  worker; missing fields remain NO-DATA.
- Native Windows installation is refused by the installer pending a real
  Windows run. Its hook commands now use Python, so the old POSIX-hook
  explanation is obsolete. The refusal directs users to WSL.
- Delivery does not authorize publishing, spending, deletion, merging, or
  acceptance on a person's behalf. The local store is not a shared account
  system or a multi-user service.

## Verify the work and the installed bytes

From this product directory, run the relevant checks:

```bash
python3 tools/test_bm_docs.py
python3 tools/test_bm_store.py
python3 tools/test_bm_fence_hook.py
python3 tools/test_bm_controller.py
python3 tools/test_bm_consent.py
```

For the complete product gate, run `python3 tools/test_all.py`. It executes
suites serially in separate processes with a checkout lock and timeouts.
Read its actual result and skipped checks. A public export omits some internal
evidence; the runner names intentionally absent suites as NO-DATA and runs
what is available. NO-DATA does not establish the omitted behavior.

For an installed copy, compare its bytes to its shipped manifest first:

```bash
bash scripts/verify-install.sh
```

Agreement proves that files match that manifest, not that the manifest's
source is trusted. The maintainer command
`sh scripts/checksums.sh CHECKSUMS.sha256` OVERWRITES the manifest from the
current files. Do not run it before verification to make a mismatch disappear.
A working tree edited after the release naturally needs a new manifest when
it is prepared for release.

Run `python3 scripts/doctor.py` from the project being diagnosed. Doctor
checks wiring, a simulated denied write and allowed owner write, consent,
versions, data locations, and store health. Its SKIP results are NO-DATA;
`--strict` makes them nonzero. A local simulation is not a signed-in host
canary.

## Updating and removing an install

For the standalone plugin, use `/brothermode:update` in Claude Code, or the
plugin manager for the identity you installed (`brothermode` versus `brother`).
For a clone, run the reviewed source's installer with `--upgrade --dry-run`,
then `--upgrade`, preserving the same target and settings path. Restart the
host and rerun doctor. Upgrades add or overwrite files; they do not remove
files deleted upstream.

The clone uninstaller supports `--dry-run` and optional `--remove-files`:
`python3 ~/.claude/skills/brothermode/scripts/uninstall.py`. It removes owned
hook entries and the install record. The vault remains, as do project data,
thread files, any `STATE.md` backups, and local autosave refs. See
[Setup](docs/SETUP.md#uninstall-and-retained-data) before removing retained data.

## Recorded capability status

The block below is generated from `capabilities.status.json`; edit that
register and regenerate with `python3 tools/bm_docs.py capability-status
--write` when updating its evidence. Its dated claims are not a fresh test
result, and it includes historical descriptions that have since changed in
code: `decide()` now validates non-string tool names before dispatch, the
clone installer writes `INSTALLED-FROM`, and SessionStart uses Python.
The current setup behavior is documented above and in the setup reference.
External service availability and earlier release runs in this register are
historical evidence, not observations of this session.

<!-- BEGIN GENERATED CAPABILITY STATUS -->
<!-- Generated from capabilities.status.json by `bm-docs capability-status --write` (the packaged console script; from a clone, tools/bm_docs.py). Edit the register, not this block. -->

Four states and no others, read out of `capabilities.status.json`, last edited 2026-09-04: certified means proven by the evidence named in the row, on the date that evidence records; beta means real, with a named gap; experimental means built or planned, not measured; unsupported means not offered, and no plan makes it offered.

That date is when the register was last edited, not a check that ran when you opened this page: every row is proven by the evidence it names, on the date that evidence records, and nothing here is re-measured as you read it.

**Certified**, proven by the evidence named in the row, on the date that evidence records.

| Capability | What proves it, or why it is not offered |
|---|---|
| Durable local store that survives a crash and can be recovered | tools/bm_store.py holds the state and tools/test_bm_store.py exercises recovery; the store job in .github/workflows/tests.yml is defined to run that suite on Linux, macOS and Windows, but STALE CLAIM FIXED 2026-09-13: the workflow is workflow_dispatch-only (disarmed 2026-08-17) and GitHub Actions is currently disabled account-wide, so this evidence is historical or on-demand, not continuously running; certification rests on the local suite passing when run directly. |
| Current pages are held to the facts read out of the tree | tools/test_bm_docs.py refuses a current page carrying a stale count, a stale version, or a dated record that declares no status; docs/ba/QA-GATES.md states the gates. |
| Guided beginner flow on Claude Code | skills/brotherme/SKILL.md drives the flow, commands/brotherme-start.md is its entry point, and docs/QUICKSTART.md is the install path a beginner follows. |
| Continuous integration on macOS and Linux | .github/workflows/tests.yml defines the suite job to run on ubuntu-latest and macos-latest at Python 3.9 and 3.x, with fail-fast disabled so one platform cannot erase another's result. STALE CLAIM FIXED 2026-09-13: the trigger is workflow_dispatch-only (disarmed 2026-08-17, no-self-firing-CI law) and GitHub Actions is currently disabled account-wide, so 'continuous' means available on manual dispatch, not automatically firing on a push or a PR today. |
| Session telemetry recorded only after the user consents | scripts/setup.py writes the consent record, tools/bm_telemetry.py is the only writer, and tools/test_bm_consent.py refuses a write without consent. |
| Two-command plugin install through Claude Code's own plugin manager | scripts/release-smoke-install.sh proves the whole path on every release inside a throwaway configuration: marketplace add, install, installed version matched against VERSION, every hook group registered, uninstall leaving settings clean; first PASSED run 2026-08-07 on the release candidate tree, with a live sandboxed end to end run the same night. `claude plugin validate .claude-plugin/plugin.json` and `claude plugin validate .claude-plugin/marketplace.json` both exit 0 (re-run 2026-09-04); the plugin manifest passes with one warning, that a CLAUDE.md at the plugin root is not loaded as project context. The path is required: `claude plugin validate` with no argument exits 1 with the message missing required argument 'path'. SURFACE LIMIT, founder-reproduced 2026-08-06: the desktop app cannot run /plugin itself; the two commands run once in a terminal and the app consumes the installed plugin. The plugin line tracks the repository's default branch by the plugin system's design; the tagged clone remains the immutable option and docs/RELEASE.md states both. |

**Beta**, real, with a named gap.

| Capability | What proves it, or why it is not offered |
|---|---|
| Single writer per file for supported write tools, refused by a hook; other writes detected, not contained | Conflicting writes are refused for the Claude Code write tools (Edit, Write, MultiEdit, NotebookEdit) and readable apply_patch envelopes on the Bash leg, wired by hooks/hooks.json and proven by tools/test_bm_fence_hook.py. Other shell and external writes are detected where possible by tools/bm_bash_audit.py but are NOT contained. Hooks are cooperative enforcement: no container or operating system sandbox is provided. MEASURED 2026-08-07 on OpenAI Codex CLI 0.146.0: the fence does NOT fire in the codex exec path. A live run overwrote a file another session had claimed, twice, and a marker probe proved the PreToolUse hook never executed, with config syntax, project trust and hook-trust bypass all ruled out (docs/mistakes/M19-the-codex-fence-does-not-fire-in-exec-mode.md). Under Codex, BrotherMode is an instruction file plus a working command line, not an enforcement layer. Downgraded to beta: proven in this tree with tools/test_bm_fence_hook.py, but not proven on an installed plugin because the install writes no INSTALLED-FROM stamp and the detector scripts/doctor.py check_install_identity can only return SKIP. Since 2026-08-17 the same hook also refuses a write to any git-tracked file while a live tools/test_all.py gate lock covers the checkout (the battery fence), for every session including the one that started the gate; a lock it cannot read is reported and, in advisory mode, never blocks. The battery classes in tools/test_bm_fence_hook.py prove it in this tree, under the same beta caveat as the rest of this row. ENFORCED MODE DRIVEN 2026-09-02 for the first time, by ../../scripts/fence_enforced_drill.py: 20 conditions, each fed to the hook twice, advisory against enforced. RE-RUN 2026-09-04 in this tree: 20 conditions, 19 PASS, 0 FAIL, 1 NO-DATA at exit 0, the one NO-DATA being the tool-name-not-a-string gap named at the end of this row. The 2026-09-02 wording claimed 20 PASS and 0 NO-DATA, which contradicted the gap the same row already recorded. Fifteen conditions that fail OPEN with the mode unset fail CLOSED with BM_FENCE_MODE=enforced, and a properly claimed write still ALLOWS under enforced, so enforcement is selective rather than blanket. The drill was itself driven backwards against a mutant copy of the hook whose enforced_mode() returns False, which produced 15 FAIL at exit 1. ONE GAP FOUND AND NOT FIXED, recorded here rather than left in a transcript: a payload whose tool_name is not a string leaves decide() at the not-a-write-tool branch BEFORE any mode check, so enforced mode cannot refuse it. That is one of the five shapes of malformed payload this row's C-01 note claims enforcement covers, so the claim is one shape wider than the code until that branch moves. |
| Windows | Only the store job in .github/workflows/tests.yml runs on windows-latest; the suite and gate jobs run on Linux and macOS only. docs/KNOWN-LIMITS.md records that the installer refuses Windows and that WSL works. There is no native Windows install lifecycle. |
| The signed authorisation an autonomous session has to work inside | tools/bm_autonomy.py is the command line, tools/test_bm_autonomy.py is its suite, and the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows; docs/AUTONOMY.md is the page. It stays beta because docs/KNOWN-LIMITS.md records open items against this layer and no use outside this project is recorded. |
| The durable controller that carries a signed outcome to a checked deliverable | tools/bm_controller.py is the engine and its command line, tools/test_bm_controller.py is its suite including an end to end run that is killed and resumed (its transcript is docs/program/absolute-lead/evidence/L03/E4-endtoend.json), the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows, and docs/FULL-AUTO.md is the page. Not experimental, because experimental here means not measured and this is measured. It stays beta because docs/KNOWN-LIMITS.md carries its own list of what the controller does not yet do, and no pilot outside this project exists. |
| A record of what was decided and why, and the short catch-up built from it | tools/bm_lead.py records and renders them over two append only tables in tools/bm_store.py, tools/test_bm_lead.py is its suite, and docs/program/absolute-lead/DESIGN-insight-ledger-and-handback.md states what a record has to carry before it may be written. It stays beta because the record holds the coordinator's judgement rather than a measurement, because no continuous integration run covers it yet, and because docs/KNOWN-LIMITS.md names what it does not do. |
| Handing a decision and the work under it back to the person who owns it | Offered on every key decision and enforced by a refusal in tools/bm_store.py rather than by a rendering convention: a key decision that offers no handback cannot be recorded at all. tools/bm_lead.py performs the handback and writes the page a developer picks the work up from, and tools/test_bm_lead.py drives both, including the refusal of a second handback on one decision. It stays beta because no continuous integration run covers it yet and no handback outside this project is recorded. |
| A half hour catch-up that arrives on its own, on by default after setup | hooks/hooks.json wires it to the Stop hook as a due check rather than a background process, tools/test_bm_consent.py drives every hook wired command against a fresh home directory and fails if any of them writes before consent (the suite job in .github/workflows/tests.yml runs that suite), and SECURITY.md discloses that it ships on by default, what it writes when a catch-up is due, and that it writes nothing when it is not. It stays beta because it cannot fire inside a turn that never ends and because its activity ceiling is a chosen constant, both recorded in docs/KNOWN-LIMITS.md. |
| Handover pages an analyst or a project lead can take a project over from | tools/bm_lead.py generates them from rows and tools/test_bm_lead.py checks both directions: every row the store says belongs on a page reaches that page, and every claim on a page resolves back to the row it came from. It stays beta because no continuous integration run covers it yet and because nobody outside this project has read a pack, which is the empty rung docs/ROADMAP.md section 1 describes. |
| One page showing where a project stands, generated from that project's own records | PROVEN: tools/bm_view.py writes the page as one self contained file from the records, through the collectors in tools/bm_lead.py rather than a second reading of its own, tools/bm_visual.py draws it, and tools/test_bm_view.py and tools/test_bm_visual.py check the structure instead of the pixels (a drawn node for each row, a label matching the row it came from, no address outside the file, one file, exactly one recommended next action). NOT PROVEN, and it is a separate half: publishing that file as a private page needs a paid plan, a signed in session and four further conditions listed in docs/KNOWN-LIMITS.md, so what the product promises is the file on disk and the published page is an addition that can be unavailable. OPEN: nobody outside this project has opened either one. |
| A scripted first fifteen minutes with three commands and something to look at at each step | PROVEN: commands/brotherme-start.md carries the opening block that writes nothing before consent and the first page after it, commands/brotherme-help.md asks one question instead of listing every command, and tools/test_bm_view.py drives the path from an empty folder and fails if a fourth command is offered before the first piece of work completes, if anything is written before consent, or if a section with no rows renders blank instead of the short note tools/bm_view.py holds for it. OPEN, and this is the whole gap: fifteen minutes is a target, no first run by a person who has never used this has been measured, and the checks are structural rather than behavioural. |
| Four levels of alert where exactly one interrupts, computed from the records rather than stored | PROVEN: tools/bm_visual.py computes the levels as one function over rows with no table behind them, so a condition that clears takes its alert with it and nothing has to be dismissed, and tools/test_bm_visual.py holds the four anti noise rules to that (at most one interrupting alert on screen, at most two levels in any one message, no promotion by age, one interrupt per cause per catch-up window). NOT PROVEN: that the ladder keeps a reader engaged, which is a claim about a person rather than about code. OPEN: hooks/hooks.json runs the check when a session stops, so it cannot fire inside a turn that never ends, which is the limit docs/KNOWN-LIMITS.md already records for the half hour catch-up. |
| The offer to take a decision and the work under it back, on screen whether or not a decision is open | PROVEN: tools/bm_view.py renders the standing panel on every page, its wording comes byte for byte from tools/bm_lead.py rather than being retyped, tools/test_bm_view.py fails a page that drops it and fails a drawn decision whose last branch is not the handback, and tools/bm_store.py already refuses to record a key decision that offers no handback at all. NOT PROVEN, and by design: nothing on the page can act on the project, the control copies a prompt the reader pastes back into the session, and docs/KNOWN-LIMITS.md states that as a limit rather than dressing it up. OPEN: no handback by anyone outside this project is recorded. |
| What the hooks cost per action, measured on a stated machine, with the parts nobody measured named as unmeasured | MEASURED: tools/bm_hookbench.py reads which programs fire at which event from hooks/hooks.json, feeds each one the payload shape docs/HOOKS.md documents, and times it against a store built for the run inside a temporary directory with HOME and every BrotherMode variable pinned there. It reports, per user action, the cost of each program AND the cost of the whole chain (the four Stop programs share one budget, so those are two different numbers), each as a median with its spread over a stated number of repetitions, plus the machine and interpreter the run was taken on, and the exit codes and fail open lines the run actually produced. docs/PERFORMANCE.md is generated from that run and carries the record it was rendered from; tools/test_bm_hookbench.py re-renders the page from that record and fails on one differing byte, so a number cannot be typed onto the page by hand, and it also refuses a sandbox whose fence fails open rather than reporting the cost of a hook that checked nothing. NOT MEASURED, named on the page rather than estimated: the fork, the exec and the interpreter bootstrap every hook process pays before any code of this project runs, which is why every published total is a LOWER BOUND (the tool runs the programs in process because tools/test_bm.py bans import subprocess in shipping modules and its allowlist does not name this one); the SessionStart hook, which is a shell script; store lock contention frequency in real use; hook failure frequency in the field; and the share of real sessions hitting a warning or a false refusal, which needs telemetry this project deliberately does not collect. OPEN: the numbers are one machine, one operating system and one Python version, and nothing here says what they are on anyone else's. |

**Experimental**, built or planned, not measured.

| Capability | What proves it, or why it is not offered |
|---|---|
| Delivering a web build through the same guided flow | not measured |
| Deployment previews attached to a delivery | not measured |
| Benchmark harness comparing runs | docs/BENCHMARK.md describes the method and docs/BENCHMARK-V1-V2-RC2.md records a dated run. No run against the current tree is recorded, so no current number is claimed. |
| Cursor compatibility mode: independent install, manage, uninstall, and a local Fable-to-Cursor harness | scripts/install_cursor.py and scripts/uninstall_cursor.py own the Cursor lifecycle; tools/bm_cursor.py is the manage and mailbox harness CLI; tools/bm_cursor_hook.py adapts Cursor hook payloads to the Claude fence contract; tools/test_bm_cursor.py covers adapter translation, install ownership, and dispatch-claim-record-adopt. docs/CURSOR-COMPAT.md states the honest limit: fence enforcement under Cursor is ADVISORY until a live Agent canary is recorded. No live Cursor Agent or Cloud Agent canary is in this tree yet. |

**Unsupported**, not offered, and no plan makes it offered.

| Capability | What proves it, or why it is not offered |
|---|---|
| Publishing to production on its own | Not offered. Cutting and publishing a release is a founder-gated sequence of steps in docs/RELEASE.md, and the suite skips the release checks until a human has cut the tag. |
| Spending money on the user's behalf | Not offered. SECURITY.md states there is no account and no server, and that the only outbound call is a version check the user invokes by hand. |
| Legal or security certification of any kind | not measured |
| A guaranteed native mobile result | not measured |
| Replacing a human specialist review | not measured |
| Multi-user or enterprise project management | Not offered. README.md states there is no shared server, no account system and no multi-user coordination layer, and that running this as a control plane for several people is not what it is for. |
| Changing its own safety rules | not measured |

<!-- END GENERATED CAPABILITY STATUS -->

## License

MIT. See [LICENSE](LICENSE).
