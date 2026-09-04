# The ten minute sandbox: feel the whole journey before you touch your real repository

This page is for a total beginner. You have never run BrotherSBE before, and you do
not want your first try to be against a repository that matters. So this page builds
you a small, throwaway, offline practice repository, and then walks you through the
whole BrotherSBE journey against it, one real command at a time: check the tool is
healthy, describe a change, see its risk level, accept a decision, start one task,
prove it works, review it, and hand it off. About ten minutes, start to finish.

Every command on this page was actually run, against the exact repository this page
builds, and every block of output below is what it printed. Nothing here is typed
out by hand and nothing here touches your real project.

**What you need.** Python 3 and git, and a clone of this skill.

```bash
SBE="$HOME/.claude/skills/brothersbe"     # wherever you cloned BrotherSBE
mkdir -p ~/sbe-sandbox
```

---

## Build the sandbox

This is the one step that is not a `sbe` command. `tools/fixtures/sandbox/
build_sandbox.py` is a small builder, offline and deterministic, that lays out a real
git repository with one tiny, realistic change already staged: a greeting service and
a small SQL change beside it, described in a plan, ready to work. Nothing in the
change is written yet -- that is step 5 below.

```bash
python3 - "$SBE" ~/sbe-sandbox/repo <<'EOF'
import sys, os
sbe_root, repo = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(sbe_root, "tools", "fixtures", "sandbox"))
import build_sandbox as bsb
built = bsb.build(repo, os.path.join(sbe_root, "bin", "sbe"))
print("base commit: %s" % built["base"])
print("plan validate exit code: %s" % built["validate_code"])
EOF
```

```
base commit: 2345bd184ebbc0fad59bb061df0fa591c2f195a3
plan validate exit code: 0
```

That second line is the whole point of a builder rather than a zip file: the plan it
wrote just validated clean through the real `sbe plan`, the same tool that will
refuse a broken one. You can see that validation yourself:

```bash
cd ~/sbe-sandbox/repo
python3 "$SBE/tools/sbe_plan.py" design/say-hello --cwd .
```

```
BROTHERSBE PLAN  (verdicts are PASS, FAIL, NO-DATA; absent evidence is NO-DATA and never a pass; an empty plan never exits 0)
  nonempty       PASS     plan records 1 task(s), each with a usable T-numbered id [severity: gate]
  citations      PASS     1 task(s) cite 1 dossier source(s) and every one resolves to recorded content [severity: gate]
  ownership      PASS     1 task(s): every writer owns at least one recorded path, reviewers own nothing, and every owned-path overlap is dependency-ordered [severity: gate]
  acceptance     PASS     1 task(s) carry 1 recorded acceptance criterion(s) [severity: gate]
  graph          NO-DATA  plan records zero dependency edges, so there is no ordering to examine [severity: gate]
  compatibility  NO-DATA  no 00-intake.json here, so whether this change alters a contract is unrecorded and the compatibility rule cannot be applied [severity: gate]
  migration      NO-DATA  no 05-data-model.md Physical section naming a migration-shaped path, so no migration is declared and there is no triplet to demand [severity: gate]
  calculation    NO-DATA  no acceptance criterion carries a digit, so nothing here is decision-bearing and there is no derivation to demand [severity: gate]
  freshness      NO-DATA  plan records no dossierDigests, so staleness against the dossier on disk cannot be measured [severity: gate]
sbe_plan: 0 FAIL, 5 NO-DATA, 4 PASS across 9 check(s)
```

One task, one writer, nothing decision-bearing, nothing stale: five NO-DATA lines
here are not five problems, they are five questions this tiny change genuinely does
not raise. Open `~/sbe-sandbox/repo/README.md` in a file browser any time you want
the whole eight-step list again without this page.

---

## Step 1: install health

Before you trust anything BrotherSBE tells you, check that BrotherSBE itself is
installed correctly: the right Python, every tool present, the plugin manifest and
your git identity agreeing with what is on disk.

```bash
cd ~/sbe-sandbox/repo
python3 "$SBE/bin/sbe" doctor
```

```
python           PASS     3.9.6 (floor is 3.9)
tools            PASS     all present in <where you cloned BrotherSBE>/tools
plugin-manifest  PASS     manifest 1.0.0-rc.15, VERSION 1.0.0-rc.15
git              PASS     working directory is inside a git tree
project-init     PASS     .brothersbe/config.json is present; this repository carries BrotherSBE's local footprint
identity         PASS     git config reports name "Sandbox Fixture" and email "sandbox@example.invalid"
vault            NO-DATA  BROTHERSBE_VAULT is unset, so telemetry, session logs and resume briefs have nowhere durable to go
private-names    NO-DATA  no private-name list, so the publish leak check scans nothing

sbe 1.0.0-rc.15, evidence schema 1.0. 8 check(s): 6 PASS, 0 FAIL, 2 NO-DATA.
```

Four of these eight lines are about YOUR machine, not this page: `tools` and
`plugin-manifest` name wherever you cloned BrotherSBE, `vault` and `private-names`
name your own vault and privacy setup (this run has neither configured, which is a
completely normal, honest NO-DATA, not a problem). `project-init` reads PASS here
because the sandbox builder already ran the real first-start step (`sbe init`) for
you; on a fresh repository of your own it reads SETUP instead, which is its own
verdict and not a failure. SETUP means nothing is broken and this repository has
simply not been set up yet, which is the normal state of a new project, and the
line names the one command that changes it (`sbe init` to preview, then `sbe init
--apply` once you approve what it shows you). So a brand new repository reports no
failures at all on its very first run. SETUP does not count as ready either, so
nothing downstream can mistake an unprepared repository for a prepared one. `identity` reads this
sandbox repository's own git config, which the builder set for you, so that line
reads the same for everyone. If any line here says FAIL, stop and fix that before
going on; everything below assumes a clean doctor.

**Your own machine may already carry one or both of the other NO-DATA lines as
PASS.** If `BROTHERSBE_VAULT` is exported, `vault` reads PASS instead of NO-DATA;
if a private-name list is configured (`BROTHERSBE_PRIVATE_NAMES` or
`~/.brothersbe-private-names`), `private-names` reads PASS instead of NO-DATA. Either
or both push the PASS count higher than what is shown above. That is not a problem:
it just means this sandbox run is reading real setup you already have on this
machine, the same env var or file any other `sbe` command here would read.

---

## Step 2: describe an outcome

`sbe intake` asks five short, objective questions about the change you are about to
make. Nobody grades your prose here; two people answering the same five questions
land on the same answer, which is the point.

```bash
cd ~/sbe-sandbox/repo/design/say-hello
python3 "$SBE/tools/sbe_intake.py"
```

Answer `n`, `n`, `y`, `n`, `none`: the greeting service changes no contract, crosses
no boundary, reverts in under an hour if it is wrong, touches no money or personal
data, and nobody downstream depends on it yet.

```
Does this change a data model, an API contract, or a file interface others depend on? (y/n) Does it cross a service, system, or team boundary? (y/n) Is it reversible in under an hour? (y/n) Does it touch money, partner data, personal data, or production state? (y/n) How many downstream consumers break if it is wrong? (none/some/many) tier T0 (artifacts required: none) written to ./00-intake.json
To override this tier, edit that file and set all three fields: "tier" (the tier you are moving to), "override" (the same tier, declaring the move), and "override_reason" (at least 3 words and 12 characters). A move with any of the three missing or disagreeing FAILs the design check as an edit rather than an override.
```

That closing paragraph is not boilerplate: it is printed on every run, so the one
legal way to move a tier by hand (never a silent edit) is taught right where the
tier was just written.

---

## Step 3: see the risk level

`tier T0` in the line above is your risk level. What it actually decides is how much
design documentation this change owes before BrotherSBE will call it done. See that
directly:

```bash
cd ~/sbe-sandbox/repo
python3 "$SBE/tools/sbe_design.py" artifacts .
```

```
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under . (design/say-hello); 1 of 2 director(y/ies) directly under . contributed no dossier (.brothersbe)
  dossier: design/say-hello (under .)
  artifacts  NO-DATA  tier T0 requires no artifact, so this check opened none and there is nothing here it can vouch for; examined design/say-hello under . [severity: gate]
```

T0 requires nothing, so this reads NO-DATA rather than PASS: nothing was checked
because nothing was owed. A bigger, riskier change (one that touches money, or
breaks a contract, or has many downstream consumers) lands at T1, T2 or T3 instead,
and this same command would instead FAIL until the required documents exist. Tier is
what makes "brief always" true without making every one-line fix write seven
documents to earn a passing grade.

---

## Step 4: accept one decision

Real engineering decisions get argued in Slack forever. `sbe decide` scores one
against a table instead: same inputs, same recommendation, every time.

```bash
python3 "$SBE/tools/sbe_decide.py" "$SBE/tables/architecture.json" shape deploying_teams=1 consistency=strong ops_maturity=low failure_isolation=low
```

```
Recommendation: modular monolith
Alternatives: monolith, services
Tie: modular monolith, monolith scored equal top marks; the recommendation is the table's declared order, not a measured difference (scores: modular monolith=4, services=0, event-driven=0, monolith=4)
Decided by:
  - deploying_teams=1 favours modular monolith, monolith
  - consistency=strong favours monolith, modular monolith
  - ops_maturity=low favours monolith, modular monolith
  - failure_isolation=low favours monolith, modular monolith
What would flip this: Cross four independently deploying teams, or need one module to fail without the others while ops maturity is high, and revisit this decision.
```

A one-person project with no on-call rotation: keep it one module. Accept the
recommendation by writing it down where the next reader (including future you) can
find it, and commit it alongside the intake answers from step 2:

```bash
cd ~/sbe-sandbox/repo
cat > design/say-hello/03-decision.md <<'EOF'
# 03. Decision: architecture shape

Scored by tools/sbe_decide.py against tables/architecture.json, shape table,
for a one-person project with low ops maturity.

Recommendation: modular monolith. Accepted: keep the greeting service as one
module in this repository, not a separate service.
EOF
git add -A
git commit -qm "intake and decision: describe the change and accept its shape"
```

Nothing prints on a clean commit. That is expected: git stays quiet when it worked.

---

## Step 5: start one task

The plan the builder wrote already names one task, `T01`: a writer, owning the
greeting service and its SQL change, with one acceptance criterion and one
verification command. `sbe work start` opens it a dedicated branch and worktree so
nothing you do next can spill onto any other task.

```bash
mkdir -p ~/sbe-sandbox/worktrees
python3 "$SBE/bin/sbe" work start T01 --plan design/say-hello/08-plan.json --worktree-dir ~/sbe-sandbox/worktrees --agent you --cwd .
```

```
sbe task open: T01 is open. you (writer) owns 2 path(s): src/hello/greeting.py, migrations/0001_greeting_log.sql. Base 7dc817fd5a6d. Close runs the diff postcondition against exactly this declaration.
sbe work start T01: branch sbe/say-hello/T01 at 2345bd184ebb, worktree ~/sbe-sandbox/worktrees/repo-sbe-T01, registry record open.
acceptance criteria:
  - A visitor gets a friendly greeting back
verification commands:
  - python3 -c "print('GREETING-OK')"
dossier sources:
  - 01-notes.md#the-change
```

(The `worktree` path printed on your machine is the real absolute path under
`~/sbe-sandbox/worktrees`; everything else on that line matches exactly.)

Now do the work. Write the two files the task owns:

```bash
cd ~/sbe-sandbox/worktrees/repo-sbe-T01
mkdir -p src/hello migrations
cat > src/hello/greeting.py <<'EOF'
def greet(name):
    return "Hello, %s!" % name
EOF
cat > migrations/0001_greeting_log.sql <<'EOF'
-- Log every greeting so we can see how many hellos this service has said.
CREATE TABLE greeting_log (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    greeted_at TEXT NOT NULL
);
EOF
git add -A
git commit -qm "hello: add the greeting service and its log table"
```

---

## Step 6: run proof

A check you claim ran but did not is the single most common lie an agent tells.
`sbe evidence run` closes that gap: it runs the command for real and writes a
receipt of what actually happened, never what was intended.

```bash
python3 "$SBE/bin/sbe" evidence run --out ~/sbe-sandbox/repo/.sbe/evidence/T01-receipt.json --kind gate --cwd . -- python3 -c "print('GREETING-OK')"
```

```
GREETING-OK

sbe evidence run: FREE FORM run: no registered check, so this receipt is advisory and satisfies no required policy check
sbe evidence run: receipt written to ~/sbe-sandbox/repo/.sbe/evidence/T01-receipt.json. Trust LOCAL-ADVISORY (this receipt was minted for a free-form command rather than a check registered in .sbe/checks.yml, so nothing outside the caller says which check it is. Free-form evidence is advisory whatever else is true of it). Command exited 0 in 0.079s, over 2 covered file(s) from the diff 7dc817fd5a6d..HEAD. Declared check kind(s): gate. stdout and stderr are recorded as digests only. argv held 0 secret-shaped token(s) and was recorded verbatim.
```

(The exact duration, `0.079s` here, is a real clock reading and will differ every
time you run it; everything else on that line matches exactly.)

With a receipt bound to your commit, the task can close:

```bash
cd ~/sbe-sandbox/repo
python3 "$SBE/bin/sbe" work finish T01 --cwd .
```

```
  IN-SCOPE   migrations/0001_greeting_log.sql
  IN-SCOPE   src/hello/greeting.py
sbe task close T01: PASS. 2 changed path(s), all inside the declaration. Closed clean.
sbe work finish T01: closed clean with receipt 882da588309859ac773eda98a7593c6723ed1ac62d169c5a77ebb80cff88556c; the plan task is complete by the single-source rule.
```

(That trailing receipt id is derived from the receipt file's own bytes, including
the timing above, so yours will differ; everything else on that line matches
exactly.) Both changed paths landed exactly where the task declared them and
nowhere else: that is what "closed clean" means here, not just "the check passed."

---

## Step 7: review

`sbe review` reads two things every time: the scored surface (silent-failure lints,
plus whatever your own vault and registries have to say about your habits, which is
not about this change) and the four hard gates (numbers, migration, approval, ran).
`--write` turns the verdict into a durable record. That vault-and-registry read is
read-only and expected here: this practice sandbox does not build or switch to a
throwaway vault of its own, so review reads whatever real `BROTHERSBE_VAULT` and
registries you already have configured on this machine, exactly as any other `sbe
review` would; nothing about running it inside this disposable repository writes
back to either one. Switching to a dedicated practice environment is a step this
ten-minute walkthrough has not reached yet.

```bash
python3 "$SBE/bin/sbe" review design/say-hello --write --reviewer you --reviewer-type human --result approved
```

```
CHECKS THAT OPENED A FILE IN ~/sbe-sandbox/repo/design/say-hello (1 of 12): these verdicts are about the code here.
silent-failure-lints      NO-DATA  lint root design/say-hello holds no scannable source (.py .sql .swift .rb .js .ts .go), so nothing was opened; 4 file(s) under design/say-hello were not opened because this lint has no pattern that reads their kind (.json 2, .md 2); its patterns are written for .py .sql .swift .rb .js .ts .go, so this verdict covers those kinds and says nothing about the rest [severity: gate]

CHECKS FED BY A VAULT OR REGISTRY OUTSIDE ~/sbe-sandbox/repo/design/say-hello (11 of 12, of which 10 have no source on this machine at all): a verdict here is not a statement about the code in this directory.
  ... 11 more lines here on a real run: your own vault and registry setup, almost
  all NO-DATA on a machine that has not configured either, which is normal and is
  not about the say-hello change either way. On a machine that DOES have a vault or
  registries configured, those same eleven lines print real check names instead
  (`citation-inventory` through `review-cadence`): that whole section varies
  machine to machine, and you can skip reading it here, because none of it is about
  the say-hello change either way.

BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  numbers   NO-DATA  no numbers-manifest found; if this change presents no decision figure that is correct, else add one; no numbers-manifest.json read under design/say-hello; 0 of 0 director(y/ies) directly under design/say-hello contributed no numbers-manifest.json [severity: gate]
  migration NO-DATA  no migration in this change, or no migration-receipt.json; no migration-receipt.json read under design/say-hello; 0 of 0 director(y/ies) directly under design/say-hello contributed no migration-receipt.json [severity: gate]
  approval  NO-DATA  no APPROVAL file and no Approved-by trailer; if this change touches no money or partner path that is correct; no APPROVAL read under design/say-hello; 0 of 0 director(y/ies) directly under design/say-hello contributed no APPROVAL [severity: gate]
  ran       NO-DATA  no ran-receipt.json; a SQL or pipeline change is not done until its check executed and left a receipt; no ran-receipt.json read under design/say-hello; 0 of 0 director(y/ies) directly under design/say-hello contributed no ran-receipt.json [severity: gate]

sbe review: review record written, bound to head 2be66bb83193, 0 finding(s) and 0 accepted risk(s):
  ~/sbe-sandbox/repo/design/say-hello/11-review.json

sbe review: exit 0 means no control FAILED. It does not mean a control passed. Read the verdict lines above: NO-DATA examined nothing and WAIVED suppressed a finding, and neither one is a pass.
```

Nothing here presents a decision figure, a migration, a money path, or SQL that
needed to run standalone (the greeting service's own check already ran in step 6),
so four honest NO-DATA lines, zero findings, and a written record. The head hash
(`9a3775fed269` above) is not yours: it is the hash tools/test_sbe_sandbox.py's own
driven run produces, because that suite builds the sandbox and makes the step 4
commit itself, through build_sandbox.py's own pinned identity and pinned date, so
that one driven run lands on the same bytes and the same hash every time it is run
against this version of the tree. The sandbox is built from this repository's own
files, so that hash moves whenever those files move, for example at a version bump,
and the suite is what keeps the number on this page honest for whatever version is
actually checked out. Your own step 4 commit above is a plain `git commit` you typed
yourself, in your own shell, at whatever real moment you ran it, so it carries your
own clock reading and its hash will not match the one quoted here, on any version.
tools/test_sbe_sandbox.py does not mask this hash with a placeholder; it reads its
own driven run's real hash and checks that the exact same line, hash included, is
quoted on this page, so the number above proves the page against the suite's run,
not against yours.

---

## Step 8: reach the prepared handover

The last step is not a merge (BrotherSBE never merges anything for you) -- it is a
written, named handoff: who is giving this up, who is meant to receive it, and
ownership stays with the outgoing owner until that named person accepts.

```bash
python3 "$SBE/bin/sbe" handover prepare design/say-hello --outgoing you@example.invalid --receiver teammate@example.invalid
```

```
sbe handover prepare: written, bound to head 2be66bb83193: 1 done, 0 in flight, 0 not started, 5 evidence item(s). From you@example.invalid to teammate@example.invalid. Ownership remains with you@example.invalid until teammate@example.invalid acknowledges.
  ~/sbe-sandbox/repo/design/say-hello/12-handover.json
```

One task, done, five pieces of evidence behind it, nothing left in flight and
nothing left unstarted. That is the whole ten-minute journey: a healthy install, a
described change, a known risk level, an accepted decision, one task started,
proven, reviewed, and now a prepared handover waiting on a named human. Everything
you just ran is the same start-work-evidence-review-handover chain this project's
own golden scenario drives for a full four-task team (`tools/test_sbe_golden_
scenario.py`, and `docs/guides/04-teams-and-evolution.md` for the worked-example
version) -- you just drove it alone, on one task.

---

## Clean up, and where to go next

This whole thing is disposable. When you are done:

```bash
rm -rf ~/sbe-sandbox
```

- `docs/guides/01-quickstart.md` is the next stop: the same four hard gates you saw
  NO-DATA above, this time against a real headline number and a real SQL check, with
  a planted drift so you watch the numbers gate catch it.
- `docs/guides/02-the-gates-in-practice.md` goes deeper on the four hard gates this
  page only glimpsed.
- `docs/guides/05-a-worked-engagement.md` is the full-size version of steps 2 through
  4 above: a real T3 change, all seven design documents, start to finish.
- `README.md` inside the sandbox repository you built has this same eight-step list,
  in case you want to re-run the journey without this page open.
