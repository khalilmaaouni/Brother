# What it will not tell you

The project publishes its own limits in `../KNOWN-LIMITS.md`, one heading per
limit. This page carries those, in its own voice, plus four things observed by
running it that are not in that file. Those four are marked.

## The design discipline is not a control

"Design before verification" and "install the check before writing the work" are
rules a person follows or does not. No tool computes whether you did. If you write
the dossier after the code, every check still passes.

## Nothing detects that a change needed an approval

The approval gate verifies an approval that was **declared**. A money-path change
that declares nothing gets `NO-DATA` and merges. Deciding a change needs review is
human work.

## A `Reviewed-in:` id is a pointer, not a control

Nothing resolves the id against your review platform. There is no shape check on
it beyond refusing placeholder tokens. The agent writes commit messages, so an
agent can write one. Its verdict is therefore `NO-DATA`, and the evidence line
says so on every run. If you want it to be a control, add a CI step that queries
your review platform for the id.

## A signature only passes if this host verified it

The `Approved-by:` trailer PASSes only when git reports the signature as good
against a key the host trusts. A valid signature whose key matched no trusted
principal reports `NO-DATA`, which is exactly what a self-generated SSH key
produces. On a stock CI runner with no imported public keys, every approval is
`NO-DATA`.

## The approval identity proof refuses some honest name pairs

The gate certifies "the approver is not the author" only when the difference is
proven. Same-script name pairs whose every differing letter folds to the same
ASCII letter cannot be proven different, so they are refused rather than passed.
Measured by the project over pools of 10 common real names per script (45 pairs
each, name only): Russian 10 of 45 unproven, Greek 2 of 45, Vietnamese 1 of 45,
Amharic and Hindi 0 of 45. The escape works and the refusal names it: record an
email address that differs from the author's.

## Text difference is not proof of independence

The numbers gate proves the second derivation's text differs beyond case,
whitespace, comments and trailing punctuation. It does not read which tables or
columns the two queries touch. Renaming an alias passes. The PASS sentence says
this rather than implying more.

## The migration gate cannot resolve a rehearsal id

`rehearsal_run_id` is checked for being a non-placeholder string. Nothing queries
your job system. It is a pointer for a human to follow.

## Blast radius revokes nothing

"No apply rights on production state" is a working rule plus whatever access
control you already have. Nothing here can revoke a credential your shell holds.

## The UNVERIFIED label is not applied by any tool

Output that has not cleared its gate is supposed to be presented labelled
UNVERIFIED. That label is written by the agent, per the rules, and no tool applies
or checks it. A session that fails to label unverified output is not caught.

## Nothing blocks until you wire CI

`--strict` blocks only in a repository that wired it. No CODEOWNERS and no branch
protection ships, so nothing stops someone editing the workflow that runs the
gates. That is your repository's setting.

## The fence and telemetry checks read registries, not the world

Fence hygiene and budget-vs-tier run only over registries named in
`BROTHERSBE_REGISTRIES`, and only over fence lines containing the word "agent".
Telemetry observes and decides nothing; no CI step reads it.

## The honesty sweep is not a proof

The meta-test hollows each check's own declared worked example and prints its
coverage, skipped cases and exemptions. It claims nothing about inputs no fixture
plants. It is an enforced declaration plus a mechanical scenario sweep, not a
proof over all inputs. The project says this in its own source.

## The citation check never opens a page

It proves every external URL cited in the project's docs has an inventory entry
answering claim, population, date and limit. It makes no network call and cannot
prove a page still says what its entry recorded.

## Every threshold was measured on one estate

`tables/`, the rubric baselines and the lint numbers were measured where the
project was built. They are defaults where you are, not measurements of your
estate. Re-measure. `NO-DATA` is a legal score.

## It has never run in anyone else's CI

Every green run the project cites happened in its own repository. No external
adoption is claimed. CI runs on Linux only. The macOS and windows-latest legs were removed on 2026-08-17 under the founder law forbidding those runners on any trigger, because they bill at 10x and 2x Linux; Windows coverage is now a written protocol a person follows. No external adoption is implied by any of it.

## The telemetry writer lock needs a filesystem that honors flock

On a network mount the lock cannot be taken. Appends proceed unlocked so no row is
lost and record themselves in a sidecar file, and both maintenance rewrites refuse
to run. What is lost there is maintenance, not data. Keep the vault on a local
disk.

---

# Newly observed here, not in KNOWN-LIMITS.md

These four came out of running the tools on this machine. Each is reproducible
with the commands shown.

## 1. `SBE_DOSSIER_ROOT` replaces the directory you name, and says so

If that environment variable is set, `sbe_design.py` discards the directory
argument. It no longer does this quietly. Run against a real dossier while the
variable points at an empty directory:

```
$ SBE_DOSSIER_ROOT=empty-root python3 tools/sbe_design.py design/jobs-idempotency
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        no dossier read under empty-root; 0 of 0 director(y/ies) directly under empty-root contributed no dossier; the directory named on the command line (design/jobs-idempotency) is NOT what was examined: SBE_DOSSIER_ROOT=empty-root replaced it, so every verdict below is about empty-root
  dossier    FAIL     SBE_DOSSIER_ROOT=empty-root holds no dossier (no directory under it contains 00-intake.json or any of 01 through 07); this repository declares that it keeps dossiers, so an empty dossier root is a broken configuration, not an absence
  artifacts  NO-DATA  no dossier under empty-root, so this check opened no file
  adr        NO-DATA  no dossier under empty-root, so this check opened no file
  datamodel  NO-DATA  no dossier under empty-root, so this check opened no file
  diagrams   NO-DATA  no dossier under empty-root, so this check opened no file
  placeholder NO-DATA  no dossier under empty-root, so this check opened no file
```

The `scope` line names both directories and states plainly that the one you typed
is not the one that was read. That is the disclosure doing its job, and it is the
reason this is a footgun rather than a trap.

The footgun is still real. If you export the variable in your shell profile, every
ad hoc `sbe_design.py <dir>` you run checks the configured root instead, and you
have to read the scope line to notice. The sibling tool takes the stricter line:
`sbe_gate.py` explicitly stopped re-rooting itself and its source says why.

Workaround: do not export it in your shell. Set it only in the CI job, per the
project's own setup guidance.

## 2. The hard gates pool receipts across the whole tree into one verdict

The design checks report per dossier, with a `dossier: <name>` header before each
group. The hard gates do not. Run them over a directory holding several dossiers
and you get four lines total, with the evidence summed.

The four example dossiers hold two `ran-receipt.json` files, one with 2 checks and
one with 3. Run at the parent:

```
$ python3 tools/sbe_gate.py design
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   PASS     1 figure(s) each pinned to a snapshot, with a second derivation whose text differs beyond case, whitespace and comments, re-run to zero drift; read 1 numbers-manifest.json under design (revenue-mart/numbers-manifest.json); 3 of 4 director(y/ies) directly under design contributed no numbers-manifest.json (jobs-idempotency, settlement-load, two-region) [severity: gate]
  migration PASS     1 receipt(s): forward and reverse both ran against a restore, 1 row-count comparison(s) matched, and a rehearsal id string is recorded; read 1 migration-receipt.json under design (settlement-load/migration-receipt.json); 3 of 4 director(y/ies) directly under design contributed no migration-receipt.json (jobs-idempotency, revenue-mart, two-region) [severity: gate]
  approval  FAIL     two-region/APPROVAL (of 1 APPROVAL file(s) read) declares 'Promoting the passive region touches production state on the', but approval is a typed name with no signature or review id; a name in a text field is not a control (add a signed Approved-by trailer or a Reviewed-in review id) [severity: gate]
  ran       PASS     5 recorded check(s), each with a zero exit and a nonzero duration; read 2 ran-receipt.json under design (jobs-idempotency/ran-receipt.json, settlement-load/ran-receipt.json); 2 of 4 director(y/ies) directly under design contributed no ran-receipt.json (revenue-mart, two-region) [severity: gate]
```

"5 recorded check(s)" is 2 plus 3, and the verdict is one line. What the line now
does is name the two receipt files it summed and list the two directories that
produced none, so you can see the pooling instead of inferring it.

The structural limit survives the disclosure. In a monorepo running gates at the
root, a `ran PASS` still tells you that every receipt found under that root is
internally consistent. It does not tell you the change in this pull request
emitted one. Absent evidence is `NO-DATA`, and `NO-DATA` does not block, so a
change that emits no receipt is still covered by somebody else's PASS in the
summed line. The difference is that its directory is now printed in the same line,
under "contributed no ran-receipt.json", for anyone who reads that far.

Workaround: run the gates against the directory the change owns, not the
repository root, or add a step that asserts the receipt exists at the path this
change is supposed to have written it to.

## 3. The citation check ignores the directory you pass it

`sbe_score.py` bundles a `citation-inventory` check. Whatever directory you give
the tool, that check scans the installed skill's own tree unless
`SBE_CITATION_ROOT` is set. Pointed at a two-file scratch directory:

```
$ python3 tools/sbe_score.py sample-etl
CHECKS THAT OPENED A FILE IN /Users/<user>/BrotherSBE (2 of 12): these verdicts are about the code here.
silent-failure-lints      FAIL     3 hit(s) in 2 file(s) scanned: load_settlements.py:7 except-then-pass (swallows the error); load_settlements.py:12 discarded subprocess result without check=True (exit code is swallowed); upsert.sql:3 conflict-skipping upsert without a logged skip count [severity: gate]
citation-inventory        PASS     36 external URL(s) across 89 document(s) scanned under /Users/<user>/BrotherSBE (the installed skill's own tree, this check's default; set SBE_CITATION_ROOT to point it at another root) (every markdown file in the CHECKSUMS.sha256 manifest), each with an inventory entry in docs/CITATIONS.md answering claim, population, date and limit; scope structure and coverage only, never live page content: this check opens no network connection [severity: gate]
```

(Only the home directory is abbreviated. The group header and the other ten checks
are shown in `01-install-and-first-run.md`.)

The PASS is about the skill's documentation, not your repository. It discloses
this inside its own evidence line, and the group header above it says which tree
these verdicts are about, which is consistent with the project's posture. The
effect is still that a green `citation-inventory` in your CI is telling you
something about the vendored tool, not about your docs. `SBE_CITATION_ROOT`
appears in no markdown file in the repository, only in that runtime sentence.

Workaround: set `SBE_CITATION_ROOT` to your docs root in CI, or read that line as
decoration and ignore it.

## 4. Editing the skill in place makes `verify-install.sh` fail forever

The manifest covers every shipped file and an unlisted file is a failure, by
design, because an extra file is the shape of a planted backdoor. The practical
consequence is that any local customisation (a note file, a tweaked threshold, a
demo dossier created inside the clone) turns the integrity check permanently red:

```
$ bash scripts/verify-install.sh
MISMATCH:  tools/sbe_gate.py
EXTRA:     NOTES.md

verify-install: checked against /Users/<user>/BrotherSBE/CHECKSUMS.sha256
verify-install: 121 file(s) match, 1 mismatched, 0 missing, 1 extra (present on disk, absent from the manifest), 0 non-regular (a symlink or pipe the manifest cannot hash)
verify-install: FAILED. Do not trust this installed copy until you understand why the files above differ from the published manifest.
```

Exit code 1. Not a bug. Just plan for it: keep your dossiers in your own
repository, never inside the clone, and if you fork the tools, regenerate the
manifest with `scripts/checksums.sh` so the check means something again.

---

## One more thing, on the honest side

Every long verdict sentence in this tool exists because the sentence used to be
shorter and was wrong. The source comments say so explicitly, case by case. When a
message tells you it is not asserting something, that is not hedging. It is the
record of a specific run where the shorter claim turned out to be false.

The scope clauses are the newest example. "PASS" used to be the whole line. Then
it had to say what it examined, because a verdict you cannot attribute to a file
is a verdict you cannot check.
