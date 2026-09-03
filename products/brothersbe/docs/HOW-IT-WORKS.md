# BrotherSBE: how it works

The mechanical half: the dossier and its completeness rules, the four tools that
compute and check it, the four hard gates, the coordination chassis underneath, and
the loop that changes the law. For the why and the what see
[DESIGN.md](DESIGN.md). To install, see [SETUP.md](SETUP.md). For all of it applied
to one system, read [the worked engagement](guides/05-a-worked-engagement.md).

Every mechanism named here is a real file in this repository. Where a section names
a tool, that tool is under `tools/` and is exercised by `evals/run_evals.py`.

```
SKILL.md              the always-on core: the spine, the law form, L6, L11, L14,
                      and the routing table naming which reference file holds the rest
references/*.md       the other sixteen laws and the six phases, in the same
                      WHEN, INPUTS, RULE, OUTPUT, ENFORCED BY form, loaded on trigger
PRACTICES.md          the advice, which says it is advice
DIGEST.md             the law's shadow, injected at session start
STATE.template.md     the per-project fence registry format
RUBRIC.md             the frozen review metrics
templates/dossier/    the seven design artifacts
tables/architecture.json   the shape decision table
tools/                intake, design checks, decision tables, gates, score, hooks
memory-template/      the memory an install copies out and then owns
evals/                fixtures with planted defects, and the release gate
```

---

## 1. The dossier

A design engagement produces at most seven files in one directory, usually
`design/<project>/`:

| File | Holds |
|---|---|
| `00-intake.json` | the five answers and the computed tier |
| `01-purpose.md` | problem, users, success criteria, non-goals, blast radius |
| `02-process.md` | actors, steps with triggers and exception paths, handoffs with contracts |
| `03-adr.md` | context, criteria, rejected alternatives, decision, consequences, flip condition |
| `04-technology-map.md` | per component: technology, owner, failure mode, recovery path; source systems; recovery objectives |
| `05-data-model.md` | conceptual, logical, physical; systems of record; cardinalities; the three lenses |
| `06-diagrams.md` | Mermaid, one section per required view |
| `07-verification.md` | every claim, the check that proves it, when it runs |

`templates/dossier/` holds all seven with worked example content. Copy the folder,
run the intake, delete what the tier does not require.

Five completeness rules are mechanical. Four are stated as laws L2 to L5 in
[SKILL.md](../SKILL.md) and the fifth backs L2, and they are exactly what
`tools/sbe_design.py` checks:

1. **artifacts.** Every file the tier requires exists, and the tier itself is
   re-derived from the answers stored beside it rather than believed as written. A
   tier that disagrees with its own answers and carries no override reason fails,
   naming both values. A missing tier or no answers is NO-DATA rather than a
   pass. No intake file at all, in a directory carrying dossier artifacts, FAILS
   and names the missing file: without a tier nothing can say which artifacts are
   owed, and reporting an absence there made deleting one file the cheapest way
   through the gate.
2. **adr.** At least two decidably rejected alternatives, plus Criteria, Decision,
   Consequences, and a "What would flip this" section. All five, or it fails.
3. **datamodel.** Every entity names a system of record with a value; every
   relationship carries a cardinality as a standalone token, in any accepted
   notation: word forms (one-to-many), crow's foot (1:N), UML multiplicity (0..*),
   prose (has many, belongs to exactly one), or an erDiagram's symbols. A system of record recorded as TBD or explicitly absent fails
   like one that is missing, and "one-to-many-ish" is not a cardinality. No entities
   at all is a failure, not a pass.
4. **diagrams.** At least one diagram node exists, and every node traces to an
   entity in `05-data-model.md`, a declared runtime component, or a declared
   lifecycle state; a node that traces to none of the three is an orphan and
   fails by name. A diagram artifact with no diagram in it is a defect, not an
   absence.
5. **placeholder.** No artifact is still the shipped template. Each template carries
   an `SBE-TEMPLATE-UNFILLED` marker, and the check fails while any survives, naming
   the artifacts. Without it, the fastest route to a green run was copying seven
   files describing someone else's system and changing nothing.

## 2. `tools/sbe_intake.py`: five questions, one tier

Asks the five questions, writes `00-intake.json`, prints the tier and the artifact
list. `compute_tier` is a decision table with first match winning:

Every answer is converted to the meaning it records before any rule reads it, and a
value outside the accepted vocabulary is refused by name rather than guessed at. Reading
these five for truthiness is how an intake written in the tool's own `(y/n)` vocabulary
computed the wrong tier in both directions: five answers of `n` computed T3, because the
string `n` is truthy, and `"reversible_under_hour": "no"` computed T0, which owes no
artifact at all.

```python
def compute_tier(a):
    v, problems = read_answers(a)
    if problems:
        raise UnreadableIntake(problems)
    if v["touches_sensitive"] or not v["reversible_under_hour"]:
        return "T3"
    if v["changes_contract"] or v[CONSUMERS] == "many":
        return "T2"
    if v["crosses_boundary"] or v[CONSUMERS] == "some":
        return "T1"
    return "T0"
```

`required_artifacts(tier)` returns the file numbers: T0 none, T1 `01`, T2 `01 02 03
05 06 07`, T3 all seven. `sbe_design.py` imports both functions rather than
duplicating the rule, so the tier logic exists once.

The written file carries `override` and `override_reason` fields, both null,
because the tier it writes is the tier its own answers compute and no tier was
moved. Moving one is an edit to that file, and it sets BOTH fields: a stored tier
that differs from the computed one with `override` still null, or with no
reviewable reason, FAILS, and the failure names both fields, the written tier and
the computed one. That is what makes L15 a rule rather than a wish. Only the
reason used to be enforced, so the half-declared override was the state every
file the tool writes starts in.

## 3. `tools/sbe_design.py`: the completeness checks

One function per check, each returning a verdict and its evidence, collected in a
`CHECKS` dict: `artifacts`, `adr`, `datamodel`, `diagrams`, `placeholder`. Same
contract as the gates: advisory by default, `--strict` exits nonzero so CI can block.

```bash
python3 tools/sbe_design.py .              # all five, advisory
python3 tools/sbe_design.py datamodel .    # one check
python3 tools/sbe_design.py --strict .     # enforcing
```

Where it looks matters as much as what it checks. A directory holding a dossier is
checked directly. Anything else is a search root, walked for every directory holding
`00-intake.json` OR any of `01` through `07`, because the documented layout puts
dossiers in `design/<project>/` while CI runs from the repository root. Anchoring on
the intake file alone was a bypass: deleting it made a directory of seven filled-in
artifacts invisible, and `--strict` exited 0. A dossier found without its intake now
FAILs, naming it, because without a tier nothing can say which artifacts are owed.

Set `SBE_DOSSIER_ROOT` when a repository is supposed to carry a dossier: a declared
root holding none is then a FAIL rather than a report. A directory that holds
dossier-shaped files without being live design work carries a `.sbe-exempt` file
whose contents say why, and that reason is printed on every run as a WAIVER naming
the directory and every check it covers, so an exemption nobody can see is not
possible. The reason meets the same reviewability threshold as a tier override: an
empty `.sbe-exempt` waives nothing, the dossier is checked anyway, and the broken
exemption is itself a FAIL.

On this repository, which carries the shipped templates and no real dossier, the run
looks like this, and the exit code is 0. Every check prints a line even with nothing
to open: a check that prints nothing is indistinguishable from a check that was
deleted.

```
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  >> dossier WAIVED   templates/dossier: .sbe-exempt waives adr, artifacts, datamodel, diagrams, placeholder here (of 5 design checks), stated reason: These are the shipped dossier TEMPLATES, not a dossier. They carry the SBE-TEMPLATE-UNFILLED marker on purpose and have no 00-intake.json, because nobody filled them in for a real change. Copy them in. Nothing opened a file for any check in that directory, so this is a waiver and not a verdict about the work
  dossier    NO-DATA  every dossier found under . (1) is waived by a .sbe-exempt, so no check opened a file. The waiver line(s) above name each one and the reason given
  artifacts  NO-DATA  every dossier under . is waived, so this check opened no file
  adr        NO-DATA  every dossier under . is waived, so this check opened no file
  datamodel  NO-DATA  every dossier under . is waived, so this check opened no file
  diagrams   NO-DATA  every dossier under . is waived, so this check opened no file
  placeholder NO-DATA  every dossier under . is waived, so this check opened no file
WAIVERS: 5 check(s) were waived by a .sbe-exempt and examined nothing. A waiver is not a pass; run `--strict --strict-waivers` to make one block a merge.
```

Two implementation details are worth knowing before you write a dossier, because
they decide what the checks can see.

**The entity list is parsed from bullets or table rows, inside the entity sections.**
`_entities` reads bullet lines, and rows of a markdown table whose first column is the
entity name, under any heading whose name contains "entit" and above the
`Relationships` heading, taking the text before the first colon (or the first cell) as
the name and the rest as its meta, which must contain the words "system of record".
Relationships were readable as a table and entities were not, so a data model written
as tables throughout FAILed over a document that named every entity and every source. Scoping it to
those headings is what lets a data model carry an honest `## Notes` list without
each note becoming an entity with no source. A name may contain a hyphen or a dot,
so `payment-token` is read rather than dropped from the set the verdict asserts
over. Bullet lines, numbered items and table rows below the Relationships heading are all read
as relationships and must carry a cardinality.

**The diagram check traces against entities AND declared components.** Diagram
source is read from fenced code blocks only, so prose containing an arrow is not a
diagram. A node that is neither an entity in `05-data-model.md` nor a declared
runtime component is an orphan and fails by name. A runtime component is declared
in the first column of a table in `04-technology-map.md`, or as a bullet under a
Components heading in `06-diagrams.md` itself. It used to have to be declared as an
ENTITY in the data model, which taught authors to put queues and services in the
conceptual model to satisfy a diagram check: the model was corrupted to please the
tool. When there is nothing to trace against at all, no entities and no declared
components, the check returns NO-DATA rather than PASS, because an empty known set
would make every invented node look traceable, which is the exact defect the check
exists to catch.

## 4. `tools/sbe_decide.py` and `tables/`

`recommend(table, context)` scores each criterion supplied in the context and
returns one shape:

| Key | Meaning |
|---|---|
| `verdict` | `OK`, or `NO-DATA` when no criterion contributed |
| `recommendation` | top-ranked option, `None` when NO-DATA |
| `alternatives` | up to two next-ranked options, empty when NO-DATA |
| `deciding_criteria` | one line per criterion that contributed |
| `evidence` | how many criteria contributed |
| `unrecognized` | one line per supplied value matching none of a criterion's keys |
| `flip_condition` | the flip line for the RECOMMENDATION (the table's `flips` map), falling back to the table-wide line |
| `scores` | the raw tally per option |

The suppression is the point: when nothing contributed, the recommendation is
withheld rather than shown, because a ranking over an all-zero tally is a guess
with a table around it. The `unrecognized` list is the other half: a typo in a
value is reported by name so it is distinguishable from an omission.

`tables/architecture.json` ships one table, `shape`, with four criteria
(`deploying_teams`, `consistency`, `ops_maturity`, `failure_isolation`), four
options, per-criterion score maps, and one `flip` line. Criteria are `number` kind
with a low and high bound per option, or `choice` kind with a list of favoured
options per key. Editing a threshold is editing this file, in a reviewed pull
request; the tool holds no numbers of its own.

```bash
python3 tools/sbe_decide.py tables/architecture.json shape   # asks for each criterion on stdin
```

It prompts for one value per criterion, so a non-interactive caller pipes them in
(`printf 'many\nstrong\nlow\nlow\n' | python3 tools/sbe_decide.py ...`); left to
read an empty stdin in CI it prints nothing and waits.

## 5. `tools/sbe_gate.py`: the four hard gates

One subcommand per silent-failure class. Each walks the directory it was named
(the default is the current directory) for its receipt file, then checks the
receipt is internally consistent rather than merely present.

| Gate | Receipt | Passes only when |
|---|---|---|
| `numbers` | `numbers-manifest.json` | each figure has a `snapshot_id`, a `second_derivation` textually different from `query`, `rerun.ran` true, and `primary` equal to `secondary` |
| `migration` | `migration-receipt.json` | both legs have `ran_against_restore`, the reverse has a `rehearsal_run_id`, and `row_counts.before` equals `row_counts.after_reverse` |
| `approval` | `APPROVAL` file plus a HEAD trailer | a signed commit carries `Approved-by:` and this host verified the signature against a trusted key (git `%G?` = `G`, and `G` alone). A valid-but-untrusted signature (`U`), an unverifiable signature (`E`) and a `Reviewed-in: <id>` all report NO-DATA, because this host can vouch for none of them. A typed name with neither fails |
| `ran` | `ran-receipt.json` | every check has `exit_code` 0 and a nonzero `duration_ms` |

```bash
python3 tools/sbe_gate.py .             # all four, advisory
python3 tools/sbe_gate.py numbers .     # one class
python3 tools/sbe_gate.py --strict design    # enforcing
```

Four properties are deliberate. A missing receipt is NO-DATA, so a change with
nothing to prove is not taxed. A receipt that exists and records zero items is also
NO-DATA and says which file and why, because an empty manifest is exactly what a run
that never happened produces, and reporting PASS over zero items would print evidence
asserting work nobody did. A receipt that exists and is either unparseable or
internally inconsistent FAILs with the reason named, because the operating record
says pasted receipts get invented and a file that cannot be read is a broken claim,
not an absent one. A crash under `--strict` exits nonzero: a broken gate blocks
rather than waves work through.

## 6. `tools/sbe_score.py`: the code-graded checks

The weekly review's mechanical half. Twelve checks, each printing PASS, FAIL, or
NO-DATA with its evidence inline, so the model judges only the residue:
`ledger-coverage`, `schema-2-uniform`, `cache-economy`, `vault-log-per-active-day`,
`fence-hygiene`, `correction-latency`, `budget-vs-tier`, `prediction-seals`,
`felt-outcome-ratings`, `review-cadence`, `silent-failure-lints`, and
`citation-inventory`, which fails a strict run when an external URL cited in
README.md, SKILL.md or docs/ has no entry in docs/CITATIONS.md answering its
claim, population, date and limit. It verifies structure and coverage offline,
never live page content, and its verdict sentence says so.

The last one is the linter for the patterns that swallow an error so a wrong result
passes for a right one. The patterns are bare `except`, except-then-`pass`, a
discarded subprocess result without `check=True`, a conflict-skipping upsert with no
logged skip count, and force-try. It is opt-in on a path, and a run that opened no
file reports NO-DATA naming why rather than the word "clean", which would assert the
opposite of what happened; a positional argument that is not a directory is a FAIL,
so a mistyped path cannot read as a clean scan. It scans tracked `.py .sql .swift .rb .js .ts .go` files under
`SBE_LINT_ROOT` or a directory argument. A reviewed exemption carries a visible
`# sbe: allow-silent <reason>` marker on the line, so the swallow is auditable in
the diff.

```bash
python3 tools/sbe_score.py "$(pwd)"
python3 tools/sbe_score.py --strict .   # gate severity, by ratified decision
```

## 7. CI: where advisory becomes blocking

`.github/workflows/brothersbe-gates.yml` runs the gates battery on every pull request:

```yaml
      # Windows-only guard: a no-op where python3 already resolves (the current
      # windows-latest image), a bridge where an image ships only python.exe.
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
      - name: Field book fixtures (every generated table checked against an independent read)
        run: python3 tools/test_sbe_fieldbook.py
      # The drift gate itself, not its fixtures: a bound source that moved without
      # a regenerate is a FAIL here and stops the merge. A stale prose stamp is
      # NO-DATA and does not, because no tool computes whether prose went wrong.
      - name: Field book drift (a derived table that no longer matches its source)
        run: python3 bin/sbe book --check --strict
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
      - name: Decision record fixtures (sbe record, bind_human_decision round trip)
        run: python3 tools/test_sbe_decision_record.py
      - name: Behaviour revision log fixtures (a reworded row with no logged entry fails naming it)
        run: python3 tools/test_sbe_behaviour_revision_log.py
      - name: Evidence fixtures (a receipt cannot be typed by the same process it verifies)
        run: python3 tools/test_sbe_evidence.py
      - name: First-contact path fixtures (no vendor path, no jargon, ahead of a stranger's first value)
        run: python3 tools/test_sbe_first_contact_paths.py
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
      - name: Binding tier fixtures (absent binding is NO-DATA from tier two up)
        run: python3 tools/test_sbe_binding_tier.py
      - name: Clarify fixtures (a skipped discussion refuses, never a silence)
        run: python3 tools/test_sbe_clarify.py
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
      - name: System doc fixtures (SYSTEM.md, P12)
        run: python3 tools/test_sbe_system_doc.py
      - name: System doc repin check (SYSTEM.md still describes the code)
        run: python3 tools/sbe_system_doc.py --check
      # The kill criterion this wave was cut against, verbatim: an install
      # that needs a manual global settings edit. This proves a plain
      # `git archive HEAD` extracts on its own into an empty directory and
      # verifies clean there (scripts/verify-install.sh, bin/sbe doctor),
      # nothing written outside that one directory.
      - name: sandbox guide repin check
        run: python3 tools/regen_sandbox_guide.py --check
      - name: bash guard
        run: python3 tools/test_sbe_bash_guard.py
      - name: bbprverify
        run: python3 tools/test_sbe_bbprverify.py
      - name: bbstatus
        run: python3 tools/test_sbe_bbstatus.py
      - name: check registry
        run: python3 tools/test_sbe_check_registry.py
      - name: ci handshake
        run: python3 tools/test_sbe_ci_handshake.py
      - name: coldstart vault
        run: python3 tools/test_sbe_coldstart_vault.py
      - name: contracts
        run: python3 tools/test_sbe_contracts.py
      - name: cwd
        run: python3 tools/test_sbe_cwd.py
      - name: data contracts
        run: python3 tools/test_sbe_data_contracts.py
      - name: design fingerprint
        run: python3 tools/test_sbe_design_fingerprint.py
      - name: discover
        run: python3 tools/test_sbe_discover.py
      - name: dispatch
        run: python3 tools/test_sbe_dispatch.py
      - name: doctor wiring
        run: python3 tools/test_sbe_doctor_wiring.py
      - name: handoff package
        run: python3 tools/test_sbe_handoff_package.py
      - name: hooks
        run: python3 tools/test_sbe_hooks.py
      - name: intake
        run: python3 tools/test_sbe_intake.py
      - name: journey3 followups
        run: python3 tools/test_sbe_journey3_followups.py
      - name: liveness rule
        run: python3 tools/test_sbe_liveness_rule.py
      - name: onepager
        run: python3 tools/test_sbe_onepager.py
      - name: passport
        run: python3 tools/test_sbe_passport.py
      - name: policy
        run: python3 tools/test_sbe_policy.py
      - name: port
        run: python3 tools/test_sbe_port.py
      - name: profile
        run: python3 tools/test_sbe_profile.py
      - name: program
        run: python3 tools/test_sbe_program.py
      - name: proof gate
        run: python3 tools/test_sbe_proof_gate.py
      - name: proofplan
        run: python3 tools/test_sbe_proofplan.py
      - name: protections
        run: python3 tools/test_sbe_protections.py
      - name: qc scaffold
        run: python3 tools/test_sbe_qc_scaffold.py
      - name: readiness
        run: python3 tools/test_sbe_readiness.py
      - name: reading paths
        run: python3 tools/test_sbe_reading_paths.py
      - name: reality
        run: python3 tools/test_sbe_reality.py
      - name: receipt shapes
        run: python3 tools/test_sbe_receipt_shapes.py
      - name: report
        run: python3 tools/test_sbe_report.py
      - name: session reconcile
        run: python3 tools/test_sbe_session_reconcile.py
      - name: skill descriptions
        run: python3 tools/test_sbe_skill_descriptions.py
      - name: testkit
        run: python3 tools/test_sbe_testkit.py
      - name: vault scope
        run: python3 tools/test_sbe_vault_scope.py
      - name: verify converge
        run: python3 tools/test_sbe_verify_converge.py
      - name: windows sim
        run: python3 tools/test_sbe_windows_sim.py
      # Row E33 (2026-09-03): these two suites existed on disk and ran in
      # neither gate. TestEverySuiteIsWiredIntoAGate in tools/test_sbe.py now
      # requires both listed here and in release-control/baseline/run-battery.sh.
      - name: Approval concentration fixtures (three changes approved by one person print who, P13)
        run: python3 tools/test_sbe_approval_concentration.py
      - name: Tier outcome fixtures (a tier's later defect links back to the closure that shipped it, H5)
        run: python3 tools/test_sbe_tier_outcome.py
```

The checkout step needs `fetch-depth: 0` because the approval gate reads commit
trailers and signatures. All three tools are standard-library Python with no
dependencies and no network calls, and so are the three suites under them.

The last three steps are not decoration. The gates are worth exactly what their
tests are worth, and those suites ran on nobody's merge path for a while, which
made them documentation rather than a gate: a fixture no merge runs cannot stop
anything. Copy all seven, or copy the first three knowing you have kept the gates and
dropped the thing that proves they still work. The seventh surfaces any design
waiver as an annotation, because a waiver is not a pass and the exit code cannot
tell you one was used.

The first step, `tools/sbe_gate.py --strict design`, now honors `.sbe-exempt` the
same way the second step already did: a directory under `design` holding a gate
artifact (a `numbers-manifest.json`, `migration-receipt.json`, `APPROVAL` or
`ran-receipt.json`) that is not live work names the gate it waives and why, and
the run prints WAIVED with that reason instead of PASS, FAIL or NO-DATA, never
in silence. `--strict` alone still exits 0 on a waiver, the same as the design
step; the new `--strict-waivers` flag, matching `sbe_design.py`'s own flag of
the same name, turns a WAIVED artifact into a failure for a team that wants an
exemption to expire on contact rather than sit unreviewed. This workflow does
not pass `--strict-waivers` to either step, by choice, not by omission: add it
to a `run:` line above if you want a waiver to block outright.

`--strict` is not overridable by a session instruction. It changes by a human
editing this file in a reviewed change, which is the entire mechanism behind "a
session instruction never waives a hard gate".

## 8. The chassis underneath

The coordination layer is inherited from BrotherModeUp
(github.com/khalilmaaouni/BrotherModeUp), where it has an operating record.
[PARITY.md](../PARITY.md) tracks what is shared verbatim and what was adapted.

**The safety floor.** Before anything is written: the ground is mapped (git status,
current branch, live writers, an environment preflight), the fence is registered,
and STATE.md carries the plan and the intent. The floor is exempt from the learning
loop by construction, because a loop allowed to grade its own safety checks will
eventually learn to skip them.

**Single-writer fences.** Exactly one writer per fenced scope, and the fence line
lands in STATE.md before the agent launches. Each fence carries objective, output
format, tool guidance, boundaries, termination, plus file scope, ids, a lease TTL,
an effort tier, and a runnable done-check. Overlapping scopes queue rather than
share. A fence closes only with an inline evidence block: the command and its last
lines. Two parts of this are checked by `sbe_score.py` over the registries named in
`BROTHERSBE_REGISTRIES`: `fence-hygiene` flags a live fence line in a registry
untouched for more than two days, and `budget-vs-tier` flags a live fence line with
no tier tag. Both see only a line containing the word "agent": a fence naming its
writer some other way ("writer W1 on src/foo.py") is invisible to both and is
neither passed nor failed, it is simply not seen. Unset registries report NO-DATA
rather than guessing. The rest of the
fence discipline is human review, and `references/laws-parallel-writers.md` L13 says so
in its own text.

**State on disk.** Event-time logging rather than batch-end logging; write-ahead
intent before a risky action; `tools/sbe_autosave.py` snapshotting the whole
working tree, untracked files included, to a private git ref at PreCompact without
touching the branch, index, or working tree and without pushing anything; resume by
id rather than respawn; durable placement of any deliverable the moment it exists.

**Context hygiene.** Grep before read, line ranges rather than whole files,
subagent return contracts capped near 1,500 tokens, and a re-read of the law and
STATE.md from disk after any compaction. Laws live on disk, not in recollection.

**The decision ladder and effort tiers.** Six rungs, stopping at the first
sufficient one: answer directly, look it up, ask the operator, do it inline,
dispatch one agent behind one fence, dispatch a fleet. Every brief and fence
declares an effort tier, which sets model routing and the token ceiling. Where the
harness cannot enforce a ceiling, "not measured" is a legal report and an invented
number is not.

## 9. The hooks

Three hooks, wired in `~/.claude/settings.json` or a project settings file. Every
hook exits 0 on every path: a broken diary never blocks an engineer's work.

- `tools/sbe_sessionstart.py` at SessionStart prints `DIGEST.md` into context, plus
  mechanical nags (overdue review, unprocessed corrections) and an offline update
  check that reads git ref files as plain files.
- `tools/sbe_telemetry.py outcomes-append` at SessionEnd appends one line per
  session to the outcomes ledger and scans short operator messages for correction
  candidates. Appends are idempotent: the write is skipped when the last line for
  the session id is byte-identical, and the done-check is firing the hook twice and
  diffing the ledger for zero growth.
- `tools/sbe_autosave.py precompact` and `tools/sbe_telemetry.py precompact-brief`
  at PreCompact snapshot the tree and write a forward-looking resume brief.

The division is the point: hooks write the ledgers, because a logging duty left to
model memory is a defect. Gate tools exist to block and therefore have two modes,
advisory and `--strict`. Observability tools exist to observe and therefore have
one: exit 0, always.

Everything the hooks write stays on the machine. The vault path is
`BROTHERSBE_VAULT`, and the user-text store ships with redaction, 0600 permissions,
a retention limit, and a purge command from its first line.
[SECURITY.md](../SECURITY.md) carries the claims and the greps that check them.

## 10. `evals/run_evals.py`: the release gate

Each eval is a fixture with a planted defect and an assertion that the matching
check catches it: an overstated total, a second derivation identical to the first,
an untested reverse migration, a typed-name approval, a green-on-red check, a
missing dossier artifact, an ADR with no rejected alternatives, a relationship with
no cardinality, an entity with no system of record, an orphan diagram node, an
empty decision context. The suite exits nonzero on any regression, so a gate that
stops catching its defect stops the release.

```bash
python3 evals/run_evals.py
python3 tools/test_sbe.py
```

## 11. How the law changes

`SKILL.md` is never edited casually. An observed weakness becomes one line in the
pending-amendments note the moment it is observed. Amendments land at the weekly
review (`tools/WEEKLY-REVIEW.md`) at most one consolidation per cycle, each naming
the measured signal it should move. The next review compares strictly and reverts
any amendment whose signal did not move. Rejected amendments keep their reasons and
are not re-proposed without new evidence.

`DIGEST.md` is maintained BY HAND against `SKILL.md` and its `references/` files,
and updated in the same change. No generator exists and nothing mechanically compares the two, so a
divergence in CONTENT is caught by review, not by a tool; it is the file
injected at session start, which is why every edit to a law carries an edit
here. Its SIZE is checked: the digest must fit the injection cap the hook
comment in `tools/sbe_sessionstart.py` names, `tools/test_sbe.py` reads that
cap out of the comment and fails when the file outgrows it, and the
qualifications each digest line compresses live in `LAWS-REFERENCE.md`, which
is not injected and enforces nothing.

A practice becomes a law by acquiring a check. It moves from `PRACTICES.md` into the
law form (WHEN, INPUTS, RULE, OUTPUT, ENFORCED BY), written into the `references/` file
whose trigger matches it, or into `SKILL.md` itself if no act announces the trigger, and
added to `SKILL.md`'s routing table either way, with the new
check named on the enforcement line and a fixture in `evals/` proving the check
catches the defect it claims to catch. On a team, that promotion is a reviewed pull
request into the shared repository and `memory-template/LEARNED.md`. No colleague's
tool changes behavior silently.

## 12. The two-layer scope model

Write scope is governed twice, by two controls that fail in opposite directions,
because neither one alone covers the whole surface.

The first layer is `tools/sbe_fence_hook.py`, the advisory PreToolUse control in
front of the keyboard. It compares an edit against the live fences BEFORE the
write, and it FAILS OPEN with a stated reason: a broken hook must not brick
editing, and shell commands cannot be parsed reliably, so a write made through
Bash walks past it entirely. Both of those are stated limits, not bugs.

The second layer is the task registry, `sbe task` over `.sbe/tasks.json`. A
writer declares what it owns at `open`, and `close` computes what ACTUALLY
changed (`git diff` from the declared base plus the uncommitted status) and
refuses to close a task whose tree changed outside the declaration. The shell
is never parsed; the diff is simply read, AFTER the fact. What the hook could
not stop, the postcondition names by path at close.

Neither layer sees an actor who never registers and never triggers the hook.
The registry narrows who can claim what (one open writer per path, reviewers
locked out of the evidence store); it does not widen what can be detected
beyond the diff it reads. Full command surface: `docs/CLI.md`. Limits:
`docs/KNOWN-LIMITS.md`.
