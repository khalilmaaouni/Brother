# The engineering reference

This is the full engineering documentation: the method, the gates, the laws and
the install paths, kept complete in one place. The short front door is the root
[README](../README.md); start there if you are new. Nothing here was cut when the
README was shortened: this file is that README, preserved whole.

BrotherSBE is an engineering colleague for Claude Code that designs backend and data systems before building them, and proves its work with checks that actually run. You tell it the outcome you want in plain language, and it handles the method, the order, and the evidence.

Why it is worth your time: install once, describe the outcome you want, then follow one recommended action at a time. You never memorize a command list, and nothing is claimed as done until a check has shown it.

Install:

```bash
claude plugin marketplace add khalilmaaouni/BrotherSBE
claude plugin install brothersbe@brothersbe
```

Then, in Claude Code:

```
/brothersbe:start
```

That is the whole install. Everything below is optional reading.

<details>
<summary>Other ways in, and what each one costs you</summary>

**Inspect before you trust.** Clone, validate, and load for one session only:

```bash
git clone https://github.com/khalilmaaouni/BrotherSBE ~/.claude/skills/brothersbe
claude plugin validate ~/.claude/skills/brothersbe
claude --plugin-dir ~/.claude/skills/brothersbe
```

`--plugin-dir` lasts for that session; the marketplace install above persists.
[docs/MIGRATION.md](MIGRATION.md) covers both.

**Update and remove.** `claude plugin update brothersbe` (restart to apply),
`claude plugin uninstall brothersbe`. Every session start compares your copy
against the version already on disk and says plainly when something changed,
with no network call to do it.

</details>

**Handing this to a team?** Give them
[the Field Book](fieldbook/BrotherSBE-Booklet.html). Two pages tell
anyone what this is, what it actually enforces and one honest limit; the
chapters after that carry the personas, the adoption path, and worked scenarios
for warehouse and BI work (Snowflake, Databricks, Power BI, Azure), backend
services, web and app builds, and architecture decisions. Its command, role,
check and limit tables are generated from this repository, and
`sbe book --check` fails in CI when a source moves without a regenerate, so it
cannot quietly describe a version that no longer ships.

That command looks at where you are and takes it from there: a new project or one already in progress, it finds the right next step. Along the way, three guided companions in [`skills/`](../skills) keep you oriented: `/brothersbe:next` recommends exactly one next action, `/brothersbe:status` explains where you are in plain language, and `/brothersbe:help` lays out the whole map when you ask for it. New to any of this? [The beginner explainer](explainer/index.html) covers the same ground in plain language.

---

## The engineering reference

Everything below this line is the full engineering documentation: what the method is, how the gates work, and how to wire the checks into CI. You do not need it to begin, and `/brothersbe:start` will bring you here when it matters.

BrotherSBE is a Claude Code skill that acts as a senior backend and data engineering colleague. It designs systems in the order the work actually runs: purpose, process, architecture, data, expression, then verification. It produces a design dossier, decides architecture from decision tables with named criteria, and holds the result to checks that run.

Identity, five words, each a law in [SKILL.md](../SKILL.md) and the [`references/`](../references) files its routing table names: **realistic, SOTA, best practices driven, proven, trustable.**

It is the domain specialist sibling of [BrotherModeUp](https://github.com/khalilmaaouni/BrotherModeUp), the general orchestrator. BrotherSBE is standalone: clone it and it works with nothing else installed. See [PARITY.md](../PARITY.md) for what the two share and where they diverge.

**Start here:** [a worked engagement](guides/05-a-worked-engagement.md), one system designed end to end with the real commands and the real output.

## Who it is for

- **Small teams first (two to eight people).** Lessons become law only through a reviewed pull request into `memory-template/LEARNED.md`, so no colleague's tool changes behavior silently.
- **Strong individual contributors second.** On a solo install the team loop collapses to local learning and everything still works.

The operator is a working backend, infrastructure, or data engineer. BrotherSBE speaks to them as a peer: it shows the diff, names the command, uses the jargon, and explains on request rather than by default.

## The spine

Two rules carry the design.

**Design comes before verification.** The expensive mistakes are made while deciding what to build, how the process runs, what shape the system takes, and how the data is modeled. Checking the result at the end catches none of them.

**An agent earns trust in exact proportion to how mechanically its output can be checked.** Not by fluency, not by model quality. Every law names the thing that enforces it; the laws live in [SKILL.md](../SKILL.md), which keeps the three that fire on no announced trigger, and in the [`references/`](../references) file its routing table sends each of the other sixteen to; a rule that cannot name one is advice and lives in [PRACTICES.md](../PRACTICES.md), which says so.

## The dossier

A design engagement produces at most eight files in one directory, seven dossier files plus the `00-intake.json` the intake writes into the same place. Templates with worked content are in [`templates/dossier/`](../templates/dossier).

| File | Holds | Checked by |
|---|---|---|
| `00-intake.json` | the five intake answers and the computed tier | `sbe_intake.py` writes it |
| `01-purpose.md` | problem, users, success criteria, non-goals, blast radius | artifacts |
| `02-process.md` | actors, steps with triggers and exception paths, handoffs with contracts | artifacts |
| `03-adr.md` | criteria, two rejected alternatives, decision, consequences, flip condition | adr |
| `04-technology-map.md` | per component: technology, owner, failure mode, recovery path | artifacts |
| `05-data-model.md` | conceptual, logical, physical; systems of record; cardinalities | datamodel |
| `06-diagrams.md` | Mermaid views, every node traceable to the dossier | diagrams |
| `07-verification.md` | every claim, the check that proves it, when it runs | artifacts |

**How much of it you write is computed, not chosen.** Five objective questions produce a tier, first match wins: T3 (money, partner data, personal data, production state, or not reversible in an hour) requires all seven; T2 (a contract change, or many consumers) requires six; T1 (one boundary crossed, or some consumers) requires the purpose brief; T0 requires nothing at all. T0 is the common case.

```bash
mkdir -p design/my-project                      # the intake refuses a directory that does not exist
python3 tools/sbe_intake.py design/my-project   # five questions, writes its 00-intake.json there
python3 tools/sbe_design.py .          # artifacts, adr, datamodel, diagrams, placeholder
python3 tools/sbe_decide.py tables/architecture.json shape   # asks for each criterion on stdin
```

`sbe_decide.py` reads its criteria interactively, so pipe the answers when you run it from a script, a CI job or an agent, where a prompt nobody answers is a hang: `printf '5\neventual\nhigh\nhigh\n' | python3 tools/sbe_decide.py tables/architecture.json shape`.

When you are done with the demo dossier, remove it: `rm -rf design/my-project`. The paths above are relative to this clone, so the demo files land INSIDE the installation, and `scripts/verify-install.sh` will (correctly) report any file you created here as EXTRA until you delete it. Real dossiers belong in your own project's repository, not in this clone.

Architecture shape is scored against named criteria in [`tables/architecture.json`](../tables/architecture.json): independently deploying teams, consistency requirement, operational maturity, failure isolation. Every run returns a recommendation, up to two alternatives, the criteria that separated them, and what would flip the decision. A run where no criterion contributed returns NO-DATA with the recommendation suppressed, because a recommendation backed by zero evidence is a guess with a table around it.

## The last mile: four hard gates

Verification comes last, and only four failure classes get structural gates. Each fails silently: a wrong result looks exactly like a right one, and detection latency runs from minutes to never.

- **numbers**: every figure that could reach a decision ships with a second derivation whose text differs from the first by more than case, whitespace and comments, re-run to zero drift against a pinned snapshot. Text difference is the floor the tool can check, not proof that the two derivations are independent: renaming an alias passes, and nothing reads which tables they touch.
- **migration**: forward and reverse both ran against a restored copy, the reverse records a rehearsal run id as a string, and row counts before and after match. A receipt with no row counts is NO-DATA, not a pass: the gate says what it compared instead of asserting a comparison it never made. Stated plainly, because the difference matters: nothing resolves the rehearsal id against a job system, so it is a pointer for a human to follow.
- **approval**: a declared approval must be bound to more than a name typed into a text field. Two paths, and they are not equally strong. A signed `Approved-by:` commit trailer THIS HOST VERIFIED proves a key holder signed it, and an agent without the private key cannot produce it. A recorded `Reviewed-in:` review id proves only that a non-vacuous id sits in the commit message: nothing resolves it, there is no shape check on it, the agent writes commit messages, so an agent can write one. Its verdict is therefore NO-DATA, the same verdict a signature this host could not verify gets and for the same reason, and the evidence line says so on every run. If you need that path to be a control, add a CI step that resolves the id against your review platform. A typed name fails, a `Reviewed-in:` id that is a hyphen fails, and CI needs the signers' public keys for the one path that PASSes. The gate checks the binding of an approval that was declared; nothing detects that a change needed one, so the declaration itself is human review.
- **ran**: no SQL or pipeline change is done until its reconciliation query or test executed and left a receipt with a zero exit code and a nonzero duration. A check that took no time did not run.

The gates live in [`tools/sbe_gate.py`](../tools/sbe_gate.py). They run advisory in a session (print the verdict, exit 0) and enforcing in CI (`--strict`, exit nonzero, stop the merge). Output that has not cleared its gate is presented with the label UNVERIFIED next to the item; that label is the agent's to write, per the law, and no tool applies it. Absent evidence is NO-DATA, never PASS, so a change with nothing to prove is not taxed. A receipt that exists and records nothing is also NO-DATA and says so; a receipt that exists and cannot be parsed, including valid JSON of the wrong shape, is a FAIL, because a broken claim is not an absent one. A check that crashes is reported as a FAIL carrying the exception, never as a missing line: a gate that disappears from the report is worse than one that fails. None of that rests on anyone remembering it. Every check is registered with a declaration of what it reads and what its empty state is, `PASS` is refused as an empty state at construction, and [`evals/test_no_data_class.py`](../evals/test_no_data_class.py) enumerates those registries rather than a written list, so a check added later is covered the moment it is registered.

A companion linter in [`tools/sbe_score.py`](../tools/sbe_score.py) catches the code patterns that swallow an error so a wrong result passes for a right one (bare except, except-then-pass, discarded subprocess result, conflict-skipping upsert, force-try) across `.py .sql .swift .rb .js .ts .go`. The upsert pattern reads the SQL wherever it is written and in whatever host language, so it fires on a `.sql` file, and it stops at the statement's semicolon so a legitimate `ON CONFLICT ... DO UPDATE` beside it is not swept in; it used to require a Python `.execute(` on the same line, which meant the one lint aimed at warehouse work could not fire on a warehouse file. A reviewed exemption carries a visible `# sbe: allow-silent <reason>` marker, so the swallow is auditable in the diff, and the reason is READ: a bare marker, or one carrying a refused token like `tbd`, waives nothing and the hit says why. The evidence names the first five hits or waived lines and then how many it did not name, names any file holding nothing to examine, names the linter's own source as skipped (by path, so a file of yours called `sbe_score.py` is still scanned), and carries the count of files that held no match at all. A run that opened no file is NO-DATA naming why, never "clean", and so is a scan whose every finding in every file was waived.

### What a gate actually reads

Each gate walks the directory it was named (the default is the current directory, never a silently substituted git worktree top) for a receipt file and checks it is internally consistent, not merely present (the operating record proves pasted receipts get invented).

**numbers** looks for `numbers-manifest.json`. A figure passes only with a `snapshot_id`, a `second_derivation` textually different from `query`, `rerun.ran` true, and matching `primary`/`secondary`:

```json
{"figures": [{
  "label": "gmv",
  "snapshot_id": "snap_2026_07",
  "query": "SELECT SUM(amount) FROM orders",
  "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
  "rerun": {"ran": true, "primary": 17570, "secondary": 17570}
}]}
```

**migration** looks for `migration-receipt.json`:

```json
{"forward": {"ran_against_restore": true},
 "reverse": {"ran_against_restore": true, "rehearsal_run_id": "job_8842"},
 "row_counts": {"before": 100, "after_reverse": 100}}
```

**approval** looks for an `APPROVAL` file (declaring the change touches a money or partner path) or an `Approved-by:` trailer or `Reviewed-in:` id on HEAD. The trailer PASSes only when this host verified the commit signature against a key it TRUSTS (`git log` `%G?` = `G`, and `G` alone; `U` is a valid signature whose key matched no trusted principal, which is exactly what a self-generated SSH key produces, so it reports NO-DATA); an unsigned typed name FAILs, and a `Reviewed-in:` id reports NO-DATA because nothing resolves it.

**ran** looks for `ran-receipt.json`:

```json
{"checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 812}]}
```

Four moments also write themselves down as **decision packages**: a gate FAIL, a WAIVED check, a tier raised or disposed by `sbe impact`, and a forced task close. Each package is a versioned Markdown file bound to the commit it was written against, quoting the verdict line verbatim, excerpting the checker code at the lines that decided, and counting rather than copying every output line outside the verdict grammar, so a package you share cannot leak what a receipt already refuses to store. `sbe explain` browses or regenerates a package without ever overwriting one bound to another commit, and `sbe lineage <artifact>` walks binding, receipts, decisions, notes and commits oldest to newest with an evidence pointer on every hop; a store that is absent is a named NO-DATA hop, never a silently shorter chain. The writer never recomputes a verdict and never starts a gate: it records what the real tools printed, at the moment they printed it.

## Install in minutes

What a first run on an unmodified repository tells you, and what it does not. It tells you one thing about your code immediately: the linter scans the tree you point it at and names every place an error is swallowed, with the file, the line and the pattern. Everything else starts empty on purpose. The four gates read receipts that a change has to produce, so on a repository that has never written one they report NO-DATA on all four, which means "no evidence either way" and never "checked and fine". The design checks read a dossier directory that does not exist yet. The graded checks read a telemetry vault and fence registries you have not installed, so their verdicts are about files outside your repository, and the report prints them under a heading that says exactly that. Nothing here infers quality from a repository's shape: a green first run is a report about what was read, and on a fresh install that is the linter and nothing else.

### As a plugin (recommended for teams)

BrotherSBE ships as a Claude Code plugin: one versioned thing to install, upgrade and roll back, with the hooks wired in the package instead of hand-copied into your settings file. Clone it anywhere, then check the package before you trust it:

```bash
git clone https://github.com/khalilmaaouni/BrotherSBE
claude plugin validate BrotherSBE
```

```
Validating plugin manifest: /path/to/BrotherSBE/.claude-plugin/plugin.json

✔ Validation passed
```

Once it validates, run the marketplace pair at the top of this page, the persistent install, executed and verified on 2026-08-01. That gives you ten namespaced skills (the guided four: `/brothersbe:start`, `:next`, `:status`, `:help`, and the specialist six: `/brothersbe:kickoff`, `:design`, `:verify`, `:review`, `:learn`, `:adopt`), seven read-only reviewer agents, and the four hooks resolving their own paths. The hooks block that [docs/SETUP.md](SETUP.md) documents for the manual path is then unnecessary: no hook goes into your `settings.json`, because the plugin package wires its own. The vault export in that same page is still worth doing by hand; the plugin does not set `BROTHERSBE_VAULT` for you. Moving from an older clone-style install is one page: [docs/MIGRATION.md](MIGRATION.md). The public repository itself is the marketplace source today, verified working; a signed, directory-listed distribution is still ahead, see [docs/ROLLOUT.md](ROLLOUT.md).

Either way you install it, there is one command line over the nine script paths:

```bash
bin/sbe doctor
```

```
python           PASS     3.9.6 (floor is 3.9)
tools            PASS     all present in /path/to/BrotherSBE/tools
plugin-manifest  PASS     manifest 1.0.0-rc.2, VERSION 1.0.0-rc.2
git              PASS     working directory is inside a git tree
vault            NO-DATA  BROTHERSBE_VAULT is unset, so telemetry, session logs and resume briefs have nowhere durable to go
private-names    NO-DATA  no private-name list, so the publish leak check scans nothing

sbe 1.0.0-rc.2, evidence schema 1.0. 6 check(s): 4 PASS, 0 FAIL, 2 NO-DATA.
```

That block is a real run on a fresh install (no vault exported, no private-name list configured), with only the absolute installation path replaced by `/path/to/BrotherSBE`. The two NO-DATA lines are the point: an unanswered environment question is reported as unanswered, never folded into the passes.

`sbe` is a facade, not a rewrite: every built subcommand delegates to the tool in `tools/` that already carries the behavior and the tests, and the old invocations shown throughout this README still work and are not deprecated. Commands, exit codes, and the six subcommands that are present and deliberately refuse: [docs/CLI.md](CLI.md).

### As a cloned skill (the manual path, still supported)

The full manual procedure, prerequisites, the clone command, the vault export, and the hooks block you wire by hand into `~/.claude/settings.json`, lives in one place: [docs/SETUP.md](SETUP.md). Reach for it when you want to inspect or hand-place every file yourself, or when [docs/MIGRATION.md](MIGRATION.md) sends you here while moving between paths.

### Universal install (any host, one command)

`install.sh` is the cross-host path: one POSIX `sh` command that checks the machine, registers the plugin (the marketplace pair when a tag is published, a clone of this repository otherwise), applies your team's committed profile (`.sbe/team-profile.json`) through `sbe init`, and closes with `bin/sbe doctor`'s own verdict. It is also the install path this project's own CI exercises (`scripts/test-install-artifact.sh`), not only documents.

```bash
sh install.sh                 # installs into the directory you run it from
sh install.sh --target <dir>  # installs into <dir> instead
sh install.sh --dry-run       # names every step it would take, writes nothing
```

Run it from the repository whose team you want on the same footing. It refuses to target BrotherSBE's own distribution directory, so it cannot initialize the tool onto itself.

### Enterprise or team-managed install

An organization rolling this out across many repositories at once, with a tag pin and a checksum-verified manifest instead of a moving branch, follows [docs/ROLLOUT.md](ROLLOUT.md): the staged rollout, the upgrade and rollback scripts, and what is and is not proven yet.

### Wire the checks into CI (every install path)

This is what turns the gates from advisory into blocking, whichever path above you installed with. Copy [`.github/workflows/brothersbe-gates.yml`](../.github/workflows/brothersbe-gates.yml) into the repo you want guarded, or add its steps to an existing job:

```yaml
      - name: Line endings stay bytes (autocrlf off before any file exists)
        # The tracked manifest hashes exact bytes and .gitattributes rides
        # INSIDE the checkout, so files extracted before it lands in the
        # working tree could still be converted by the runner's default
        # autocrlf: rounds 1 to 3 of this leg watched the same four
        # early-alphabet files (.brothersbe/config.json, the two
        # .claude-plugin manifests, .gitattributes itself) hash stale on
        # first read and identical on re-read (run 31042529271). Turning
        # conversion off before checkout removes the ordering race the
        # in-tree attributes file cannot close by itself.
        run: git config --global core.autocrlf false
      # Windows-only guard: a no-op where python3 already resolves (the current
      # windows-latest image), a bridge where an image ships only python.exe.
      - name: Ensure python3 resolves (guard for images shipping only python.exe)
        run: |
          if ! command -v python3 >/dev/null 2>&1; then
            py="$(command -v python)"
            cp "$py" "$(dirname "$py")/python3.exe"
          fi
      - name: Hard gates (numbers, migration, approval, ran) block on failure
        run: python3 tools/sbe_gate.py --strict design
      # A waiver is not a pass. `.sbe-exempt` lets a template library or a finished
      # project stop blocking every unrelated merge, and the exit code cannot tell
      # you one was used, so this step surfaces every WAIVED line as an annotation
      # and in the job summary. A human sees it, or it is not a control. Add
      # --strict-waivers here if you want an exemption to block outright.
      - name: Design checks (dossier completeness) block on failure
        run: |
          set -o pipefail
          python3 tools/sbe_design.py --strict . | tee design-checks.out
      # The pattern is `^  >> `, the prefix sbe_design.py puts on a waived line, and
      # not the word WAIVED. The banner the tool prints on every run ends "WAIVED
      # is not a pass either", so `grep -q 'WAIVED'` was unconditionally true: every
      # clean run told the reviewer that a .sbe-exempt had waived one or more design
      # checks and that nothing opened a file for them, over a run in which every
      # check opened its files. An assurance signal that always fires carries no
      # information, and this one asserted something false, which trains a reviewer
      # to ignore the single control that makes WAIVED visible in CI at all.
      - name: Surface design waivers (a waiver is not a pass)
        if: always()
        run: |
          if grep -qE '^  >> ' design-checks.out; then
            grep -E '^  >> ' design-checks.out | while read -r line; do
              echo "::warning title=BrotherSBE design waiver::$line"
            done
            {
              echo '### BrotherSBE design waivers'
              echo 'A `.sbe-exempt` waived one or more design checks. Nothing opened a file for them.'
              echo '```'
              grep -E '^  >> |^WAIVERS: ' design-checks.out
              echo '```'
            } >> "$GITHUB_STEP_SUMMARY"
          fi
      - name: Silent-failure lints and code-graded checks block on failure
        run: python3 tools/sbe_score.py --strict --strict-soft .
      # The gates above are only worth what their tests are worth. These two ran
      # on nobody's merge path until now, which made them documentation rather
      # than a gate: a fixture no merge runs cannot stop anything.
      - name: Regression evals (every gate against the defect it exists to catch)
        run: python3 evals/run_evals.py
      - name: Replay detail on failure (which excerpt blocks differ, and how)
        if: failure()
        run: |
          python3 --version
          python3 evals/replay_book.py || true
          python3 evals/replay_guide05.py || true
      - name: Honesty meta-test (no check may PASS over evidence it never examined)
        run: |
          python3 evals/test_no_data_class.py
          python3 evals/test_no_data_class.py --quiet --seed 1 --seed 2 --seed 3
      - name: Tool tests (redaction, permissions, identity, autosave, plugin surface, CLI)
        run: python3 tools/test_sbe.py
      - name: Fence hook tests (the write boundary)
        run: python3 tools/test_sbe_fence_hook.py
      - name: Impact fixtures (a declared tier cannot contradict the diff silently)
        run: python3 tools/test_sbe_impact.py
      - name: Install-from-artifact test (a fresh `git archive` install verifies clean)
        run: sh scripts/test-install-artifact.sh
      - name: Release invariant (distributable bytes cannot move without VERSION moving)
        run: python3 tools/sbe_release_invariant.py --strict
      - name: Upgrade and rollback test (NO-DATA until a previous tag exists, never a false pass)
        run: sh scripts/test-upgrade-rollback.sh
      - name: Cold-start grader fixtures (the receipt's three states stay distinct)
        run: python3 tools/test_coldstart_grader.py
      - name: Cold-start parser fixtures (transcript to the five north-star metrics)
        run: python3 tools/test_coldstart_parse.py
      - name: Cold-start runner fixtures (sandbox, dry run, and the spend-ceiling refusal)
        run: python3 tools/test_coldstart_runner.py
      - name: Adopt and init fixtures (sbe adopt, sbe init)
        run: python3 tools/test_sbe_adopt.py
      - name: Authority hook fixtures (undeclared edits to authority files refused)
        run: python3 tools/test_sbe_authority_hook.py
      - name: Benchmark fixtures (the comparative harness, and its ground-truth leak guard)
        run: python3 benchmarks/test_sbe_bench.py
      - name: Book estate fixtures (the worked example the book's chapters paste)
        run: python3 tools/test_sbe_book.py
      - name: Bypass fixtures (the ways a person or an agent gets past these controls)
        run: python3 tools/test_sbe_bypass.py
      - name: Consumer minting fixtures (the job produces the evidence it demands)
        run: python3 tools/test_sbe_consumer_mint.py
      - name: Converge fixtures (sbe converge)
        run: python3 tools/test_sbe_converge.py
      - name: Decision contract fixtures (every key decision surface names its falsification tier)
        run: python3 tools/test_sbe_decision_contract.py
      - name: Decision package fixtures (sbe explain, sbe lineage)
        run: python3 tools/test_sbe_decisions.py
      - name: Evidence fixtures (a receipt cannot be typed by the same process it verifies)
        run: python3 tools/test_sbe_evidence.py
      - name: Golden scenario (the whole chain, start through acknowledge, real engine)
        run: python3 tools/test_sbe_golden_scenario.py
      - name: Handover fixtures (sbe handover, identity forgeries refused)
        run: python3 tools/test_sbe_handover.py
      - name: Import hygiene fixtures (the six sys.path mounts collapsed into one)
        run: python3 tools/test_sbe_import_hygiene.py
      - name: Instruction surface fixtures (changed authority files outside declared scope)
        run: python3 tools/test_sbe_instruction_surface.py
      - name: Interoperability fixtures (namespacing, no foreign writes, coexistence)
        run: python3 tools/test_sbe_interop.py
      - name: Install script fixtures (dry-run, missing prerequisites)
        run: python3 tools/test_sbe_install.py
      - name: Public install path (the two commands the front page promises)
        run: python3 tools/test_sbe_public_install.py
      - name: Map fixtures (sbe map, a deterministic status map, never a filled template)
        run: python3 tools/test_sbe_map.py
      - name: Plan fixtures (sbe plan)
        run: python3 tools/test_sbe_plan.py
      # This is the canned/offline suite: every GitHub API call is routed
      # through a fake fetch, so it needs no network and no token, and it
      # runs on every PR. tools/test_sbe_prverify_live.py is a separate,
      # deliberately unwired script: it needs BOTH SBE_LIVE_GH_REPO and
      # SBE_LIVE_GH_PR plus a token discoverable the way `sbe pr verify`
      # itself discovers one, none of which this workflow provides, and
      # without them it already prints one NO-DATA line and exits 0 (its
      # own docstring). Wiring it here would either skip silently on every
      # normal run or require CI secrets this repository does not carry, so
      # it stays a manual, opt-in script instead.
      - name: PR verify fixtures (sbe pr verify, canned GitHub API, offline)
        run: python3 tools/test_sbe_prverify.py
      - name: Release invariant fixtures (distributable bytes cannot move without VERSION)
        run: python3 tools/test_sbe_release_invariant.py
      - name: Review record fixtures (normalized findings, commit binding, staleness)
        run: python3 tools/test_sbe_review_record.py
      - name: Review route fixtures (deterministic reviewer selection)
        run: python3 tools/test_sbe_review_route.py
      - name: Review skill fixtures (the skill consumes the route)
        run: python3 tools/test_sbe_review_skill_fixtures.py
      - name: Sandbox fixtures (doc-truth for docs/guides/00-sandbox.md)
        run: python3 tools/test_sbe_sandbox.py
      - name: Status fixtures (sbe status)
        run: python3 tools/test_sbe_status.py
      - name: Team status fixtures (sbe status --team)
        run: python3 tools/test_sbe_status_team.py
      - name: Task fixtures (sbe task)
        run: python3 tools/test_sbe_tasks.py
      - name: Team workflow fixtures (eight execution laws over one fixture)
        run: python3 tools/test_sbe_team_workflow.py
      - name: Version bump fixtures (one command moves every declaration site)
        run: python3 tools/test_sbe_version_bump.py
      - name: Work fixtures (sbe work)
        run: python3 tools/test_sbe_work.py
      - name: Work brief fixtures (sbe work brief)
        run: python3 tools/test_sbe_work_brief.py
      # The kill criterion this wave was cut against, verbatim: an install
      # that needs a manual global settings edit. This proves a plain
      # `git archive HEAD` extracts on its own into an empty directory and
      # verifies clean there (scripts/verify-install.sh, bin/sbe doctor),
      # nothing written outside that one directory.
```

The last three matter as much as the first three. The gates are worth what their tests are worth, and a fixture no merge runs is documentation rather than a gate.

The approval gate reads commit trailers and signatures, so the checkout step needs `fetch-depth: 0`. Everything here is standard-library Python, no dependencies to install.

Invoke the skill with `/brothersbe` at the start of any backend, infrastructure, or data engineering task.

## Status: read this before trusting anything above

The same disclosure the checks demand of evidence, applied to the project itself.

- **Measured:** the eval counts, the meta-test scenario count, the lint numbers and the defect-reinjection record ([INVARIANTS.md](../INVARIANTS.md)) are recomputed by the suites that print them; a doc quoting a stale one fails an eval.
- **Run on one estate only:** every threshold in `tables/`, every baseline in [RUBRIC.md](../RUBRIC.md), and the hooks in daily use. They are defaults where you are, not measurements of your estate.
- **Never executed anywhere else:** this project's CI workflow has run in its own repository and in nobody else's; no external adoption is claimed. There is NO Windows or macOS leg: both were removed on 2026-08-17 under the founder law forbidding those runners on any trigger, because they bill at 2x and 10x Linux. Measured 2026-08-29, every job in every workflow here runs on ubuntu-latest. Windows coverage is a written protocol a person follows (docs/WINDOWS-CHECK.md), which is weaker than a runner. The release tag and push steps in [docs/RELEASE.md](RELEASE.md) have been executed for `v1.0.0-rc.1` (tagged and pushed to origin); `v1.0.0-rc.2` is tagged locally but, as of this writing, not yet pushed.

The full list, one heading per limit, is [docs/KNOWN-LIMITS.md](KNOWN-LIMITS.md).

## Requirements

- Python 3, standard library only. There is no `pip install`, no lockfile, and no dependency to audit beyond the tree itself.
- git (the autosave, the approval gate, and the manifest all read it).
- Claude Code with hooks, for the session wiring above. The checkers run fine without it: every tool is a plain script you can run by hand.
- A POSIX shell for the two `sh` tools; CI runs Linux, macOS, and Windows, with those two scripts excluded on Windows by name.

## Uninstall

Removal is three deletions, and this section names what each leaves behind so nothing lingers silently:

1. Remove the hook entries from `~/.claude/settings.json` (and any project `.claude/settings.json` you added them to).
2. Delete the clone: `rm -rf ~/.claude/skills/brothersbe`.
3. Decide about your data, which uninstalling does NOT delete: the vault at `$BROTHERSBE_VAULT` (your session logs and telemetry, yours to keep or delete), the `export BROTHERSBE_VAULT` line in your shell profile, and the autosave snapshots under `refs/brothersbe/` in any repository where the hook fired (list them with `git for-each-ref refs/brothersbe/`, delete with `git update-ref -d <ref>`).

## A 60-second first run

Run the eval bed. Each case is a real failure class turned into a fixture with a planted defect, plus an assertion that the matching check CATCHES it. This is the mechanism behind the "proven" claim: the checks are tested against the defect classes the operating record produced, not asserted.

```bash
python3 evals/run_evals.py
```

Eight of its lines and the closing count, each verbatim (the suite grows, so these are picked
lines rather than the tail): the dossier lines are the honest path, which a gate is only worth
having if it clears, and the two consistency lines are the docs checking their own numbers:

```
  cache-counters-that-are-not-counts-are-caught want=FAIL     got=FAIL     ok
  a-complete-t0-dossier-blocks-nothing   want=clear    got=clear    ok
  a-complete-t1-dossier-blocks-nothing   want=clear    got=clear    ok
  a-complete-t2-dossier-blocks-nothing   want=clear    got=clear    ok
  a-complete-t3-dossier-blocks-nothing   want=clear    got=clear    ok
  a-change-with-no-numbers-and-no-migration-blocks-nothing want=clear    got=clear    ok
  no-shipped-doc-prints-an-eval-count-the-suite-does-not-produce want=consistent got=consistent ok
  no-shipped-doc-prints-a-meta-test-count-the-meta-test-does-not-produce want=consistent got=consistent ok

536 evals: 536 passed, 0 regressions.
```

The bed exits nonzero if any check stops catching its defect, so it doubles as a release gate for the skill itself.

Then run the honesty meta-test, which is the one that keeps the rest honest. It carries no list of
checks and no list of registries: it DISCOVERS every registry of checks in `tools/`, and refuses to
run if it finds one it was not taught to invoke. For each check it takes the worked example that
check declares and hollows it out, one leaf, one subtree and one whole receipt at a time, in empty
strings, whitespace and nulls, and requires that none of it produces a PASS. A check added later is
covered without anyone remembering, and so is a field added to an existing one.

```bash
python3 evals/test_no_data_class.py
```

Its last line, verbatim:

```
32 checks discovered from 6 registries in 60 module(s), 3780 scenarios run, 2 waived by declared exemption, 0 failure(s).
```

To watch one check on a real change:

```bash
python3 tools/sbe_design.py .           # the five design checks, advisory
python3 tools/sbe_gate.py numbers .     # one hard gate
python3 tools/sbe_gate.py --strict design    # enforcing: exits nonzero on any FAIL
```

## What this is not

- **Not autonomous.** No agent holds apply rights on production state (databases, IaC apply, deploy, partner endpoints). It drafts; a human applies. Credentials are never typed, stored, or logged.
- **Not an oracle.** Confidence is stated at the claim (verified by command, verified by inspection, likely, assumed), every number carries its source, and where the record shows agents do not help it stands down instead of guessing.
- **Not a checkbox.** Cloning the skill gives you the tools. It does not enforce anything until you wire `--strict` into CI, and that CI wiring is real setup, not a toggle. Advisory mode tells a session; only CI stops a merge.
- **Not a set of numbers you inherit.** Every threshold in `tables/` and every baseline in [RUBRIC.md](../RUBRIC.md) was measured on one estate. Re-measure on your own before treating one as yours. NO-DATA is a legal score and never a pass.

## Learn more

- [docs/guides/05-a-worked-engagement.md](guides/05-a-worked-engagement.md): one system designed end to end, real commands, real output. The best place to start.
- [docs/DESIGN.md](DESIGN.md): the why and what, in the real order.
- [docs/HOW-IT-WORKS.md](HOW-IT-WORKS.md): the mechanical half, tool by tool.
- [docs/for-engineers/](for-engineers): onboarding for backend, data, infrastructure and ETL engineers who have never seen this tool. Start at [00-READ-ME-FIRST.md](for-engineers/00-READ-ME-FIRST.md); four complete worked dossiers are in [docs/for-engineers/examples/](for-engineers/examples).
- [docs/SETUP.md](SETUP.md) to install, and the rest of [docs/guides/](guides) for the gates, the doctrines, and teams.
- [SKILL.md](../SKILL.md) plus the [`references/`](../references) files its routing table names are the law itself; [SECURITY.md](../SECURITY.md) is the data and network posture (no network calls, no analytics, no account, no server).

## License

MIT. See [LICENSE](../LICENSE).

Created by Khalil Maaouni.
