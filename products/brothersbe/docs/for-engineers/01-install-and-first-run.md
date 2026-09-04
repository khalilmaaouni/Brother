# Install and first run

Everything below was executed on macOS 26.5.2. Anything not executed is labelled.

## What a fresh machine needs

Three things, and no package install.

```
$ python3 --version
Python 3.9.6
$ git --version
git version 2.50.1 (Apple Git-155)
$ sw_vers -productVersion
26.5.2
```

Python 3.9.6 is what the tools were verified against here. It is the macOS system
Python, so on a stock Mac you already have it. The tools are standard library
only: no `pip install`, no lockfile, no dependency tree.

That sentence used to be the ONLY version evidence in this repository, which
made the front page's "3.9 or newer" half a promise and half a guess: 3.9.6 is
the floor, and nothing above it had been run. On 2026-08-18 the 67 suites were
run on 3.9.23, 3.13.7 and 3.14.0rc2. The failure set is identical on all three
(seven suites, all pre-existing and none version-related), so "or newer" now
holds as far as 3.14 on measurement rather than on intent. `TESTERS.md` carries
the table. The one interpreter-specific defect that sweep found was in a test
rather than the product: `tools/test_sbe_evidence.py` compared a receipt's
recorded version against a hand-built `major.minor.micro` triple, which is
equal to `platform.python_version()` on every final release and unequal on
every pre-release, so the suite could not run on a release candidate at all.

Git is needed because the approval gate reads commit trailers and signatures.

A POSIX shell for the two `sh` tools. There is NO Windows CI leg: it was removed on 2026-08-17 under the founder law forbidding a Windows or macOS runner on any trigger, because Windows bills at 2x Linux and macOS at 10x. Measured 2026-08-29: every job in every workflow of this repository runs on `ubuntu-latest` and nothing else. Windows coverage is now a written protocol a person follows (docs/WINDOWS-CHECK.md), which is weaker than a runner and is said plainly rather than implied.

## 1. Get the clone

```
git clone <repository-url> ~/.claude/skills/brothersbe
```

**Not executed here.** The published clone URL needs credentials this machine
does not hold, so git refused it: `could not read Username for` the published
host, `terminal prompts disabled`. (The host name is dropped from the quote
because a bare URL in a shipped page owes an entry in this project's citation
inventory, and a hostname inside an error message is not a source.)

Everything below was run against a clone of the repository made locally, which
exercises the same code paths. Ask whoever gave you the repository for read
access, then continue.

## 2. Verify what you installed

```
$ bash scripts/verify-install.sh
```

Real output:

```
verify-install: checked against /Users/<user>/BrotherSBE/CHECKSUMS.sha256
verify-install: 122 file(s) match, 0 mismatched, 0 missing, 0 extra (present on disk, absent from the manifest), 0 non-regular (a symlink or pipe the manifest cannot hash)
verify-install: the excluded paths (*/__pycache__/*, .superpowers/, docs/superpowers/, and files named .DS_Store, *.pyc, STATE.md, ~$*, *.docx; .git/ not enumerated) currently hold 55 entr(y/ies) of any type, 0 of them source code and 0 of them non-regular (a symlink or pipe this check cannot hash).
verify-install: PASSED. Every file the manifest names matches on disk,
verify-install: and no file exists on disk that the manifest does not name,
verify-install: outside the excluded paths enumerated above (their current
verify-install: file count is printed on every run, and source code among
verify-install: them fails this check).
verify-install: a manifest records CONTENT, not file mode: a data file that arrived with the execute bit set still matches its hash here, so this says the bytes are the published bytes and says nothing about permissions.
verify-install: this does not prove the manifest itself is authentic; it proves your files match whatever manifest you pointed this at. Get the manifest from the release you trust (the tag's git history, or a release asset), not from the same untrusted channel as the code.
```

Exit code 0. (The first line prints the absolute path of the manifest on the
machine that ran it; only that path is abbreviated above.)

What it checks: every one of the 122 manifest files hashes to its published value,
and no unlisted file exists on disk. Note the last two lines. It verifies content
against a manifest, not the manifest's own authenticity, and it says nothing about
file modes. Both limits are printed on every run rather than buried.

### What a failure looks like

One line was appended to `tools/sbe_gate.py` and an empty `NOTES.md` created:

```
$ bash scripts/verify-install.sh
MISMATCH:  tools/sbe_gate.py
EXTRA:     NOTES.md

verify-install: checked against /Users/<user>/BrotherSBE/CHECKSUMS.sha256
verify-install: 121 file(s) match, 1 mismatched, 0 missing, 1 extra (present on disk, absent from the manifest), 0 non-regular (a symlink or pipe the manifest cannot hash)
verify-install: FAILED. Do not trust this installed copy until you understand why the files above differ from the published manifest.
verify-install: an EXTRA file is exactly the shape of a planted backdoor: it runs automatically along with everything else in this installation, and the manifest says nothing about it because nothing here declared it.
```

Exit code 1. Note that an extra file fails, not only a modified one. If you edit
the skill in place, this check will fail from then on, correctly.

## 3. Prove the checks catch what they claim to catch

```
$ python3 evals/run_evals.py
```

Real tail:

```
  a-self-declared-component-trace-is-disclosed want=disclosed got=disclosed ok
  a-change-directory-with-no-receipt-is-named-in-the-verdict-that-pools-it want=disclosed got=disclosed ok
  the-approval-verdict-names-which-approval-file-it-read want=named    got=named    ok
  an-empty-directory-cannot-print-the-report-of-a-dossier-somewhere-else want=disclosed got=disclosed ok

547 evals: 547 passed, 0 regressions.
```

Exit code 0. Each case is a real defect turned into a fixture, plus an assertion
that the check catches it. The case count and the manifest file count both move as
the repository changes; read the "0 regressions", not the total.

Some of those cases check the repository against its own documentation. Adding a
file to the clone and running the suite without regenerating the manifest gives
this, which is worth seeing once:

```
  the-tracked-manifest-matches-the-tree-it-ships-with want=matches  got=the tracked manifest is stale for: docs/for-engineers/NOTE.md (regenerate with scripts/checksums.sh CHECKSUMS.sha256) REGRESSION
```

The summary line under it counted one regression, and the run exited nonzero. (It
is quoted as one case line rather than as the whole tail on purpose: a shipped
document may not print a suite total that a fresh run cannot reproduce, and a
deliberately broken run never can. A guard in the suite enforces exactly that, and
would fail this page for pasting the total.)

That is the shape of the whole idea: a file list or a number printed in a shipped
document that the tool cannot reproduce is a regression. If you fork the tools,
expect to regenerate the manifest.

Then the meta-test, which is the one that keeps the rest honest. It discovers
every registry of checks, takes each check's own declared worked example, and
hollows it out leaf by leaf, subtree by subtree, and whole, in empty strings,
whitespace and nulls, requiring that none of it produces a PASS.

```
$ python3 evals/test_no_data_class.py
```

The summary line. It quotes below what it once was, the true last line the
command prints: with failures present, this run also prints each one, by
name, underneath it.

```
38 checks discovered from 6 registries in 132 module(s), 4258 scenarios run, 2 waived by declared exemption, 58 failure(s).
```

Exit code 1: this run finds real gaps in the tools right now (twelve check functions
sitting outside any registry, thirty report lines that bypass the print choke
point, spread across install.py, sbe_system_doc.py, sbe_decision_record.py and
test_ba_status_page.py, and twenty seven hollowed-out clarify scenarios), and the
meta-test's whole point is to fail loudly on exactly that rather than average it
away. The two waivers are printed above that line with their stated
reasons, so a waiver is never silent:

```
  sbe_intake.py: excused 11 print(s); an interactive interview: its prompts, echoes and refusals are dialogue with the operator, and nothing machine-parses them as verdict lines
  sbe_telemetry.py: excused 80 print(s); operator status lines and hook JSON, never parsed as gate verdicts; its ledger writes are data, not report lines
```

And the tool tests:

```
$ python3 tools/test_sbe.py
```

Real tail:

```
Ran 27 tests in 9.573s

OK
migrate: outcomes.jsonl SHRANK while this rewrite held the writer lock (69 bytes read, 19 on disk); a second writer is ignoring the lock, so nothing was replaced
```

Exit code 0. That last line is a test asserting the refusal path prints, not a
failure.

Total wall time for the three suites on this machine: under two minutes.

## 4. Run a check on a directory

```
$ python3 tools/sbe_gate.py empty-dir
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   NO-DATA  no numbers-manifest found; if this change presents no decision figure that is correct, else add one; no numbers-manifest.json read under empty-dir; 0 of 0 director(y/ies) directly under empty-dir contributed no numbers-manifest.json [severity: gate]
  migration NO-DATA  no migration in this change, or no migration-receipt.json; no migration-receipt.json read under empty-dir; 0 of 0 director(y/ies) directly under empty-dir contributed no migration-receipt.json [severity: gate]
  approval  NO-DATA  no APPROVAL file and no Approved-by trailer; if this change touches no money or partner path that is correct; no APPROVAL read under empty-dir; 0 of 0 director(y/ies) directly under empty-dir contributed no APPROVAL [severity: gate]
  ran       NO-DATA  no ran-receipt.json; a SQL or pipeline change is not done until its check executed and left a receipt; no ran-receipt.json read under empty-dir; 0 of 0 director(y/ies) directly under empty-dir contributed no ran-receipt.json [severity: gate]
```

Exit code 0. Four `NO-DATA` verdicts, no PASS anywhere, on a directory holding no
evidence. That is the product working, not the product failing to find anything.
Each line names the file it looked for and the directory it looked in, so a
`NO-DATA` you did not expect tells you exactly where to look.

## 5. The vault and the hooks (optional, and skippable tonight)

The skill can write telemetry and session logs to a directory you name:

```
export BROTHERSBE_VAULT="$HOME/BrotherSBEVault"
```

Without it, the ten soft checks in `sbe_score.py` report `NO-DATA` naming what
they did not open, and nothing else changes. Real output from this machine, with
no vault set:

```
ledger-coverage           NO-DATA  no /Users/<user>/BrotherSBEVault/99-System/telemetry/outcomes.jsonl, so nothing was opened and there is no session coverage to report on [severity: soft]
schema-2-uniform          NO-DATA  no /Users/<user>/BrotherSBEVault/99-System/telemetry/outcomes.jsonl, so nothing was opened and there is no schema uniformity to report on [severity: soft]
cache-economy             NO-DATA  no /Users/<user>/BrotherSBEVault/99-System/telemetry/outcomes.jsonl, so nothing was opened and there is no cache economy to report on [severity: soft]
vault-log-per-active-day  NO-DATA  no /Users/<user>/BrotherSBEVault/99-System/telemetry/outcomes.jsonl, so nothing was opened and there is no session logs per active day to report on [severity: soft]
fence-hygiene             NO-DATA  set BROTHERSBE_REGISTRIES to enable; nothing was opened [severity: soft]
correction-latency        NO-DATA  no /Users/<user>/BrotherSBEVault/99-System/telemetry/corrections.jsonl, so nothing was opened and there is no correction latency to report on [severity: soft]
budget-vs-tier            NO-DATA  set BROTHERSBE_REGISTRIES to enable; no registry was opened, so no fence line was checked for a tier tag [severity: soft]
prediction-seals          NO-DATA  no /Users/<user>/BrotherSBEVault/50-Reference/operator-model.md, so no prediction ledger was opened [severity: soft]
felt-outcome-ratings      NO-DATA  no scored ratings in /Users/<user>/BrotherSBEVault/99-System/telemetry/ratings.jsonl [severity: soft]
review-cadence            NO-DATA  no review recorded in /Users/<user>/BrotherSBEVault/99-System/telemetry/reviews.jsonl [severity: soft]
```

(Only the home directory is abbreviated above.) Four of the ten name
`outcomes.jsonl`, three name other vault files, two name the unset
`BROTHERSBE_REGISTRIES` variable, and one names a ratings file. None of them
guesses at a value it does not have. That is the point of reading them: a
`NO-DATA` here tells you the exact path that would have made it a verdict.

The Claude Code hooks (SessionStart, SessionEnd, PreCompact) are in the project's
`docs/SETUP.md`. **Not executed here.** They are not needed for any check in this
document. Skip them on night one.

## 6. What blocks a merge

Nothing yet. Local runs are advisory and exit 0 on a FAIL. `--strict` is what
makes a FAIL exit nonzero, and CI is what makes that stop anything. See
`30-adopting-it-on-a-team.md`.
