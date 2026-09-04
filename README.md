# Brother

**When AI says done, Brother gives you proof.**

Brother turns a plain-language outcome into checked work, a rerunnable receipt, useful local memory, and a human acceptance decision.

## Install

Brother ships one repository for two clients, Claude Code and Codex. Pick the one you have open; you do not need both. Keep `python3` and `git` on the machine, plus `pytest` for the toy delivery below, whose second check runs `python3 -m pytest`.

One prerequisite is easy to miss because nothing creates it for you. Brother's single-writer fence reads a per-repository store, and the store does not exist until you make it. The fence runs advisory by default, which warns and lets the edit through, so most machines never notice. Set `BM_FENCE_MODE=enforced` and it fails closed instead: every edit in that repository is refused, with `this project has no BrotherMode store`, until you run these two commands once from the root of the repository you are working in.

```bash
python3 products/brothermode/tools/bm_store.py init
python3 products/brothermode/tools/bm_store.py claim <a short name> --lifetime ephemeral --objective "<what you are about to do>" --files <the paths you will edit>
```

Both paths are relative to a checkout of this repository; from an installed plugin the same two files live under the plugin's own `products/brothermode/tools` directory. Enforced mode needs both steps, not just the first: a store with no active claim refuses too, with `the BrotherMode store holds no active claims`. The fence's own refusal message names `tools/bm_store.py`, which is where that file sits inside the product, not where it sits from your project root; the two commands above are the ones that run.

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

The marketplace this repository declares is named `brother`, in lower case, whatever case the repository slug you added it from carries. Use that spelling everywhere a command names the marketplace: `claude plugin marketplace update Brother` answers `Marketplace 'Brother' not found. Available marketplaces: brother` and exits 1.

To upgrade to the latest published version:

```bash
claude plugin marketplace update brother && claude plugin update brother@brother
```

To uninstall:

```bash
claude plugin uninstall brother@brother && claude plugin marketplace remove brother
```

### Codex

Run this in a terminal from any directory, using the app-bundled Codex CLI:

```bash
codex plugin marketplace add https://github.com/khalilmaaouni/Brother && codex plugin add brother@brother --json
```

The two commands are joined with `&&` on purpose. On separate lines the shell runs the second whatever the first did, so a failed marketplace step followed by a plugin step that succeeds against an older copy leaves a zero exit code and a reader with no sign that anything went wrong.

A local clone path works in place of the HTTPS URL. Confirm the install:

```bash
codex plugin list --available --json
```

The result names `pluginId brother@brother`. A Codex plugin cannot carry hooks, so this alone leaves Brother's single-writer fence and its other hook-driven controls silent. Run the second, required step once per Codex home, from the root of a checkout of this repository, because `scripts/codex_hooks_install.py` is a path inside this repository and an installed plugin does not carry it:

```bash
python3 scripts/codex_hooks_install.py --codex-home ~/.codex --allow-default-home --trust
```

This writes Brother's 18 hook registrations into Codex's own user-scope hooks file, then asks Codex itself to read them back and prints the count and trust state Codex reports. Treat the hooks as wired only when it exits 0 on the line `PASS: codex reports all 18 hook(s) trusted and enabled`. Read `python3 scripts/codex_hooks_install.py --help` for the exact flags. Skip this step and every control those hooks carry, the write fence included, reads NO-DATA in Brother's receipts instead of running.

Codex invokes Brother through the plugin's skills, never a slash command. Inside a checkout, the same engine runs with:

```bash
python3 scripts/brother_run.py "<outcome>" --cwd <repo>
```

That command needs a configured model worker on the machine: the engine asks a planning model to decompose the outcome, and it refuses the run and exits non-zero when no worker answers with a plan it can read.

To upgrade, add the marketplace again at the new ref and add the plugin again, joined so the second step does not run over a failed first one:

```bash
codex plugin marketplace add https://github.com/khalilmaaouni/Brother --ref <new ref> && codex plugin add brother@brother --json
```

To uninstall:

```bash
python3 scripts/codex_hooks_install.py --codex-home ~/.codex --allow-default-home --uninstall
codex plugin remove brother@brother
codex plugin marketplace remove brother
```

`--uninstall` removes only the hook commands this repository's installer wrote, identified by those exact command paths, and only its own marked trust section in `config.toml`; any other hook in `$CODEX_HOME/hooks.json` (`~/.codex/hooks.json` by default) survives, and a second run answers NO-DATA rather than removing anything. Do not delete the whole hooks file instead: it can hold hooks that are not Brother's. Write `brother@brother` in the plugin removal: `codex plugin remove brother` is refused with `plugin requires --marketplace unless passed as <plugin>@<marketplace>`.

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

The real run behind this example produced two units and changed two files, `mathlib.py` and `test_mathlib.py`. A delivery record naming only those two files and two PASS units, with no check a stranger could re-run, is the shape Brother refuses: `scripts/receipt_door.per_file_checks` turns a run's receipts into one entry per changed file instead. The lines below are what that function prints from the run's recorded facts: `python3 scripts/readme_receipt_sample.py` renders them through the shipped code, and `python3 scripts/test_readme_honesty.py` fails this page when the rendered text and the text printed here differ. Those two commands prove that this block is reproducible from the stored facts; they say nothing about how the stored facts were first written.

```text
mathlib.py (unit guard): guard delivered: the check python3 -c "import mathlib; assert mathlib.add(1,2)==3 and mathlib.add(1.5,2)==3.5" && python3 -c "import mathlib; mathlib.add('a','b')" 2>&1 | grep -q '^TypeError: .' was run and exited 0 (no dependency declared: this check proves its own change only), and its full output is in ~/.claude/brother-run/docs/plan/runs/20260903T071356-make-add-refuse-non-numeric-input-with-a/run.log. Evidence family NO-DATA, oracle NO-DATA, independence unverified. Target revision NO-DATA, environment lock NO-DATA, data identity NO-DATA. Check written by the planning model, harness 015760192728 (private hub revision).

test_mathlib.py (unit test): test is NO-DATA: this unit depends on guard, and its check was never re-run with that change reverted, so nothing shows the check exercises it. The check it named was python3 -m pytest test_mathlib.py -q -k 'type or numeric or error or raise'. Evidence family NO-DATA, oracle NO-DATA, independence unverified. Target revision NO-DATA, environment lock NO-DATA, data identity NO-DATA. Check written by the planning model, harness 015760192728 (private hub revision).
```

Each line names the exact check command, its captured exit code, where its full output lives, and the before-and-after discrimination: the guard check failed before the work and passed after, so it proves its file; the test unit declared a dependency on the guard and its check was never re-run with that change reverted, so nothing shows the check exercises it, and Brother reports that as NO-DATA rather than letting a green command claim more than it established. `harness 015760192728...` is the engine revision the run recorded, cut to its first 12 hex, and it is stamped `(private hub revision)` because that object is not in this public repository: `git cat-file -t 015760192728a32c5055cd7dd741f7d3a3522d0b` exits non-zero in a public clone. That command proves the object is absent here, and nothing more; a public clone cannot check what a private revision contains. A run made from an installed public copy resolves its own harness sha instead of carrying this label.

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

### A refusal can leave you with nothing to type next

A run that refuses tells you why and stops, which is only half an answer if you then have to guess the recovery. Every refusal now ends on one copyable command. A run that reached the end with work still unfinished prints `--continue`, which resumes that same run instead of starting a second one against the same repository. A door refusal, where nothing was claimed and the store is untouched, prints the ask again instead, because there is no run to continue. Asking to continue in a repository that has nothing to continue answers NO-DATA and exits 0, never a traceback. Prove all three with `python3 scripts/test_brother_run_continue.py`.

### Work can reach your checkout before you agree

Each piece runs in its own isolated checkout. Integration is serial, and the acceptance screen is for a person to answer. Prove the isolation and integration rules with `python3 scripts/test_worktree_lane.py` and `python3 scripts/test_integrate.py`.

### The same failed approach can return

Before a known file is edited, Brother can recall a recorded lesson from the Vault. A third use of the same failed technique is refused and sends the run to gather new information. Prove those controls with `python3 scripts/test_attempt_ledger.py`, `python3 scripts/test_attempt_hook.py`, and `python3 scripts/test_find_out.py`.

### A release can be hard to verify

Two products carry a checksum manifest and a verifier, BrotherMode and BrotherSBE. In each of those two product directories, run `bash scripts/verify-install.sh`: it compares the bytes you received against the `CHECKSUMS.sha256` that shipped with them, and it changes nothing. Run only the verifier. `sh scripts/checksums.sh CHECKSUMS.sha256` rewrites that manifest from whatever is on disk, so running it first would overwrite the very thing under test, turn any tamper into a fresh agreeing manifest, and prove nothing about what was shipped. Regenerating the manifest is the maintainer's step, taken when cutting a release or when the product's own files change, never the reader's step before verifying.

The shipped engine carries its own manifest too, and its own verifier beside it. Which path you type depends on where you are standing, because the bundle is unwrapped on install: from a checkout of this repository run `python3 bundle/runtime/verify_runtime.py`, and from an installed plugin's own root, where the same files sit in `runtime/` with no `bundle/` above them, run `python3 runtime/verify_runtime.py`. It re-hashes every file `bundle/runtime/RUNTIME-MANIFEST.json` names and prints PASS, FAIL naming each file that differs, or NO-DATA when the manifest is missing. It needs nothing beside it, so it runs on an installed copy that has no `scripts/` directory.

### The cost can be missing

The receipt prints input, output, and cached token fields when the worker returns them. When it does not, each field says NO-DATA and why. Prove the cost block with `python3 scripts/test_brother_run.py`.

### A battery can hide its red lines

Expected failures are named one by one with review dates. NO-DATA never counts as a pass. Prove that reading with `python3 scripts/test_battery_verdict.py`.

## Limits you should know first

Brother's hooks are registered for every Claude Code session on the machine. Which repositories they then do anything in depends on which install path you took, and only the two product installer scripts scope them: that split is stated in full two paragraphs below, and it is the decision recorded in `docs/decisions/hook-scope-at-install-2026-09-04.json`. They run at session start and end, when a turn stops, before compaction, before edits and shell commands, and after shell commands. They call `git` and `python3` locally and read or write their own state files under your Claude config directory, inside the open repository, and in the Vault after you set it up. Before compaction, they snapshot the working tree into a private local git ref. They can refuse an edit or shell command that crosses a file fence, refuse a shell command that would start a fifth concurrent session, and hold a session open at stop until writes outside its declared scope are reconciled. They do not use the network or push. Prove the undeclared-write refusal, and the fail-open behaviour it must keep on any condition it cannot read, with `python3 scripts/test_lifecycle_hooks.py`.

The single-writer fence named earlier in this file goes further than the hooks above: a worker claims its unit before it starts, the claim is an exclusive lease, and a second worker cannot win the same unit while that lease is live. Prove it with `python3 scripts/test_claim_store.py`. And nothing reaches a remote unchecked: a push-time gate reads the outgoing commits for a private term or a credential shape and refuses the push before it lands, rather than after somebody has to notice. Prove the private-term refusal with `python3 scripts/test_private_terms_scan.py` and the push-time gate with `python3 scripts/test_pre_push_gate.py`. Prove that a session is held open until an undeclared write is reconciled, and that the same hook fails open rather than blocking the machine when its own parser is confused, with `python3 scripts/test_lifecycle_hooks.py`.

Which repositories they run in is the install's decision, and the two product installer scripts, `products/brothermode/scripts/install.py` and `products/brothersbe/tools/install.py`, default to: only the ones you name. Both of those installers write one marker file, `brother-hook-scope`, beside the Claude settings file they edited, and print in one line where hooks are active and how to add a repository. While that marker is there, a repository without a `.brother/config` file gets nothing: every hook from both products returns at entry, having read no file of its own and written none, so a machine full of unrelated repositories pays nothing for having Brother installed. Opt a repository in with `mkdir -p .brother && printf 'hooks: on\n' > .brother/config` at its root, or name it at install time with `python3 products/brothermode/scripts/install.py --repo /path/to/your-project`. BrotherSBE's installer opts its target project in for you, because you already told it which project you meant. Either installer's `--hooks-everywhere` takes the marker away and goes back to hooks in every repository on the machine; uninstalling removes it too.

The marketplace install at the top of this file is the other path, and it is not scoped: `claude plugin install` runs neither installer script, so it writes no marker, and its hooks do run in every repository until you write the marker yourself with `printf 'scope: repositories\n' > ~/.claude/brother-hook-scope`. That is the ruling, not an oversight: the decision record `docs/decisions/hook-scope-at-install-2026-09-04.json` records the founder taking option A, scope the script installs only, and leaving the plugin path machine wide rather than having a hook write installation state behind a machine's back.

A repository can also turn every hook off without uninstalling anything: write `hooks: off` to `.brother/config` at the repository root, and every hook from both products exits immediately at entry, printing a one-time notice the first time any hook sees it in a session. That line wins over the file's presence, so an opted-in repository can be switched off and back on without deleting anything. No other configuration exists in that file.

Prove both halves with `python3 -m unittest test_bm.TestBmRepoScope test_bm.TestBmInstallHookScope` from `products/brothermode/tools`, and the same two class names with the `sbe` spelling from `products/brothersbe/tools`.

The engine runs a unit's done_check as a real shell command on your machine, to decide whether the work it claims is actually finished. When a done_check cannot run at all, the engine asks the planning model once for a replacement and adopts what comes back; that replacement is checked against a fixed allowlist of interpreters (python3, pytest, sh, bash, git, make, gh, grep, test) and refused outright if it chains, pipes, substitutes, or redirects outside the tree, so a plan record cannot steer the engine into running arbitrary text. A unit's own original, human-written done_check is never filtered this way; only a model-adopted replacement is. Prove the guard with `python3 scripts/test_door.py` and `python3 scripts/test_brother_run.py`.

Brother is not the cheap way to make a one line change, and it says so before you wait rather than after. Measured on 2026-09-04: an outside evaluator asked the door for a one line edit plus a one line test, did the same thing by hand in 0.78 seconds, and got it through the door in 568.03 seconds, about 728 times longer. Almost none of that is Brother's own code: on a throwaway repository with both model calls stubbed out, a whole two unit run cost the engine a median 2.58 seconds and a one unit run 1.69 seconds, so roughly 565 of those 568 seconds were two headless model sessions waiting to answer. Nothing in this release removes that wait. What it does is name it up front: the intent screen, posed before anything is claimed or run, now prints how many model sessions the run will open, what earlier runs against this same repository really took, and the median of those as the expected wall clock, or NO-DATA when there is no earlier run to derive one from. What the same edit would cost you by hand is not measured there and the run does not claim to beat it. The ruling behind this, with the alternative that was declined and what would flip it, is `docs/decisions/light-path-for-small-changes-2026-09-04.json`.

- The public toy delivery has no recorded human acceptance. Brother never fills the acceptance answer on your behalf.
- The link between recalling a lesson and avoiding the same mistake has not yet been measured on a comparison run. The recall and refusal mechanisms exist; their real effect remains NO-DATA.
- The current public tag is unsigned. `git tag -v <the tag>` will not produce a verified signature.
- A receipt proves only the checks it names. Weak checks remain weak, and missing checks remain invisible.
- Brother integrates a finished unit's changes into your checkout, one unit at a time. It does not merge a pull request, publish a release, delete anything outside its own run directory, spend money, or accept a result for you.

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
