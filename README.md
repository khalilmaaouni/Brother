# Brother

**When AI says done, Brother gives you proof.**

Brother turns a plain-language outcome into checked work, a rerunnable receipt, useful local memory, and a human acceptance decision.

## Install

Brother ships one repository for two clients, Claude Code and Codex. Pick the one you have open; you do not need both. Keep `python3` and `git` on the machine, plus `pytest` for the toy delivery below, whose second check runs `python3 -m pytest`.

### Claude Code

Run this in a terminal from any directory:

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

This is the picture of a successful install:

```text
Successfully added marketplace
Successfully installed plugin
```

Open your repository and type:

```text
/brother
```

With no earlier run to resume, the first answer includes:

```text
no unfinished run found
```

Brother asks for the outcome in ordinary words. There is no command menu.

To upgrade to the latest published version:

```bash
claude plugin marketplace update Brother && claude plugin update brother@brother
```

To uninstall:

```bash
claude plugin uninstall brother@brother && claude plugin marketplace remove Brother
```

### Codex

Run this in a terminal from any directory, using the app-bundled Codex CLI:

```bash
codex plugin marketplace add https://github.com/khalilmaaouni/Brother
codex plugin add brother@brother --json
```

A local clone path works in place of the HTTPS URL. Confirm the install:

```bash
codex plugin list --available --json
```

The result names `pluginId brother@brother`. A Codex plugin cannot carry hooks, so this alone leaves Brother's single-writer fence and its other hook-driven controls silent. Run the second, required step once per Codex home:

```bash
python3 scripts/codex_hooks_install.py --codex-home ~/.codex --allow-default-home --trust
```

This writes Brother's 18 hook registrations into Codex's own user-scope hooks file and prints the trusted count Codex itself reports back. Read `python3 scripts/codex_hooks_install.py --help` for the exact flags. Skip this step and every control those hooks carry, the write fence included, reads NO-DATA in Brother's receipts instead of running.

Codex invokes Brother through the plugin's skills, never a slash command. Inside a checkout, the same engine runs with:

```bash
python3 scripts/brother_run.py "<outcome>" --cwd <repo>
```

To upgrade, add the marketplace again at the new ref, then add the plugin again:

```bash
codex plugin marketplace add https://github.com/khalilmaaouni/Brother --ref <new ref>
codex plugin add brother@brother --json
```

To uninstall:

```bash
codex plugin remove brother
codex plugin marketplace remove brother
```

`codex_hooks_install.py` has no uninstall or `--uninstall` route, so removing the hooks it wrote is a file edit, not a command: delete `~/.codex/hooks.json` (or `$CODEX_HOME/hooks.json` if you used a different home). Codex reads no hooks it cannot find, so the trust entries the install left in `~/.codex/config.toml` go inert with it; delete the `[hooks.state...]` blocks there too if you want that file clean.

## What success looks like

Brother separates your result into bounded pieces, runs and checks them away from your checkout, integrates them one at a time, and gives you a receipt. The receipt names changed files, each check, its author, the engine revision, and returned cost data.

Brother starts each piece as a headless model session in permission mode `acceptEdits`: edits are auto-accepted in that piece's own worktree, never in your checkout.

A finished run does not approve itself. You read the intent screen before risky work and the acceptance screen after delivery. This repository stores its acceptance records under `docs/deliveries`. Each says whether a person or an agent under a named delegation typed it. Only a person's recorded acceptance counts.

The Vault carries lessons into later work as local Markdown you can read and edit. A remembered lesson can warn a later run but cannot overrule current evidence or a person.

## One small delivery

In a throwaway repository, start with an `add()` function and one passing test. Then ask:

```text
/brother make add() refuse non-numeric input with a clear error and cover it with a test
```

The real run behind this example produced two units and changed two files, `mathlib.py` and `test_mathlib.py`. A delivery record that named only those two files and two PASS units, with no check a stranger could re-run, is exactly the shape a 2026-09-04 review found and refused. Brother's own `scripts/receipt_door.per_file_checks` now turns a run's receipts into one entry per changed file. The lines below are that function's own output over the run's real recorded facts, never typed by hand: `python3 scripts/readme_receipt_sample.py` reprints them from those facts through the shipped code, and `python3 scripts/test_readme_honesty.py` refuses this page when the two disagree.

```text
mathlib.py (unit guard): guard delivered: the check python3 -c "import mathlib; assert mathlib.add(1,2)==3 and mathlib.add(1.5,2)==3.5" && python3 -c "import mathlib; mathlib.add('a','b')" 2>&1 | grep -q '^TypeError: .' was run and exited 0 (no dependency declared: this check proves its own change only), and its full output is in ~/.claude/brother-run/docs/plan/runs/20260903T071356-make-add-refuse-non-numeric-input-with-a/run.log. Evidence family NO-DATA, oracle NO-DATA, independence unverified. Target revision NO-DATA, environment lock NO-DATA, data identity NO-DATA. Check written by the planning model, harness 015760192728 (private hub revision).

test_mathlib.py (unit test): test is NO-DATA: this unit depends on guard, and its check was never re-run with that change reverted, so nothing shows the check exercises it. The check it named was python3 -m pytest test_mathlib.py -q -k 'type or numeric or error or raise'. Evidence family NO-DATA, oracle NO-DATA, independence unverified. Target revision NO-DATA, environment lock NO-DATA, data identity NO-DATA. Check written by the planning model, harness 015760192728 (private hub revision).
```

Each line names the exact check command, its captured exit code, where its full output lives, and the before-and-after discrimination: the guard check failed before the work and passed after, so it proves its file; the test unit declared a dependency on the guard and its check was never re-run with that change reverted, so nothing shows the check exercises it, and Brother reports that as NO-DATA rather than letting a green command claim more than it established. `harness 015760192728...` is the exact commit of the engine that ran this, cut to its first 12 hex; it is stamped `(private hub revision)` because it is a commit in the private development checkout that produced this run, not one a public clone of this repository can look up (`git cat-file -t 015760192728a32c5055cd7dd741f7d3a3522d0b` fails there). A run made from an installed public copy resolves its own harness sha instead of carrying this label.

The same facts also decide what to read first. `receipt_door.reading_order` sorts every changed path into four sections the acceptance screen prints in order, `REVIEW FIRST` (a path naming auth, money, migration, parsing, concurrency or a dependency manifest, or a path outside the scope its unit declared), `NOT PROVEN` (a path whose own receipt is not verified), `LOW-RISK MECHANICAL` (a declared, risk-free path its check proved) and `NO NEED TO RE-READ` (a path a unit declared and never touched). A section holding nothing is still printed as empty, because a heading that disappears reads exactly like a heading nobody computed. Beside them, on the machine receipt only, `receipt_door.cognitive_debt` counts what this delivery costs the next reader: a changed dependency manifest, a path naming a new indirection, and a file outside its unit's declared scope. That count is deliberately internal, never a flag or a mode, because a number a person is shown is a number a person can be asked to hit.

A delivery record naming these checks (built by `receipt_door.per_file_checks`, stored by `accept_delivery.record(..., checks=...)`) is refused outright when it carries no per-file checks or a malformed one; run the check that holds both rules in place:

```bash
python3 scripts/test_receipt_door.py
```

## The problems Brother answers

### A green check can prove nothing

A command that passed before the work, a unit that changed no file, or a test that still passes after its dependency is reverted does not prove the delivery. Brother reports NO-DATA for those cases. Prove the rule with `python3 scripts/test_receipt_door.py`.

### A receipt can be impossible to trace

Every receipt names who wrote the check and which engine revision ran it, and a stored delivery record now carries one entry per changed file naming the exact check command, its exit code, its output path, and the before-and-after discrimination, instead of a bare list of file names and a PASS count nobody can re-run. A record missing that is refused before it is written. Prove the report shape with `python3 scripts/test_brother_run.py` and the per-file refusal with `python3 scripts/test_receipt_door.py`.

### A run can end with files changed and no receipt on disk

Every run that reaches the end writes its receipt to `receipt/receipt.json` inside its own run directory, and prints that path as its last line, so a stranger who scrolled past the report still holds one file naming every changed file, every check, and what was not proven. The run directory lives under the runs root (`--runs-root`, `~/.claude/brother-run/docs/plan/runs/` when Brother runs as an installed plugin), never inside your checkout, which integration requires to stay clean. A run that cannot write that file exits non-zero and says so instead of returning 0. Prove it with `python3 scripts/test_brother_run.py`.

### Work can reach your checkout before you agree

Each piece runs in its own isolated checkout. Integration is serial, and the acceptance screen is for a person to answer. Prove the isolation and integration rules with `python3 scripts/test_worktree_lane.py` and `python3 scripts/test_integrate.py`.

### The same failed approach can return

Before a known file is edited, Brother can recall a recorded lesson from the Vault. A third use of the same failed technique is refused and sends the run to gather new information. Prove those controls with `python3 scripts/test_attempt_ledger.py`, `python3 scripts/test_attempt_hook.py`, and `python3 scripts/test_find_out.py`.

### A release can be hard to verify

Each installed product carries a checksum manifest and a verifier. In each product directory, run `bash scripts/verify-install.sh`: it compares the bytes you received against the `CHECKSUMS.sha256` that shipped with them, and it changes nothing. Run only the verifier. `sh scripts/checksums.sh CHECKSUMS.sha256` rewrites that manifest from whatever is on disk, so running it first would overwrite the very thing under test, turn any tamper into a fresh agreeing manifest, and prove nothing about what was shipped. Regenerating the manifest is the maintainer's step, taken when cutting a release or when the product's own files change, never the reader's step before verifying.

The shipped engine carries its own manifest too, and its own verifier beside it: run `python3 bundle/runtime/verify_runtime.py` from an installed plugin. It re-hashes every file `bundle/runtime/RUNTIME-MANIFEST.json` names and prints PASS, FAIL naming each file that differs, or NO-DATA when the manifest is missing. It needs nothing beside it, so it runs on an installed copy that has no `scripts/` directory.

### The cost can be missing

The receipt prints input, output, and cached token fields when the worker returns them. When it does not, each field says NO-DATA and why. Prove the cost block with `python3 scripts/test_brother_run.py`.

### A battery can hide its red lines

Expected failures are named one by one with review dates. NO-DATA never counts as a pass. Prove that reading with `python3 scripts/test_battery_verdict.py`.

## Limits you should know first

The install registers hooks in every Claude Code session on the machine, and scopes them to the repositories you opt in. They run at session start and end, when a turn stops, before compaction, before edits and shell commands, and after shell commands. They call `git` and `python3` locally and read or write their own state files under your Claude config directory, inside the open repository, and in the Vault after you set it up. Before compaction, they snapshot the working tree into a private local git ref. They can refuse an edit or shell command that crosses a file fence, refuse a shell command that would start a fifth concurrent session, and hold a session open at stop until writes outside its declared scope are reconciled. They do not use the network or push.

Which repositories they run in is the install's decision, and the default is: only the ones you name. Both installers write one marker file, `brother-hook-scope`, beside the Claude settings file they edited, and print in one line where hooks are active and how to add a repository. While that marker is there, a repository without a `.brother/config` file gets nothing: every hook from both products returns at entry, having read no file of its own and written none, so a machine full of unrelated repositories pays nothing for having Brother installed. Opt a repository in with `mkdir -p .brother && printf 'hooks: on\n' > .brother/config` at its root, or name it at install time with `python3 products/brothermode/scripts/install.py --repo /path/to/your-project`. BrotherSBE's installer opts its target project in for you, because you already told it which project you meant. Either installer's `--hooks-everywhere` takes the marker away and goes back to hooks in every repository on the machine; uninstalling removes it too.

One honest gap in that paragraph: the `claude plugin install` line at the top of this file does not run either installer, so it writes no marker, and its hooks do run in every repository until you write the marker yourself with `printf 'scope: repositories\n' > ~/.claude/brother-hook-scope`. Whether the plugin path should scope itself is the open half of the decision record `docs/decisions/hook-scope-at-install-2026-09-04.json`.

A repository can also turn every hook off without uninstalling anything: write `hooks: off` to `.brother/config` at the repository root, and every hook from both products exits immediately at entry, printing a one-time notice the first time any hook sees it in a session. That line wins over the file's presence, so an opted-in repository can be switched off and back on without deleting anything. No other configuration exists in that file.

Prove both halves with `python3 -m unittest test_bm.TestBmRepoScope test_bm.TestBmInstallHookScope` from `products/brothermode/tools`, and the same two class names with the `sbe` spelling from `products/brothersbe/tools`.

The engine runs a unit's done_check as a real shell command on your machine, to decide whether the work it claims is actually finished. When a done_check cannot run at all, the engine asks the planning model once for a replacement and adopts what comes back; that replacement is checked against a fixed allowlist of interpreters (python3, pytest, sh, bash, git, make, gh, grep, test) and refused outright if it chains, pipes, substitutes, or redirects outside the tree, so a plan record cannot steer the engine into running arbitrary text. A unit's own original, human-written done_check is never filtered this way; only a model-adopted replacement is. Prove the guard with `python3 scripts/test_door.py` and `python3 scripts/test_brother_run.py`.

- The public toy delivery has no recorded human acceptance. Brother never fills the acceptance answer on your behalf.
- The link between recalling a lesson and avoiding the same mistake has not yet been measured on a comparison run. The recall and refusal mechanisms exist; their real effect remains NO-DATA.
- The current public tag is unsigned. `git tag -v <the tag>` will not produce a verified signature.
- Installing the bundle from its own release tag is guaranteed only from 1.0.1 onward. The 1.0.0 release did not establish that guarantee.
- A receipt proves only the checks it names. Weak checks remain weak, and missing checks remain invisible.
- Brother does not merge, release, delete, spend money, or accept a result for you.

For maintainers: There is no headcount cap on commands, skills, hooks, or workers. The public surface still changes only by deliberate human choice.

## Choose your path

| You are | Start here |
| --- | --- |
| An engineer who must satisfy a security reviewer | [Engineer start](docs/for-engineers/00-START-HERE.md) |
| A founder who writes and ships the code | [A startup week](docs/for-engineers/STARTUP-WEEK.md) |
| An analyst or non-engineer who defines what success means | [Analyst start](docs/for-analysts/00-START-HERE.md) |
| Anyone deciding what the Vault should remember | [What the Vault is](docs/explanation/VAULT.md) and [how to use it](docs/how-to/USE-THE-VAULT.md) |

## License

MIT. See [LICENSE](LICENSE).
