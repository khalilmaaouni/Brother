# Changelog

Newest first. Each entry names the behavior that changed and the check or test
that proves it, because a changelog line nothing can verify is a press release.
What this file does NOT record: internal working notes and measurements from
the estates this project was built on, which stay untracked by the publish
checklist's own rules.

## 3.7.3 (2026-09-02)

The eval suite reaches 547 of 547 for the first time. Seven standing regressions
were one input-contract change wearing seven faces: an interview grew a sixth
question while two fixtures still fed five, so the interview hit end of input,
wrote nothing, and four separate output blocks then read as drifted prose. The
fixtures answer the question the tool now asks, the quickstart fence resyncs to
the shipped workflow, and the drifted guides repaint from live output rather
than from an edited copy. Proven by evals/run_evals.py printing 547 of 547 at
exit 0.

The silent-failure lint reaches PASS on this product's own tree: two statistics
stopped shrinking in silence, and five deliberate degrades now carry a visible
reviewed waiver naming why. Proven by tools/sbe_score.py over the product root.

## 3.7.2 (2026-08-31)

The session cap refusal now names a file escape a stopped user can actually
reach (~/.claude/bm_session_cap.override wording per the E2 first-contact work),
first contact speaks the user's language and measures the user's change, and
sbe_score's lint surface gained the vault-scope and first-contact path
coverage. Proven by tools/test_sbe_first_contact_paths.py,
tools/test_sbe_vault_scope.py and tools/test_bm_session_cap.py in the hub's
own battery.

## 3.7.1 (2026-08-30)

The two change cards merged into one technical-change card: the
service-and-contract card and the SQL-and-pipeline card carried an identical
section skeleton and near-identical closing sentences, differing only in
their domain bullets, so one card now holds both domain sections and every
command and check either had. Every live reference updated; historical
mentions in this changelog and docs/specs kept deliberately. Proven by the
repository's own battery on the branch and the release invariant
(tools/sbe_release_invariant.py --strict) going green with this bump. First
landing of the command-surface redesign, ruled to proceed 2026-08-30 under
the founder's night delegation.

## 3.7.0 (2026-08-29)

Windows certification: BrotherSBE 3.4.2 ran natively on Windows Server 2022,
found seven defects, and this release carries the fix set, re-verified on
macOS so a Windows fix does not silently break the platform this project
mostly ships on. 11 files changed, mostly new Windows handling: the bash
write guard and its tests, telemetry locking and encoding, checks, score,
gate, design, coldstart parse, a new Windows-simulation test
(`tools/test_sbe_windows_sim.py`), and the no-data eval. No product test was
weakened and nothing was added to a lint allowlist.

Two Git Bash drive-path tests added by the certification assumed a Windows
drive letter and errored on macOS; they now skip off Windows, so the suite
stays green on both platforms.

Verified at this head: `python3 tools/sbe_gate.py --strict` exit 0;
`evals/run_evals.py` 547 evals, 547 passed, 0 regressions;
`evals/test_no_data_class.py` 4222 scenarios, 0 failures;
`tools/test_sbe_bash_guard.py` 50 tests OK, 2 skipped off Windows;
`tools/test_sbe_windows_sim.py` OK.

Not covered, checked rather than assumed: Windows itself is verified only
through the certification run this branch carries forward, not by this
machine, which is macOS.

## 3.6.0 (2026-08-29)

The baseline battery grows from 36 steps to 86 by wiring every `tools/test_sbe_*.py`
suite that existed but ran in nothing. 39 of 74 suites were named in no workflow and
no battery; three of them carry the coverage `docs/HOSTILE-SCENARIO-COVERAGE.md`
cites for six of the ten red-team scenarios. `TestEverySuiteIsWiredIntoAGate` now
asserts, by invocation rather than by name, that the battery and the workflow
invoke the same set (77 suites each), so this debt cannot regrow silently.

A test inside the battery that ran green while asserting nothing is fixed:
`test_an_unreadable_registry_is_an_unavailable_finding_and_a_nonzero_exit` used
`chmod 000`, which does not deny reads to uid 0, so both its assertions were
satisfied by something other than the unreadable state it claimed to force. It
now checks its own premise before asserting on it, calibrated by forcing that
check to fail where it used to silently pass.

`tools/regen_sandbox_guide.py --check`, which owns the derived git ids
`docs/guides/00-sandbox.md` pins, existed since 2026-08-17 and ran in no gate at
all. It is battery step 85 now, calibrated by drifting the guide to a wrong hash
and watching it exit 1. Fixing it surfaced two more defects: the tool only
repinned two of the three shapes the guide pins (now a `PINS` tuple of three,
one journey), and a seed commit id had three separate copies (guide, test, live
output) where the tool owned only one.

Four tests added the same night never ran: `unittest.main()` sat above where
they were appended in the file, so `python3 test_sbe_program.py` direct-run
(what the battery and CI use) never collected them, though `python3 -m
unittest` did. Caught by the runner reporting 137 while the file held 141
`def test_`.

`docs/RELEASE.md`'s own step list omitted three of the five regenerations
`sbe version bump` prints as owed (`evals/replay_book.py --write`, `sbe book`,
`tools/regen_sandbox_guide.py`) and ordered the manifest before them.
`TestTheReleaseDocNamesEveryStepTheToolPrints` now keeps the document and the
tool's printed list from drifting apart again; the manifest step moved to
last, since a manifest generated before a regeneration step ships stale.

`docs/KNOWN-LIMITS.md`'s Windows telemetry entry cited four line numbers as the
owner-only write sites; all four pointed at unrelated code. They are now named
as symbols, kept real by `TestTheOwnerOnlyWriteSitesStayNamed`. Hostile-scenario
B8's blast radius is pinned at the single `strictWaivers: false` rule
(`TestTheWaiverBlastRadiusStaysOneRule`); B10 is confirmed already fixed.

Not covered, checked rather than assumed: the Bitbucket credential gate is
closed on this machine, so `bbstatus.py` stays NO-DATA; Windows is verified
only in simulation, not on a real machine.

## 3.5.7 (2026-08-29)

`PARITY.md`'s two rows deferred against Bitbucket are closed: host-routed build
status posting (`src/brothersbe/bbstatus.py`, the routing case in
`scripts/local-gates.sh`) and the shared credential names (`BITBUCKET_TOKEN`,
or `BITBUCKET_USERNAME` plus `BITBUCKET_APP_PASSWORD`), verified against the
sibling repository's own equivalents rather than assumed. `_scrub` in both
trees was confirmed to replace the secret in returned messages, transport
error text included.

Row 16's real divergence, that this repository refuses to post when the
origin remote does not parse to `owner/repo` while the sibling's GitHub arm
posted to a hardcoded canonical repository, is fixed on the sibling's own
main (BrotherModeUp #61, `4e4304c`): its GitHub arm now derives `owner/repo`
through a new `slug` subcommand and refuses the same way this repository
does. Row 16 keeps both the pre-fix and post-fix statements, dated, since a
parity row that reads closed while the other tree's main still carried the
old behavior would be the exact drift this file exists to catch.

That same merge carried an unrelated Windows defect fix: the sibling's
runner captured a status tool's stdout through `$(...)`, which strips a
trailing newline but not a trailing carriage return, so on Windows
`HOST_KIND` held `github\r` and matched neither routing case. This repository
does not share the defect (the routing here reads the origin URL in a shell
`case`, no Python stdout captured on that path), checked rather than
assumed.

`docs/book/05-the-first-loop.md` pinned a commit range that no longer exists
in the object database (a history rewrite, not a shallow-clone truncation,
confirmed by unshallowing to 874 reachable commits). Re-pinned to a range
that reproduces the documented output byte for byte; `evals/replay_book.py`
now names which ref failed to resolve rather than silently comparing against
nothing. The path masks that normalize a machine's home directory for
comparison covered `/Users` and `/home` only; a container running as root
(`$HOME=/root`) matched neither, so `04-install-day.md` block 1 read as
documentation drift for days on such a host. The running machine's own home
root now joins the mask, derived rather than hardcoded.

Not covered, checked rather than assumed: no real POST has reached
`api.bitbucket.org` from either repository, since no session holds a
Bitbucket credential. Windows remains unverified on a real machine; the
newline defect above was found by reproducing the platform's newline policy
on a stand-in stream, not by running Windows.

## 3.5.6 (2026-08-29)

The sandbox guide stops telling newcomers their first run will fail.

The page said that on a repository of your own, `project-init` "reads FAIL
until first start repairs it". That has not been true since 3.5.0, the release
whose own heading is that a first run reads as a welcome. The check returns
SETUP, a verdict of its own that says nothing is broken and this repository has
simply not been set up yet, and it names the one command that changes that. A
brand new repository reports no failures at all on its first run.

Proven by running `sbe doctor` against a freshly created git repository with no
BrotherSBE footprint: `project-init SETUP`, and the summary line reads 15
checks, 13 PASS, 0 FAIL, 1 SETUP, 1 NO-DATA. `python3 tools/test_sbe_sandbox.py`
stays green at 10 tests, exit 0.

## 3.5.5 (2026-08-29)

Two writers with disjoint claims can both close honestly. `sbe task close`
counted every changed path between a task's base commit and now, including
paths another open task had already declared, so whichever writer closed second
saw a peer's files as undeclared and had to force. That produced four recorded
forced dispositions in one morning, which is a protocol teaching people to
override it. A changed path owned by another OPEN task now renders as
PEER-OWNED, names the task that owns it, and does not force the close. A path
nobody owns still fails, which is the whole point of declaring one.

Proved by: `python3 -m unittest tools.test_sbe_tasks` prints OK over 39 tests at
exit 0, with the new case red before the change.

Stated rather than hidden, because it is the same defect one size larger: this
fixes peer-owned paths and does nothing for the wider case, where a day of
unrelated commits sits between a task's base and now and is charged to that
task. A task whose owner has died therefore still cannot close cleanly, and
forcing with a recorded disposition remains the only exit. That is a registry
postcondition to revisit, not a gap in this change.

## 3.5.4 (2026-08-28)

Fewer cards, and the ones that remain say when they fire. The two change cards
(service and contract, SQL and pipeline) merge into one card, prove-this-change,
and the evidence card folds into it and into verify, taking the skill surface
from sixteen to fourteen. Kickoff, design and work now fire on the work itself
rather than waiting to be typed, each keeping one line telling you how to invoke
it on purpose. Every remaining description is written in the situation a person
is actually in, not the product's nouns. Proved by: python3 tools/test_sbe_interop.py
prints OK over 19 tests, python3 tools/test_sbe_skill_descriptions.py prints PASS
for all 12 skills, bin/sbe book --check --strict prints 0 FAIL, and ls skills/
counts 14. Stated rather than hidden: the adopted design names thirteen, and the
moves it specifies land at fourteen; closing that last one is a decision, not a
cleanup.

## 3.5.3 (2026-08-28)

The intake reads memory before it asks anything. Kickoff now opens by reading
what this estate already recorded for the project and playing back one sentence
of it, so a person is never asked what the system already knows; when nothing
is on record it says so in one line rather than skipping the step silently. Its
questions travel through the interactive question window, one decision at a
time with a recommended option first, and the options step renders its own
diagram of the recommended approach with labels adapted to who is asking.
Proved by: bin/sbe book --check --strict prints 7 section verdicts and 0 FAIL,
and the three behaviors are greppable in skills/kickoff/SKILL.md. Still owed,
stated rather than implied: a live driven run showing all three in a real
session.

## 3.5.0 (2026-08-27)

A first run reads as a welcome, not a failure, and the intake adapts to who is
asking.

The doctor's overall result becomes tri-state: PASS (ready), SETUP (a brand new
project, not broken and not yet set up), FAIL (real breakage). A fresh marketplace
install used to meet a `project-init` FAIL and a repair walk before any value; it
now reads as the ordinary new-project state it is. The safety invariant is
preserved rather than weakened: an uninitialized repository never reads PASS, so
guided mode still cannot treat it as ready to run gates, and SETUP keeps a non-zero
exit so a caller reading only the exit code does not mistake it for ready.
Proof: on a fresh git repository `bin/sbe doctor --json` reports result SETUP,
project-init SETUP, and zero occurrences of the word FAIL in the output, while a
genuinely broken install (a bad hooks path) still reports result FAIL.

The start, next and status surfaces teach the SETUP state as a welcome rather than
a repair, and the field book was regenerated so its rendered sections match what
the tools now print (`python3 bin/sbe book --check --strict` reports 7 section
verdicts, 0 FAIL).

## 3.4.2 (2026-08-24)

Session handover packages stop shipping inside the product.

Four handover directories, 16 files, were tracked and shipped inside the public
3.4.1 tag: OVERNIGHT-PROMPT.md, CLOSE-REPORT.md, START-HERE.md,
NEXT-ROUND-PROMPT.md and a suite sweep. They are one session's working notes,
nothing in the product reads them back, and .gitignore's own runtime section
already drew this line: state is ignored, and the CONFIG under .sbe/
(policy.yml, checks.yml, team-profile.json) stays tracked. The handover packages
were never added to that list, so this states the convention rather than
changing it.

Why nothing caught it, which is the part worth keeping. The companion product
shipped ONE stray session note in its own release the same morning and its
doc-truth suite failed at once, because that suite asserts the manifest matches
the tagged tree. This repository shipped SIXTEEN and every check passed, because
the manifest was perfectly correct: the files were tracked, so they were in it,
and verify-install reported 0 extra over the released tag. A MANIFEST CANNOT
TELL YOU A FILE SHOULD NOT HAVE BEEN TRACKED. It answers a different question,
and answering that one correctly is exactly what hid this one.

So the fix is not only the ignore rule. `test_no_session_handover_package_is_tracked`
asks the question no manifest can, and it is calibrated rather than merely green:
re-adding a single handover file to the index makes it fail naming that file, and
removing it makes it pass again. Both directions were run. It refuses an empty
result as NO-DATA too, because the config that belongs under .sbe/ should be
tracked and an empty answer means the check looked in the wrong place.

Scanned before untracking, not assumed: no client term and no secret is in any of
the 16, so this is a category defect (working state inside a shipped product) and
not a disclosure one. Nothing is lost: the files stay on disk and remain readable
in the 3.4.1 tagged tree. Two en dashes leave the public product as a side effect,
having been inside those notes.

Why this is a release rather than a quiet commit: it was going to be one. The
release invariant refused it, correctly, naming tools/test_sbe.py as a
distributable file changed while VERSION held at 3.4.1. Removing files from the
shipped product IS a distributable change, and a marketplace user is offered an
update only when the version string moves. The gate overruled the judgement and
the gate was right.

## 3.4.1 (2026-08-24)

The release seal names the sandbox repin, the step it was silently missing.

`sbe version bump` prints the steps a release still owes after it runs. That
printed list is not documentation: it is the only place a releaser is told what
remains, so a step missing from it is a step that does not happen. The sandbox
repin was missing from it, and 3.4.0 followed the list exactly and still failed
the gate battery at command 42 of 52.

The cause: `docs/guides/00-sandbox.md` quotes two commit hashes taken from a
deterministic driven run, and that run builds from this repository's own files.
Moving the version moves the tree, which moves the hashes. The guide's own prose
already predicted this and `tools/regen_sandbox_guide.py` already existed to fix
it. Nothing told the releaser to run it.

The new line sits BEFORE the checksums line, deliberately: the repin edits a
tracked file, so a manifest generated ahead of it would ship stale.

Proved by `python3 tools/test_sbe_version_bump.py`, which prints Ran 14 tests,
OK, and by this release itself: the bump to 3.4.1 printed the repin as its third
remaining step, which is the behaviour the row exists to produce.

## 3.4.0 (2026-08-23)

The doctor gains a check that catches an installed copy lying about its
version, and that check is built so its own output can never leak a path. A
minor rather than a patch because a new doctor check is a capability a person
can run, in the same sense 3.1.0 shipped the guidance layer.

Why the leak matters more than it looks: this repository ships `docs/book/`,
`evals/replay_book.py` pastes REAL doctor output verbatim into those chapters,
and the book is public. Every detail string this check returns is therefore
public text by construction, so a full path would print one maintainer's home
directory into a public repository.

- `install-identity` reports an installed copy that claims this source's own
  VERSION while its file manifest differs, which is the exact lie a version
  string is supposed to make impossible. A different version is a lagging
  install and is counted as such, never folded into a FAIL:
  `python3 -m unittest tools.test_sbe.TestDoctorInstallIdentityCheck`.
- No verdict this check returns carries an absolute path, PASS, FAIL or
  NO-DATA, the cache root's own path included. PASS summarises counts; FAIL and
  NO-DATA name the offending copy by its directory basename. The guard is a
  test rather than a habit, so the property survives the next person to edit a
  message string:
  `python3 tools/test_sbe.py -k absolute_path`.
- The book's two affected chapters were regenerated from live runs rather than
  hand typed, which is the repair path the doc-truth eval itself documents:
  `python3 evals/replay_book.py` reports every block consistent.

## 3.3.1 (2026-08-22)

Merge preparation, on top of 3.3.0: the forward-compatibility seams and the
doc fixes that ready this plugin to sit under the Brother umbrella ship
together. No user-facing behavior of 3.3.0 changes; these are new seams and
corrected counts. Each line names the check that proves it, and the whole set
is green under the 52-command battery `scripts/local-gates.sh`.

- The handoff package wire format is defined, so a change passport can carry a
  BrotherSBE verdict across the seam to another plugin without a shared
  library (MERGE-P4): `python3 tools/test_sbe_handoff_package.py`.
- The gate battery survives extraction into a monorepo subdirectory: the
  trusted ref and the workflow path are inputs (`SBE_TRUSTED_REF` and the
  workflow argument), not roots hard-coded to this repository (MERGE-P16),
  proven by the battery running from a named ref:
  `SBE_TRUSTED_REF=HEAD scripts/local-gates.sh`.
- The installer's module count and the guides' scanned-file count name the
  tree after the contract file was added (115, not 114), and SKILL.md's
  silent-failure-lint clean-file count matches the tool's own scan: the counts
  are emitted by `python3 tools/sbe_score.py --strict .`.
- The release-blocking subset is defined from a measurement of what the merge
  actually makes more expensive to keep green, recorded in
  `docs/plans/2026-08-23-release-blocking-subset.md`.
- The doctor reports when an installed copy claims this source's own VERSION
  while its file manifest differs (`install-identity`), so a version string a
  person checks can no longer describe code that is not actually running:
  `python3 -m unittest tools.test_sbe.TestDoctorInstallIdentityCheck`.

## 3.3.0 (2026-08-22)

The adopter team's review fixes ship together: the fixes that were closed in
source since 3.2.1 reach an installable version for the first time. Each line
names the behavior and the check that proves it; the night's run that produced
this entry is recorded in docs/plans/2026-08-22-overnight-complaints-wbs.md.

- The doctor reports whether the installed hook wiring is the shipped one
  (`hooks-wiring`), and the probe for `${CLAUDE_PLUGIN_ROOT}` citations lives
  in `tools/sbe_hooks_wiring.py`, so the CLI carries no plugin-loader reference:
  `python3 tools/test_sbe_interop.py`.
- A publishable-history hit can be ACCEPTED through the tracked record
  `.sbe-private-history-acceptance.json`; the history test reports WAIVED by
  object id and never PASS, an id absent from the record still fails, a blank
  reason waives nothing, a malformed record waives nothing: `python3
  tools/test_sbe.py -k private`.
- The stall detector's disk reading is injectable for its fixtures and the
  15 GiB floor is the single source named by docs/RELEASE.md:
  `python3 tools/test_stall_detector.py`.
- The honesty meta-test is green again: every verdict-shaped helper returns
  named fields and every report line goes through the one-line choke point:
  `python3 evals/test_no_data_class.py` prints `0 failure(s)`.
- The walkthroughs answer the intake questions the tool asks today (the
  three-way contract question and the intent prompts), and their output blocks
  are what the tools print: `python3 evals/replay_book.py` and
  `python3 evals/replay_guide05.py` print `0 differ`.
- The discover suite runs the session-start command `hooks/hooks.json`
  declares; the interoperability checker derives the guarantee count from the
  document it checks and resolves a named test against the file the document
  names; chapter 15 declares the `gh` capability its doctor transcript assumes;
  the booklet is regenerated from canonical state: `python3 bin/sbe book
  --check --strict` prints `0 FAIL`.
- The upgrade-and-rollback gate names a previous release's own immutable
  manifest defect instead of failing the current release on it: v3.2.0 shipped
  `CHECKSUMS` entries that do not match its own tree for
  `docs/book/assets/mermaid.min.js` and `scripts/derive_refusal_table.py`. The
  gate records that as a named finding, and still hard-fails on any HEAD
  manifest defect, on a rollback that does not reproduce a fresh install of the
  previous tag, and on any previous-tag defect that is not that exact pinned
  pair: `sh scripts/test-upgrade-rollback.sh`.

The "Python 3.9 or newer" promise is measured instead of assumed, and the
Windows shell step can be run by the shell Windows actually ships.

"3.9 or newer" was half a claim. The floor was tested and blocking; the newer
half had a `3.x` matrix leg carrying `continue-on-error`, on a workflow that
fires only on manual dispatch, so no modern interpreter had run these suites
and nothing could have failed a merge if one broke. The 67 suites were run on
3.9.23, 3.13.7 and 3.14.0rc2. The failure set is identical on all three, so
the claim holds as far as 3.14; `TESTERS.md` carries the table and names
3.14.2 as still unmeasured rather than implying coverage. The one
interpreter-specific defect the sweep found was in a test, not the product:
`tools/test_sbe_evidence.py` compared a receipt's recorded interpreter against
a hand-built `major.minor.micro` triple, which equals
`platform.python_version()` on every final release and differs on every
pre-release, so the suite could not run on a release candidate at all. The
matrix gains a pinned, blocking 3.13 leg, because a promise no gate can fail
on is not a gate.

`docs/WINDOWS-CHECK.md` step 2 named three shells and handed all three one
`bash` line. That line is a parse error in Windows PowerShell 5.1, where `||`
is a 7.0 pipeline chain operator, and in `cmd.exe` the `;` is not a separator,
so the line echoed and nothing ran. Two of the three shells it named could
never execute it, including the one Windows installs by default. There is now
a block per shell, each in that shell's own language, and the PowerShell block
prints `$PSVersionTable` because 5.1 and 7 disagree about operators, about
`where`, and about encoding. 5.1 is stated as the baseline in `README.md` and
`TESTERS.md`; testers are asked not to install 7 on the project's account.

`sbe doctor`'s platform row read `$SHELL or %COMSPEC%`. COMSPEC is set on every
Windows machine whatever shell you are in, so that row told every PowerShell
tester they were in `cmd.exe`. It is the row every other row is read against,
which made it worse than an absent value. It now reads the parent process
through the ToolHelp snapshot API and names `powershell.exe` (5.1 or earlier)
apart from `pwsh.exe` (6 or later), and says "unknown" with the chain it walked
when it cannot tell, rather than naming a shell nobody measured.

Proved by `python3 tools/test_sbe_windows_sim.py` (25 tests, 12 of them new:
the shell mapping, a pin that COMSPEC can never reach the report, and four that
read the protocol's own fenced blocks and fail on a PowerShell-7-only operator
or a `;` in the cmd block), `python3 tools/test_sbe_evidence.py` on 3.9.23 and
3.14.0rc2, and the 67-suite sweep on all three interpreters.

## 3.2.1

The gate battery passes again, and the report channel one class of forgery
used is closed in the two files that had reopened it.

Five of the fifty-two gate commands were failing, all on code that arrived with
the August squash and had therefore never been through the battery. Nine report
lines in `sbe_testkit.py` and one in `sbe_gate.py` printed interpolated
filesystem paths without passing through the `one_line()` choke point, which is
the channel a directory name carrying a newline or a cursor escape uses to
write a verdict line of its own; they route through `say()` now, and
`unflattened_report_prints()` reports zero. Five helper functions returning
2-tuples are declared as the non-verdicts they are, one of them (`add_row`)
with its reason stated separately rather than filed among the others, because
it genuinely does return the word PASS: that naming collision is recorded as
owed rather than quietly accepted. The `artifacts` check's own worked example
could not pass its own check, its purpose fragment designing a refund system
while its behaviour fragment designed a checkout, and the check's rule is that
an artifact sharing no named subject with a sibling is filler rather than an
artifact; the behaviour fragment designs refunds now, so the worked example is
a real one. The booklet and the sandbox guide are realigned by their own
regenerators, `tools/regen_sandbox_guide.py` for the second, per the standing
rule that no shipped document pins a value no command can regenerate.

Proved by `python3 evals/test_no_data_class.py`, `python3
tools/test_sbe_sandbox.py`, `python3 bin/sbe book --check --strict` and
`python3 tools/sbe_release_invariant.py --strict`, each exiting 0.

## 3.2.0

The two-machine handover works. A prepared record bound itself to the current
commit, so committing the record moved the commit past its own binding and the
receiver's clone read it as stale, which made the feature impossible to use the
only way git has of sharing anything. A commit whose entire diff is the record
and its lock is now read as the record announcing itself rather than the code
moving. A commit that bundles the record with anything else still stales it,
unchanged, and that guard is covered by its own test.

Also in this release: this suite's own subprocess helper is declared as the
non-verdict it is, so the honesty sweep can prove no check passes over evidence
it never examined.

## 3.1.0

### The guidance layer: help arrives without anything being started

The adoption complaint this release answers, in the words it was reported in:
people do not know how to use this, how it sits beside the orchestration
product, or how to deliver a task with it, and they expect guidance to arrive
the way other assistant frameworks give it, without starting anything. The
machinery to fire without a command already existed, because skills carry
description-based triggers. What was missing is that every description was
written in this product's own vocabulary and ended by naming a command, so
none of them matched the situation a person was actually in. The rule this
release is judged on: a person who has started nothing, knows no commands, and
has never read a page still receives the right next step in their own words,
in the session they were already in.

**Four role cards and four delivery pages, executed rather than described.**
One printable card per technical lane (warehouse and data, backend and
integration, test evidence, analysis and specification) under `docs/cards/`,
and one page per lane under `docs/deliver/` answering the question that was
asked by name: from "I have this task" to "here is what I hand over". Every
command and every line of output on a card was run in a sandbox clone by the
agent that wrote it, and re-verified before the fold.

**Two reading paths, so the enterprise material is opt-in.** A reader who
wants delivery speed never meets the governance vocabulary, and a reader who
needs governance meets it early. Proven by the link sweep, which requires the
delivery path to reach no page carrying the enterprise vocabulary, a grep that
must return empty.

**The reviewer packet is a file, not a link.** An artifact that requires an
account cannot be forwarded as somebody's own work, so one documented command
now produces a self-contained file with zero external references, calibrated
by injecting a reference and watching the check go red.

**The receipt schema defect is closed, and the last home-directory vault leak
with it.** Both were found by controls rather than by reading.

**Why the version moves.** Nine distributable files changed since 3.0.0. The
release invariant refuses that range while `VERSION` stands still, because a
marketplace user is offered an update only when the version string changes, so
those bytes would otherwise ship as if unchanged. Proven by
`tools/sbe_release_invariant.py --strict` and the version bump fixtures, which
require every declaration site to move in one command.

## 3.0.0

### V3: the refocused product, shipped on the journeys, not on green gates alone

**Why the number jumps from the 1.0.0-rc line.** The founder ratified the
generation naming on 2026-08-11: v1 was the first design, v2 the restructure,
V3 is the refocused product (the assurance layer that owns five things and
borrows execution; see docs/DIRECTION.md). The release condition he also
ratified the same evening: a stable number ships only when the four user
journeys pass when walked. They were walked twice tonight: three discovery
walks by stranger agents on clean clones, then a re-walk of all four on this
release candidate, verdict PASS on each, with the receipt persisted.

**A repo-scoped run mints only what lives inside the repo.** Discovery walks
found a dossier verify minting vault-telemetry decision packages into the
change dossier with `BROTHERSBE_VAULT` set, and tools probing a hardcoded
home-directory default with it unset. Now an unset vault resolves to an
explicit sentinel, nothing builds the home path, and verify, gate, and score
mint decision packages only for checks whose declared evidence resolves
inside the directory under check, stating what was excluded. Proven by
`tools/test_sbe_vault_scope.py` (5 tests, the two discriminating ones red
before the fix) and re-forced on the release candidate: a fixture vault
producing three real FAILs still minted zero packages into the dossier.

**The one-pager binds a receipt by content, and its marker is honest.** The
documented `evidence run --out` flow read as unverified because the renderer
treated `evidenceId` as a filename while the status tool binds it to the
receipt's `runId`. Binding is now exact runId equality first, with ambiguity
reported rather than guessed, and the marker is an object because the honesty
sweep refused the tuple shape the night it landed. Proven by
`tools/test_sbe_onepager.py` (18 tests), `tools/test_sbe_report.py` (10), and
`evals/test_no_data_class.py` (4024 scenarios, 0 failures); the re-walk's
independent Python count matched the rendered SUMMARY exactly.

**A3, the reporter that cannot fail a build.** A reporting-only entry point
(`tools/sbe_report.py`) renders the reviewer packet through the A1 renderer
by import and exits zero always, including on its own crash, where it prints
an honest could-not-report line. The forced test makes a deliberately broken
change produce a report naming the defect while the run exits zero; probed on
good, empty, and corrupt targets at exit 0 each time. The CI fragment a
consumer pastes is in docs/ci-cd.md.

**--cwd is accepted and honoured by every command.** Declared once (new
`src/brothersbe/cwd.py`), honoured everywhere, with a parser-enumerated suite
(`tools/test_sbe_cwd.py`) so a future command cannot dodge it. The first
calibration probe could not fail and was replaced; the rewritten test went
red on the re-injected defect.

**The skipped intake is named, the None is gone.** `review-route` without a
dossier still derives its tier from the diff but now names the intake files
it did not read; `impact` with no dossier explains that no intake path exists
instead of leaking a Python `None`. Both defect tests ran red first
(`tools/test_sbe_journey3_followups.py`).

**The V3 surface.** README leads with the identity and tagline;
docs/DIRECTION.md carries the distilled, roles-only direction; two estate
onboarding one-pagers (docs/adoption/) bind every claim to a receipt; four
getting-started frictions a stranger hit are fixed. The tranche plan with
per-loop done-checks is docs/specs/2026-08-11-v3-refocus-and-release-plan.md.

**Known and stated, not hidden.** `tools/sbe_coldstart.py` still carries its
own home-directory vault default (same class as the fixed leak, queued by
name); the vault rebinding from machine env to per-repo declaration is the
week tranche's work; benchmark rows remain UNRUN by a named person, so no
comparative claim ships.

## 1.0.0-rc.39

### The delivery of 2026-08-11: trust fixes proven, a detector that has been seen firing, and a first measured baseline

**The apply_patch guard is now reachable, and it holds.** An adversarial
review of the shared-parser fix found the shipped PreToolUse matcher never
ran either hook for apply_patch at all, plus three fail-open paths beside
it. All closed with forced tests, including the two-step where a worker
rewrites the task registry to grant itself an authority file: the registry
and every grant file are now surfaced through the same control-plane list
the Bash guard enforces. Proven by `python3 tools/test_sbe_authority_hook.py`
(56 tests) and `tools/test_sbe_fence_hook.py` (68 tests, including the
matcher drift test).

**The stall detector v2 ships only because every condition was seen firing.**
Nine conditions, layered liveness (heartbeat ledger first, process table
second, the answerer named), report-only with the exact remediation in every
alert line. `python3 tools/test_stall_detector.py` forces all nine live and
asserts the healthy fixture silent (19 tests). The Windows CI leg became the
non-blocking discovery leg it was built as, by the founder's ratified
deferral.

**The first cold-start baseline exists.** Five of five headless runs
completed a governed change; thresholds are declared beside the baseline and
`coldstart-thresholds` reports PASS. The first attempt died five times on a
flag requirement the CLI had grown, recorded honestly in the receipt, and
the context-tax metric read a suspiciously perfect zero, so it carries no
threshold and an investigation instead. LEARNED.md seeds twelve deduplicated
failure classes, each naming its enforcement point or wearing UNENFORCED
visibly.

## 1.0.0-rc.38

### One main branch, and a leak detector that stopped crying wolf

**The branch fold turned out to be mostly deletion.** Two of the three branches
still open carried zero unique content: `loop-g/runtimes` and
`feat/br-1009-watchdog` were verified byte-identical to `main` for every file
their unique commits touched, their work having already landed through earlier
pull requests. Only `loop8/simplify` had anything new, a dead-import cleanup
across seven files. Proven by `git diff origin/main origin/<branch> -- <files>`
returning empty, which is a content test rather than a filename test. Every
branch tip was archive-tagged and pushed before anything moved: 21 archive tags
became 26.

**A four-character private name matched the word "hurry".** The leak detector
reported nine tracked files as holding a client name. Nothing had leaked: 19 of
the 21 occurrences were the ordinary English word, and the other two were a
fragment of a minified CSS identifier. The boundary rule that would have caught
this already existed, scoped to one vendored minified file because that is where
the first collision happened to be seen. The cause is the NAME being short, not
the file being minified, so boundary matching now follows name length instead of
file kind.

The narrowing this buys is stated in the source rather than buried: a short name
deliberately glued inside a longer word is no longer caught in ordinary files.
That is real lost coverage, accepted because a detector that fires on a common
word teaches people to ignore it, and the threshold is a judgement rather than a
measurement. Proven by three calibration tests that pin both halves, including
the false positive itself: `python3 tools/test_sbe.py TestNoPrivateNameShips`
reports `Ran 7 tests OK` with no skips, meaning the real list was scanned.

**The field book cover no longer reads as abandoned.** It said it described
rc.28 on a tree at rc.37, which to an outside reader looks like a document
nobody maintains. The field is in fact a deliberate human declaration of when
the prose was last read, so bumping it automatically would manufacture the claim
that somebody read it. The label changed instead, to "Prose last read by a human
at", and the parser accepts both spellings so an older booklet still parses.
`python3 bin/sbe book --check --strict` reports `7 section verdict(s), 0 FAIL`.

**A regeneration command for the one value the sandbox guide pins.**
`docs/guides/00-sandbox.md` quotes the git head its decision record binds to,
and the sandbox suite asserts the guide quotes the head a live run computes.
That assertion proves the sandbox build still produces the bytes the page
claims, so masking it would delete the property being proven. Until now the only
way to realign it was to paste fresh hashes by hand, which breaks again on the
next commit that moves the sandbox inputs.
`tools/regen_sandbox_guide.py` does it instead, rewriting only the twelve hex
characters after the literal "bound to head " and nothing else, with `--check`
reporting drift without writing and a guide carrying no pinned head exiting as
NO-DATA rather than as a pass.

Recorded because the theory it disproves was written down: the value was never
non-deterministic. Two consecutive runs on an unchanged tree both produced the
same head. The guide was simply stale in both slots.

## 1.0.0-rc.30

### Windows reaches the middle of its own suite, and one simulation that could not be built there

The `gates-windows` leg has been red since rc.19. It now clears every step through 21, having previously stopped at 12, and fails further down at the converge fixtures, which no run had ever reached before.

**The book fixtures pass on Windows.** The one failing test there simulated a bare machine by stripping PATH down to `/usr/bin:/bin`, the canonical bare POSIX box: stripped of anything providing the Claude CLI or a vault, yet still holding the shell and tools the undeclared chapters need in order to run and match. A host without those directories cannot be put in that state at all, so every chapter failed for want of a shell rather than for want of the declared capability, and the run reported all 136 blocks as differing in 1.28 seconds against roughly eleven where the scenario is real. It now skips there and says why, decided by asking the filesystem for those directories rather than by naming a platform.

**A correction, recorded because it was nearly shipped.** The first diagnosis of that failure was that the book's transcripts cannot run on Windows at all, and a capability was added to skip fifteen chapters there, with a matching entry in `docs/KNOWN-LIMITS.md`. Both were wrong and both are reverted. The same Windows job passes step 10, the regression evals, which contains the same book comparison run with a real PATH: those transcripts are verified on Windows and always have been. The evidence that refuted the diagnosis was in the same job, four steps earlier, and green.

**The book replay no longer writes its own script in text mode.** It generates a shell script and hands it to bash; the default newline handling would translate every line ending on a platform that does so, putting a stray character inside the arguments of every command. That is a corruption of what the commands were told to do rather than of how their answers were read, so no amount of normalising the captured output could have caught it. Pinned to a bare newline, which is what POSIX already produced.

**A throwaway path is out of three shipped chapters.** Regenerating book blocks from live output records whatever machine the tool ran on, and an earlier regeneration had baked a temporary worktree name into `04-install-day.md`, `10-the-vault-and-memory.md` and `15-the-platform-lead-deep-dive.md`. Readers were being shown a scratch directory that no longer exists as though it were an installation path.
### Converge could never converge on Windows, because one path was compared in two spellings

`converge.py` built its dossier path with `os.path.relpath`, which answers
`design\chg` on Windows, and then compared it against git diff output, which is
forward-slash spelled on every platform. The two spellings never matched, so
every edit to a dossier artifact fell through into "unplanned" and SCOPE read
REVIEW-REQUIRED forever, on every range, on that platform only.

The path is now canonicalized once at the point it is produced, which is the
rule `status.py:302` already stated at length and which six sibling call sites
already followed. This was the one missed instance.

This is the tenth defect of one shape in this repository: a path treated as
syntax rather than as data. The family is named in the mistakes record so the
eleventh is looked for rather than discovered.

What proves it, stated honestly after an independent refuter killed the first
version of this sentence. `os.sep` is `/` on POSIX, so `.replace(os.sep, "/")`
is a no-op there and `startswith(x + os.sep)` and `startswith(x + "/")` are the
same expression. The refuter restored the old file into a clean tree and ran
`python3 tools/test_sbe_converge.py`: 15 tests, OK, exit 0. **The suite cannot
tell the two versions apart on any POSIX host, and no Windows run in this
repository has ever gone green.**

So this fix is READ FROM THE DEFECT, not proved by a watched red: the two
spellings are shown to differ by inspection of `converge.py:233` against
git's always-forward-slash output, and by the six sibling call sites that
already carried the normalisation. It is UNVERIFIED on the platform it
repairs, and it stays that way until a Windows run is green.

### The book stopped telling readers to look inside a worktree that no longer exists

Three chapters shipped an installation path of
`.../.claude/worktrees/integrate-loops12`, five occurrences across
`04-install-day.md`, `10-the-vault-and-memory.md` and
`15-the-platform-lead-deep-dive.md`. That directory does not exist. It was
captured when the book regenerator was run from a temporary worktree, and a
guard was added afterwards so the regenerator now refuses to run from a
temporary location, but the content already in main was never recaptured. The
guard stopped the next occurrence and left the existing one in place.

Recaptured here from a live run in the real repository root, which is the only
place that mode is allowed to run. Proved by `git grep integrate-loops12
-- docs/book/`, which returns five hits on the previous commit and none here.

## 1.0.0-rc.29

### The write boundary did not bind on Windows, and the board hid what it could not read

Two findings from independent review, both reproduced before they were believed.

**The fence hook split its registry list on a literal colon.** A Windows path begins with a drive letter and a colon, so `C:\work\STATE.md` split into `C` and `\work\STATE.md`, and the registry was never opened. The consequence was not a crash, which is what makes it serious: the hook found no fence and ALLOWED the write, so the one-writer-per-file boundary silently failed to bind for any registry named through `BROTHERSBE_REGISTRIES` on that platform. `tools/sbe_score.py` already used `os.pathsep` correctly, so this was drift from a pattern the repository already had, in two of the three tools that read that variable. All three now agree, which the tests require of them, and the ninth defect of the class this project keeps finding: a path read as syntax rather than as data.

**The program board discarded two of three kinds of parse error.** A clean-room architecture review blocked the release over this, and it was right to. The board named work-item errors and threw away source and dependency errors, so a work-items directory that could not be read rendered as a program with nothing in it: zero of zero items measured, exit zero, no trace of why, while `sbe program status` reported the same fact correctly. An unreadable program presented as an empty one, on the surface that exists to stop exactly that. The board now reports every parse error it is given rather than a named subset, which also means a kind added later is reported by default instead of silently dropped. Both cases are pinned by tests calibrated against the old filter.

**The profile module work landed under its SPLIT verdict.** Both of its known Majors were re-verified by putting each defect back and watching the test fail: deleting the module-enforcement check makes the merge-path test name the loss, and re-gating the update notice behind the release module makes the unconditional-notice test fail on both halves. The hunks carrying stale counts were discarded rather than applied, and the one sentence in `SKILL.md` that quotes lint numbers was regenerated from a live run instead of from the patch, which turned out to be wrong before and after: it read 90 clean files where the tree has 92.

### SECURITY: the install verifier certified a planted backdoor, and a filename was the exploit

An adversarial security review found this and reproduced it before anyone believed it. It is the most serious defect this project has shipped.

`scripts/verify-install.sh` asked whether each walked file was named in the manifest by passing the filename to `grep` as a bare operand. A filename that begins with a dash is therefore parsed as an OPTION. The option then consumed the manifest-paths filename as its own argument, which left `grep` with no file to read, so `grep` read STDIN instead. Stdin, at that moment, is the walk output the enclosing loop is still consuming. Every remaining entry was swallowed and never checked.

Reproduced here before the fix, on a tree containing an undeclared file named `-v` and an undeclared `skills/backdoor.py`:

```
verify-install: 2 file(s) match, 0 mismatched, 0 missing, 0 extra
verify-install: PASSED. Every file the manifest names matches on disk,
EXIT=0
```

The one tool whose entire job is telling you whether your installation has been tampered with certified a tampered installation, and did it silently. After the fix, the same tree reports `EXTRA: -v`, `EXTRA: skills/backdoor.py`, `2 extra`, `FAILED`, exit 1. The fix names the pattern with `-e`, ends option parsing with `--`, and denies stdin outright so a future edit reintroducing the operand mistake cannot steal the walk again. The same shape was swept and fixed in `scripts/checksums.sh`, where the input is maintainer-controlled and the risk was hardening rather than a live hole.

This is the eighth defect of one class in this repository: a path read as syntax rather than as data. The first seven were regex, glob, escaping and spelling. This one was option syntax. It is pinned now by the eval `a-planted-file-named-like-an-option-is-still-reported`, calibrated by restoring the operand form and watching it report that the walk was truncated.

### Executable configuration is reported without accusing a clean installation

The same review planted a `.claude/settings.json` declaring a SessionStart hook, which the harness runs on every session, and the verifier counted it as "0 of them source code" and PASSED. The gap was real: it is the highest-value execution vector on this platform and it was invisible.

The obvious repair, adding `*.json` to the source list, was tried and reverted, because it turned every healthy installation red. A `.claude/settings.json` is ordinary state a correct install is supposed to have, and this script has already shipped four separate defects that told a clean installation it looked compromised. Trading a fifth for this would have been a bad bargain.

Configuration inside an excluded path is now counted and named on its own line, and does not by itself decide the verdict. That is this project's own vocabulary applied honestly: the file's presence is expected, its content is something this script does not read, and neither a pass nor a block follows from evidence that thin. The residual risk is written into the known-limits page rather than settled by a verdict it cannot support.

### Windows says what it can actually enforce, and one shipped sentence stops overpromising

Windows reached the tool tests for the first time in this project's history, having never before got past the honesty meta-test, and reported four failures out of 108. Three distinct defects, all reproduced here by simulating the platform rather than waiting for a runner.

**A shipped sentence was false on Windows, and that is the worst of the three.** `data-export` printed that it had written a file `(owner-only)`. The writes ask for mode `0o600`, but on Windows that argument sets only a read-only attribute and writes no access list, so the file inherits whatever the parent directory allows, while carrying redacted but still sensitive session context. The sentence now reports the mode the platform actually gave and says plainly that it does not promise enforcement where POSIX modes are ignored, reusing wording this repository already had in `tools/sbe_autosave.sh`. The same overclaim was corrected in the command help, in the stored inventory that `data-show` prints, and in the export's own JSON. `docs/KNOWN-LIMITS.md` now carries the limitation, names the four writing sites, and records the rejected alternative: setting a real access list needs pywin32 or icacls, a new dependency with its own failure modes in a project that ships none.

**The eighth defect of a shape that has now produced eight.** `DEFAULT_EVIDENCE_DIR` is the string `.sbe/evidence`, and joining it with the platform separator produced `...\.sbe/evidence` on Windows, a mixed path that was then serialized into a JSON field. A path was again treated as syntax rather than as data. The correct idiom already existed three files away, so this was drift rather than an unknown: a single `tasks.evidence_dir(root)` helper now owns the join and eight call sites route through it. The rest of the sweep was checked one site at a time and deliberately left alone, because a join that only ever opens a file is fine on Windows; the ones that were fixed are those whose result gets compared, serialized, or printed.

**A path spelling no native reader could resolve.** The recovery command asks the Git-for-Windows shell for a temporary directory and gets back an MSYS spelling like `/tmp/tmp.XXXXXXXX`. Git understands it, native Python does not, and resolves the leading slash against the current drive, so the command printed a directory a Windows user could not change into. It now emits the native spelling through `cygpath -w`, falling back to the original everywhere else. Fixing it surfaced a second bug in the same line: `echo` was eating the backslash in `\Temp`, so the line is a `printf` now.

**The permission tests were made platform-aware rather than skipped.** Asserting `0o666` on Windows would have pinned a platform accident, and skipping would have removed a real check. POSIX keeps the exact `0o600` assertion; Windows asserts the guarantee that is actually reachable, which is that the write went through the owner-only path and that the message printed claims nothing more.

### Deleting ambiguity: one duplicated judgement, one decorative version claim, one artifact deliberately kept

The approval judgement in `src/brothersbe/status.py` existed twice, and the two copies had already drifted: one remedy ended in `(MISSING APPROVAL)` and the other did not, one recorded a full path and the other a bare filename. The inline copy is gone and both callers now share one function. No test pinned either spelling, which is its own finding.

The explainer page carried a hand-written version string that nothing updates and that had been wrong for twenty-seven releases. It was decoration rather than a declaration, so the claim was deleted instead of being wired into the bump command.

`skills/help/map-template.html` was proposed for deletion as dead and is deliberately RETAINED. Two independent reasons: a live test class guards it, and `src/brothersbe/mapgen.py` states in its own docstring that it does not supersede the template, because the template's richer slots need dossier content that the generator deliberately leaves out of scope. An artifact that a shipped module names as intentionally not-yet-replaced is not dead code.

Also recorded rather than changed: `.brothersbe/config.json` is tracked and carries a `toolVersion` that goes stale every release. Nothing reads that field, including the `project-init` doctor check, which tests only that the file exists.

### The freshness gate was blind in the one place it runs, plus the Windows fixture writer and the install checker's last two path defects

**The most serious finding first: the manifest freshness gate could not fail on a clean checkout.** Two releases ago this gate was fixed so that a path added to or removed from the tracked set could no longer be forgiven by its race filter. The other half of the same filter was left as it was, and it was worse. For a path whose hash disagreed with the manifest, the filter re-read the working-tree file against the COMMITTED GIT BLOB and forgave the path when those two matched. After any commit those two are identical by construction, so a genuinely stale manifest always re-read as identical, was classified transient, and was forgiven. CI checks out a clean, committed tree, which means this gate has never been able to fail in the place it exists to protect.

Reproduced before the fix, on a committed tree with nothing uncommitted: `CHECKSUMS.sha256` recorded `4c8b2430...` for `README.md` while the file hashed to `0d75ef2c...`, and the check printed `The manifest matches`.

The fix makes the second opinion ask the question the drift was found on. Drift is detected by hashing the working tree and comparing against the hashes the manifest records, so the re-read now re-hashes the file and compares it against that same manifest hash. The race filter survives, because that was never the part that was wrong: on a host where a file read immediately after checkout differs from the same file read a moment later, the second hash lands on the manifest's value and the path is still correctly forgiven. What can no longer happen is forgiving a file whose bytes do not match what shipped.

Calibrated in an isolated clone, both directions. A clean tree with a fresh manifest still reports `matches`. A clean, committed tree with one tracked byte changed and the manifest deliberately not regenerated now reports `the tracked manifest is stale for: README.md`, where the previous code reported `matches`.

This is the third time a check in this repository has been found reporting PASS over evidence contradicting it, and all three were the same shape: a filter written for a real edge case that answered a slightly different question than the one being asked.

### The Windows fixture writer, the install checker's last two path defects, and two lanes that graded themselves too kindly

**The Windows cause, fixed at the writer rather than the fixture.** `evals/test_no_data_class.py` wrote every fixture through `open(path, "w")`. Text mode on Windows turns each newline into two bytes, so the fixture on disk stopped matching the hash the registry pinned, and the freshness check in `tools/sbe_plan.py` failed. The writer now opens in binary and encodes to UTF-8, which cannot translate a newline on any platform. Calibrated by emulating the Windows translation and watching `FAIL sbe_plan.py freshness [full] want PASS got FAIL` appear, then restoring.

A pin in two halves came with it, in the shape this file already uses elsewhere. One half asserts the bytes on disk are exactly what was handed in. The other asserts the writer itself is still non-translating, so an edit back to text mode goes red on Linux and macOS too, instead of waiting for a Windows run to notice. The second half was calibrated separately: reverting to text mode leaves the freshness check green on macOS and still turns the pin red.

**The fifth and sixth install-checker defects, both of the same shape as the four before them.** `scripts/verify-install.sh` spliced the install root into `find -path` glob patterns, so a root containing `[` and `]` turned every exclusion into a character class and a documented exclusion came back as an added file. Both walks now run from inside the install root, which makes every pattern a literal string. Reproduced before and after on a root at `x[1]/inst`: one extra file and FAILED, then zero extra and PASSED.

The second: `find` emits its starting point as its own entry when that starting point is a symlink, and the relative-path strip never fired on an exact match, so the script flagged the install root as a planted non-regular entry. Its own comment scopes that rule to entries inside the tree, and `install.sh` says a symlinked clone is supported, so two shipped surfaces contradicted each other. Changing into the root before walking resolves it. A symlink planted inside the tree is still caught.

**The undo stops guessing which installation it is undoing.** `scripts/rollback-install.sh` let `--install-dir` replace the directory while version, commit and marketplace kept coming from whichever entry the harness record already held. Pointed at a directory the record did not name, it ran to completion and attached one installation's identity to another installation's bytes. It now refuses, naming both paths. A directory that does match a record adopts that record's own identity. The rejected alternative, reporting the identity as unknown and rolling back anyway, is recorded in the script's header: it removes the false claim but not the wrong-state action underneath it.

**`install.sh` was telling the truth.** Its claim that both sides of the self-install comparison resolve through `cd ... && pwd` was checked against lines 48 and 99 and found correct, so nothing changed. A claim that survives an audit is worth recording as clearly as one that does not.

**Two lanes graded themselves too kindly, and hostile review caught both.** The Windows lane reported `536 evals: 536 passed, 0 regressions`. An independent refuter re-ran it and got `535 passed, 1 regressions`, then found why: the lane had committed its own checkpoint scratch files, which the manifest did not name. A path present on one side only is exactly the drift this project fixed the freshness gate to stop forgiving two releases ago, so the gate was right and the lane's summary was wrong. The checkpoint files are untracked now and ignored.

The install lane's fixes all reproduced, but its new refusal removed a capability that nothing disclosed. That removal is now a decision in the script's header rather than a surprise, with two behavioral tests that were watched failing, printing the borrowed identity, before they were watched passing.

Neither correction came from the author of the work. That is the whole argument this project makes, applied to itself.

**Counts moved, so the documents that quote them moved in the same change.** Two new eval cases took the suite from 536 to 538, and six shipped documents quoting the old line were swept with it.

## 1.0.0-rc.28

### The benchmark harness reaches the merge path, and a law I had been overstating

37 benchmark tests shipped in rc.25 and not one of them ran in CI. Confirmed
rather than assumed, because two agents disagreed about it and one was wrong:

```
grep -c "benchmarks" .github/workflows/brothersbe-gates.yml
0
```

Among those 37 is the guard that stops the benchmark's own answers leaking into
the exam paper it grades. That guard has already been broken twice: once by
runbook examples naming real planted defect locations, and once by a scan that
only looked inside json-tagged fences, so the defect could return simply by
dropping the tag. A third break would have shipped in silence.

Both jobs run it now, inserted between the Authority and Book steps so the
file's own alphabetical ordering rule still holds.

**On the law.** This project's sessions had been treating
`.github/workflows/**` as human-edit-only, and deferring changes to the founder
on that basis. Reading L16 rather than recalling it shows the claim was wider
than the law:

> A session instruction never waives a hard gate: --strict changes only by a
> human editing the CI workflow, which is visible in the diff.

L16 protects a gate from being WEAKENED. It does not forbid strengthening one.
Adding a test step touches no `--strict` flag and waives nothing, so this was
never blocked. Overstating a law is the same failure this project refuses
everywhere else: a claim stronger than the thing that enforces it.

The diff is verified to be only the addition, measured against the previous
commit rather than against a remembered baseline:

```
--strict            before 16   after 16
continue-on-error   before  2   after  2    (both pre-existing; one is a comment saying it is never set)
total lines         before 506  after 510   (+4, the two steps)
```

**What this release does not claim.** That the step passes in CI. It passes
here (`Ran 37 tests ... OK`), and a step that has never run in CI can fail there
for reasons a local run cannot surface, on Windows most of all. The next run
settles it, and if it goes red that is information the harness was hiding
before, not a reason to revert.

## 1.0.0-rc.27

### A gate that reported PASS over a manifest it had just proven stale

Found by a hostile refuter reading PR 29's CI run, where this gate PRINTED the
three offending paths by name and then passed, in the same run where a different
gate failed on those same three files.

`the-tracked-manifest-matches-the-tree-it-ships-with` re-reads every drifted
path's committed blob against the working tree and forgives any that come back
byte-identical. That filter is correct, and it closes a documented Windows false
block: on that host a file read immediately after checkout can differ from the
same file read a moment later, which is a visibility race rather than a stale
manifest.

The defect was its SCOPE. The drift list was the union of both key sets, so it
conflated two unrelated failures. A path on both sides with different hashes is
a CONTENT change, where a race is genuinely possible. A path on only one side is
an ADDITION or a REMOVAL, where no amount of re-reading can ever exonerate the
manifest, because the bytes were never the question: the question was whether
the manifest names the same set of files the tree tracks. Those paths re-read as
identical every single time, so they were classed transient and forgiven.

The fix splits them. Only same-path hash mismatches are eligible for the
re-read; the set difference is seeded straight into persistent and can never be
forgiven. The race filter is kept, not deleted, so it still closes the Windows
false block, but it now only ever sees the failure it was written for.

**Proven by isolation, in a detached worktree whose only drift was two files
untracked while the manifest still named them, both measured from a baseline
that read `matches`:**

```
old code:    VERDICT: matches                                    <- passed over a stale manifest
fixed code:  VERDICT: the tracked manifest is stale for: STATE.md.bak-..., STATE.md.bak-...
```

The byte-level detail on those paths reads `identical bytes on this read`, which
is precisely the evidence that used to buy them a pass.

### A pin that could not fail, removed rather than shipped

A shape test was written to pin the fix cheaply on every leg. Its calibration
did not go red when the defect was re-injected, because the test's own source
contained the literals it searched for, so it was matching itself rather than
the function it audited. A test that cannot fail is the same defect class this
release fixes, one level up. It was deleted rather than repaired under time
pressure, and the behavioural proof above stands on its own.

### Also in this release

- `.gitignore` now covers `STATE.md.bak-*`. Two backups totalling 477 KB were
  tracked and shipped in the release manifest, and their own first line reads
  "gitignored, not shipped". They are untracked here, not deleted from disk.
- `docs/KNOWN-LIMITS.md` OWED-4 rewritten with evidence read from the Windows
  job rather than predicted: all four install-checker defects are green on
  `windows-latest` at rc.24, each eliminated by its own quoted log line, and the
  one remaining cause is named with its mechanism and its minimal fix.
- `program/MASTER-PLAN-2026-08-06.md` retracts a claim that the ten hostile
  scenarios were a hard release gate. No such gate was ever built; they are a
  manual tester checklist. The retraction deliberately does NOT replace it with
  a claim about eval coverage that has never been enumerated.

## 1.0.0-rc.26

### The benchmark lane blocked its own merge, and the policy engine was right

rc.25's consumer-checks leg went red with `verdict: BLOCKED` and two MISSING
requirements, `check:migration-rehearsal` and `check:migration-reconciliation`,
demanded of a change that touches no database at all.

The engine was not wrong. `benchmarks/defects.json` is the ground-truth file
that ENUMERATES the defects the harness scores against, so it necessarily
carries the DDL of the migration defect it plants. The `sql-ddl` content signal
read those added lines and concluded a schema change was being shipped.

This is the same shape `EXAMPLE_SURFACES` already existed to handle, and its own
comment says so: the tests that PROVE a migration is caught contain the
migration text, and the guides that TEACH the migration gate quote the DDL they
teach. The benchmark harness is a third instance of exactly that, and it was
simply missing from the list.

**What was deliberately NOT done.** No requirement was waived, no gate was
softened, no receipt was fabricated to satisfy a check that had no business
firing. `benchmarks/` was added to the surfaces where a content signal is held
back, which is a narrower change than any of those.

**The half that keeps this from being a bypass.** `EXAMPLE_SURFACES` holds back
only the INFERENCE FROM CONTENT that a file describes a change to production
state. It does not touch path globs. So a real `.sql` file parked under
`benchmarks/` is still caught by the rule's `**/*.sql` path match, and the new
eval asserts that directly rather than trusting the mechanism's docstring. An
exemption that quietly widened from holding back inference into disabling a rule
would be worse than the bug it closes.

Calibrated by removing the two benchmarks entries, which returns
`benchmarks/defects.json is not held back from content signals`, the exact red
the merge hit. The eval carries its own NO-DATA guard: if a refactor emptied the
surface list, every assertion above would be vacuously true, so the check
verifies the list actually contains what it claims to.

## 1.0.0-rc.25

### Comparative outcome benchmarks, with the ground truth kept out of the room

Point 4 of the external assessment asked for the same tasks given to
BrotherSBE, to unassisted work, and to a peer, measured on defects, time,
tokens, corrections, false blocks and reviewer findings. `benchmarks/` is that
harness: four scenarios (a migration, an API contract, a data pipeline, an
incident), a fixture repository carrying nine planted defects, a scorer, and a
report renderer.

The interesting part is what it took to make the one measure with real ground
truth trustworthy. This lane was refused twice.

**The first refusal: the answers were printed in the exam paper.** Every
scenario runbook's `findings.json` EXAMPLE named a real planted defect at its
real declared line. Since scoring matches on file plus line window and
deliberately never reads wording, an estate that pasted its own runbook's
example scored those defects FOUND. Four of nine planted defects were
unmissable by anyone who read their own instructions, which inflated
`defects_missed` for every row in the results table.

**The second refusal: the guard against that had a hole the exact size of the
defect.** The rewritten leak test only scanned fences tagged ```json, so the
same leak could be reintroduced simply by dropping the tag, with the test still
reporting clean. That was demonstrated rather than argued: the previous guard's
own bytes, run over a leak in an untagged fence, report `ok`.

The scan now reads each document WHOLE and depends on no code fence at all.
Three forms count as naming a location: a `file` key within 200 brace-free
characters of a `line` key in either order, the `path:line` form, and a path
within 60 characters of the words `line N` on one line in either order. The
planted set is read out of `defects.json` and expanded over each defect's
declared window, never hardcoded, so the guard cannot drift away from what it
guards. Twelve evasion shapes were probed and caught; four clean shapes stayed
clean.

Three NO-DATA guards sit under it, each with its own sentence: zero planted
locations, zero documents scanned, or zero pairs extracted all report that the
scan vouched for nothing rather than reading clean.

Calibrated, and independently repeated before this was merged: injecting the
original leak into an UNTAGGED fence turns the suite red naming the document,
the location, the defect id and the form it was carried in, while the count of
```json fences stays unchanged so the tag is never the tell. Restoring returns
a byte-identical fixture and green.

`benchmarks/README.md` previously claimed the scan covered prose, which was
broader than the code. It now states the three actual forms with their actual
bounds, and says plainly that the JSON-example parse check does still read
```json fences, rather than papering over the one place a fence still matters.
A README that overstates a guard is the same failure class the lane was refused
for, one level up.

### Known limit, stated rather than implied

The guard is a text scan. It is proof against the spellings above, not against
every conceivable one: a path broken across two lines, a line number written as
a word, or an encoded pair would pass it. Closing that needs the fixture line
CONTENTS rather than their coordinates, which is a larger change than this lane
authorised.

`benchmarks/test_sbe_bench.py` is reached through the existing gates, so this
change is covered by the shipped suite.

## 1.0.0-rc.24

### The fourth defect, and the claim that nearly shipped instead of a fix

rc.23 shipped a sentence saying the remaining Windows cause was "not attempted,
because a POSIX host cannot test it". That sentence was wrong twice. It asserted
a cause that had never been reproduced, and it declared untestable something
that had been tested by simulation an hour earlier, in this same file, when a
Linux-only escaping bug was proven on macOS with a stub on PATH. It was caught
by an instruction-drift check rather than by its author, which is worth
recording: the failure was not a missing capability, it was stopping in front of
one that had already been demonstrated.

It was testable. It is now fixed.

**The bug was two DERIVATIONS of one value, not a bad comparison.** A walked
file got its relative path by stripping the install root exactly as the caller
spelled it, because that is what `find` echoes back. The manifest got its
relative path through `cd ... && pwd`, which answers in the shell's own
spelling. Where those two disagree, the manifest fails to recognise itself, is
walked, is not matched, and is reported as an added file. A clean installation
reads `1 extra` and FAILED.

Reproduced on POSIX by handing the script a root containing a `.` segment, which
`find` echoes verbatim while `pwd` normalises away:

```
EXTRA:     CHECKSUMS.sha256
verify-install: 2 file(s) match, 0 mismatched, 0 missing, 1 extra
verify-install: FAILED.
```

`0 missing, 1 extra` is exactly the signature the windows-latest leg reported.
On Windows the disagreement is guaranteed rather than incidental, because the
Git for Windows shell answers `pwd` as `/d/a/BrotherSBE` for a root passed as
`D:\a\BrotherSBE`, and two spellings of one directory never compare equal.

The fix canonicalises the install root ONCE, before anything derives a path from
it, so walked paths and the manifest path can no longer drift apart. That fixes
Windows by construction rather than by guessing at MSYS specifics, and it also
fixes every POSIX caller who passes a root with a `.` segment, a trailing slash,
or a symlinked parent.

Canonicalising means the script now depends on being able to ENTER the root, so
that failure path got its own guard and its own eval: an unreachable root exits
2 saying nothing was checked, and never prints PASSED over a tree it could not
open. NO-DATA is never a pass, including in a path this release introduced.

### Four defects, one shape

A CRLF manifest line, a path spliced into a sed regular expression, a filename
escaped by the hashing tool, and two spellings of one root. Every one was a path
being read as syntax rather than as data. Every one made the tool whose entire
job is detecting tampering tell a correct installation that it looked
compromised. The shape was the finding, and looking for the shape is what turned
up the third and the fourth.

### What the legs say

rc.23 closed the Linux failure: both ubuntu legs pass, confirming the coreutils
escaping diagnosis. macOS passes. Windows remains red and remains a non-required
check, and whether this release closes it is the one thing here that only a
`windows-latest` run can settle.

## 1.0.0-rc.23

### The prediction rc.22 wrote down, and what Windows did to it

rc.22 fixed one cause and predicted, in writing, that it would close both
Windows regressions. Run 31182895590 falsified that. Recording it here rather
than quietly restating the claim, because a prediction that only gets mentioned
again when it was right is not a prediction.

What actually happened, per leg:

- **macOS, both Python versions: green.** First time in this series.
- **Linux, both Python versions: one regression**, and it was the new eval rc.22
  added. A genuinely different cause, described below, now fixed.
- **Windows: still red**, and the fix moved the numbers without closing them.
  The new eval reports `0 missing, 1 extra` there against 3 accused files
  without the fix, so the walked files strip correctly now and exactly one entry
  does not: the manifest itself. That last one is a path-FORM mismatch. The Git
  for Windows shell answers `pwd` in MSYS form while the caller supplies the
  install root in Windows-native form, and two spellings of one directory never
  compare equal however carefully you strip a prefix.

### The third instance of one shape

GNU coreutils escapes a filename containing a backslash or a newline: it doubles
the backslashes and prefixes the whole output line with one. That shifts the
hash a column right, so `cut -c1-64` returned a backslash glued to 63 hex digits
and every such file reported MISMATCH. Apple's and BSD's tools do not escape,
which is exactly why every macOS leg was green while both Linux legs were red,
and why this could not be found by running anything on the machine it was
written on.

`verify-install.sh` and `checksums.sh` now both feed the file on STDIN, so the
name never enters the tool's output and no escaping rule can apply. Both were
changed together deliberately: a generator that escapes and a checker that does
not would produce manifests that disagree by platform, which is worse than
either bug alone. The regenerated manifest is byte-identical for this tree
except for the two scripts that were edited, so the change is behaviour
preserving here and load bearing elsewhere.

The mechanism was proven on macOS rather than assumed, with a stub on PATH that
reproduces GNU's escaping. Under it the old argument form reports `0 file(s)
match, 2 mismatched` and FAILED; the stdin form reports `2 file(s) match, 0
mismatched, 0 missing, 0 extra` and PASSED.

A CRLF manifest line, a path spliced into a sed regular expression, and a
filename escaped by the hashing tool. Three defects, one shape: a path read as
syntax rather than as data, each one making this script tell a correct
installation that it looked compromised. The shape is the finding.

### A failure message that named nothing

The Linux failure read `a clean tree was accused: 0 missing, 0 extra (exit 1)`.
Nothing missing, nothing extra, and a failure: the message omitted the one field
the failure lived in, because it reported only missing and extra while the real
signature was two MISMATCHED files. It now reports mismatched as well. This is
the same flaw this release criticises in the older message whose "0 path(s)" is
a hardcoded phrase rather than a measurement, and it cost an investigation
before it was noticed.

### Guarded so it cannot return

`no-checksum-tool-is-handed-a-filename-it-would-escape` asserts the shape of
both scripts rather than their output, because the defect is invisible to any
macOS run. It carries its own NO-DATA guard: if a rewrite removed the hashing
entirely, the check says it found nothing to vouch for instead of passing over
an empty set. Calibrated by restoring `sha256sum "$1"` in either script, which
turns it red naming that script and that line.

## 1.0.0-rc.22

### The install checker stops accusing clean installations, for the second time

The `gates-windows` leg has been red since rc.19 with what this repository
recorded as "one Windows eval failure, unidentified". Both halves of that
sentence were wrong. The run reports TWO regressions, and the log naming them
was readable the whole time; nobody had read it back. It has now been read, and
every theory the KNOWN-LIMITS entry used to list was wrong.

One cause explains both, and it is not really about Windows. `verify-install.sh`
removed the install root from each walked path with `sed "s|^$TARGET/||"`,
which splices the install path into a REGULAR EXPRESSION. Every metacharacter
in that path changes what the pattern means. Windows supplies backslashes in
every path, so the strip silently failed there: each walked file kept its
absolute path, matched no entry in the manifest, and the script told the reader
their clean installation contained files "exactly the shape of a planted
backdoor". Three files existed in that fixture and all three were accused.

That is the same false accusation the carriage-return fix removed in rc.20,
reached by a different route. The repeat is the finding, not the backslash:
both bugs were a path being read as syntax rather than as data, and this
script's loudest sentence fired twice over installations where nothing at all
was wrong.

A backslash is legal in a POSIX directory name, so the defect was reproduced
here rather than reasoned about: a target of `a\runner\inst` produced `2 extra`
and FAILED, with the mangled prefix visible in the script's own output, which
printed the path back as `aunner\inst`. All three sed sites now use
`${var#"$TARGET/"}`, POSIX parameter expansion with a quoted and therefore
literal pattern, so no character in a path can be read as syntax. After the fix
the same tree reports `2 file(s) match, 0 mismatched, 0 missing, 0 extra` and
PASSED.

The new eval `a-backslash-in-the-install-path-does-not-accuse-a-clean-tree`
runs on every leg. It was calibrated by putting the sed form back at all three
sites, which turns exactly that one case red with `a clean tree was accused: 0
missing, 2 extra (exit 1)`, and green again on restore.

**Stated plainly: the fix is proven, its effect on Windows is predicted.** A
POSIX host cannot execute the windows-latest leg, so that both Windows
regressions close is a falsifiable prediction the next Windows run settles.
`gates-windows` stays a non-required check until it has been green at least
once, so a prediction never becomes a merge gate.

### A seal that was broken by the next commit

rc.21's ubuntu legs failed on `program/MASTER-PLAN-2026-08-06.md` MISMATCH. Not
a code defect: the checksum manifest is regenerated last at seal, and then a
documentation commit landed on top of it, which is enough to make an
installation fail its own integrity check. The law is that checksums come last;
what this run showed is that "last" has to mean last in the branch, not last in
the seal.

Evidence, all run after the final edit and quoted from this machine:

```
536 evals: 536 passed, 0 regressions.
```

## 1.0.0-rc.21 (2026-08-07)

- New: `scripts/rollback-install.sh`, the undo the recommended install never
  had. A first attempt was HELD rather than shipped because its default target
  was the clone directory while the recommended install lives elsewhere; both
  exist on a real machine, so that undo would have run, reported success, and
  left the installation the user actually has untouched. A rollback that
  silently does nothing is worse than none, because it turns "I have no undo"
  into "I ran the undo and I am fine".
  This one names no default path at all. It reads Claude Code's own records to
  find where the plugin is installed and where its source lives, re-reads them
  after applying, and refuses to print ROLLED BACK if the reported version did
  not move. Ten refusals, all evaluated before the first write, and the count
  is machine-checked rather than asserted: the header word must equal the
  number of refusal markers, they must be contiguous, and the reachable
  message count must match.
  Ten new tests. Six defects were re-injected one at a time and each turned the
  suite red before restoring it green, including the fatal one: pointing the
  resolver at the clone path fails five tests.

## 1.0.0-rc.20 (2026-08-07)

Loop B-close, four lanes, plus all five release blockers named
by a hostile review the founder commissioned. Every claim below was produced by
a command run after the last edit; the seal evidence is in the pull request.

- Loop B-close, PARTIAL and said so: four of six preserved lanes integrated
  after a fresh hostile re-verification of all six (one refuter per patch,
  each re-executing the prior round's findings rather than trusting them).
  wB2 contracts, wB3 tasks and wB4 review came back MERGE; wB5 ladder came
  back REVISE on one finding, repaired here (a comment in handover.py claimed
  neither status surface turns an unparseable registry into a candidate; a
  refuter reproduced the team surface doing exactly that, so the comment now
  states the narrower claim it can support). wB0 design and wB1 applicability
  came back REVISE and are NOT integrated: wB1's new applicability column
  ripples into doc-truth checkers it never taught, which is open-ended repair
  off this release's critical path. Both are preserved and named in STATE.md
  rather than quietly dropped.
- New: the required-evidence policy engine (`.sbe/policy.yml`, `sbe policy
  evaluate`). Missing required evidence is MISSING and exits nonzero; not
  required is NOT-REQUIRED and never a manufactured pass; neither is NO-DATA,
  which is the separation that let an absent receipt read as a clean bill of
  health before. The detected tier is a floor that may be raised and never
  silently lowered. The consumer action no longer skips verification because
  an input string was empty. Found by this engine's own refuter and fixed
  here: the grant files (`.sbe/approvals.json`, `.sbe/waivers.json`,
  `.sbe/decisions/**`) were unclassified, so the thing that grants permission
  sat outside the rule that guards permission.
- New: the Bash write guard and the Stop reconciliation hook. Writes through
  `Bash` (redirection, an interpreter, a generated script, a rename, a
  deletion, a symlink) were outside every fence until now: the hooks only saw
  the editor tools. The guard refuses positively identified protected writes
  and fails open loudly rather than claiming a command is proven read-only;
  the session baseline and the Stop hook are the authoritative control, and
  they fail closed on a missing baseline or an unreadable registry, so a
  deleted control is never read as permission.
- New: `sbe protections verify`, the control-plane verifier, plus
  `.github/CODEOWNERS`. Without a token it reports NO-DATA per fact and says
  the release checklist treats that as blocking, rather than passing quietly.
  Found by its refuter and fixed here, with a calibrated test: the CODEOWNERS
  parser did not cut comments the way GitHub does, so a pattern whose only
  owner sat behind a `#` was counted as owned.
- Removed: `PROTECTED-CI`. It was minted whenever `SBE_CI_RUN_ID` was present,
  and any local process can export that; the receipt seal is a checksum over
  the receipt's own fields, so it proves the receipt was not edited afterwards
  and proves nothing about who produced it. Following the review's own stated
  fallback, the label is gone rather than explained away: this release mints
  `LOCAL-ADVISORY` and `CI-CLAIMED` only, and CI-CLAIMED carries its whole
  claim in its own sentence. Two consequences are stated rather than hidden.
  A policy that demands protected evidence now refuses both levels, which
  tools/test_sbe_check_registry.py asserts directly by turning the demand on.
  And the shipped `.sbe/policy.yml` turns that demand OFF, because leaving it
  on while no protected level exists would not make this repository strict, it
  would make it stuck: every governed change would report UNPROTECTED forever,
  demanding a proof nothing can currently issue. Both flip back in the change
  that lands cryptographic attestation; docs/KNOWN-LIMITS.md carries the same
  statement for a reader who never opens the policy file.
- Three defects found by running the new engine against THIS repository, which
  is its first consumer, and each fixed with calibrated tests rather than
  waived. First, content signals fired on examples: the tests that prove a
  migration is caught contain the migration text, and the guides that teach the
  gate quote the DDL they teach, so a change touching no database was required
  to produce a rehearsal receipt. Content signals now skip test, fixture, eval
  and documentation surfaces; path globs are untouched, and a real migration
  outside every declared directory is still caught. Second, the shipped policy
  required what its own registry says cannot exist (an unbuilt end-to-end
  runner, and an approval one human cannot give themselves), which would have
  blocked every control-plane change here forever; both are omitted with the
  reason written beside the line, and the structure test asserts those reasons
  are present. Third, staleness judged by the head commit alone made kept
  evidence permanently stale, because writing a receipt moves the head; it now
  reconciles a head mismatch against the covered files' own recorded hashes and
  stays stale unless every one still matches.
- Honest scope, stated once: the GitHub ruleset itself, cryptographic
  attestation for CI evidence, and the Claude Code end-to-end job in CI are
  NOT in this release. The first is a human step in repository settings, the
  other two are named fast-follows. `TEST-PROTOCOL.md` and `TESTERS.md` carry
  that same list in the words a tester needs.
- New: `TESTERS.md` and `TEST-PROTOCOL.md`, written for the Claude Code
  surface: after a one-time install, a tester types slash commands and
  sentences, never terminal commands. The red-team track asks Claude to
  attempt each of ten documented bypasses, which is the surface these guards
  actually defend.
- Program: the master plan gains its acceleration amendment and the
  release-blocker amendment; OWED-2 (the post-rc.16 remeasure) and OWED-6
  (the tag contradiction) close with their evidence; BR-1011 to BR-1015 enter
  the ledger.

## 1.0.0-rc.19 (2026-08-06)

- The Windows leg (12 commits, rounds 1 to 4) folded into main. Its
  windows-latest CI job (gates-windows) arrives still RED: one Windows eval
  failure is open as owed item OWED-4, and on the folded head run 31043311726
  showed gates-windows as the only red job while the four POSIX gates legs
  and the consumer checks were green. The merge law is measured on those five
  legs; the red sixth leg is reported, not silenced, and per the constitution
  a CI workflow change that would soften it is a human edit, proposed to the
  founder rather than made here. Nothing in the job was weakened at the fold.


- Windows round 4, the declared final round of the night: conversion is off
  BEFORE checkout (a workflow step, because .gitattributes rides inside the
  checkout it needs to protect: rounds 1 to 3 watched the same four
  early-alphabet files hash stale on first read only), the same step pasted
  into all three copy-ready doc blocks and the quickstart fence, and the two
  transcript-replay evals become named PLATFORM-GAPs off POSIX: run
  31042529271 showed every live capture collapsing to one line because the
  replay harness itself is bash against POSIX-captured transcripts, so that
  guarantee is measured on the POSIX legs and says so.


- `.github/workflows/brothersbe-gates.yml`: added a `gates-windows` job that
  runs the gates job's full battery on `windows-latest`, pinned to the 3.9
  floor, started from the existing job's complete step list. Only the two
  POSIX `sh` scripts (`scripts/test-install-artifact.sh`,
  `scripts/test-upgrade-rollback.sh`) are skipped, each named where it is
  skipped; nothing is marked `continue-on-error` and no existing step was
  weakened. A new step aliases `python3` to the `python.exe` that
  `actions/setup-python` installs on Windows (it installs no `python3`
  executable there), so every other step still calls `python3` unmodified,
  identical to the Linux and macOS steps. Proven by
  `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/brothersbe-gates.yml'))"`
  parsing clean and a step-by-step diff against the existing job confirming
  the only two steps missing from the new job are the two named exclusions.

## 1.0.0-rc.18 (2026-08-06)

Phase 0 of the approved master plan: program visibility and the dispatch gate,
built under four hostile review rounds whose verdicts ship verbatim in
design/phase-0/. (rc.17 was consumed by the held Windows branch; the Loop
B-close seal that this number was once pencilled for moves to rc.19.)

- New: `sbe program status` and `sbe program check`. Program wide truth
  generated from the ledger (gantt, finished, in flight, blocked, risks with
  their mitigations, docs index, budget against recorded spend), drift gated
  by three new eval cases. Progress comes only from declared figures or
  acceptance criteria actually met; a status word never becomes a percentage;
  an absent or unreadable ledger source is NO-DATA, never a clean program.
- New: `tools/sbe_dispatch.py`, the dispatch gate. Refuses agent briefs
  missing a capability profile, budget, file list, done check or tier;
  enforces the T1/T2/T3 scaling ceilings; refuses loop open when the owed
  register names unfinished work, cannot be read, or records a budget that
  cannot be measured. A file containing the JSON token null cannot forge the
  parse failure sentinel; NaN, Infinity and arbitrarily large integers all
  produce verdicts rather than passes or tracebacks. Every report line routes
  through the say() choke point.
- New: the program ledger carries the plan phases as work items BR-1000 to
  BR-1010 with persona needs, budgets and risks with mitigations;
  program/OWED.json is the owed register the gate reads; docs/PRINCIPLES.md
  is the operating constitution, each principle naming what enforces it.
- Doc truth per the counts law: eval counts repasted from live output (527 to
  530) in six docs, the moved lint line repasted, 27 research citations added
  to the inventory with honest limit lines.
## 1.0.0-rc.17 (2026-08-06)

- Windows round 3, each fix at its root, every trigger named from run
  31040612827: the decision store's writer lock is portable (the same
  byte-range shape as the telemetry lock, refusal-on-timeout semantics
  unchanged); BROTHERSBE_REGISTRIES splits on the platform path-list
  separator because a drive path used to split at its own colon and read no
  registry at all; the transcript comparators mask Windows path renderings
  the same way they mask POSIX ones; three name-asserting evals compare
  separator-normalized lines because the naming, not the glyph, is the
  guarantee; and the eval harness gains a PLATFORM-GAP result, counted and
  printed with its reason, never a pass and never a block, carried by the
  FIFO case (no mkfifo exists there) and the two verify-install cases (a
  POSIX sh script this leg does not measure, now a named gap).

- Windows round 2, from the first real run of the ported leg (run 31039904060,
  read line by line): a `.gitattributes` with `* -text` makes every checkout
  byte-identical (the manifest eval's own byte-level harness proved CRLF
  conversion: committed newline, working tree carriage return); the telemetry
  rewriter uses `os.replace` because Windows `os.rename` refuses an existing
  target; and the path-forgery eval records a filesystem that refuses to
  create the forging directory name as the channel honestly closed, replacing
  a comment claim the same run disproved.

- Windows integration sweep, measured at integration: the five shipped
  assertions that Windows is untested (README twice, the install and
  what-it-will-not-tell-you pages, the PARITY row) now state the truth of this
  branch: a windows-latest leg runs the battery with the two POSIX sh scripts
  excluded by name, and every citation of the deleted KNOWN-LIMITS heading
  points at its replacement. SECURITY.md's auditable-surface figure restated
  from a fresh measure (36,152 lines, 2026-08-06); the pasted meta-test line in
  README and the install page repasted to the derived 46 modules; quickstart's
  verbatim workflow fence repasted from this branch's workflow.

- Windows porting lane, the `gates-windows` leg's first read acted on, then a
  hostile round-2 review corrected three ways the first pass had understated
  what it proved (each noted below at its finding):
  1. `tools/sbe_telemetry.py`'s writer lock (`_writer_lock`) was `fcntl`-only,
     so on Windows it always yielded unheld and `migrate`/`dedup` always
     refused to rewrite, never running at all there. It is now portable,
     `fcntl.flock` where that exists and `msvcrt.locking` where it does not,
     never both in one interpreter, with identical refusal wording on POSIX
     (the two shared refusal sentences now name both primitives rather than
     only `fcntl`, since a platform lacking one may still have the other).
     Round 2: the first pass proved the lock itself but shipped no test that
     could fail if the `msvcrt` byte-range reset (`os.lseek(fd, 0,
     os.SEEK_SET)` before `msvcrt.locking`) were deleted. A calibration
     fixture (`tools/test_sbe.py`, `TestWriterLockByteRangeCalibration`) now
     records the real file descriptor position at every `msvcrt.locking`
     call under a fake `msvcrt` and asserts it is 0; run against a scratch
     copy with the pre-LOCK reset deleted, it goes red. A second reset that
     used to sit before the UNLOCK call could not be made to go red by the
     same fixture under any mutation, because nothing between a successful
     lock and this function's own unlock ever moves that descriptor's
     position; it was dead code and is removed, not kept behind an
     assertion that could never fail. Round 3: that fixture asserted fd
     position only and never made two openers actually contend, so
     `msvcrt.locking`'s raise-and-retry path, and the timeout refusal
     `migrate`/`dedup` print to the operator, ran on no committed test.
     `TestWriterLockContentionCalibration` (`tools/test_sbe.py`) adds a
     range-aware fake whose `locking()` raises when the byte range a caller
     wants is already held by a different fd; opener A takes the lock and
     holds it, opener B (a short timeout) is asserted refused only after
     its OWN retry loop has attempted the lock more than once, not on the
     first failure. A scratch mutant that turns the retry's
     `time.sleep(0.05)` into an immediate `break` collapses B to exactly
     one attempt and the fixture goes red.
  2. One eval fixture (`evals/run_evals.py`, `gd_pathforge`, case
     `a-directory-name-cannot-write-verdict-lines-into-the-report`) built a
     dossier directory whose name embedded a raw newline (0x0A) to prove a
     report-forgery defense; Windows refuses any path component containing
     an ASCII control character (OSError 22, confirmed verbatim against this
     branch's own CI run). The fixture now embeds U+2028 LINE SEPARATOR
     instead, a character `sbe_checks._LINE_BREAKS` and Python's
     `str.splitlines()` both already treat as a line boundary exactly like
     `\n`, so that half of the defense (flattening onto one report line) is
     exercised the same way on every platform. Round 2 correction: an
     earlier draft of this entry and the fixture's own comment claimed the
     swap exercises "the identical code path" / "the same defense... on
     every platform". False as written: a literal `\n` is also Unicode
     category Cc, so `one_line()`'s separate Cc/Cf/Cs visible-escape loop
     independently catches it even if `_LINE_BREAKS` regressed; U+2028 is
     category Zl, outside that loop, so this fixture is caught by
     `_LINE_BREAKS` alone, a strictly MORE sensitive probe of that one
     mechanism, not an equivalent substitution for the redundant coverage
     the literal newline happened to also exercise.
  3. The tracked-manifest eval (`gd_manifest_fresh`) read several files as
     stale on Windows with no way to reproduce it from a POSIX machine. It
     now reads the git-tracked blob and the working-tree file as raw bytes
     for each stale path and reports the first offset where they disagree,
     so the next Windows run states the byte-level cause. No portable
     content fix is applied: the leading theory is checkout-time
     line-ending conversion (no `.gitattributes` pins the tree's line
     endings), but `.gitattributes` sits outside this branch's writable
     scope, and the theory is stated as a theory pending that run's own
     evidence, per the standing rule against guessing twice.
  4. The honesty meta-test's shipped-doc consistency check (`dc2` in
     `evals/run_evals.py`) reads `evals/test_no_data_class.py`'s `counts()`
     to verify a doc's pasted summary line, but `counts()` called the
     platform-gated `access_cases()`, so a host where `ACCESS_APPLIES` is
     False (Windows, or POSIX as root) computed 192 fewer scenarios than
     POSIX (32 checks times the 6 ACCESS scenarios each, confirmed against
     this branch's own CI run: 3588 there against 3780 on POSIX and in the
     shipped docs). `access_cases` takes a `force` parameter now;
     `counts()` passes `force=True` so the figure it reports is the suite's
     platform-independent shape, while `main()`'s real run (unchanged) still
     runs, and discloses, fewer scenarios on a host where they do not apply.
     Round 2 found this finding was not actually closed: `dc2` matched its
     own regex against a doc's pasted module count and then never compared
     the number it matched to anything, so that figure could drift with
     nothing to catch it, live and on POSIX, unrelated to Windows. `dc2`
     now derives the expected module count from the same `load_tool_
     modules()` discovery `main()` runs (`counts()` returns it as a fifth
     value, never a number written into either file) and compares it. Run
     on this POSIX machine, that correction showed `README.md` and
     `docs/for-engineers/01-install-and-first-run.md` both asserting "35
     module(s)" where the suite discovers 43; round 2 disclosed this as
     uncorrectable from this lane's then-fence rather than chasing it.
     Round 3: the fence was extended to exactly these two files for exactly
     this figure. Both are now updated from "35 module(s)" to "43
     module(s)" (this branch's live count, `python3 evals/test_no_data_
     class.py`'s own last line, reproduced verbatim), and `dc2` passes
     against them (see `docs/KNOWN-LIMITS.md`).
  Proven: `python3 evals/run_evals.py` now runs one REGRESSION on POSIX on
  this branch (was two through round 2), not introduced by defective code:
  `the-tracked-manifest-matches-the-tree-it-ships-with`, which reads this
  entry's own uncommitted, in-scope edits as drift against
  `CHECKSUMS.sha256` (a file outside every lane's writable scope; the eval
  returns to matching once the manifest is regenerated at the usual point
  in this repository's release process). `no-shipped-doc-prints-a-meta-
  test-count-the-meta-test-does-not-produce` (finding 4 above) is CLOSED
  this round: `README.md` and `docs/for-engineers/01-install-and-first-
  run.md` are updated to "43 module(s)" and the case now reads `ok`. The
  renamed U+2028 fixture and the manifest harness verified directly, each
  also proven red under its reintroduced defect in a scratch copy and green
  restored; `access_cases`/`counts()` verified both on this POSIX host and
  under a monkeypatched `ACCESS_APPLIES=False` (simulated Windows), red
  before `force=True` and green after, against the real shipped-doc text.
  The Windows lock path (`msvcrt` is not importable here) is proven by two
  fixtures in `tools/test_sbe.py`, both green with the real code and both
  independently proven red under a reintroduced defect in a scratch copy,
  restoration confirmed byte-identical after each:
  `TestWriterLockByteRangeCalibration`, a fake `msvcrt` recording the real
  file descriptor position at every call, both the pre-LOCK reset (load-
  bearing, red when deleted) and the removed pre-UNLOCK reset (dead, could
  not be made to fail); and `TestWriterLockContentionCalibration`, a
  range-aware fake `msvcrt` whose `locking()` raises on an overlapping
  range held by a different fd, real code retries several times (7 on this
  run, timing-dependent but never fewer than 2, which the fixture asserts)
  against a 0.3s timeout before refusing (`held=False`), a scratch mutant
  with the retry's `time.sleep(0.05)` replaced by `break` collapses that to
  exactly 1 attempt before the same refusal. `python3 tools/test_sbe.py`
  stays green (`OK`, 102 tests) with both classes included.
  `docs/KNOWN-LIMITS.md`'s
  "Windows is untested" section is replaced with a read of what this leg's
  actual runs showed, its remaining named gaps (now three, the module-count
  gap above added), and pointers to the fixes. Two further findings from
  the same CI run are out of this entry's scope and untouched here: a
  POSIX-only `os.mkfifo` eval case, and an `fcntl`-only lock in
  `src/brothersbe/decisions.py` unrelated to the telemetry ledger.
  Round 3 also found a problem this lane caused itself: round 2's own new
  code (the `msvcrt` branch's comments in `tools/sbe_telemetry.py` and
  `TestWriterLockByteRangeCalibration` in `tools/test_sbe.py`) had been
  trimmed for wording, not for correctness, to pull `tools/`'s measured
  line count back under `TestAuditableSurface`'s 15 percent drift ceiling
  against `SECURITY.md`'s stated figure, rather than by correcting that
  figure; the margin left (14.9851 percent, 4 lines of headroom against a
  122-line budget at base) meant the very next line added anywhere in
  `tools/` would flip it red. That is fixed the way the test's own failure
  message names: `SECURITY.md`'s stated line count is re-measured and
  restated rather than gamed. Before, on this branch: `29,963` lines
  (dated 2026-08-05). After this round's own additions, including
  restoring the trimmed comments and the new
  `TestWriterLockContentionCalibration` class above: `34,575` lines
  (`wc -l tools/*.py tools/*.sh`, dated 2026-08-06 in `SECURITY.md`),
  0 percent drift against itself, real headroom restored rather than
  borrowed from comment density.


## 1.0.0-rc.16 (2026-08-06)

- Seal note, measured at integration: the sandbox guide's two `bound to head`
  lines (review and handover) move at every version bump, because evidence
  receipts stamp the tool version and that stamp reaches committed sandbox
  content. Re-captured from the live rc.16 run (deterministic across two runs);
  the doc-truth suite `tools/test_sbe_sandbox.py` is the check that catches the
  drift at the next bump too.

- rc.15 remeasure, Loop A round 2: BLOCKER/MAJOR A2 fixed for real, one
  shared candidate builder replacing two independent ones.
  Round 1's own fix for A2 added `_approval_ladder_candidate` to `build_
  report`'s ladder UNCONDITIONALLY, gated only on `08-plan.json` existing;
  since `lifecycle.RUNG_MISSING_APPROVAL` (40) outranks `RUNG_ACTIVE_TASK`
  (60) and `RUNG_READY_TASK` (70), a fresh plan whose tasks had not been
  started yet recommended chasing a pull-request approval on BOTH surfaces
  before anyone had started the work: the ORIGINAL journey-2 disease
  (`sbe status --team` already carried it, unfixed, before round 1),
  unified across both surfaces instead of cured. New `_task_and_approval_
  candidates` is now the ONE shared candidate builder `_change_ladder_
  candidates` (`build_report`'s own ladder) and `build_team_report`'s own
  severity-10 derivation both call, so the two surfaces cannot diverge by
  construction. Approval is a candidate only once every task the plan
  declares is closed clean (`_closed_clean`, the identical predicate
  severity 9's own "nothing left to do" finding already uses); `build_team_
  report`'s own severity 5 (approval), 6 (convergence) and 12 (evidence)
  NO-DATA findings are excluded from its severity-10 candidate pool under
  the SAME gate (a FAIL at any of the three stays eligible regardless of
  task state, unchanged). A sibling divergence one rung further back is
  fixed the same way: a fresh-intake-only repository (no plan yet) used to
  read as `finish` ("nothing blocking here") on plain `sbe status` while
  `sbe status --team` already named `start-ready-task` ("derive the plan");
  `_task_and_approval_candidates` now returns that SAME candidate on both
  surfaces when `00-intake.json` exists and `08-plan.json` does not (a
  repository with NEITHER, the flat layout's own placeholder target, still
  recommends nothing: `_team_changes` never discovers a change there
  either). New `tools/test_sbe_status_team.py::TestThreeSurfaceConsistency`
  proves all three surfaces (`sbe status`, `sbe status --team`, `sbe map`)
  agree across four states: fresh intake only, two ready tasks unclaimed
  (the reproduction: ready-task now wins), a task closed clean with
  evidence and no approval saved, and evidence/convergence/review all
  clean with approval genuinely the only thing missing. `TestFullSeveritySet
  .test_every_change_carries_exactly_one_severity_ten_next_action`'s own
  "highest severity" check is corrected from a raw `min(severity)`
  comparison (which, for a fresh single-ready-task plan, happened to
  coincide with the exact bug this fixes) to the same gated priority rule,
  documented inline; it still proves severity 10 is derived from a real
  recorded fact, never a filler. All four calibrated red-first: BLOCKER
  A2's round-1 fix restored unconditionally reproduces the ready-task
  regression; the severity-10 pool-filter removed reproduces the raw-
  severity regression on both `TestThreeSurfaceConsistency` and
  `TestFullSeveritySet`; the intake gate removed reproduces "derive the
  plan" leaking onto a truly empty repository
  (`tools/test_sbe_status.py::TestNoStores`).
  CRITICAL (docs/book drift, disclosed, not fixed here, integration-class):
  `python3 tools/test_sbe_book.py` still fails, unchanged from round 1's
  hostile verdict: `compared 136 output blocks, 1 differ`.
  `PATH=/usr/bin:/bin SBE_REPLAY_TIMEOUT=240 python3 evals/replay_book.py`
  isolates the SAME single block: `docs/book/03-reading-the-truth.md`
  BLOCK 1 (lines 71-99), where the recorded transcript still says
  `team-operating-model` declared tier T1 and the live run says T3 (round
  1's Ledger 12 reading the documented override in `design/team-operating-
  model/00-intake.json`, untouched by this round). `docs/book/` is outside
  this change's fence; the orchestrator's own regeneration recipe
  (`evals/replay_book.py --write` at seal) owns closing it.
  Battery after the last edit: `tools/test_sbe_status.py` 39 OK, `tools/
  test_sbe_status_team.py` 40 OK (36 carried over plus 4 new), `tools/
  test_sbe_decisions.py` 46 OK, `tools/test_sbe_golden_scenario.py` 5 OK,
  `tools/test_sbe_team_workflow.py` 11 OK, `tools/test_sbe_map.py` 7 OK,
  `tools/test_sbe_handover.py` 34 OK, 0 regressions, 0 counts lowered.

- rc.15 remeasure, Loop A: three journey blockers and five majors closed,
  red-first (each repro landed as a failing test before its fix; every
  fixture calibrated by mutating a scratch copy of the source, confirming
  red, restoring, confirming green on the real code).
  BLOCKER A1: `sbe lineage`'s own receipt scan
  (`decisions._lineage_receipts`) verified every receipt against the
  resolved repository root's OWN head only, never against a claiming task's
  own declared worktree, so a closed task's sound receipt (the state
  `status._scan_evidence` already resolves clean, rc.11) read as a
  `broken-receipt` hop here over the same commit:
  reproduced with a real task worktree branched off root HEAD, its own
  further commit and sealed receipt, registered closed with `worktree` set
  in `.sbe/tasks.json`, where root's own HEAD never advances and the
  covered file never exists there. `decisions.py` now builds the identical
  `{runId: (worktree, taskId)}` lookup `status._scan_evidence` already
  builds (new `_claimed_worktrees`) and resolves a claimed receipt's
  `evidence.verify` against that worktree, naming the resolution on the
  hop exactly the way status names it on its own clean entries. Proved by
  `tools/test_sbe_decisions.py::TestLineageWorktreeResolution`.
  BLOCKER A2: `build_report`'s LANE C1 candidate ladder (documented as
  covering sections 1-4 plus task/review candidates) never asked
  `10-approval.json` anything, so a change whose only outstanding
  obligation was approval read as `finish` ("nothing blocking here") on
  plain `sbe status` while `sbe status --team` and `sbe map` (built
  entirely from the team report) named the SAME commit's own
  `resolve-missing-approval`. New `_approval_ladder_candidate`, called
  from `_change_ladder_candidates`, mirrors team's own severity-5 judgement
  (missing, stale, or a saved `final` other than PASS) through the same
  `lifecycle.reduce_next_action` both surfaces already share. Proved by
  `tools/test_sbe_status_team.py::TestCanonicalNextAction.
  test_missing_approval_is_the_same_action_id_on_both_surfaces`.
  BLOCKER A3: after a handover is acknowledged, `12-handover.json`'s
  `status`, `outgoingOwner` and `intendedReceiver` reached the JSON
  `handover` array but `render_team` never read that array at all, so a
  human running `sbe status --team` with no `--json` never saw who now
  owned the change. `render_team` now prints one `OWNERSHIP HANDOVER` line
  per change whose handover status is not `none`, reusing `_handover_
  state`'s own `detail` sentence rather than re-deriving it. Proved by
  `tools/test_sbe_status_team.py::TestHandoverIntegration.
  test_an_acknowledged_handover_states_ownership_in_the_plain_text`.
  MAJOR A4: the team severity table had no rung for missing evidence (a
  plan's verification commands never run under `sbe evidence run`), so
  that finding was crammed into severity 5, "missing approvals," alongside
  the unrelated fact that no approval report is saved, both printing under
  the same header. `TEAM_SEVERITIES` gains severity 12, "missing
  evidence" (appended rather than inserted among 1-11, because renumbering
  any existing slot would move `lifecycle.TEAM_SEVERITY_TO_RUNG`'s
  existing entries out from under themselves); the finding moves to that
  severity, `TEAM_BLOCKING_SEVERITIES` (replacing `team_blocking`'s bare
  `1 <= severity <= 6` range check) names it alongside 1-6 so it still
  exits nonzero exactly as MISSING EVIDENCE already does for plain status,
  and `lifecycle.TEAM_SEVERITY_TO_RUNG` gains a `setdefault(12,
  RUNG_MISSING_EVIDENCE)` registration from status.py itself (see the
  deviation note below: this table is lifecycle.py's own by that module's
  documented design, and belongs there once a future change is free to
  touch that file). The JSON contract bound in `tools/test_sbe_status_
  team.py` widens from 1..11 to 1..12 accordingly. Proved by
  `tools/test_sbe_status_team.py::TestMissingEvidenceRung`.
  MAJOR A6: `_team_changes` discovered a dossier only when it carried
  `00-intake.json`, so a dossier holding a validated `08-plan.json` but no
  intake (deleted, or a plan derived without one ever being recorded) was
  invisible to `sbe status --team`, to plain `sbe status`'s own CR-06
  dossier discovery, and to `sbe map` (built entirely from the team
  report). Discovery now also fires on a validated plan (new `_has_
  validated_plan`: a parseable `08-plan.json` carrying a non-empty `tasks`
  list, each entry naming an `id`); `build_team_report` and `render_team`
  gain a new, purely additive `planOnly` list (mirroring the `handover`
  list's own additive pattern) naming every such change, labeled "exists,
  no intake recorded yet." Proved by `tools/test_sbe_status_team.py::
  TestPlanOnlyDiscovery`.
  MAJOR A7: task completion was never stated plainly in the team view; a
  reader had to notice the absence of a severity-8 "ready" finding and
  infer completion from silence. `build_team_report` now returns a
  `completedTasks` list (closed, non-FORCED registry records, sourced from
  the same `_closed_clean` rule the per-task severity-9 finding already
  uses) and `render_team` states it plainly in one header line. Proved by
  `tools/test_sbe_status_team.py::TestCompletedTasksLine`.
  Ledger 12: after a valid, documented tier override in `00-intake.json`
  (the `tier`/`override`/`override_reason` shape `tools/sbe_design.py`
  itself reads and treats as authoritative once the two tier fields agree
  and a reason is recorded), `build_report`'s scope notes and its MISSING
  EVIDENCE obligation gate kept using the COMPUTED tier, because `impact.
  read_intake` always re-derives the tier from `answers` alone (by design,
  L15) and status never separately consulted the override fields at all.
  New `_declared_tier` reads the same two fields `sbe design` reads (never
  replicating its reviewability threshold on the reason's word/character
  count, which stays that gate's own job) and `build_report` now reassigns
  `human_tier` through it once per target, so the disagreement text, the
  scope note, and the evidence obligation all agree with the documented
  override. Proved by `tools/test_sbe_status.py::TestTierOverride`.
  Ledger 16: `team_blocking`'s exit-code rule is now named explicitly
  (`TEAM_BLOCKING_SEVERITIES`, replacing a bare inline range check) with
  its own doc comment stating exactly which severities are actionable and
  why 7-11 are not, mirroring `any_blocking`'s existing docstring for
  plain status. No behavior change beyond MAJOR A4's own severity-12
  addition above: systematic empirical probing (a fully green end-to-end
  scenario, task closed clean, convergence and approval both PASS and
  bound to head, review approved by a non-author) already exits 0 today,
  and this project's own golden-scenario suite explicitly calls the
  severity-5/6 NO-DATA findings "legitimate" blockers, not a bug; no
  scenario reproducing "every finding informational and healthy, exit 1"
  was found. Recorded as a deviation rather than guessed at.
  Ledger 18: NOT fixed here. The re-run-review staleness gap described
  (no warning when `11-review.json`'s bound head differs from current,
  without `--write`) belongs to `sbe review`'s own read path, `_cmd_
  review` in `src/brothersbe/cli.py`, which never reads `11-review.json`
  at all without `--write`; `reviewroute.py` is a different command
  (`sbe review-route`) that routes specialist reviewers from a diff and
  never reads a saved review record either. `cli.py` is outside this
  change's fence. Recorded as a deviation rather than guessed at.
  Battery after the last edit: `tools/test_sbe_decisions.py` 46 OK,
  `tools/test_sbe_status.py` 39 OK, `tools/test_sbe_status_team.py` 36 OK,
  `tools/test_sbe_handover.py`, `tools/test_sbe_map.py`,
  `tools/test_sbe_golden_scenario.py` and `tools/test_sbe_team_workflow.py`
  unchanged and green, 0 regressions.

- The beginner sandbox guide (`docs/guides/00-sandbox.md`) is truthful again
  after the rc.15 remeasure surfaced eight small drifts. Step 3's captured
  `sbe_design.py artifacts` block was recaptured from a live run (the
  `.brothersbe` project-init folder now sits beside `design/`, so a run from
  the repo root reads two directories there, not zero; the guide's command
  now `cd`s back to the repo root before it, matching what a real run
  prints). The false promise that a reader's own commit hash "will read the
  same" as the guide's is rewritten honestly: only git IDENTITY is pinned
  into the sandbox repository, the commit DATE is pinned only inside the
  builder's own subprocess, so a reader's live step 4 commit differs and
  that is expected. A stale, orphaned hash (`71b0b5c910b2`, quoted nowhere
  else on the page) is corrected to the hash the page actually prints
  above it. Three new sentences state what the doc previously left the
  reader to discover the hard way: the review step's vault-and-registry
  section prints roughly eleven unfamiliar check names on a machine that
  has one configured, varies machine to machine, and can be skipped in the
  sandbox; that same read is read-only and expected, because the sandbox
  does not build or switch to its own vault; and a configured vault or a
  configured private-name list each independently raise the doctor step's
  PASS count above what the page shows, no specific number promised,
  because `vault` and `private-names` are two separate NO-DATA sources and
  either or both may already read PASS on a reader's own machine.
  `tools/test_sbe_sandbox.py` now drives step 3 from the sandbox repo root
  (matching the guide's corrected command) and pins the newly captured
  `scope`, `dossier:` and `artifacts` lines, calibrated by mutating each
  line in the tracked guide file and watching the suite go red, then
  restoring it clean. The quickstart duplicated between `README.md` and
  `docs/SETUP.md` is deduplicated: `README.md`'s "A 60-second first run"
  stays the one source for the eval bed and the honesty meta-test, and
  `docs/SETUP.md` step 4 now points there for those two while keeping its
  own gate commands (`sbe_gate.py .`, `sbe_gate.py numbers .`,
  `sbe_gate.py --strict design`) inline, since README's section never
  carried the bare all-four-gates advisory form.
  `docs/KNOWN-LIMITS.md` gains one entry: a refused `sbe task open`
  overlap leaves no durable record (no registry write, no telemetry row),
  by design, because the registry records state that exists, never
  attempts that failed to change it. Proven by
  `python3 tools/test_sbe_sandbox.py` (10 tests, OK), plus
  `grep -c 71b0b5c910b2 docs/guides/00-sandbox.md` (0) and
  `grep -nE 'will read the same' docs/guides/00-sandbox.md` (no match).

- LANE A wiring: `.github/workflows/brothersbe-gates.yml` ran nearly every
  `tools/test_sbe_*.py` suite but was missing four that existed on disk and ran on
  nobody's merge path: `test_sbe_sandbox.py`, `test_sbe_map.py`,
  `test_sbe_decision_contract.py`, `test_sbe_import_hygiene.py`. All four are now
  wired in as steps, in the position their name sorts to among the neighboring
  suites, mirroring the existing step shape. `tools/sbe_design.py`'s
  `check_artifacts` builds `label` (the sentence naming the written tier, the
  computed tier, and the override's direction) and appends it on the PASS branch
  and on the NO-DATA branch, but the FAIL-for-missing-artifacts branch dropped it,
  exactly the branch that fires right after an override raises the tier and the
  newly required artifacts are not there yet: a reader saw the missing list but
  never which tier was written or why. That branch now appends the same `label`.
  `skills/status/SKILL.md` told the reader to run `sbe fences`, which reads the
  hand-written `STATE.md` registry this repository does not configure, a second
  source that contradicted `sbe status`'s own `.sbe/tasks.json`-backed picture; it
  now points at `sbe task list` instead. Proven by
  `tools/test_sbe.py::TestDesignOverrideLabelOnMissingArtifactsFail` (calibrated:
  red when `label` is removed from that branch in a scratch mutation, green after
  restore, restoration verified with `git diff` clean), `python3 tools/test_sbe.py`
  (count not lowered), the four newly wired suites each run directly and green, and
  `grep -n 'sbe fences' skills/status/SKILL.md` returning nothing.
- LANE A wiring, round 2: the four newly wired steps above went into the workflow
  without going into the copy-ready CI blocks three docs carry, so a reader who
  assembled CI from the doc fragment instead of copying the file got 4 of 42 steps
  fewer than the shipped workflow, and `docs/guides/01-quickstart.md`'s fence under
  "The workflow, verbatim:" stopped being verbatim. `README.md`,
  `docs/HOW-IT-WORKS.md` and `docs/guides/01-quickstart.md` are repasted from the
  edited workflow (quickstart's fence replaced whole, byte for byte; the other two,
  the four missing step blocks inserted at the position their name already sorted
  to). Proven by `python3 evals/run_evals.py`: both
  `no-copy-ready-ci-block-shows-fewer-steps-than-the-shipped-workflow` (for these
  three docs) and `guide-01s-verbatim-workflow-fence-is-the-shipped-workflow` are
  green. `docs/guides/05-a-worked-engagement.md` carried the same class of
  copy-ready CI block and the same 4-of-42 gap; the fence was extended for exactly
  that file in the final cycle, its block is repasted from the shipped workflow in
  this same change, and its CI fragment's gate step now names `--strict design`,
  matching the workflow line it quotes, so the parity eval covers all four docs.

## 1.0.0-rc.15 (2026-08-05)

- LANE C1 (B-003), one canonical next action: at least three independent
  derivations of "what to do next" used to coexist and could disagree.
  `status.py`'s plain `build_report` picked blocker-first over its own four
  sections only (broken claims, merge blockers, active conflicts, missing
  evidence), never looking at task or review state at all; `build_team_
  report`'s severity-10 finding picked the MINIMUM raw team-severity number
  among a change's other findings, which let severity 9 ("completed
  changes") outrank severity 11 ("review record") as a bare integer;
  `skills/next/SKILL.md` carried a third, hand-written priority ladder in
  prose. Reproduced with a temp-dir dossier whose only outstanding
  obligation was review (evidence complete, both plan tasks closed clean,
  convergence and approval both PASS): plain `sbe status` read it as "no
  action; this receipt is sound evidence" and `sbe status --team`'s own
  severity-10 read it as "nothing left to do, open a pull request," both
  wrong, because review had never run.
  `src/brothersbe/lifecycle.py` (new) now owns the one reducer,
  `reduce_next_action`, a pure function over a caller-supplied list of
  `{rung, reason}` candidates that picks the most urgent by a single
  canonical rung order (with `TEAM_SEVERITY_TO_RUNG` translating team's own
  1..11 severity numbers into that order, deliberately placing "review not
  cleared" ahead of "nothing outstanding" so a change is never called done
  before it has been reviewed). `status.py`'s `build_report` now also reads
  task-active/task-ready/review-not-cleared candidates for every target
  through two new helpers, `_review_ladder_check` and `_change_ladder_
  candidates` (reusing the same `_read_review_record`/`_commit_author`/
  `_same_identity`/`tasks_mod`/`work_mod` primitives `build_team_report`
  already reads the identical facts through, never a second copy of the
  rule), and exposes the reducer's own `{actionId, label, reason, basis}`
  as a new, additive `nextActionDetail` field alongside the unchanged
  `nextAction` string. `build_team_report`'s severity-10 finding is now
  derived through the same reducer and carries the same `actionId`/`label`.
  A repository with no plan anywhere gets byte-identical `nextAction` text
  to every earlier version of this file (`_next_action`'s own sections 1-4
  ordering and wording are unchanged; the new candidates are additive).
  `skills/next/SKILL.md`'s ladder for the parts the reducer now covers
  (task readiness, missing evidence, review, "everything green") collapses
  to "read `nextAction`/`nextActionDetail` and act on it"; the three
  checks the reducer genuinely cannot run (environment, intake presence,
  design completeness -- each requires a live subprocess `sbe status`
  itself is built never to start) stay separate, explicit probes.
  `skills/status/SKILL.md`'s claim that `nextAction` is "the same field
  `/brothersbe:next` reads for the same state" is now true by construction,
  and cites the reducer. Proven by
  `tools/test_sbe_status_team.py::TestCanonicalNextAction` (the reproduced
  disagreement as a red-first fixture, green after: both surfaces now
  emit `actionId` `run-review` for the identical dossier), with
  `tools/test_sbe_status.py`, the rest of `tools/test_sbe_status_team.py`,
  `tools/test_sbe.py`, `tools/test_sbe_golden_scenario.py` and
  `evals/run_evals.py` all re-verified green.

- LANE C2, gate LP-0301, GUI security ADR and promise amendment (docs only, no
  GUI code): `SECURITY.md`'s "no server" promise is amended to "no remote
  server; a loopback-only GUI workspace is authorized," recorded in
  `docs/adr/2026-08-05-gui-server-amendment.md`, which weighs and rejects two
  distinct alternatives (keep the generated page only, and a cloud or remote
  UI) before reaching the decision. `SECURITY.md`'s promise paragraph and its
  audit-grep prose are rewritten so a reader running the file's own grep still
  finds the truth: the reserved path, `src/brothersbe/gui/server.py`, does not
  exist yet, so the grep shows the same single real hit it always has
  (`src/brothersbe/prverify.py`). `tools/test_sbe.py`'s zero-network AST scan
  gains that one named exact-path allowlist entry and is extended to walk
  `src/brothersbe/gui/` recursively, so any OTHER file placed under `gui/`
  stays banned; `TestGuiNetworkAllowlistIsNarrow` (new) proves both directions
  against a scratch copy under `/tmp`, red before the scan change (a planted
  `import socket` in a fake `gui/api.py` was not caught) and green after.
  `docs/KNOWN-LIMITS.md` records the boundary change and names the documents
  (`docs/THREAT_MODEL.md`, `README.md`,
  `design/final-release-program/01-purpose.md`) that still state the
  pre-amendment wording and are out of scope for this lane. Proven by
  `python3 tools/test_sbe.py` (`TestAuditableSurface`,
  `TestGuiNetworkAllowlistIsNarrow`), `python3 evals/run_evals.py`, and
  `python3 tools/test_sbe_interop.py`, all green.

- LANE C3, B-010, first start initializes: the marketplace install path never
  ran `sbe init`, `sbe doctor` had no way to detect the missing project
  footprint, and a beginner's first `/brothersbe:start` could land in an
  uninitialized repository with `doctor`'s own `result` reading PASS.
  `_doctor_checks()` (`src/brothersbe/cli.py`) now carries a `project-init`
  check: FAIL, detail naming the state REQUIRED-and-missing, whenever
  `.brothersbe/config.json` (`initcmd.CONFIG_PATH`) is absent, so the check
  joins the same FAIL list the overall JSON `result` is computed from and
  Guided mode can never read PASS while the main capability cannot run.
  `skills/start/SKILL.md`'s opening steps now detect a `project-init` FAIL,
  say so in plain language, and repair it through the skill's existing
  preview-then-apply consent register (`sbe init` dry run, then `sbe init
  --apply` only on the user's yes) before continuing to the rest of the
  guided flow. Proven red then green by
  `tools/test_sbe.py::TestDoctorProjectInitCheck` (an uninitialized repo
  names the missing footprint and never reads PASS overall; `sbe init
  --apply` clears the check), with `TestDoctorIdentityCheck`'s own fixture
  updated to carry the footprint so its assertions stay about identity, not
  this new check. `tools/test_sbe.py`, `evals/run_evals.py` and
  `tools/test_sbe_instruction_surface.py` re-verified green.

- Plan 12.4, Lane C4, the ten minute sandbox: a beginner can now build a small,
  disposable, offline practice repository (`tools/fixtures/sandbox/
  build_sandbox.py`, mirroring the golden scenario builder's idiom, smaller and
  human-facing: one task, friendly file names, a step-naming README) and follow
  `docs/guides/00-sandbox.md` end to end -- install health, describe an outcome,
  see the risk level, accept one decision, start one task, run proof, review,
  reach a prepared handover -- with every quoted command output captured from a
  real run. Proven by `python3 tools/test_sbe_sandbox.py`, which builds the same
  sandbox, drives the same eight steps through the real engine, and asserts the
  guide's quoted verdict lines are what the tools actually print there.

## 1.0.0-rc.14 (2026-08-05)

- LANE B-004, per-change evidence scoping: `sbe status`'s MISSING EVIDENCE
  check, on the CR-06 multi-dossier path, used to clear an obligation by
  reading `status._scan_evidence`'s own `kindsCovered`, a single set
  computed ONCE over the whole evidence store with no notion of which
  change a receipt belonged to, so a gate receipt scoped to change A's own
  owned file cleared change B's obligation too, purely because both
  dossiers were discovered in the same run. Reproduced with a two-dossier
  fixture, red on the prior code. `src/brothersbe/status.py` now computes
  per-dossier coverage (`_dossier_evidence_attribution`, new): a receipt
  clears a dossier's obligation only when a registry task record that is
  genuinely that dossier's own (its `id` is one of that dossier's own plan
  task ids AND its own `ownedPaths` overlap a path that dossier's plan
  owns, not an id match alone, because every derived plan starts fresh at
  "T01" and an id-only match would reopen the identical bug through the
  registry-claim path) claims it by `evidenceId`, or every one of its
  `coveredFiles` falls inside a path that dossier's plan `owns`. A receipt
  attributable to no dossier stays UNSCOPED: it clears nothing on the
  discovered-dossier path, and the finding says so by name rather than
  staying silent. The flat single-dossier layout never calls the new
  function and its output stays byte-identical to every earlier version of
  this file. Proven by
  `tools/test_sbe_status_team.py::TestPerChangeEvidenceScoping` (three
  fixtures: the reproduced bug, the UNSCOPED wording, and the T01-collision
  guard), with `tools/test_sbe_status.py`, `tools/test_sbe.py`,
  `tools/test_sbe_golden_scenario.py` and `evals/run_evals.py` all
  re-verified green. See `docs/KNOWN-LIMITS.md` ("Evidence obligations
  under CR-06 multi-dossier status, fixed") for what remains an honest
  limit.

- LANE B-006, immutable review history: a second `sbe review --write` at the
  SAME head used to overwrite `design/<name>/11-review.json` in place
  (`src/brothersbe/cli.py`'s `_record_review`, mode `"w"`), silently erasing
  a `changes-required` verdict the moment a later `approved` write landed at
  the identical commit, undetectable at that same head. `_record_review` now
  calls a new `_archive_prior_review` immediately before it opens
  `11-review.json` for writing: whatever the file currently holds is
  appended, verbatim (or wrapped as `{"corrupt": true, "raw": ...}` when it
  will not parse), as one JSON line into a new append-only
  `11-review-history.jsonl` beside it, before the new record replaces it.
  `11-review.json` stays the current pointer, byte-for-byte what every
  existing reader (status.py slot 11, the review skill) already expects, so
  nothing downstream changes. `src/brothersbe/status.py`'s `build_team_report`
  reads the history back: a record superseded at the SAME head as the
  current one but naming a DIFFERENT `result` now surfaces as its own named,
  severity-11 finding (`FAIL`, basis `derived`), never silent. Proven by
  `tools/test_sbe_review_record.py`'s new `TestReviewHistory` (red first,
  by stashing the fix and running the new tests against the unfixed write
  path: 5 of 7 failed, showing the prior verdict gone and no disclosure;
  green after restoring the fix): a second write preserves the first
  verdict, a third write keeps both priors, the overwrite is disclosed with
  the prior result and the head named, and three false-positive guards (a
  single write, two writes with the same result, a fresh review at a
  genuinely different head) confirm the disclosure never fires when nothing
  was actually overwritten.

- LANE B-013, skills facade convergence: five skill files invoked
  `python3 tools/*.py` directly in parallel to the `bin/sbe` subcommand that
  already carries the same behavior. `skills/design/SKILL.md` now calls
  `sbe design --strict` (a straight passthrough to `sbe_design.py`, byte
  identical output confirmed), `skills/kickoff/SKILL.md` now calls
  `sbe intake --help` (same passthrough), `skills/adopt/SKILL.md` now calls
  `sbe fences .` (same passthrough), `skills/verify/SKILL.md` now calls
  `sbe score --strict --strict-soft <dir>`, and `skills/next/SKILL.md` now
  calls `sbe gate <dir> --no-decisions`, with its "writes nothing" claim
  updated to name the flag that keeps the CLI's own decision-package write
  from firing on what is meant to stay a plain diagnostic read (confirmed
  the verdict lines match the direct tool call byte for byte; only a
  trailing decision-package note differs without the flag). Proven by
  `python3 tools/test_sbe_interop.py`, `python3 tools/test_sbe_instruction_surface.py`,
  and a grep for `python3 .*tools/` across the five files returning empty.

- LANE FT-rule, the founder's decision contract: every key decision surface
  now names the falsification tier behind each claim it makes (a
  deterministic check, then a mutation calibration, then a fresh-context
  refute, with reasoning alone read as NO-DATA rather than a tier) and
  offers an explicit option to return control to the developer instead of
  accepting the recommendation. Landed on the three surfaces the checkpoint
  shape is built from: `SKILL.md`'s L6 checkpoint-shape line, the L12 RULE
  paragraph in `references/laws-decision-tables.md` (the file SKILL.md's
  routing table loads when a decision table's recommendation is about to be
  reported), and two new sections (`## Falsification tier`,
  `## Return to developer`) in `templates/dossier/03-adr.md`, each extended
  in the register the surface already used rather than rewritten. Proven by
  `tools/test_sbe_decision_contract.py` (new), which isolates the exact
  sentence or paragraph on each surface and asserts both elements are
  present verbatim; calibrated red by rsyncing the repository to a `/tmp`
  scratch copy, deleting the control-return clause from the scratch
  `SKILL.md`, and confirming the test fails there while the real working
  tree stays green. Known fallout, both out of this change's owned-file
  scope to fix: `CHECKSUMS.sha256` is now stale for the three edited files
  (regenerate with `scripts/checksums.sh CHECKSUMS.sha256`), and
  `docs/guides/02-the-gates-in-practice.md`'s pasted `silent-failure-lints`
  line under-counts `tools/` by one file now that
  `tools/test_sbe_decision_contract.py` exists (43 to 44 scanned, 40 to 41
  clean); `evals/run_evals.py` names both by their case name
  (`the-tracked-manifest-matches-the-tree-it-ships-with`,
  `no-shipped-doc-prints-a-silent-failure-lint-line-the-scorer-does-not-produce`).

## 1.0.0-rc.13 (2026-08-05)

- LANE PT-3, deterministic `sbe map`: `skills/help/SKILL.md`'s "project map"
  section used to tell the MODEL to fill `skills/help/map-template.html` slot
  by slot from whatever it happened to read, which is nondeterministic and can
  invent data that was never actually there. `src/brothersbe/mapgen.py` (new)
  builds the page in code instead, from exactly three canonical sources: the
  status module's own team report (`brothersbe.status.build_team_report`,
  imported the way `cli.py` already imports it, never parsed out of rendered
  prose), the task registry, and dossier artifact presence (a boolean per
  lifecycle file, never the file's content). Every user-controlled string is
  HTML-escaped before it reaches the page, and no wall-clock timestamp or
  machine-specific filesystem path is ever written into it, so the same
  repository state always renders the same bytes. Routed as a new `sbe map
  --out FILE` subcommand (`src/brothersbe/cli.py`, one `COMMANDS` entry and
  one `PASSTHROUGH` entry, delegating to `mapgen.main`, nothing else changed
  in that file). `skills/help/SKILL.md`'s project-map section now points at
  this command instead of the old fill-the-template instruction. Proven by
  `tools/test_sbe_map.py` (identical bytes across two runs of the same state,
  every canonical section rendering from a populated fixture, an
  HTML-injection payload in a task's own agent field arriving escaped),
  calibrated red for the escaping test by neutralizing `_esc` in a `/tmp`
  rsync scratch copy, never in the working tree.

## 1.0.0-rc.12 (2026-08-05)

- Documentation truth pass (LT-503, first half): historical and superseded
  markers on the release-1.0 handover and status snapshots and the shipped
  loop plans, a not-executed marker on the data and infra books plan, and a
  design-only marker on the team-docs spec; the team playbook no longer
  references the unbuilt health screen in present tense and labels the
  integration diagram nodes as design targets; `docs/CLI.md` gains the
  missing `instruction-surface` row and its passthrough list now matches the
  CLI's own PASSTHROUGH set (adding instruction-surface, handover, explain,
  lineage). Deferred and disclosed: collapsing the overlapping
  getting-started surfaces into one quickstart and one engineering reference.

- One mount point for `tools/` on `sys.path` (`src/brothersbe/_toolspath.py`,
  new): `converge.py`, `evidence.py`, `impact.py`, `reviewroute.py`,
  `handover.py`, `decisions.py`, and (folded in at integration the same
  night, once the lane's own scan surfaced it) `work.py` each used to
  compute the path to `tools/` and insert it onto `sys.path` themselves,
  seven separate copies of the same three lines. All seven now call the one
  shared `mount()` function; `tasks.py`, the eighth copy the scan surfaced,
  stays inline BY PROOF: `tools/sbe_authority_hook.py` loads it standalone
  with `spec_from_file_location`, a package-relative import there makes the
  authority guard fail open, and `tools/test_sbe_authority_hook.py` went
  red on the fold-in attempt, so the exception is named in the hygiene
  suite's `KNOWN_UNCONVERTED` with that reason rather than converted
  instead; `decisions.py` still calls it lazily, inside `registries()`, for
  the same cycle-avoidance reason its old inline mount was lazy. Zero
  behavior change: every one of the seven still resolves the `tools/`-flat
  module it imports. Proven by the new `tools/test_sbe_import_hygiene.py`
  (source read with `ast`, not text search, so a comment mentioning
  "sys.path" in prose never counts as a hit; each of the seven also proven to
  still resolve its `tools/` import in a fresh interpreter), calibrated red
  by reintroducing a `sys.path` line into a converted module in a scratch
  copy under `/tmp`. Two more modules, `tasks.py` and `work.py`, mount
  `tools/` the same old inline way and are named, not silently excluded, in
  that test's `KNOWN_UNCONVERTED` set: they sit outside this change's owned
  files. All eight pre-existing focused suites for the six converted
  modules stay green: `tools/test_sbe.py`, `tools/test_sbe_converge.py`,
  `tools/test_sbe_evidence.py`, `tools/test_sbe_handover.py`,
  `tools/test_sbe_decisions.py`, `tools/test_sbe_impact.py`,
  `tools/test_sbe_review_route.py`.

## 1.0.0-rc.11 (2026-08-05)

The two engine gaps the LT-501 golden scenario disclosed in rc.10 are fixed,
which unblocks the LT-503 consolidation rc.10 deliberately held.

- Team status evidence resolution (`src/brothersbe/status.py`): the evidence
  scan resolves a receipt claimed by a task record, meaning the record's
  `evidenceId` equals the receipt's `runId`, against that record's declared
  worktree when it still exists, so a finished task's own receipt verifies
  clean instead of misreading as a severity-1 broken claim against root
  HEAD. The resolution is disclosed on the entry (`verifiedIn`, `task`, and
  the finding sentence); a claimed receipt whose worktree is gone, an
  unclaimed receipt, and an unreadable registry still verify against root,
  so linkage that cannot be read never upgrades a verdict. Proven by
  `tools/test_sbe_golden_scenario.py::TestTeamModeCIPostcondition` (zero
  severity-1 findings after real task work, the legitimate NO-DATA blockers
  named exactly, the plain status JSON naming the worktree resolution),
  calibrated red by suppressing the resolution in a scratch copy.
- Converge coveredFiles membership (`src/brothersbe/converge.py`): the
  VERIFICATION dimension extracts `path` from each `coveredFiles` entry
  (tolerating a legacy bare-string entry) before the `owns` membership
  test, so a writer task's own receipt is credited with covering its owned
  paths. Proven by `tools/test_sbe_golden_scenario.py::TestFullChain` (the
  sealed-receipt PASS finding present, the old does-not-cover text absent,
  VERIFICATION still FAIL for the inherent wrong-head reason and FINAL
  still FAIL by the documented rule), calibrated red by reverting the
  extraction in a scratch copy.
- `docs/KNOWN-LIMITS.md` LT-501 section rewritten: the two fixed gaps are
  recorded as fixed with their proofs, and the remaining true boundary
  (receipts for other tasks bind to their own branch heads until
  integration outside `sbe`) stays documented instead of being deleted
  with the fixes.

## 1.0.0-rc.10 (2026-08-05)

Wave E of the lean team program, first half: the end to end proof of the
whole workflow, and the interoperability contract. LT-503 consolidation is
deliberately held until the two engine gaps LT-501 disclosed are fixed,
because consolidating documentation on top of known-wrong verdict paths
would bake the wrong claims in.

- Plugin interoperability minimum (LT-502, `docs/INTEROPERABILITY.md`,
  documentation and tests, not a compatibility framework): the seven
  guarantees the lean plan names (every skill namespaced, no generic command
  claimed outside the namespace, no global user settings overwritten, hooks
  installed only through the approved manifest or documented manual path,
  coexistence with the official GitHub plugin and common MCP tools, the
  documented CLI fallback when no companion plugin is present, and no
  reading of another plugin's conversation), each labeled PROVEN BY TEST or
  DOCUMENTED CONTRACT, never implied. Proven by `tools/test_sbe_interop.py`
  (19 tests: every skill's own namespaced invocation line, a bare-command
  scan over every skill and doc, an install-path scan for `settings.json`,
  the real `sbe_instruction_surface.py` FAILing an undeclared `hooks/**`
  edit and PASSing a declared, reviewed one, a synthetic companion-plugin
  fixture proving two identically named skill directories never collide
  once namespaced, and an AST scan for any read of another session's
  conversation store; every scanner calibrated red, hash-verified restore,
  green against a scratch copy, never the tracked file). `sbe doctor`
  gained no new row: the branch taken, and why, is recorded in
  `docs/INTEROPERABILITY.md`'s "Doctor: branch taken" section and in
  `docs/KNOWN-LIMITS.md`.
- LT-501, one end-to-end team scenario (`tools/test_sbe_golden_scenario.py`,
  its builder at `tools/fixtures/golden-scenario/build_scenario.py`): the
  real engine, no mocked core, drives start through acknowledge over one
  deterministically built scenario repository (a backend service change, a
  small SQL change, two ready disjoint tasks, one dependent task, one
  planted security-sensitive configuration edit, one evidence requirement,
  one review finding, one ownership transfer). Every pass criterion in the
  spec's own list is proven against the real `bin/sbe`: four or fewer
  BrotherSBE skills touched in the happy path (kickoff, review, handover,
  read from the same JSON `skills/next/SKILL.md` reads, never from prose);
  max three writers with no overlapping owned paths, read from the task
  registry; every completed task closes with a real evidence receipt; only
  the reviewers `sbe review-route` itself selects ever contribute a finding
  (zero for a plain internal file, two for the SQL migration); a duplicate
  finding submitted by two reviewer sources dedupes to one; the planted
  authority-surface edit is caught by `sbe instruction-surface` and
  calibrated in a scratch clone under `/tmp` (declared and reviewed there,
  it is no longer caught, proving the FAIL above was never vacuous, with
  the live scenario repository's own git history hash-verified untouched
  before and after); no merge, rebase, push or deploy argv exists anywhere
  in this suite's own two files (TestNoMergeLaw pattern, calibrated on a
  scratch mutated copy); a session restart (a brand-new `sbe status --team
  --json` subprocess) recovers every closed task purely from disk; the
  team-mode CI postcondition (`status.team_blocking`, folded from the
  parity triage's row 34) is proven as a contract across two real states of
  the same scenario, not assumed clean, because this suite also discovered
  and named two disclosed, unfixed engine gaps along the way (see
  `docs/KNOWN-LIMITS.md`): `sbe status --team`'s evidence scan always
  verifies a receipt against the repository root's own checked-out HEAD,
  never a task's own worktree branch, so a finished task's own evidence
  reads as a severity-1 broken claim by construction; and `sbe converge`'s
  VERIFICATION dimension compares a bare path string against
  `receipt["coveredFiles"]`, a list of objects `sbe evidence run` always
  writes, so the membership test can never match a writer task's own owned
  path. Both are named by exact reason text in the fixture, not routed
  around.

## 1.0.0-rc.9 (2026-08-05)


Wave D of the lean team program: the instruction and configuration trust
boundary, plus the parity rows that serve the release process itself.

- Instruction trust model (LT-401): `docs/THREAT_MODEL.md` gains the "Trust
  classes" section (four trusted instruction sources, everything else
  untrusted data, the baseline instruction rule), mirrored compactly in
  `references/team-execution.md` and in the implementation worker's rule 9.
  A task that legitimately changes an instruction surface is CODE under
  security review for that change, never a live instruction for the worker
  making it.
- `sbe instruction-surface` (LT-401.B, `tools/sbe_instruction_surface.py`):
  names every changed authority surface (CLAUDE.md, `.claude/**`,
  `.mcp.json`, `.claude-plugin/**`, `hooks/**`, agent and skill definitions,
  CODEOWNERS, CI workflows) between a base ref and HEAD; an undeclared or
  malformed one FAILs by name, no relevant change is NO-DATA, never PASS.
  Sixth registry in the honesty sweep (32 checks, 3780 scenarios). Proven by
  `tools/test_sbe_instruction_surface.py` (18 tests, FAIL verdict calibrated:
  neutralizing it kills exactly the 5 FAIL-path fixtures).
- `sbe version bump <new>` (parity row PT-2, `src/brothersbe/versionbump.py`):
  one command moves every version declaration site (VERSION, plugin.json,
  marketplace.json twice, DIGEST.md line 1), refuses malformed shapes,
  refuses sites that already disagree naming each one, refuses a same-version
  no-op, re-reads every site after the edit, and prints the three steps it
  deliberately does not do (CHANGELOG prose, book replay, checksums last).
  Proven by `tools/test_sbe_version_bump.py` (10 tests, calibrated: dropping
  DIGEST.md from the site list kills exactly the 3 happy-path fixtures).
- Fable Forge triage verdict recorded at
  `docs/plans/2026-08-05-fable-forge-triage-verdict.md`: REVISE, five rows
  conflict with ratified registers, egress rows blocked on a founder
  boundary decision, accepted rows fold into existing mechanisms behind
  wave E, nothing from the document executes tonight.
- `sbe_authority_hook.py`: a narrow PreToolUse hook that refuses a worker's
  undeclared edit to an authority-bearing file (CLAUDE.md, `.claude/**`,
  `.mcp.json`, `.claude-plugin/**`, `hooks/**`, `agents/*.md`,
  `skills/*/SKILL.md`, `CODEOWNERS`, `.github/workflows/**`), unless the wave-5
  task registry (`.sbe/tasks.json`) carries an OPEN task whose `ownedPaths`
  declares it. Reuses the authority-surface list from
  `tools/sbe_instruction_surface.py` and the write-tool surface, path
  canonicalization and case-fold confirmation from `tools/sbe_fence_hook.py`
  by import, so none of the three can drift into a second copy. Fails open on
  every error path (an unreadable registry, an unimportable helper, a
  malformed payload); refuses only the one rule this file exists for, and only
  when a worker context is detectable, a heuristic named in full in
  `docs/KNOWN-LIMITS.md`. Wired beside `sbe_fence_hook.py` in
  `hooks/hooks.json`, never replacing it. Proven by
  `tools/test_sbe_authority_hook.py` (43 tests).
## 1.0.0-rc.8 (2026-08-05)

Wave C of the lean team program: explicit human handover.

- `sbe handover prepare/show/acknowledge/reject`: ownership transfer that
  stays with the outgoing owner until the receiver acknowledges, bound to
  HEAD, stale when the code moves, refusing agent identities and every
  forged shape of self handover including dot-fold, case-fold, and Unicode
  normalization aliases. Proven by `tools/test_sbe_handover.py` (34 tests).
- Status reports six handover states per change, and absence is never a
  blocker when ownership is not changing.
- `/brothersbe:handover`: prepare, the exact summary shape, receiver
  inspect and acknowledge guidance, one guided next action.
- A prepared record is a frozen snapshot by design, like every record in
  the family; a task that changes without a new commit is visible through
  live status beside it, and that boundary is stated rather than hidden.

## 1.0.0-rc.7 (2026-08-05)

Wave B of the lean team program: risk-sized review.

- `sbe review-route`: deterministic reviewer selection from a diff. Seven
  trigger priorities, at most two specialists, zero is a legal result,
  unselected triggers named in unmeasured, never a claim of a clean review.
  Proven by `tools/test_sbe_review_route.py` (25 tests).
- Normalized findings in the review record: deterministic fingerprints,
  deduplication that keeps the highest severity and records disagreement,
  low-confidence findings that cannot block, accepted risk requiring a named
  human who is not the reviewer, old records readable unchanged. Proven by
  `tools/test_sbe_review_record.py` (63 tests).
- `/brothersbe:review` consumes the route: mechanical checks first, only the
  selected read-only lenses dispatched, findings normalized through the
  landed write path, contradictions marked for arbitration rather than
  resolved, one next action. Proven end to end by
  `tools/test_sbe_review_skill_fixtures.py` (9 fixtures).

## 1.0.0-rc.6 (2026-08-05)

Wave A of the lean team program: bounded team execution.

- `sbe work brief`: a deterministic, read-only JSON work order for one plan
  task, byte-identical for identical repository state, refused above 8 KB,
  atomic on --out. Proven by `tools/test_sbe_work_brief.py` (22 tests).
- `agents/implementation-worker.md`: the one generic implementation worker,
  worktree isolated, untrusted-content rule stated, compact return contract.
  The agent-surface control gained an explicit writer allowlist; the seven
  reviewers keep their write-tool ban.
- `/brothersbe:work`: one entry point that reads engine JSON, briefs and
  starts one to three disjoint ready tasks, dispatches the worker, verifies
  through `sbe work check` and `sbe work finish`, and hands a claimed task to
  a human while preserving dirty work and single ownership.
- `tools/test_sbe_team_workflow.py`: the end to end fixture proving the eight
  team execution laws on a real scratch repository, including session-restart
  recovery purely from disk.

## 1.0.0-rc.5 (2026-08-04)

Closes the remaining three lifecycle blockers.

- CR-07 and CR-10: the four beginner skills consume `sbe status --json`,
  `sbe doctor --json`, and `sbe status --team --json` instead of interpreting
  rendered prose; rung 5 of next recommends verify only on a FAIL or a named
  obligation, so a T0 change with four NO-DATA gates can no longer loop.
  Proven by the skill audits in `tools/test_sbe.py`.
- CR-03: install.sh's closing doctor grades the TARGET repository and says so;
  new tests replay hooks from an installed layout against the real PreToolUse
  wire contract, prove activation hands off to the recorded `claude` argv, and
  cover a distribution path containing a space. Proven by
  `tools/test_sbe_install.py` (23 tests) under both a full and a bare PATH.

## 1.0.0-rc.4 (2026-08-04)

Closes the first two lifecycle blockers from the release handover, with the
design recorded in `design/lifecycle-blockers/` before any code moved.

- CR-06: single-project `sbe status` discovers the documented dossier layout
  through the same walker the team report uses; flat layout wins when both
  exist; empty repositories keep their exact NO-DATA messages. Proven by
  `tools/test_sbe_status.py` (31 tests, three new).
- CR-08: `sbe verify` mints design, gate, and score receipts into
  `.sbe/evidence`, the store `sbe status` reads, so verify-then-status closes
  on real proof. Receipts on a dirty tree read NO-DATA naming the dirty state,
  and a receipt-write failure can never move verify's exit code. Proven by
  `tools/test_sbe.py` (TestVerifyMintsEvidence) and `tools/test_sbe_evidence.py`
  (TestWriteReceiptAndMintDefault).
- The consumer action's status step now reads a produced, parseable report as
  its pass (exit 0 or 1) and still fails on a crash or usage error, because a
  client mid-change legitimately has open findings.
- The chapter 3 book transcript and the CLI reference now describe the new
  discovery honestly; `evals/replay_book.py` re-executes them.

## 1.0.0-rc.3 (2026-08-04)

Everything below accumulated since `v1.0.0-rc.2` and is folded into this
release. Read together, the entries close the defects that made rc.2 unsafe
to hand to a new project: the installer now targets the invoking project and
refuses to run from inside the distribution directory itself; team profile
fields are applied or rejected by name instead of being silently ignored;
startup context injected at session start is cut from roughly 9100 bytes to
roughly 2400 bytes; a review verdict is now recorded as a durable, commit
bound record rather than a claim that can be typed and forgotten;
documentation is reduced to three install paths that were actually run,
instead of several that only seemed plausible; the release battery no longer
overwrites its own frozen evidence when it reruns; and remote branch
topology is reduced to `main` alone.

This entry also closes the last mechanically closable release blocker named
in the release-plan review's own kill criteria: "distributable bytes change
without a version change." VERSION, `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` could all agree with each other while none
of them were tied to the CONTENT being shipped, so a marketplace user could
believe an install was current while running older bytes that lacked
security, evidence and concurrency fixes; `scripts/test-install-artifact.sh`
already guarded the checksums manifest against drifting from the bytes it
describes, but nothing guarded the VERSION string itself. `tools/
sbe_release_invariant.py` closes that gap mechanically: it diffs a base ref
(default `origin/main`) against HEAD, and FAILs under `--strict` when any
tracked path under `src/`, `tools/`, `bin/`, `skills/`, `hooks/`, `agents/`,
`scripts/`, `install.sh` or `.claude-plugin/` changed in that range without
VERSION changing in the same range. A change touching none of those paths
reads NO-DATA, never PASS, matching this project's own law that absence of
evidence is never a pass; wired into `.github/workflows/brothersbe-gates.yml`
alongside the install-artifact test, and proved by
`tools/test_sbe_release_invariant.py` against real `tempfile` and `git init`
fixtures, including the seeded acceptance case the release-plan review names
by its own words: a distributable change with no version bump FAILs, the
same change with a version bump PASSes, and a docs-only change is NO-DATA.
This release itself is the calibration: running the checker with base
`v1.0.0-rc.2` against this commit reports FAIL before VERSION moved to
`1.0.0-rc.3` in this same change, and PASS after.

- The evidence store no longer poisons itself. A receipt's `coveredFiles`,
  when computed from a diff rather than an explicit `--covers` list, is every
  file that changed in `base..head`, and that diff cannot tell "the code this
  run tested" apart from "another evidence receipt that happened to land in
  the same range". A receipt regenerated at a fixed `--out` path (the
  ordinary shape of a CI re-run: the design, gate and score checks all write
  to well-known paths on every push) is not an edit to any code under test,
  and before this change an unrelated receipt that merely covered its OLD
  bytes by diff-range accident FAILed the moment it refreshed, naming
  "covered file .sbe/evidence/other.json now hashes to ...: the code
  changed after the evidence was made" for a check that never touched that
  file. `src/brothersbe/evidence.py`'s `verify` gains an `exclude_dirs`
  parameter (default: none, so every existing caller not updated in this
  change keeps today's behavior exactly) naming path prefixes whose
  `coveredFiles` entries are still recorded and shown in the note but never
  hashed, timed, or allowed to decide a verdict; a receipt whose ENTIRE
  coverage sits under an excluded path now reads NO-DATA rather than a
  silent PASS. `src/brothersbe/status.py`'s `_scan_evidence`, the one place
  this repository reads every receipt in the store to build BROKEN CLAIMS and
  COMPLETED EVIDENCE, passes the evidence store itself as that exclusion.
  This is an interpretation change, not a schema change: a receipt's own
  `coveredFiles` field still lists an evidence-store path when the diff found
  one, faithfully; only what that record is allowed to prove changed, so no
  receipt already on disk needs regenerating.
  WHAT THIS DOES NOT CLOSE, named rather than left implicit:
  `src/brothersbe/decisions.py` and `src/brothersbe/work.py` also call
  `evidence.verify` and neither passes `exclude_dirs` in this change, so the
  identical accidental coupling can still reach a decision package's judged
  receipts or a task's close postcondition; and the pre-existing,
  already-tested commit-binding rule (a receipt that is itself committed to
  the repository necessarily records a `headCommit` one commit behind
  whatever HEAD becomes once that commit lands, so ANY further commit, T6 or
  not, still names it under BROKEN CLAIMS for that separate reason) is
  untouched. Both are recorded in `docs/KNOWN-LIMITS.md` ("Evidence covering
  evidence").
  Proved by three new fixtures in `tools/test_sbe_status.py::
  TestEvidenceStoreSelfPoisoning`:
  `test_regenerating_the_covered_receipt_does_not_break_the_one_that_covers_it`
  builds a "design" receipt (explicit `--covers`), commits it, generates a
  "gate" receipt with the default diff-based coverage (confirmed by an
  in-test assertion to include `design.json` alongside the real source file),
  regenerates `design.json` in place, and asserts `gate.json` never appears
  under BROKEN CLAIMS;
  `test_the_same_scenario_committed_end_to_end_never_shows_a_covered_file_reason`
  repeats it with every receipt committed, and asserts that wherever
  `gate.json` IS named (the unrelated, pre-existing headCommit staleness,
  which this change does not remove) the reason is never a covered-file
  complaint; `test_a_receipt_covering_only_the_evidence_store_reads_no_data_not_pass`
  pins the limiting case. Calibrated: with `status.py`'s call to
  `evidence.verify` reverted to omit `exclude_dirs`, all three fixtures fail,
  the second and third quoting the exact line this change removes
  (`receipt .sbe/evidence/gate.json fails verify: ... covered file
  .sbe/evidence/design.json now hashes to ..., not the ... the receipt
  recorded: the code changed after the evidence was made`) and the first
  quoting the silent-PASS regression this closes (`.sbe/evidence/
  onlyevidence.json` unexpectedly present in `soundEvidence`); the file was
  restored afterward and its `evidence_mod.verify(...,
  exclude_dirs=(exclude_rel,))` call line matches the version before the
  break, byte for byte. The existing 25 `tools/test_sbe_status.py` fixtures,
  the 19 in `tools/test_sbe_status_team.py`, and the 57 in
  `tools/test_sbe_evidence.py` stay green.

- `tools/sbe_telemetry.py check-update` no longer skips itself in silence
  inside a linked git worktree. `cmd_check_update` tested
  `os.path.isdir(SKILL_DIR/.git)`, true only for a normal clone: a linked
  worktree's top-level `.git` is a FILE holding `gitdir: <path>`, not a
  directory, so that test read False and the whole command returned with
  exit 0, no output, no marker written, and no warning, reproduced this
  session against a real `git worktree add` checkout. A new
  `_resolve_git_dirs` helper follows that `gitdir:` pointer to the
  per-worktree directory (for HEAD, which is genuinely per-worktree) and,
  via its `commondir` file, to the COMMON directory (for refs/heads and
  refs/remotes, which a linked worktree does not duplicate); `cmd_check_update`
  now reads HEAD and refs from the two correctly instead of assuming they
  are the same directory. The genuinely-not-a-git-install path, and a
  worktree pointer this cannot follow, now say so on stdout instead of
  returning nothing, so silence is never the answer to "did the check run".
  Proved by two new fixtures in
  `tools/test_sbe.py::TestCheckUpdateFindsAWorktreeGitdir`, each building its
  own repo and vault in a temp directory:
  `test_check_update_writes_the_marker_from_a_linked_worktree` builds a real
  linked worktree and asserts the version marker is written (proof the
  command reached code the old check made unreachable there), and
  `test_check_update_names_a_non_git_directory_instead_of_silence` asserts a
  genuinely non-git directory names itself in the output rather than
  printing nothing. Calibrated: reverted to
  the old `os.path.isdir(SKILL_DIR/.git)` check, both fixtures read
  `AssertionError: False is not true : check-update never reached the
  marker write from a linked worktree; output was: ''` and `AssertionError:
  '<tmp path>' not found in '' : a genuinely non-git directory produced no
  named line: ''`; the file was restored and its SHA256
  (`6c23f3814f7e5af096c1b8011ad79f981f8941b322969467359d998446c98ba2`) and
  `git hash-object` (`b3163f67219cf88bb71baa565f40538bb16b1512`) match the
  fixed version from before the break. A narrower residual gap, a worktree
  whose `commondir` file is itself missing or broken, is named in
  `docs/KNOWN-LIMITS.md` rather than forced through more of this file than
  the reproduced defect asked for. The full suite in `tools/test_sbe.py`
  (93 tests) stays green.

- The three hard-gate evidence files (`numbers-manifest.json`,
  `migration-receipt.json`, `ran-receipt.json`, read by `tools/sbe_gate.py`)
  gain the same commit binding `src/brothersbe/evidence.py`'s own `sbe
  evidence` receipt store already carries. An optional `headCommit` field,
  the same field name and comparison the evidence store uses, is now read by
  `gate_numbers`, `gate_migration` and `gate_ran` via two new shared
  functions, `_current_head` (the directory's `git rev-parse HEAD`, or `None`
  when there is nothing to bind against) and `_commit_problem` (the FAIL
  sentence for a mismatch, or `None`). A receipt naming a commit that is not
  the directory's current HEAD is stale: the code it covered has moved on,
  and the gate now FAILs rather than PASSing over it, so a receipt copied
  forward from an earlier commit no longer clears the gate at a later one. A
  receipt naming no commit at all is left exactly as this gate always treated
  it: this gate cannot tell a receipt that predates the field apart from an
  operator who chose not to record one, and every worked receipt this
  repository ships today is the first kind, so none of them are affected.
  That residual gap, and a second, smaller one (six new eval cases move
  `evals/run_evals.py`'s own case count past what a few shipped docs outside
  this change's scope quote), are both named in `docs/KNOWN-LIMITS.md` rather
  than forced through files this change was not scoped to touch.
  Proved by `evals/run_evals.py`'s
  `a-stale-headcommit-ran-receipt-no-longer-passes`,
  `a-stale-headcommit-numbers-manifest-no-longer-passes`,
  `a-stale-headcommit-migration-receipt-no-longer-passes` (each pins a
  receipt sound in every other field to an old commit, moves HEAD on with a
  second, unrelated commit, and asserts FAIL), `a-non-string-headcommit-is-
  caught` (a `headCommit` of the wrong type is a broken claim, not a stale
  one, mirroring the type checks `numbers-manifest.json`'s own `snapshot_id`
  already gets), and two controls, `a-headcommit-bound-to-the-current-commit-
  still-passes` and `no-git-history-leaves-the-receipt-unaffected`, proving
  the check catches staleness specifically rather than presence, or any
  directory at all. Calibrated: with `_commit_problem` neutralized to always
  return `None`, the three stale-receipt cases and the type-check case all
  read `want=FAIL got=PASS REGRESSION`; the file was restored and both its
  SHA256 and its `git hash-object` match the version before the break
  (`c0ce2c240397494f868cd1eb7438b69795027693d618872c9048d7a18475fa27` and
  `481e50e8b49efe68377402cc2a9840379f397ec9`).

- A receipt now says WHICH CHECK it ran, and `sbe status` believes the receipt
  instead of reading the command line. This closes a live bypass, reproduced
  before it was fixed: `src/brothersbe/status.py::_receipt_kinds` decided which
  of the design, gate and score obligations a receipt satisfied by
  substring-matching its recorded `argv`, so one receipt for `/bin/cat
  tests/test_design_of_gate_score.txt` spelled all three words and cleared all
  three obligations, for a command that ran no check at all. A T2 change owing
  three checks went green on a file read. `sbe evidence run` gains
  `--kind {design,gate,score}` (repeatable), recorded as the receipt fields
  `checkKinds` and `checkKindsSource` and covered by the `runId` seal, so a
  kind cannot be typed into a receipt after the fact and cannot exist without a
  run that happened. `status` reads that field through
  `evidence.declared_kinds`, the single reader of it, and reads the command
  line for nothing. A receipt that declares no kind clears no obligation: it is
  NO-DATA there, never a silent pass, and each shape of that (a receipt older
  than the field, a field that does not parse as a kind list, an honest run
  that declared nothing) is named in the MISSING EVIDENCE finding rather than
  dropped, so nobody is told an obligation is unmet while receipts sit unread
  in the store. The vocabulary lives once, in `evidence.CHECK_KIND_NAMES`, and
  `status.CHECK_KINDS` is derived from it rather than keeping a second copy
  that could drift.
  FORWARD ONLY, never a rewrite: the evidence schema goes to `1.2`, `1.0` and
  `1.1` stay readable, `_fields_for` now judges every receipt against the
  fields its own version defined (the same compatibility precedent `1.1` set
  for `argvRedactions`, generalized into a `FIELDS_INTRODUCED_IN` timeline),
  and no receipt already on disk is touched.
  WHAT THIS DOES NOT DO, stated because a control that oversells itself is
  worse than none: a declared kind is the operator's statement, bound to a real
  run and sealed against later editing. It is not proof that the command
  performs the check it names. The wrapper starts a process and times it; it
  does not understand it. The recorded argv sits beside the declaration so a
  reader can see the two disagree.
  Proved by `tools/test_sbe_status.py::TestCheckKindIdentity::
  test_a_command_named_after_the_checks_clears_no_obligation`, which builds a
  T2 change, generates a real receipt over a command whose argv names all three
  checks, asserts the receipt verifies as sound evidence (so the fixture cannot
  pass for the wrong reason) and asserts all three obligations stay open.
  Calibrated: with the old argv classifier reinstated in `_receipt_kinds`, that
  fixture fails ("AssertionError: 'design completeness check' not found in
  '\n  clean. scope: declared tier T2 from ...00-intake.json\n\n' : a command
  that ran no check cleared the design completeness check obligation by naming
  it in a filename"), along with three others in the same class; the file was
  restored and its SHA256 matches the version before the break
  (`cff8a5c75631f48f843352e8adf6e43ec41a42aba387ecea5cb711390d3059cf`). Three
  more calibrations on the generator side, each restored and hash-verified
  against `221e8878af0cffe6e8fcdec54010ef3aab4b5b64d66b80da7db909f131652c8b`:
  removing `checkKinds` from `SEALED_FIELDS` makes the forged-kind fixture read
  PASS where FAIL is asserted; removing `checkKindsSource` from
  `REQUIRED_FIELDS` makes the missing-provenance fixture read PASS; and
  reverting `_fields_for` to the pre-`1.2` timeline fails an honest `1.1`
  receipt for a field its version never had.
  `sbe status --json` and the rendered text now name a receipt's declared kinds
  on its own line, and `sbe evidence show` prints them with the provenance
  sentence, so a reader never has to infer the identity from a command line
  again. Documented in `docs/CLI.md` under both `sbe evidence` and `sbe
  status`.

- The task registry (`src/brothersbe/tasks.py`) no longer loses an open task
  to a race: `cmd_open` and `cmd_close` used to call `load_registry` and
  `save_registry` with nothing serializing the two, so two concurrent `sbe
  task open` calls could both read the same tasks list, both append their
  own record to their own in-memory copy, and the second `save_registry`
  call would silently rewrite the file with a blob that never saw the
  first writer's task at all. Both commands now hold one exclusive lock for
  the whole read-modify-write (`_registry_lock`, an `fcntl.flock` on a
  `.sbe/tasks.json.lock` sidecar beside the registry, mirroring the writer
  lock `tools/sbe_telemetry.py` already ships and the store lock this same
  loop added to `brothersbe.decisions`, so the codebase carries one locking
  style rather than a second one invented here). `save_registry`'s own
  on-disk format is untouched, byte for byte: this is not a schema change,
  and a registry written before this change still parses the same way. The
  lock sidecar is exempted, by exact name alongside the registry file
  itself, from the diff `sbe task close` reads for its own postcondition,
  for the same reason the registry file already was: opening a task now
  touches both, and counting either would make an ordinary single-writer
  flow unable to close clean without --force. `tools/test_sbe_tasks.py::
  TestConcurrentWriters::
  test_twenty_concurrent_writers_open_distinct_tasks_all_survive` spawns 20
  real `sbe task open` subprocesses against one temp registry, releases
  them from a filesystem barrier so the 20 writes genuinely overlap, and
  asserts all 20 land as 20 distinct open tasks, 0 lost. Calibrated: with
  the lock turned into a no-op the same fixture fails reliably, losing more
  than half the writes in the run that produced this line ("expected 20
  distinct open tasks, got 10: ['w0', 'w10', 'w11', 'w13', 'w19', 'w4',
  'w5', 'w6', 'w8', 'w9']"); the file was restored afterward and its
  `git hash-object` matches the version before the break
  (`24d3071fba569a0b508f6ada23689dce6d0cd481`). Left out of scope for this
  change: `src/brothersbe/work.py`'s `cmd_remove` also calls `load_registry`
  and `save_registry` directly, its own read-modify-write outside this
  lock, so that path is a real residual race this change does not close;
  `work.py` is not a file this change touches, and the gap is named here
  rather than silently left unsaid.

- Decision packages (`src/brothersbe/decisions.py`) no longer lose a package
  to a race: `next_id` was a plain, unlocked directory scan, and a
  `DECISION.md` already bound to the SAME commit as an incoming write was
  treated as "rewrite in place", so two writers that both read "the next id
  is 001" both wrote it and the second landed on the first in silence.
  `write_package` now holds one exclusive lock per store (`_store_lock`, an
  `fcntl.flock` on a `.lock` sidecar beside the store directory, mirroring
  the writer lock `tools/sbe_telemetry.py` already ships) across the whole
  append-only check, id allocation and file write. A `DECISION.md` bound to a
  DIFFERENT commit is still refused exactly as before: that is the
  append-only law and this change does not touch it. A collision bound to
  the SAME commit is now itself refused as a write target and reallocated to
  a fresh id under the same lock instead of being overwritten, so the
  incoming package survives as its own directory rather than erasing the
  one that got there first. The docstring's old caveat, "build and write one
  package at a time", is removed: the code that made it true is gone, and
  every production caller (`record_from_run`, `record_tier_decision`,
  `record_forced_close`) already built and wrote in the same call, so this
  closes a real race between separate `sbe` processes rather than changing
  any caller's own code. `tools/test_sbe_decisions.py::
  TestConcurrentWriters::
  test_fifty_concurrent_writers_against_one_store_lose_none` spawns 50 real
  subprocesses against one temp store, releases them from a filesystem
  barrier so the 50 writes genuinely overlap, and asserts all 50 land as 50
  distinct, durable packages naming their own worker, 0 lost. Calibrated:
  with the lock removed the same fixture fails reliably, in different
  concrete ways across repeated runs (a silent overwrite, "worker 6's
  package landed at .../007-gate-race-check-fail/DECISION.md, already
  claimed by worker 0: one write landed on top of the other", and a bare
  `DecisionUnwritable` from the unlocked `os.makedirs` race itself, "Errno
  17 File exists"); the file was restored afterward and its SHA-256 matches
  the version before the removal.

- `sbe pr verify` (`src/brothersbe/prverify.py`) no longer trusts an owner or
  repo segment shaped like `.` or `..`: `REPO_SHAPE_RE`'s character class
  allows periods (real names carry them), so it matched `../repo` and
  `owner/..` on shape alone; `valid_repo_shape` now rejects either side
  being exactly `.` or `..`, explicitly, before any fetch. Two API-sourced
  values that reach a URL path segment are now validated before that
  composition rather than trusted as already safe because they came from a
  JSON body: the pull request's head sha must match `fullmatch
  [0-9a-f]{7,40}` before the check-runs URL is built, and the pull
  request's base ref is urlencoded via `urllib.parse.quote(base_ref,
  safe="")` before the branch-protection URL is built, so a `/` inside it
  becomes `%2F` instead of an extra path segment. Adversarial fixtures in
  `tools/test_sbe_prverify.py` cover `../repo`, `owner/..`, `./repo`,
  `owner/.`, a hostile base ref (`main/../../orgs/evil-org/repos`), and a
  hostile head sha (`deadbeef/../../evil`). Calibrated: each guard removed
  in turn made its own new fixture fail (and only that one), naming the raw
  unescaped URL the module would otherwise have built; restoring the file
  made every fixture pass again, and the restored file hashes identical to
  the original.

- The zero-network claim in `SECURITY.md` and `docs/THREAT_MODEL.md` is now
  scoped instead of blanket: tools that run inside a session make no network
  calls, with two named exceptions, `sbe pr verify` (GitHub API, token-gated,
  opt-in, documented in `docs/KNOWN-LIMITS.md` lines 713-731) and
  `install.sh` (the one-time installer, not a tool a session invokes; `git
  ls-remote` at line 98, `git clone` or `pull --ff-only` at lines 106-110,
  then `claude plugin` commands). `SECURITY.md`'s own suggested audit grep
  now also walks `src/`, `hooks/`, `scripts/`, and `bin/`, not only `tools/`.

- `tools/test_sbe.py`'s zero-network AST scan (`TestAuditableSurface.
  test_the_zero_network_property_holds_by_ast`) now parses the same surface
  `SECURITY.md` claims is auditable: `src/brothersbe/*.py`, `hooks/`,
  `scripts/`, `bin/sbe` and `install.sh`, not only `tools/`. One exact-path
  exception, `src/brothersbe/prverify.py`, is allow-listed by name (`sbe pr
  verify`'s own documented GitHub API client) so no other module can hide
  behind it. The shell-side pattern check now also flags `nc`, not only
  `curl` and `wget`. `install.sh`'s own documented `git` network calls stay
  untouched by this scan; the property under test is the absence of direct
  network imports and network CLI invocations, not the absence of `git`.
  Calibrated: `import urllib.request` planted in `src/brothersbe/status.py`
  fails the test naming that exact file, restoring the file makes it pass
  again, and the restored file hashes identical to the original.

- `.github/workflows/brothersbe-gates.yml` now runs 13 test suites that
  existed on disk and passed locally but had never once run on a merge:
  `test_sbe_adopt.py`, `test_sbe_book.py`, `test_sbe_bypass.py`,
  `test_sbe_converge.py`, `test_sbe_decisions.py`, `test_sbe_evidence.py`,
  `test_sbe_install.py`, `test_sbe_plan.py`, `test_sbe_prverify.py`,
  `test_sbe_status.py`, `test_sbe_status_team.py`, `test_sbe_tasks.py`, and
  `test_sbe_work.py`. Every `tools/test_sbe_*.py` file now appears in the
  workflow exactly once, except `test_sbe_prverify_live.py`, which stays
  deliberately unwired: it is an opt-in script that needs both
  `SBE_LIVE_GH_REPO` and `SBE_LIVE_GH_PR` plus a discoverable GitHub token,
  none of which this workflow provides, and the workflow now carries a
  comment saying so next to the `test_sbe_prverify.py` step it sits beside
  (that step is the canned, offline suite and needs neither network nor a
  token). No strictness flag changed and both OS legs still run every step;
  the diff is purely additive.

- The book grew its persona spine: Part IV, one deep dive each for the
  backend engineer, the data engineer, and the platform lead, and Part V,
  working as one team, the vault opened in Obsidian for real, and the
  mastery ladder. Six new chapters, roughly twenty two thousand words,
  every terminal excerpt re-executed by the replay harness (142 blocks,
  0 differ) and the two blocks the day's other changes made stale patched
  from live runs, never by hand. The cover and the explainer route each
  persona to their chapter.

- The team operating model is designed end to end and recorded the tool's
  own way: a full dossier at design/team-operating-model (real intake, a
  deliberate recorded raise to T3, strict design check green on all five
  gates), the human-facing docs/TEAM-PLAYBOOK.md, three integration work
  items (Jira and Confluence, Asana, Teams) designed against research from
  opened vendor pages with every unverifiable limit named, and the team
  vault pattern shipped in memory-template/TEAM-VAULT.md. Zero engine code
  changed; the stance is one way: the ledger broadcasts, it never obeys.

## 1.0.0-rc.2 (2026-08-01)

Everything below accumulated in one pre-release stretch. `v1.0.0-rc.1`
(commit `dacee900`, 2026-07-31) was cut partway through this list and is
superseded by this release before any general announcement.

- The explainer grew from a leaflet into a field guide, and help learned to
  draw a map. `docs/explainer/index.html` is now twelve sections behind a
  table of contents: a ninety second story, the whole journey end to end
  with real excerpts lifted from the replay-tested book and labeled as real,
  loop advice, an honest good-at and not-good-at table, six use cases, three
  follow-along tutorials on the book's estate, and a co-writing section for
  developers; its self-containment guard is unchanged and still bites. The
  help skill gained a build-the-map flow with a shipped template
  (`skills/help/map-template.html`, eleven slots, honest absent-sentences,
  held by `TestHelpMapTemplate`); filling it writes an offline
  `brothersbe-map.html` into the user's project. The README names the
  session-start version-change announcement beside the update command, and
  `program/DIRECTORY-SUBMISSION.md` records the verified truth about
  listing: the official directory is curated with no application process,
  the community marketplace takes submissions through a web form, packet
  prepared for the product owner's click.

- Install docs now teach only the paths that were run, not the paths that
  seemed plausible. README, `docs/SETUP.md`, `docs/ROLLOUT.md`, and
  `docs/RELEASE.md` name the marketplace add plus install pair as the
  primary path, because that pair was executed end to end in an isolated
  config directory on 2026-08-01 (marketplace add exit 0, install exit 0,
  plugin listed 1.0.0-rc.1 enabled, content verified at main head 71f4d3f
  with all ten skills), and `scripts/test-install-artifact.sh` passed for
  that same commit. Stale tag claims in `docs/ROLLOUT.md` and
  `docs/RELEASE.md` are corrected: `v1.0.0-rc.1` lags main by 9 commits and
  a fresh tag is a decision for the product owner, not a fact to assert.
  Two guided skills are fixed to match what they actually do: `adopt` now
  runs its own command instead of describing one, and `next` explains its
  recommendation in plain words before the command. A new beginner
  explainer page ships at `docs/explainer/index.html`, a single
  self-contained file held by a new guard, `TestExplainerSelfContained`,
  that proves it carries no external reference and no em or en dash. The
  program ledger gains three work items (`BR-0201`, `BR-0301`, `BR-0310`)
  alongside `BR-0000`, each recording what shipped this train and what is
  still open by name. Held by `python3 tools/test_sbe_book.py` and a dash
  scan over every file this entry touches.

- `sbe impact --strict` no longer fails a run over pure absence. A NO-DATA
  verdict whose derived answers are all at their lowest values (a docs, data or
  test-only diff no detector covers, or an empty diff) now exits 0 under
  `--strict`, with a stderr sentence saying so; a NO-DATA carrying detector
  hits that propose a tier above T0 with no intake to reconcile them against,
  and a diff that could not be read at all, both still exit 1. Before this,
  every docs-or-data pull request through the consumer workflow went red:
  `bin/sbe impact . --json --strict` exited 1 with verdict NO-DATA and every
  derived answer at its lowest value, which graded absence, and this project's
  law is that NO-DATA never decides an exit code. The strict semantics are now
  documented in full in `docs/CLI.md`'s impact section (they were not written
  down anywhere before), and the consumer workflow's comment states them where
  the flag is set, so the change is visible in the workflow diff a human
  reviews, per L16. Held by `TestStrictOverAbsence` in
  `tools/test_sbe_impact.py`, four fixtures, calibrated by reinjecting the old
  exit rule: exactly the two absence fixtures went red, the two
  evidence-still-blocks fixtures stayed green, and the fixed file was restored
  with a matching SHA256 before and after.

- A decision can now be read back on demand and traced end to end. `sbe explain
  <id|gate|check>` prints a recorded decision package, or, when no run has
  written one, regenerates a package from the shipped registry with the verdict
  section marked NO-DATA, because no run has been made; a package bound to a
  different commit is never overwritten, the new one allocates the next id and
  names the one it supersedes, so packages stay append-only. `sbe lineage
  <artifact>` walks the task binding, evidence receipts, decision packages,
  notes and git commits oldest to newest with an evidence pointer on every hop;
  an absent store, including the notes store that ships in Loop 4, renders one
  named NO-DATA hop rather than a silently shorter chain, and a receipt that
  fails verification stays visible as a broken-receipt hop. Four calibrated
  evals feed each new control its defect: a WAIVED trigger packaged as PASS, a
  line outside the verdict grammar copied into a package, a package rewritten
  under an older commit, and a lineage hop carrying no evidence pointer. The
  command table in `docs/CLI.md` gains both rows, and every shipped doc that
  prints the eval count now prints the live 521, recomputed from a run rather
  than typed. Loop 2, tasks 5 to 8 of 8. Held by `TestExplain`, `TestLineage`,
  the four eval cases above, and the doc-count eval
  `no-shipped-doc-prints-an-eval-count-the-suite-does-not-produce`.

- The product now opens with a guided layer instead of a command inventory.
  Four new skills route a person who does not know the machinery into the
  machinery that exists: `/brothersbe:start` detects prior state and resumes it
  or asks for the outcome in plain language, `/brothersbe:next` walks a fixed
  priority ladder and returns exactly one recommended action with its reason,
  `/brothersbe:status` reframes the status command as a plain answer with one
  next action, and `/brothersbe:help` explains the product before it lists it.
  No engine file changed: the skills wrap commands that already ship, per the
  master plan's rule to wrap the proven engine rather than rewrite it. The
  README's first screen now answers what this is, why a beginner should care,
  and the one first move, with the full engineering reference intact below a
  named divider. The public release program itself is tracked in `program/`
  (the founder's master plan verbatim, PROGRAM.yaml, and work items), with the
  unbuilt parts of its section 9 named as unbuilt in program/README.md. Held by
  the existing frontmatter, citation-drift, and YAML tests over `skills/`, the
  eval battery, and the manifest check.

- The other two decisions worth recording now record themselves: a tier raised
  or disposed by `sbe impact`, and a forced task close. Both go through helpers
  that return nothing and are invoked as bare statements, so neither can move an
  exit code, and both catch every exception class and say so rather than
  escaping. Two honesty rules are enforced where they are easy to get wrong: a
  raise whose disagreements are all disposed is recorded WAIVED, never the PASS
  the impact report prints, because copying PASS onto a suppressed control is
  exactly the defect these packages exist to catch; and a forced record that
  CLAIMS a PASS, or carries no verdict at all, is recorded NO-DATA with the
  claim named rather than believed. Under `--json` the package still gets
  written and its sentence goes to stderr, so machine-readable stdout stays
  parseable. Loop 2, task 4 of 8. Held by `TestOtherTriggers`, calibrated by
  eight deliberate breaks with none uncaught. Two of the book's terminal blocks
  print those commands and are regenerated from live runs in the same change

- A failing or waived gate now writes its own decision package, without the
  writing ever being able to change what the gate decided. `sbe verify`, `gate`
  and `score` tee their delegate's output, parse only lines matching the shipped
  verdict grammar, and record one package per FAIL and per WAIVED. A PASS is
  never packaged. A line outside the grammar is COUNTED and never copied, so an
  unrecognized line cannot smuggle its text into a shared artifact.
  The no-effect-on-exit-code property is structural rather than promised:
  `_record_decisions` returns nothing, so no caller has a value to fold in; every
  caller returns the delegated tool's own code, computed before the call; and
  every exception class is caught there, printed with its class named, and
  stopped. A fixture proves it by making the write fail while the gate FAILs, and
  the exit code stays 1. `--no-decisions` suppresses the write and SAYS it did,
  because a silent suppression is indistinguishable from a tool that never ran.
  Loop 2, task 3 of 8. Held by `TestGateTriggers`, calibrated by six breaks with
  none uncaught

- A decision package now carries the code that decided, not just the verdict it
  reached. `deciding_code` returns the check's own function, excerpted at the
  source span it names, resolved through the shipped check registries rather
  than from memory; `logic_flowchart` draws the check's parts from what the
  registry DECLARES it reads, and every verdict the registry gives no example
  for becomes a NO-DATA node rather than a confident arrow. An unknown check
  gets NO-DATA and never an invented span, and a name that two registries
  declare (`migration`, from both `sbe_gate.py` and `sbe_plan.py`) is reported
  as ambiguous with every declaration named, rather than resolved quietly to
  whichever was found first. Neither helper starts a subprocess. Loop 2, task 2
  of 8. Held by `TestDecidingCode`, calibrated by nine deliberate breaks each
  traced to the single test that caught it

- One `.sbe-exempt` format, two scanners, and each used to refuse a file addressed only
  to the other, so this repository could not pass its own whole-root gate scan with both
  shipped exemption files well-formed: `sbe_gate.py --strict .` FAILed the
  templates/dossier exemption (a `checks:` file with no `gates:` line) as "records a
  reason but names no gates", and `sbe_design.py --strict .` FAILed the new teaching
  waiver the same way in mirror. The rule now, in both parsers: an exemption naming ONLY
  the other registry's field is addressed to that scanner, which honors it and PRINTS it
  as a WAIVED line, and this scanner skips it, so every exemption file stays visible in
  exactly one shipped report; a file naming NEITHER field is still refused by both,
  because that shape is an off switch, not an exemption. Alongside this,
  docs/for-engineers/examples/infra-topology gains a `gates: approval` waiver stating
  that its APPROVAL is DESIGNED to fail as pedagogy (the CI note in
  docs/guides/01-quickstart.md records the scoping decision this mirrors), so a
  whole-checkout scan now reads the teaching refusal as WAIVED with the reason printed,
  never as this repository failing its own controls and never as a PASS; the example's
  numbers, migration and ran fixtures stay unwaived and judged. The publish checklist's
  self-consistency line (`sbe_gate.py --strict .` exits 0) is true for the first time
  since the estates wave tightened the exempt gate. Proof: `TestExemptionAddressing` in
  `tools/test_sbe.py`, four fixtures, calibrated red against the parsers with the
  addressed-elsewhere branches removed (a design refusal where exit 0 was asserted, a
  gate refusal where an approval FAIL was asserted, exit 1 at the repo root) before the
  fix was restored, the restore verified against the pre-recorded `git hash-object` of
  both tools.

- `sbe score` reports on the directory you asked about, not the one you happen
  to be standing in. The split that opens the report groups checks by whether
  they opened a file inside the directory being reported on, and that directory
  was read from the WORKING directory. Running the tool from its own checkout
  against another tree therefore inverted the entire report: `citation-inventory`,
  reading THIS repository's own docs, was filed under "these verdicts are about
  the code here", while `silent-failure-lints`, which had just read the caller's
  tree, was filed under "a verdict here is not a statement about the code in this
  directory". The one line the reader came for sat beneath the heading that
  disowned it, which is the exact failure the split was added to prevent. The
  anchor is now SBE_LINT_ROOT, then the positional directory, then the working
  directory, matching `_resolved_sources`. Found while scanning three outside
  repositories. Held by `TestReportIsAboutTheScannedTree`, calibrated by
  restoring the working-directory anchor and watching both of its tests go red

- Help means help on EVERY `sbe` subcommand, closing external-proof open item 8: the
  whole-surface sweep of the defect class fixed on the three telemetry data commands in
  the entry further down ("make help mean help on the data commands"). Two mechanisms
  produced the same wrong answer, exit 2 for an explicit `-h`/`--help`, on all 23
  subcommands: the top-level parser built every child with add_help=False while
  argparse's REMAINDER drops a LEADING flag, so `sbe intake -h` was refused by a parser
  with no help to give and never reached the tool that had one; and the package modules
  behind `evidence`, `task`, `work` and `pr` caught argparse's SystemExit(0) for help
  and folded it into exit_usage, so `sbe evidence run -h` printed the RIGHT usage and
  still exited 2. Run standalone, three tools were worse than a wrong exit code:
  `sbe_design.py -h` and `sbe_score.py -h` stripped flags wholesale and ran a REAL scan
  (the `data-export --help` shape again), `sbe_fence_hook.py -h` fell into hook mode and
  sat reading stdin, and `sbe_decide.py -h` was read as a table named '-h'. Now the
  passthrough commands (design, gate, score, intake, decide, fences, plan, evidence,
  task, work, pr) are dispatched by hand in `src/brothersbe/cli.py` before argparse sees
  their argv, every other child parser answers `-h` itself, each owning surface prints
  its own usage and exits 0 before touching anything, and a flag a surface does not know
  is refused with usage and exit 2, never silently dropped (`sbe_intake.py`'s refusal
  was exit 1, now 2, matching the CLI's documented table). The bare hook invocation of
  `sbe_fence_hook.py` is untouched and still fails open, and its usage prints on stderr
  because stdout is that tool's decision channel. Proof: `TestHelpMeansHelpOnEveryCommand`
  in `tools/test_sbe.py` (every command in cli.COMMANDS, both help spellings, plus the
  scanning tools examining nothing on -h and the whole-surface bad-flag refusal) and a
  `TestHelpMeansHelp` class in each owning suite: `test_sbe_evidence.py`,
  `test_sbe_tasks.py`, `test_sbe_work.py`, `test_sbe_prverify.py`, `test_sbe_plan.py`,
  `test_sbe_fence_hook.py`, 17 fixtures in all. Calibrated red in three rounds by
  reinjecting add_help=False plus the argparse-first dispatch, the SystemExit fold in
  each module, and each tool's missing help branch: every help fixture failed (a
  returncode of 2, or a real scan where usage was asserted) before the fix was restored,
  each restore verified byte-identical against the pre-recorded `git hash-object` of the
  fixed file rather than by `git checkout`.

- `sbe adopt` no longer proposes ghost paths. The proposal used to carry
  protectedPaths and CODEOWNERS entries hardcoded to this repository's own
  layout (the plugin manifest, `hooks/`, `src/brothersbe/`, the release
  files), none of which exist in a foreign clone, so a consumer applied
  protection rules over paths that protect nothing while looking like they
  do; the first external-proof run named this as its one deliberately
  deferred repair. Now each layout path is proposed only if it exists under
  the target root, a category that loses paths has every missing one named
  under the policy's `_notProposed` block (and in a CODEOWNERS comment, and
  as NOT-PROPOSED lines in both output modes) instead of vanishing, and the
  two paths the kit itself creates (`.brothersbe/` by `sbe adopt --apply`,
  `design/` by `sbe init --apply`) stay proposed unconditionally, because
  existence-checking a path your own first apply creates makes the second
  apply disagree with the first. Proof: `tools/test_sbe_adopt.py`
  `TestGhostPathsNeverProposed` (eight fixtures) plus
  `TestAdoptOnThisRepository` (nothing real is dropped where every path
  exists), each counted green only after being shown red against the
  defect it names: eight against the restored fixed-proposal behavior, and
  the second-apply fixture against a filter that existence-checks the
  self-created paths too.

- External proof round one: three public estates (a FastAPI application, a dbt
  project, an infrastructure deployment) ran the whole assurance path with
  attack rounds, and four defects they surfaced are fixed with calibrated
  tests in the same train: receipt matching is shlex-canonical in converge
  AND work, so a quoted verification command finally binds its own receipt;
  detector kinds honor content patterns, so a SELECT-only model is no longer
  ordered to produce a data model; a markdown-escaped pipe survives the
  07-verification table; and the migration triplet fires only on
  migration-shaped paths. docs/EXTERNAL-PROOF-2026-07-31.md carries the whole
  account: what the tools caught on foreign trees, what broke, what stays
  open by name, and what this round does not prove.

- The update notifier's state file is namespaced to this tool. PARITY.md names
  the notifier as a mechanism this skill shares with BrotherModeUp, and both kept
  "which commit did the operator last see" under the SAME basename in
  `<vault>/99-System/telemetry`. The vault path is the operator's own choice and
  nothing reserves it, so pointing both tools at one vault is a supported setup:
  under the shared name each overwrote the other's stamp every session, and both
  then reported a version change on every following start, forever, reading the
  sibling's commit hash as their own drift. The mechanism meant to catch a real
  change became a permanent false alarm. Observed on a real machine 2026-07-31,
  the day both vaults were pointed at one directory. Held by `TestVersionMark`,
  whose sibling comparison reads BrotherModeUp's real source when it is installed
  and reports NO-DATA rather than passing when it is not, and calibrated by
  restoring the shared name and watching both of its tests go red

- The silent-failure lint no longer reads English as Swift. Every pattern now
  carries an explicit language scope, and `try!` is scoped to `.swift`, where it
  is syntax; unscoped, it matched the ordinary word in prose, so a pure-Python
  file whose docstring reads "Give it a try!" was reported as a discarded error
  at GATE severity, which under `--strict` blocks a merge. Found by running this
  lint against a real outside tree (pallets/click, whose
  examples/colors/colors.py carries exactly that sentence), which is also the
  first time this project's headline check has been exercised on code it did not
  write. Held by
  `TestLintSelfSkipThroughSymlink::test_a_language_scoped_pattern_never_fires_on_another_language`,
  calibrated in both directions by reinjecting the unscoped pattern and watching
  exactly that test go red, then restoring it: the prose file must come back
  clean AND a real Swift force-try must still FAIL, so deleting the rule cannot
  pass the test

- The five first-rank commands were audited against their own specs and the
  nine confirmed defects fixed, each with the test that now holds the fix.
  `sbe pr verify` validates `--repo` against an anchored `owner/name` shape
  before token discovery and before any fetch, so a hostile value never
  reaches URL composition (`REPO_SHAPE_RE`, `valid_repo_shape`); it reads
  REQUIRED CHECKS and CODEOWNERS from the one authoritative source, the base
  branch's protection endpoint, instead of inferring CODEOWNERS from the
  presence of a file and REQUIRED CHECKS from an unfiltered check-run scan,
  and the scan survives only as prose labeled ADVISORY inside the detail line;
  an unreadable reviews endpoint is now carried as an explicit unavailability
  so "no satisfying approval" and "could not check" stop collapsing into one
  verdict. `sbe converge` splits SCOPE's leftovers in two: a changed file that
  no impact detector recognizes and whose extension is outside the tracked
  source-text set is reported as `unmeasured`, a category distinct from
  `unplanned`, and it is named in the PASS detail rather than absorbed into
  it. `sbe status --team` refuses a `designRoots` entry that resolves outside
  the repository root, by its literal spelling and as a visible severity-3
  FAIL rather than a silent skip; it computes each open task's postcondition
  against its declared ownership through the same `tasks.postcondition` that
  `sbe task close` refuses against, so a merge blocker this run can see for
  itself is reported at severity 2 with `basis: observed`; and it fills the
  two empty severity slots, ready-to-start tasks (8) and a fully closed plan
  (9), both `derived` and both suppressed when the registry was unreadable, so
  neither is ever guessed from data the run could not read. Held by
  `tools/test_sbe_prverify.py`, `tools/test_sbe_converge.py`,
  `tools/test_sbe_status_team.py`, `tools/test_sbe_work.py` and
  `tools/test_sbe_plan.py`, all passing, with the eval bed at 517 passed, 0
  regressions

- `sbe status --team` reads every active change under `design/` (plus any
  `designRoots` a team profile adds) into one blocker-first report over ten
  severities, broken claims first, next actions last, with zero network by
  construction: approval facts come only from a saved `10-approval.json`, and
  staleness against the current head is computed and labeled `derived`, never
  presented as observed
  (`TestEvidenceAndConvergence::test_a_stale_approval_report_is_derived_not_observed`,
  calibrated by disabling exactly that control and watching exactly that test
  go red, restored hash-verified). Every finding carries the honesty field
  `basis` (observed, derived, unavailable), and an unreadable task registry
  keeps its severity slot visible as `unavailable` with a nonzero exit
  instead of vanishing
  (`TestJsonContractAndExit::test_an_unreadable_registry_is_an_unavailable_finding_and_a_nonzero_exit`).
  Scope conflicts are computed pairwise over ALL open registry records rather
  than per change, because plan task ids are per-change (every derived plan
  starts at T01) while the registry is one global fence table; the fixture
  that forced this design names two agents holding the same path from two
  changes
  (`TestConflictsAndForced::test_overlapping_open_tasks_across_changes_is_a_scope_conflict_naming_both`).
  A plan with no convergence report is NO-DATA at the convergence severity
  and blocks, because unexamined is not PASS; a change with no plan is a
  starting state whose next action names `sbe plan`, not an error. The human
  view is deterministic, no timestamps. Proof: `tools/test_sbe_status_team.py`,
  11 tests, `OK`.

- `sbe converge` (`src/brothersbe/converge.py`) answers whether the code between
  two pinned commits still matches the approved dossier, in five dimensions,
  every hard verdict grounded in a citable fact and never in a judgment: an
  unplanned changed file is REVIEW-REQUIRED by name
  (`TestScope::test_an_unplanned_changed_file_is_review_required_by_name`); a
  removed OpenAPI operation no dossier artifact or plan task mentions is a
  direct contradiction and FAILs naming the operation, while an undocumented
  added one is REVIEW-REQUIRED
  (`TestContracts`); a changed migration that drops a column the data model
  still documents FAILs naming the attribute and both files
  (`TestData::test_dropping_a_documented_attribute_is_fail_naming_it`); a plan
  verification command with no receipt bound to the assessed head FAILs
  naming the command, and a receipt bound to another commit FAILs naming both
  shas (`TestVerification`, the head-binding control calibrated by disabling
  it and watching exactly that test go red, restored hash-verified). There is
  no force flag and argparse refuses one
  (`TestUsage::test_there_is_no_force_flag`); the only path from divergence
  to PASS is the amendment round trip the suite walks end to end: diverge,
  FAIL, amend the dossier, regenerate the plan and the receipt, PASS
  (`TestAmendment`). The report at `09-convergence.json` carries no
  timestamps, so two runs are byte-identical, and a FINAL PASS lists every
  NO-DATA dimension by name as not examined. Proof: `tools/test_sbe_converge.py`,
  12 tests, `OK`.

- `sbe pr verify` (`src/brothersbe/prverify.py`) binds a PASS/FAIL verdict to
  a pull request's live head commit on GitHub: PR existence, approval bound
  to the current head sha, no self-approval, no dismissed or bot-only
  approval, no changes-requested left standing, CODEOWNERS coverage, and
  required checks on the head sha. Every control that cannot be checked
  reports UNVERIFIABLE, a taxonomy that exists only at this report level
  (a 401 or 403 with a token present, a race between fetch and force-push
  naming both shas) and never a stand-in for PASS
  (`test_403_with_a_token_present_is_unverifiable_never_pass`,
  `test_a_force_pushed_head_between_first_and_last_fetch_is_unverifiable`).
  Missing GitHub credentials on this machine are NO-DATA, never PASS: with no
  GITHUB_TOKEN, no GH_TOKEN, and no `gh auth token`, every network-dependent
  control reports NO-DATA with a one-line remedy and the process exits
  nonzero (`test_no_token_no_gh_is_no_data_everywhere_with_remedy_and_nonzero_exit`).
  The token, when one is found, is held in memory only: it is never printed,
  never written to a report or receipt, and the canary fixtures plant a
  distinctive token value and grep every line of output to prove it never
  surfaces, in a full canned run and in a no-token CLI run
  (`test_the_canary_token_never_appears_in_a_full_canned_run`,
  `test_the_canary_token_never_appears_in_a_no_token_cli_run`). The GitHub
  client is read-only by construction: a source level fixture inspects every
  constructed request and fails if any method but GET appears anywhere
  (`test_every_request_construction_is_get_no_mutating_method_anywhere`).
  Proof: `tools/test_sbe_prverify.py`.

- `sbe work` (`src/brothersbe/work.py`) gives a plan task an isolated
  lifecycle: `start` validates the plan with the landed `sbe plan` checks,
  refuses an incomplete or force closed dependency by naming it, refuses
  every branch, worktree, or open registry collision by name, then opens a
  dedicated git branch and worktree and a registry record through the
  existing `sbe task` machinery. `check` reports scope, dependency, and
  verification state without mutating anything. `finish` closes a task only
  when the registry postcondition is clean AND a verification receipt bound
  to the worktree's current commit exists in the evidence store; an agent
  saying it ran the command is not evidence, so an absent receipt refuses
  closure as NO-DATA prose naming the command
  (`TestFinishRefusals::test_finish_refuses_as_no_data_when_the_verification_receipt_is_absent`).
  One writer owns one branch and one worktree, never shared. A forced close
  (`finish --force`) is visible and loud in `check` and in the record, and it
  never satisfies a dependent task
  (`TestForceFinish::test_finish_force_closes_marks_forced_visible_in_check_and_never_satisfies_a_dependent`).
  There is no merge, no rebase onto the default branch, no push, and no
  deploy code path anywhere in this module, held by a source level fixture
  that greps the file for those verbs
  (`TestNoMergeLaw::test_work_module_never_constructs_a_merge_rebase_or_push_argv`).
  `remove` deletes a CLOSED task's worktree and leaves the branch in place, because branch deletion is not one of this module's allowed git mutations, and a dirty worktree
  refuses removal until a human gives `--override-dirty` with a nonempty
  reason, recorded permanently on the registry record
  (`TestRemove::test_remove_with_override_dirty_succeeds_and_records_the_reason`).
  Proof: `tools/test_sbe_work.py`.

- `sbe plan` (`tools/sbe_plan.py`) derives a task plan mechanically from a dossier: no LLM
  anywhere in derivation, only parsing and the rules the spec names. An empty
  dossier never yields a success verdict (`TestEmptyDossier`), a task citing a
  dossier source that does not resolve is FAIL by task id because that is the
  planner-inventing-work case (`TestPlannerNeverInvents`), and a migration
  task written without its stated reversal task FAILs
  (`TestMigrationWithoutRow`). Ownership, citations, acceptance criteria, the
  dependency graph, and freshness against recorded dossier digests are each
  checked and each FAILs by naming the offending id or file
  (`TestValidationFailures`, `TestCompatibilityGap`). A derived plan's first
  task also opens through `bin/sbe task open` with fields read mechanically
  from the JSON, proving the registry accepts it without reinterpretation
  (`TestRegistryIntegration`). Proof: `tools/test_sbe_plan.py`, 14 tests, `OK`.

- The book's replay harness (`evals/replay_book.py`) now declares exactly one
  substring volatile: the live merge-base diff line the status and impact
  tools print (`git diff <sha>..HEAD over N changed file(s)`), whose sha and
  count move with every commit and push, which is how the published pages
  went stale within hours of `5be26b2` landing. Chapter 03 says so in prose
  beside the block; chapter 05 stops using a live range at all and pins
  `--base 47422a88df57 --head f924538`, which is deterministic on any clone.
  Proof: `tools/test_sbe_book.py::TestDeclaredVolatileLine`, calibrated both
  ways (a volatile-only difference passes, any other difference still fails,
  and a pinned range is never masked).
- The private-name scan (`tools/test_sbe.py::TestNoPrivateNameShips`) counts
  a hit inside vendored minified code (`docs/book/assets/mermaid.min.js`,
  the only file on that list) only when the name stands alone rather than
  flanked by letters or digits: a short name is a near-certain substring of
  SOME generated identifier in two megabytes of minified JavaScript, and the
  first false positive (the name inside mermaid's own motion-blur
  identifier) turned the whole baseline red. Every file this project authors
  keeps the plain substring rule. Proof: three new calibration fixtures with
  a synthetic name, including one pinning the vendored list to exactly one
  file so widening it is a decision rather than a drive-by.

- `00-intake.json` may now carry an OPTIONAL `binding` block (row 23 of
  `docs/BYPASS-COVERAGE.md`, a stale dossier reused for a new change): a head
  commit plus a sha256 per artifact the dossier covers. Absent, nothing
  changes, and the absence is what keeps the row UNCOVERED rather than
  COVERED. Present, `tools/sbe_design.py::_binding_problem` verifies it before
  letting `check_artifacts` reach its own PASS: a HEAD that moved since
  binding FAILs naming both commits and says "re-bind deliberately"; an
  artifact whose digest moved FAILs naming the file; a binding naming a
  commit this tool cannot resolve is NO-DATA, never a pass. Resolution reads
  git's own on-disk files directly (`HEAD`, a ref, a loose object's path)
  rather than shelling out to git, so
  `tools/test_sbe_bypass.py::test_the_design_checks_never_read_a_commit_which_is_a_limit`
  is unaffected: it pins the absence of a `subprocess` import and of a
  `git log`/`rev-parse` call, both still true. Proof: `tools/test_sbe.py`'s
  new `TestDossierBindingScenario23`, five fixtures (bound-and-current
  passes; HEAD-moved fails by name; artifact-digest-drift fails naming the
  file; absent binding is unchanged behavior; an unresolvable bound commit is
  NO-DATA), each calibrated red against a one-line break and restored to the
  pre-recorded `git hash-object` of the fixed file before being counted
  green. `docs/KNOWN-LIMITS.md` states the one gap this resolution carries: a
  bound commit old enough to have been folded into a pack by housekeeping
  reads NO-DATA rather than a confirmed verdict, because confirming existence
  is checked only against loose objects.

- `tools/sbe_gate.py` now honors `.sbe-exempt`, which it had zero support for until now
  (the CI workflow comment promised it; a grep of the file found nothing). Mirrors
  `tools/sbe_design.py::parse_exemption`'s semantics: a `gates: <names>` field naming
  which of the four hard gates a directory waives, a required `reason:` field, `gates: *`
  refused BY NAME because a wildcard is not the name of a gate, and a blank or
  whitespace-only reason refused as its own FAIL naming the file rather than a quiet
  waiver. An artifact found IN OR UNDER an exempt directory reports WAIVED, quoting the
  reason, never PASS and never silently skipped; the summary counts waived artifacts per
  gate. Exit stays 0 under `--strict`
  alone (a waiver is a visible decision, not a violation); the new `--strict-waivers`
  flag, matching `sbe_design.py`'s own flag of the same name and wording, makes any
  WAIVED artifact block a `--strict` run. Proof: `evals/run_evals.py`'s new
  `an-exempted-approval-reads-waived-with-the-reason-and-strict-exits-clear`,
  `strict-waivers-blocks-an-exempted-approval-that-strict-alone-does-not`,
  `a-blank-sbe-exempt-reason-fails-by-name-and-the-artifact-is-still-checked`, and
  `a-pass-is-impossible-for-an-exempted-artifact`, each calibrated red against a
  one-line break in the new code and restored to the pre-recorded `git hash-object`
  of the fixed file before being counted green.

- The resume brief is now opt-in, matching the `metrics` and `corrections` default wave 6 already
  set. It was the one capture path still writing a file by default: `BROTHERSBE_TELEMETRY_TRANSCRIPT`
  off (the default) wrote the brief anyway, with a `[REDACTED]` placeholder in place of every
  transcript-derived section and a line naming the switch that would fill them in. Flip decision
  (founder, 2026-07-29): off now means no file at all, and the `precompact-brief` code path that
  would have written it names the switch once on stderr instead, the same "who kept this off and
  why" sentence the other two categories already print, so an absent file is never mistaken for a
  quiet session. The opt-in path (`BROTHERSBE_TELEMETRY_TRANSCRIPT=1`) is unchanged: it still reads
  the transcript tail and still redacts before writing. `SECURITY.md` and `docs/KNOWN-LIMITS.md` are
  updated to the new default and its own limit: the stderr line only reaches whoever is watching the
  hook at the moment of compaction, and a resumed session's `compact-hint` reader has nothing on disk
  to relay, unlike the old placeholder file. Proof: `tools/test_sbe.py`'s `TestResumeBrief`, rewritten
  from the test that asserted a file existed by default (its docstring states the old meaning and this
  decision) into `test_default_writes_no_brief_and_names_the_switch_on_stderr`, plus the new
  `test_opt_in_writes_the_brief_and_still_redacts` fixture for the opt-in path; three fixtures in
  `TestCaptureDefaultsAndAutosaveContentScan` (`test_a_default_installation_captures_no_transcript_text_and_no_correction`,
  `test_each_switch_turns_on_exactly_one_category`, `test_the_organization_override_forces_every_category_off`)
  updated to the same default. Calibrated red against the pre-flip code (4 fixtures failed, each on a
  now-missing-file assertion) before the fix restored it, the restore verified byte-identical to the
  pre-recorded `git hash-object` of the fixed file rather than by `git checkout`.

- `sbe evidence run` no longer writes `argv` fully verbatim: every token is now checked against
  `SECRET_PATTERNS`, imported from `tools/sbe_telemetry.py` rather than reinvented, and a match
  is replaced by a named marker, `[REDACTED:<shape>]`, before the receipt is written. The
  command that RUNS still receives the untouched, unredacted argv; only the recorded copy is
  masked. The receipt gains `argvRedactions`, the count, so a reader can tell whether argv is
  verbatim (`0`) or not without diffing it by hand, and `argvRedactions` sits in `SEALED_FIELDS`
  beside `argv` so a redacted receipt seals and verifies exactly like any other. This narrows
  the argv limit; it does not close it, because the pattern list is finite by nature and a
  secret in a shape it does not know still reaches the receipt whole, which stays a stated limit
  in `docs/KNOWN-LIMITS.md`. Proof: `tools/test_sbe_evidence.py`'s
  `test_a_secret_shaped_argv_token_is_redacted_not_recorded_verbatim` (a planted `sk-`-shaped
  token lands in the receipt as the marker, never the secret; this replaces the retired
  `test_argv_is_recorded_verbatim_which_is_a_limit_not_a_leak_to_ignore`, which pinned the
  opposite on purpose as a stated limit),
  `test_a_receipt_with_redactions_still_verifies_pass_on_an_untouched_tree` (a redacted receipt
  still verifies PASS), `test_zero_redactions_keeps_argv_byte_identical_to_what_ran`, and
  `TestRedactArgv` for the function directly.

- `sbe init` now also ensures `.gitignore` carries its own install-receipt line,
  `.brothersbe/install-receipt.json`, under a one-line comment, because a fresh clone that ran
  `sbe init --apply` could track a receipt naming this machine's absolute install path with
  nothing in the way. Appended, not owned: existing `.gitignore` content is left untouched, the
  mutation compares identical (present means untouched) exactly like every other proposal,
  dry-run shows it as a proposed diff like every other mutation, and the install receipt's
  `writtenPaths` and uninstall instructions never include `.gitignore`, because `sbe init` does
  not delete a file it only appended a line to. Proof: `tools/test_sbe_adopt.py`'s
  `TestInitGitignoreLine`, calibrated red (three fixtures failed) against the mutation removed
  from `plan()`, before the fix restored it.

- `sbe doctor` gains an `identity` check: it reads this repository's `git config user.email` and
  `user.name` and reports a `WARNING`, quoting the value found, for a fixture identity, an
  `@example.com` email or the literal name `ci`. That shape of identity authored a run of real
  public commits before anyone noticed it; the check exists to catch the same class earlier, and
  it never blocks a run by itself: `WARNING` moves neither the exit code nor the `FAIL` count,
  matching `NO-DATA` and `PASS`, the same never-a-hard-failure rule `doctor` already applies
  everywhere else. Proof: `tools/test_sbe.py`'s `TestDoctorIdentityCheck`, calibrated red (both
  leak fixtures failed) against the `WARNING` branch disabled, before the fix restored it.

- `tools/sbe_score.py`'s silent-failure lint named a self-skipped file by
  name but never counted it: a directory where the walk reached its own
  source under thirteen of its fourteen names (a hardlink or a case/symlink
  alias reaches the same inode more than once) printed "1 file(s) scanned
  under X, clean" with the thirteen listed and no number attached, because
  the "clean in what was opened, which is not the same as a clean tree"
  withdrawal only checked the KIND-skip count, never the self-skip one. Both
  are counted now, and either withdraws the bare "clean". Proof:
  `tools/test_sbe.py`'s
  `test_a_directory_mostly_self_skipped_names_the_count_and_withdraws_clean`,
  planted with thirteen hardlinks of the scorer's own file, calibrated red
  against the prior sentence before the fix restored it.

- `tools/sbe_autosave.sh` already exits 0 on an unwritable vault (an earlier
  fix closed that), but the REASON did not: `log_line` and `excl_record`
  tried to write their explanation into the same vault directory that had
  just failed to write, so a skipped precompact or tick left no trace
  anywhere, not in `autosave.log`, not in `autosave-exclusions.log`, not on
  stderr (kept clean on purpose). Both now fall back to a file beside the
  repository's own git metadata (`.git/brothersbe-autosave-fallback.log`),
  which does not depend on the vault at all, when the primary write fails.
  Proof: `tools/test_sbe.py`'s
  `test_an_unwritable_vault_still_lands_the_reason_in_a_fallback_log`, which
  chmods a vault telemetry directory read-only and asserts the reason lands
  in the fallback log instead of nowhere, with a writable-vault control
  asserting no fallback file is written when none is needed.

- docs/CLI.md gains a section documenting the telemetry data commands (`data-show`,
  `data-export`, `data-purge`): where they live (`tools/sbe_telemetry.py`, not `sbe`, because
  the facade fronts assurance commands run routinely on somebody else's schedule and these
  three read or delete what BrotherSBE captured about the operator's own sessions, a privacy
  surface a person runs deliberately, by name, on purpose), what each shows or deletes, the
  vault location (`BROTHERSBE_VAULT`, default `~/BrotherSBEVault`), and real output quoted
  from a run against this repository's own vault. Docs-only; the three commands themselves
  are unchanged.

- The first real run of the CI gates found the hard-gates step grading the
  teaching dossier under docs/for-engineers/examples: its deliberately failing
  APPROVAL broke the build, and its receipts printed PASS lines as if they
  were this repository's claims. The step now scopes to the live dossier root
  (design/, declared by this repository's own `sbe init`), where all four
  gates honestly read NO-DATA today. The consumer workflow's strict flag now
  applies on pull requests only: a push to main has no proposed change, and
  grading its empty self-diff under --strict manufactured a failure out of
  absence. The install receipt is gitignored because it records this
  machine's absolute path.

- PRACTICES.md gains the loop close-out interview: open loops are put to their
  decision owner as per-loop question sets, each question carrying a
  recommendation, its pros, and its cons, triaged gating-first and run through
  the harness's native question surface by default rather than as prose. Advice,
  not a control, and it says so.

- `sbe status`: one truthful, blocker-first answer to "where does this change stand",
  read from state other commands already recorded rather than computed fresh. It never
  runs the suites itself and never becomes a second gate runner: nothing in it starts a
  subprocess, and every design/gate/score FAIL it names comes from an EXISTING evidence
  receipt someone already generated with `sbe evidence run`, never from status invoking a
  check on its own. Six sections, blocker-first: BROKEN CLAIMS (a receipt failing `sbe
  evidence verify`, or a disposition bound to a commit that is not HEAD), MERGE BLOCKERS
  (an intake tier disagreeing with the diff-derived tier with no disposition, an unreadable
  intake, a task closed `--force`d, or a verified receipt recording a nonzero exit code),
  ACTIVE CONFLICTS (overlapping open tasks, read by calling `tasks.load_registry`,
  `tasks.open_tasks` and `tasks.claims_overlap` directly, the same functions `sbe task
  check` runs, so there is no second copy of wave 5's overlap rule), MISSING EVIDENCE (a
  design/gate/score kind no verified receipt names, for a tier above T0, each naming the
  command that would fill it), COMPLETED EVIDENCE (a clean, zero-exit receipt with its
  trust label), and NEXT ACTION (the first nonempty section's remedy, or "nothing blocking
  here that this tool can see", plus the scope sentence naming exactly which stores were
  read). An absent store is NO-DATA with a reason, never clean, and every positive or empty
  line in text mode names the scope it inspected. Exit 0 means sections 1-4 are empty, not
  that everything was inspected, and the closing line says so. Proof:
  `tools/test_sbe_status.py`, 17 fixtures over 10 classes, each calibrated by planting the
  break, watching the section go red, then restoring the clean state and watching it clear:
  a stale receipt, a tier disagreement without and then with a disposition, an injected
  registry overlap and its non-overlapping counterpart, a forced close, missing evidence
  for a T2 tier against a T0 tier's clean bill, a clean receipt and a failing one, all three
  NEXT ACTION arrangements, `--json` carrying every section and the scope object, and a
  guard that greps rendered output (not source) for a scope phrase on every empty-section
  line. Limits stated beside the behavior in `docs/CLI.md`, including the flat,
  single-dossier convention this wave reads (`00-intake.json`, `disposition.json` and
  `.sbe/` at the inspected path itself, not discovered under a nested `design/<change>/`
  dossier) and the argv-substring heuristic used to recognize a design/gate/score receipt.
  Maturity: INTERNAL-EVAL.
- Release-candidate packaging: `.claude-plugin/marketplace.json` names the
  plugin, the repository, and the version, in the shape `claude plugin
  marketplace add` and `claude plugin validate` (installed CLI 2.1.207)
  accept; `tools/test_sbe.py`'s new `TestMarketplaceManifest` class pins its
  version against `VERSION` and `.claude-plugin/plugin.json` (a four-way pin,
  extending the pin `TestPluginSurface` already held) and re-runs the
  installed CLI's own validator as a subprocess, skipping honestly (not
  passing) when `claude` is not on PATH. `scripts/test-install-artifact.sh`
  proves a plain `git archive HEAD` extracts into an empty directory and
  verifies clean there (`scripts/verify-install.sh`, `bin/sbe doctor`),
  nothing written outside that directory, the kill criterion this wave was
  cut against. `scripts/test-upgrade-rollback.sh` proves the same for a
  previous-tag-to-HEAD-to-rollback cycle when a previous tag exists, and
  reports NO-DATA honestly (exit 0, no claim of a tested upgrade) today,
  because this repository has cut no tag yet; both scripts were calibrated
  by breaking each fixture in a disposable clone of this repository and
  watching it go red, then restoring it and watching it go green, on both
  branches of the upgrade-rollback script. Both new steps are wired into
  `.github/workflows/brothersbe-gates.yml`. `docs/ROLLOUT.md` is new: a
  staged rollout for an adopting organization (shadow mode, then a
  founder-gated move to enforced, then the adoption kit), a support and
  ownership model with no invented SLA, the upgrade and rollback procedure,
  and the blocked list verbatim (signed release, branch protection, `gh
  auth`, real-estate maturity claims). `docs/KNOWN-LIMITS.md` gets a matching
  section naming the same four blocks in this project's own voice. No
  control was weakened; the pre-existing suite counts only rose (`sbe`: 47 to
  49). Maturity: INTERNAL-EVAL, proven against this repository and a
  disposable clone of it, and no other estate, exactly as `docs/ROLLOUT.md`
  states plainly rather than implies.
- `sbe task`: a write-scope registry with a diff postcondition that survives
  Bash. The fence hook fails open and cannot govern shell writes because shell
  cannot be parsed reliably; `sbe task open` now records who owns what in
  `.sbe/tasks.json` (one file, atomic rewrite, no lock, concurrent registry
  writers a stated limit), and `sbe task close` reads the union of
  `git diff --name-only <base>...HEAD` and `git status --porcelain` and
  refuses to close a task whose tree changed outside its declaration, naming
  every violation by path; uncommitted edits count, a rename counts both
  sides, and an unresolvable base is NO-DATA, never a pass. `open` refuses an
  owned path overlapping another open task's, using the fence hook's own
  `paths_overlap` imported rather than re-typed (a fixture fails if that
  import is ever replaced by a local copy); `check` re-runs the scan so a
  collision injected into the JSON by hand is caught; `fence` renders the
  markdown fence view one way, JSON to markdown; `--force` records who and
  why and marks the close FORCED, never clean; and a reviewer task can
  neither open owning the evidence store nor close over a touched receipt,
  even forced. Proof: `tools/test_sbe_tasks.py`, 15 fixtures, every one
  calibrated by breaking the control and watching it go red. Limits beside
  the behavior in `docs/CLI.md`, `docs/KNOWN-LIMITS.md` and
  `docs/HOW-IT-WORKS.md` (the two-layer scope model). Maturity:
  INTERNAL-EVAL.
- `sbe adopt` and `sbe init` are built, closing the refusal `cli.py` used to print for `adopt`
  ("the adoption doctor cannot yet tell a protected repository from an unprotected one, and a
  readiness report that omits that is worse than none"). `sbe adopt` detects a repository's
  stack by walking the tree (languages, a migrations directory, dbt models, API contract files,
  existing CI workflows), reusing the SAME path patterns `sbe impact` already carries
  (`brothersbe.impact.DETECTORS`) rather than a second copy of them, and proposes a provisional
  `.brothersbe/policy.json` (wave 3's own policy file and JSON schema had not shipped when this
  was written; the file's own `note` field says so) plus a `.github/CODEOWNERS` generated from
  that same policy, protecting the manifest, hooks, this repository's own policy and config,
  where the evidence schema is declared, product and consumer CI, and release files. THE KILL
  CRITERION THIS WAVE WAS BUILT AROUND: the adoption report never claims a GitHub-side
  protection (branch protection, required status checks, review from a code owner being
  REQUIRED) is PRESENT, because nothing here holds a GitHub token or asks for one; all three
  report UNVERIFIABLE-HERE unconditionally, naming what checking them for real would take, and
  `tools/test_sbe_adopt.py::TestAdoptionReportNeverClaimsPresent` pins exactly that. A
  CODEOWNERS file merely existing in the tree is reported separately, under `localFacts`, so it
  is never read as proof GitHub actually requires that review. Both proposals are deterministic
  (no timestamp, no run id), which is what lets a second `--apply` recognize nothing changed and
  write nothing; an existing file that differs is never overwritten without `--force`.
  `sbe init` installs BrotherSBE's own local footprint (`.brothersbe/config.json`, a
  `design/.gitkeep` dossier marker, and, only with `--with-consumer-ci`, a copy of this
  installation's own consumer CI workflow and composite action), refuses outside a git
  repository naming the reason, and writes or refreshes an install receipt
  (`.brothersbe/install-receipt.json`) naming every path it has written and the exact `rm -f`
  uninstall line for each, only when something was actually written that run; a no-op run leaves
  the receipt, timestamp included, untouched. `.github/workflows/brothersbe-gates.yml` gained
  the missing `push` (to `main`) trigger beside `pull_request`, and a new
  `.github/workflows/consumer-check.yml` plus `.github/actions/sbe-consumer/action.yml` give a
  client repository something to copy or call that runs ONLY `sbe impact`, `sbe evidence
  verify` (when receipts exist), `sbe status` (wave 8; guarded by a file-exists check and
  skipped honestly until that wave lands), and the design checks in strict mode when a dossier
  is declared, and never BrotherSBE's own test files; both new workflow files carry the same
  honest header the product workflow already does, that a workflow file guards nothing on its
  own until branch protection requires it (`docs/KNOWN-LIMITS.md` L16). Eleven fixtures across
  17 test methods in `tools/test_sbe_adopt.py` build real temporary git repositories and run the
  real command: dry-run writes nothing (proved by hashing the whole tree, not by checking the
  paths this module happens to propose today), apply-then-reapply is a no-op, an existing file
  is never overwritten without `--force`, the kill criterion itself, stack detection shifting
  the proposed policy for a planted dbt project and a planted migrations directory, the init
  receipt and its uninstall instructions, `sbe init` refusing outside a git repository, and the
  two shipped workflow files' triggers and scope, read as text and grepped the same
  line-oriented way `evals/run_evals.py` already reads `brothersbe-gates.yml` (this project
  ships no YAML parser). Calibrated by neutralizing each control in turn and confirming the
  matching fixture goes red before trusting the green: the dry-run guard, apply idempotence, the
  force guard, the kill criterion itself, each stack-detection shift, the receipt's uninstall
  match, the git-repository refusal, and both workflow-file assertions, eight breaks, eight red.
  One pre-existing test could not be updated in this change and is a known, expected regression
  rather than a silently accepted one: `tools/test_sbe.py::TestCliSurface::
  test_an_unbuilt_command_refuses_loudly_and_names_its_wave` hardcodes `adopt` in its list of
  commands still expected to refuse and exit 3; building `adopt` this wave makes that one line
  false, and the one-line fix (removing `"adopt"` from that list) touches a file this wave was
  not allowed to edit. Limits in full in `docs/KNOWN-LIMITS.md`; maturity INTERNAL-EVAL.
- The two defects the bypass-coverage table recorded rather than fixed are
  closed, and the table and `docs/KNOWN-LIMITS.md` are updated to match.
  `sbe evidence verify` used to open a receipt path with no access check, so a
  FIFO where a receipt was expected hung the command forever with no verdict
  in either mode; it now runs the same `sbe_checks.evidence_problem` access
  check the hard gates use before opening, and refuses a FIFO, socket, device
  or unreadable file by name in bounded time instead, in both text and
  `--json` mode (`tools/test_sbe_evidence.py::TestAccessAndTimeout`, two
  fixtures). `sbe evidence run` gained an optional `--timeout SECONDS`: past
  it, the child is killed and no receipt is written, so nothing can later
  verify PASS for a run whose exit code was never observed. There is
  deliberately no default, because a silent one would kill a legitimate
  long-running suite, the exact false-positive shape this project's own kill
  criteria warn against, so a command run with no `--timeout` can still hang
  the wrapper as before; that residual is stated on row 35 of
  `docs/BYPASS-COVERAGE.md` rather than left implicit. Separately,
  `tools/sbe_fence_hook.py::paths_overlap` closed the case-insensitive-
  filesystem escape (row 21): a fence written for `docs/SETUP.md` used to let
  a second writer land on `docs/setup.md` because the comparison was
  case-sensitive on a filesystem that is not. A missed comparison is now
  retried case-folded and the fold is confirmed against the filesystem
  (`os.path.samefile` when both spellings exist, a volume-level probe when
  one does not) before it is trusted, so two honestly different files named
  `a.md` and `A.md` on a case-sensitive filesystem still do not conflict.
  `tools/test_sbe_bypass.py::test_a_case_variant_of_a_fenced_path_is_refused`
  moved from a LIMIT fixture to a COVERAGE fixture, and
  `tools/test_sbe_fence_hook.py::TestCaseFoldConfirmation` pins the
  confirmation itself. Bypass-coverage totals move from 16 COVERED / 6
  UNREACHABLE HERE / 13 UNCOVERED to 18 / 6 / 11.
- The 35 bypasses an external review listed are now answered one by one, in
  `docs/BYPASS-COVERAGE.md`, and the answer for each is exactly one of three:
  COVERED with the fixture named, UNREACHABLE HERE with the missing thing named
  (a GitHub token, branch protection, a warehouse, a real second estate), or
  UNCOVERED with what covering it would take. Sixteen are COVERED, twelve of
  them by suites that already existed and four by the new
  `tools/test_sbe_bypass.py`: an invented review id is a pointer and never an
  approval, an approval stops counting at the next commit, an exemption naming a
  wildcard waives nothing, and a monorepo package carrying no receipt is named
  in its neighbour's PASS line. Six are UNREACHABLE HERE and thirteen are
  UNCOVERED, and the table says so rather than quietly dropping them, because
  the honesty of that count is the deliverable and the fixture count is not.
  Fixtures that pin a bypass WORKING carry `_is_a_limit` in their names (an
  alias-only second derivation passes, a rehearsal against an empty database
  passes on zero equals zero, a case variant escapes a fence on a
  case-insensitive filesystem), so each hole is a decision somebody made rather
  than a surprise somebody finds. Every fixture was calibrated by breaking the
  control it targets and watching it go red: 21 breaks, 21 red. Two holes found
  in the writing are recorded in `docs/KNOWN-LIMITS.md` and not fixed here,
  because fixing them changes code this wave was not allowed to touch:
  `sbe evidence verify` hangs forever on a FIFO receipt (no access check before
  the open), and the evidence wrapper runs the operator's command with no
  timeout.
- Two privacy defects an external review found are closed, and both were
  defaults rather than bugs. FIRST: this tool parsed the session transcript and
  stored excerpts of the operator's own messages by default, with best-effort
  redaction standing between a customer name, a partner term or an unreleased
  design and a file on disk. Best effort is the right engineering for a redactor
  and the wrong basis for a default. Capture is now OFF unless switched on, per
  category and independently: `BROTHERSBE_TELEMETRY_METRICS` for the per-session
  row in `outcomes.jsonl`, `BROTHERSBE_TELEMETRY_TRANSCRIPT` for the transcript
  text in the resume brief, `BROTHERSBE_TELEMETRY_CORRECTIONS` for the excerpts
  in `corrections.jsonl`. The invariant, and the fixture that holds it: a
  default installation captures no transcript text and no correction excerpt,
  and nothing is read out of a transcript until a category that needs it is on.
  `metrics` is opt-in too, because its row carries the working directory
  basename and a basename can be a client's name. An organization override
  (`BROTHERSBE_TELEMETRY_DISABLE`, or `capture = off` in
  `/etc/brothersbe/telemetry-policy.conf`) forces all three off and no local
  switch reverses it; a policy file that cannot be read, or that carries a
  directive this version does not recognize, FAILS CLOSED and names the file and
  the line. Three new subcommands make the stored data visible, portable and
  removable from one shared inventory: `data-show`, `data-export` and
  `data-purge`, the last re-checking the filesystem after each removal and
  reporting anything that survived rather than reporting success from its own
  intention. Every field that can be stored is now published field by field in
  `SECURITY.md`. SECOND: the autosave excluded secret-shaped file NAMES, and a
  secret in a normally named source file (`src/config.py` holding an API key)
  matched no pattern and became a permanent git object; the documentation said
  the name patterns meant "credentials never enter the autosave ref", which was
  never true. Every candidate file's CONTENT is now read BEFORE `git add` runs,
  which is the moment a blob would be created, so a rejected file never becomes
  a git object at all. Files past `BROTHERSBE_AUTOSAVE_MAX_BYTES` (1 MiB) and
  binary files are excluded as UNSCANNED rather than assumed clean, as is a path
  git cannot print literally. Every exclusion is recorded in
  `99-System/telemetry/autosave-exclusions.log` with its reason, as a path and a
  reason only, and `recover` points at that record because what a snapshot does
  NOT hold matters at recovery time. In a repository declared production
  (`BROTHERSBE_REPO_CLASS=production` or a `.brothersbe-production` file)
  autosave is opt-in and snapshots nothing until `BROTHERSBE_AUTOSAVE_PRODUCTION`
  is set. Seven fixtures in `tools/test_sbe.py`
  (`TestCaptureDefaultsAndAutosaveContentScan`) run the real tools in temporary
  vaults and real git repositories, and the secret-in-a-source-file fixture
  asserts that no git OBJECT for that content exists anywhere, not merely that
  the tree omits it. Calibrated by breaking each control in turn and confirming
  the matching fixtures fail: capture default (2 fixtures), organization
  override (1), content scan (2), exclusion record (1), production opt-in (1),
  purge removal (1). `docs/THREAT_MODEL.md` is new and covers fifteen threats,
  including the ones nothing here stops: a direct push, a deleted workflow, a
  compromised CI runner, and prompt injection from repository content. What
  these controls do NOT stop is in `docs/KNOWN-LIMITS.md`, including the two
  sentences that matter most: a path exclusion never prevented secret capture,
  and a local git ref can still be carried off the machine by a backup or a
  mirror.
- `sbe evidence` closes the hole under every gate in this project: a receipt
  could be typed by hand by the same agent whose work it verified, so a
  fabricated duration, exit code, row count or rerun id satisfied the schema and
  a gate could PASS on a run nobody's command ever performed, and nothing bound
  a receipt to a commit either, so one written against older code still passed
  after that code changed. The invariant now: a receipt only counts as evidence
  for the commit it was generated against, by a wrapper that ran the command
  itself. `sbe evidence run --out r.json -- <command...>` EXECUTES the command
  through subprocess and records what it observed (repository identity, base and
  head commit, the exact argv, start and end in ISO 8601 UTC, duration, exit
  code, python and sbe versions, platform, tree dirtiness, and the covered files
  with their content digests); there is no flag that accepts a duration or an
  exit code, and the wrapper's own exit code is the command's, so a failing
  command cannot be laundered into a passing evidence step. `sbe evidence
  verify` FAILs on an unknown schema version, a vacuous required field (through
  the same `answered()` every other receipt field goes through), a broken
  `runId` seal, a head commit that has moved, or a covered file that changed,
  vanished or was written after the run ended; it returns NO-DATA rather than
  PASS for a receipt generated on a dirty tree or covering no file, because
  advisory is not a pass; and every verdict line names what it inspected.
  `sbe evidence show` prints the trust level unconditionally: PROTECTED-CI only
  when `SBE_CI_RUN_ID` was set by the environment AND the tree was clean,
  LOCAL-ADVISORY otherwise, since a CI job over uncommitted edits is a local run
  wearing a badge. stdout and stderr are recorded as SHA256 digests and byte
  counts, never as text, because a receipt is the one artifact everybody is
  encouraged to share and a command that prints a token would otherwise persist
  it there forever. The `runId` seal is stated as tamper evidence rather than a
  signature, in the module, in `docs/CLI.md` and in `docs/KNOWN-LIMITS.md`: it
  catches a receipt nobody's command produced and it does not stop somebody who
  read the source, which is exactly why a local receipt is never more than
  advisory. Twenty-seven fixtures in `tools/test_sbe_evidence.py` build real git
  repositories and run real commands: the defect (three hand-authored and
  doctored receipts), the sound case, a stale commit, a stale file, a deleted
  covered file, a dirty tree, malformed and unknown-schema receipts, vacuous
  required fields, and the assertion that a secret printed by the command
  reaches the receipt only as a digest. Calibrated by neutralizing each of the
  four controls in turn and confirming the matching fixtures fail (commit
  binding 1, seal 2, dirty-tree NO-DATA 2, covered-file staleness 2) before
  trusting the green. One limit the fixtures found and now pin: `argv` is
  recorded verbatim, so a credential passed on the command line IS persisted.
  Limits in full in `docs/KNOWN-LIMITS.md`; maturity INTERNAL-EVAL.
- `sbe impact` closes the oldest hole in this project: the tier was computed
  from five answers and nothing ever read the code, so a change rewriting an API
  contract could be classified T0 by answering "no" five times, and every gate
  downstream then agreed the change owed no evidence. The scan derives those
  same five answers from the git diff and runs them through
  `sbe_intake.compute_tier`, the SAME table a person's answers go through: one
  rule, one place, two inputs, so a derived tier and a declared tier cannot drift
  apart. It may raise a declared tier and may never lower one. A disagreement is
  resolved only by a disposition naming the detector, the decision, the reason,
  the author and the head commit it was decided against; a disposition from
  another commit resolves nothing and one with no reason is an off switch rather
  than a decision. The proposed tier is a FLOOR, not a ceiling: `consumers`
  cannot be read from a diff and is assumed at its lowest value, and every
  changed file no detector covers is listed by name under `unmeasured` rather
  than folded into a clean result. Verdicts are PASS, REVIEW-REQUIRED, FAIL and
  NO-DATA, and `--strict` makes NO-DATA block for protected CI. Twenty-two
  detectors ship (OpenAPI, AsyncAPI, protobuf, Avro, GraphQL, event schemas,
  migrations, SQL data definition language, dbt models, ORM models, destructive
  operations, payment paths, partner paths, personal data paths and field names,
  authorization paths, production configuration, secret material,
  infrastructure, CI pipelines, queue configuration). Sixteen fixtures in
  `tools/test_sbe_impact.py` build real git repositories and run the real
  command: the defect, the sound case, hollow and malformed intakes, an
  unsupported language reported as unmeasured, four bypass attempts (deleting
  the intake, a stale disposition, an unreasoned disposition, a deletion
  misread as an addition), and the invariant that a declared tier is never
  lowered. Calibrated by neutralizing the control and confirming five of the
  sixteen fail before trusting the green. Limits stated beside the behavior in
  `docs/KNOWN-LIMITS.md`; maturity INTERNAL-EVAL.
- Two suites that existed and ran on nobody's merge path now run in CI: the
  fence hook tests and the impact fixtures. The workflow's own comment already
  said it: a fixture no merge runs cannot stop anything.

- One command line, `bin/sbe`, over the nine script paths, plus an importable
  `src/brothersbe/` package. It is a facade and says so: every built subcommand
  delegates to the tool in `tools/` that already carries the behavior and the
  tests, and returns that tool's exit code. Nothing in `tools/` changed, and the
  old invocations are NOT deprecated in this change: deprecating commands that
  509 evals and a dozen pasted doc examples point at is its own change with its
  own risk, and it is not being smuggled into a packaging wave. No install step
  either, because zero dependencies is a promise on the front page and
  `pip install` would retract it for anyone in a CI image with no package index.
  Six subcommands the finalization brief calls for (`inspect-change`, `plan`,
  `evidence`, `policy`, `exceptions`, `adopt`) are PRESENT and REFUSE: each names
  what is missing, names the wave that builds it, and exits 3 rather than
  printing an empty result. Exit codes are fixed at 0 no control failed, 1 a
  control failed, 2 usage, 3 not built, and `verify` and `review` close with a
  line saying that exit 0 is not a pass, because a run where every check reported
  NO-DATA also exits 0 and an exit code cannot tell those apart. Documented in
  `docs/CLI.md`. Six new tests in `tools/test_sbe.py` pin the surface: the
  launcher reports the one version in `VERSION`, no command is advertised without
  a runner or implemented without a help line, an unbuilt command exits 3 and
  names its wave, the four exit codes a CI job would branch on hold, `verify`
  cannot exit 0 over an empty directory without saying no control passed, and the
  package imports with nothing installed. Two of them were calibrated by
  injecting the defect (an unbuilt command made to succeed, and a command
  advertised with nothing behind it) and confirming they fail before trusting the
  green.

- BrotherSBE is now a Claude Code plugin, and nothing was moved to make that
  true. `.claude-plugin/plugin.json` declares it, `skills/` holds six thin
  namespaced skills (`kickoff`, `design`, `verify`, `review`, `learn`, `adopt`)
  that route into the existing law rather than restating it, `agents/` holds
  seven read-only reviewer agents, and `hooks/hooks.json` ships the four hooks
  with self-resolving `${CLAUDE_PLUGIN_ROOT}` paths so no engineer hand-edits a
  shared settings file. `SKILL.md`, `references/`, `tools/`, `tables/`,
  `templates/` and every law citation stay exactly where they were: the
  conversion was constrained to add a surface, never to move the law. Proven by
  `claude plugin validate .` (passes) and by five new tests in
  `tools/test_sbe.py` that pin the manifest to the `VERSION` file, require the
  frontmatter each skill and agent loader reads, forbid a write tool in an agent
  documented as read-only, and resolve every `${CLAUDE_PLUGIN_ROOT}` path cited
  by a skill, an agent or a hook. The frontmatter test exists because
  `claude plugin validate` caught a defect this suite's first version accepted:
  an unquoted colon in a description makes the YAML parse fail, and the skill
  then loads with empty metadata in silence. That test was calibrated by
  re-injecting the defect and watching it fail. Rationale, the three rejected
  alternatives and the flip condition: `docs/adr/2026-07-28-plugin-conversion.md`.
  What the conversion does NOT fix is unchanged and still listed in
  `docs/KNOWN-LIMITS.md`: evidence can still be hand-authored, the tier is still
  computed from answers rather than from the diff, approvals are still not
  resolved against a review platform, and the write fence still fails open and
  does not gate Bash.

- An onboarding set for engineers who have never seen this tool ships at
  `docs/for-engineers/`: eight pages (install and first run, one per role for
  backend, data, infrastructure and ETL, the limits page, and the adoption page)
  plus four complete worked dossiers under `docs/for-engineers/examples/`, one
  per role, each carrying the receipts its change would owe. Every block of tool
  output on every page was executed against this tree rather than carried
  forward, and the four dossiers pass all five design checks at their shipped
  path. The eight pages are in `SHIPPED_DOCS`, so the guards that recompute eval
  counts, meta-test counts and pasted lint lines read them too; the ETL page's
  three lint lines are recomputed by
  `no-shipped-doc-prints-a-silent-failure-lint-line-the-scorer-does-not-produce`
  from a fixture rather than exempted from it.

- Every citation that credited `SKILL.md` with a law the lazy-core split moved
  out of it now names the reference file that declares the law. Fifteen
  (file, law) pairs across six documents were dangling: `docs/KNOWN-LIMITS.md`,
  `docs/guides/01-quickstart.md`, `docs/guides/03-work-doctrines.md`,
  `docs/HOW-IT-WORKS.md`, `RUBRIC.md` and `MANIFEST-extraction.md`'s own worked
  example. Nine further pointers that describe `SKILL.md` as the whole law, and
  carry no law number for a check to read, were widened by hand. Proven by
  `every-law-citation-names-a-file-that-holds-that-law`, which globs
  `references/` for the law-text files and requires the file being credited to
  declare the law with its own heading, and which fails on a planted wrong
  pointer rather than on a remembered list of known-bad citations.

The first named version. Before this line the only name for an install was a
commit hash, which `tools/sbe_telemetry.py check-update` compares but no human
can read a promise into.

- Every verdict names the root it examined and the targets it read, and names
  or counts the directories inside that root that contributed nothing. Two
  defects were the same absence: pointed at an EMPTY directory with
  `SBE_DOSSIER_ROOT` set elsewhere, the design tool printed five PASS lines
  byte for byte identical to the run against a complete dossier, and a parent
  holding three change directories, the middle one carrying no receipt at all,
  printed one gate PASS over the pool while that directory alone printed
  NO-DATA. Closed once in `sbe_checks.scope_note`, which both walkers
  (`sbe_gate.find` and `sbe_design.find_dossiers`) call, so a gate added later
  inherits it; the design tool also prints a scope line and prints the dossier
  heading unconditionally, and a configured root that replaces a directory
  named on the command line is disclosed next to the root it replaced. The
  approval refusal names which APPROVAL file it quoted and how many it read.
  No verdict changed. Proven by
  `a-change-directory-with-no-receipt-is-named-in-the-verdict-that-pools-it`
  (executed on the numbers, migration and ran gates, not asserted from their
  source shape), `the-approval-verdict-names-which-approval-file-it-read` and
  `an-empty-directory-cannot-print-the-report-of-a-dossier-somewhere-else` in
  `evals/run_evals.py`.
- The scorer's report is split by what it opened. Checks that opened a file in
  the directory being examined print first, under a heading that says so;
  checks fed by a telemetry vault or fence registries outside that directory
  print under a second heading that says a verdict there is not a statement
  about the code here, and counts how many of those sources are not on the
  machine at all. Measured against a foreign repository, the single line in the
  whole first run that was true about the reader's own code printed eleventh,
  underneath ten NO-DATA lines about a vault path that does not exist for them
  and one PASS about the installed skill's own tree. Severity ordering alone
  did not fix that. No verdict changed, and nothing is aggregated, scored or
  graded. The quickstart's first ten minutes now runs against the reader's own
  repository first, with the real output including the pruning disclosure, and
  the eval bed is step two; the README states what a first run on an unmodified
  repository does and does not tell you; `docs/KNOWN-LIMITS.md` states that the
  intake's contract question sets the tier and that no checker reads a contract.
- The approval identity proof examines something before it certifies. A
  bracketed or parenthesized approver is read as the reader reads it rather
  than parsed to an empty set, a character whose glyph carries no ink
  separates or disappears but never welds two names into one, a comparison
  with no emails and no names on either side certifies nothing, a proven
  email difference is itself the proof, two names of one script compare by
  code point, and the approver who amends and signs is the approver (the
  signature's matched principal is the ground). Proven by the `c15i` approval
  cases in `evals/run_evals.py`; the measured refusal remainder per script is
  disclosed in `docs/KNOWN-LIMITS.md`.
- Ledger rewrites measure the live file before the rename, never after their
  own output: the bytes are read once under the writer lock, anything appended
  since is carried into the replacement verbatim, and a file that shrinks or
  keeps growing is never renamed over. Dedup rewrites through the same
  primitive with a per-run backup name, the lock sidecar can fail without
  dropping the row it exists to protect, short writes complete, and a
  line-delimited record stays one line. Proven by
  TestTelemetryWriterSerialization in `tools/test_sbe.py`.
- The autosave tick treats its counter as untrusted input and its lock as
  leakable: a counter that cannot be written is a named skip with a log line
  (never a silent off), non-numeric and empty counters are named resets, the
  lock is released by a trap on every exit path, and a stale lock is broken
  only when it predates the whole wait, with the presumption named in the log.
  Proven by TestAutosaveCoversTheWorktree in `tools/test_sbe.py`.
- An ADR's rejected count states only what the document establishes: the
  winner is identified per option rather than per document, a Decision
  sentence that names one listed option in ordinary English identifies it
  without quotation marks, a chosen marker that resolves to no listed option
  establishes nothing about the others, and "Flip condition" (this project's
  own name for the section in four shipped pages) is an accepted heading.
  Proven by `an-unquoted-decision-sentence-naming-one-listed-option-is-the-winner`,
  `a-chosen-marker-resolving-to-no-listed-option-establishes-nothing` and
  `the-projects-own-name-for-the-flip-section-is-accepted`.
- The doc-honesty guard classifies a sentence instead of remembering a
  phrasing: the scanned set is every markdown page the manifest ships (42
  pages, where a curated tuple of 18 was read before, so SECURITY.md,
  CHANGELOG.md and the shipped templates were never opened), and a
  claim that the receipt lookup is wider than the directory the caller named
  is recognized by its meaning, with a denial of that same mechanism read as
  the denial it is. A run that derives no page reports that, rather than
  reporting the pages consistent. Proven by
  `a-phrasing-of-the-removed-re-root-nobody-has-written-yet-is-caught`.
- Autosave recovery checks the snapshot out into a NEW detached worktree and
  never writes into the live working tree; the in-place restore path is gone,
  not warned about. Proven by TestAutosaveRecover in `tools/test_sbe.py`.
- The injected digest fits the cap its own SessionStart hook comment names,
  with the long qualifications moved to `LAWS-REFERENCE.md`. Proven by
  TestDigestCap in `tools/test_sbe.py`, which reads the cap out of the hook
  comment rather than hardcoding a second number.
- Every check declares its severity (gate or soft) at write time, prints it on
  every verdict line, and the exit-code mapping is explicit: `--strict` blocks
  on gate FAILs, `--strict-soft` opts graded FAILs into blocking. Proven by
  TestStrictMode in `tools/test_sbe.py` and the severity evals in
  `evals/run_evals.py`.
- `INVARIANTS.md` states the numbered promises with their asserting tests and
  the defect-reinjection record. Proven by the eval cases it names, verified
  present on the date it states.
- Release discipline ships: `VERSION`, this changelog, `CHECKSUMS.sha256` with
  `scripts/checksums.sh` and `scripts/verify-install.sh` (both directions,
  planted extra files fail), and the cut runbook in `docs/RELEASE.md`. Proven
  by the verify-install eval in `evals/run_evals.py`.
- Evidence rendering neutralizes the whole control-character class (categories
  Cc and Cf, plus stray surrogates), not only line breaks, so a receipt field
  cannot forge verdict lines by moving the terminal cursor. Proven by
  `a-receipt-field-cannot-move-the-cursor-in-the-rendered-report` in
  `evals/run_evals.py` and TestOneLineNeutralizesTheControlClass in
  `tools/test_sbe.py`.
- Placeholder detection folds SHAPES (bracketing, a trailing owner, dotted
  initialisms, combining marks and confusables) to a fixpoint, so `[TBD]`,
  `<TODO>`, `TODO(dana)` and `t.b.d.` record no answer, and a container where a
  snapshot id belongs pins nothing. Proven by `a-dressed-up-placeholder-...`,
  `a-bracketed-placeholder-is-not-a-pin` and `a-container-snapshot-id-pins-nothing`.
- The approval gate certifies the NEGATIVE "the approver is not the author"
  only when the difference is proven: proven means no one-for-one look-alike
  substitution this host can read maps one identity onto the other (they
  differ in structure, at a plain-ASCII position, or across a wide or
  right-to-left letterform). A word mixing script families is refused as a
  disguise shape; an identity that is letter-for-letter
  substitution-compatible with the author (a Lisu, Cherokee or Coptic
  spelling of the author's name) is NO-DATA naming the ambiguity, never a
  certificate; a value carrying a reordering bidi control (U+202E and
  family) is refused by code point; and an honest name that merely carries a
  letter no fold reduces (Þóra, Kjær, Bæk, sœur) is certified by its
  readable letters instead of being refused for its unreadable one. Proven by
  `a-confusable-outside-the-curated-table-is-refused-not-passed`,
  `a-multi-script-forgery-is-refused-whatever-scripts-it-uses`,
  `a-partially-mapped-single-script-word-is-refused-not-passed`,
  `a-lisu-spelling-of-the-author-cannot-certify-a-second-person`,
  `a-bidi-override-cannot-render-an-approver-as-the-author`,
  `an-icelandic-name-with-thorn-passes-cleanly`,
  `a-danish-name-with-ae-passes-cleanly`,
  `a-lisu-spelling-of-tbd-is-not-a-snapshot-pin` and
  `a-wholly-single-script-approver-still-passes` (the printed case ids, so the
  citation greps straight out of the suite's own output).
- The gate examines the directory it was named and never a silently
  substituted git top level, so an empty named directory is NO-DATA for that
  directory. Proven by
  `the-gate-examines-the-directory-it-was-named-not-the-git-toplevel`.
- Every diagram and entity grammar starts at any letter (Unicode word
  properties), a diagram line the parser cannot read is confessed and refuses
  the "all traceable" verdict, create/destroy participants keep their alias,
  and `A & B --> C & D` reads every member. Proven by the ten design evals from
  `a-japanese-data-model-is-a-data-model` through
  `a-line-the-parser-cannot-read-refuses-all-traceable` (the printed case ids, so the
  citation greps straight out of the suite's own output).
- The two-alternatives floor counts only rejections that are ESTABLISHED: an
  option whose own text or governing heading carries a rejection verdict, or,
  once the decision is identified among the listed options (a chosen marker
  in any authoring form, a chosen table row, or the Decision's quoted
  choice), the remaining listed options. When no option carries a verdict
  and the Decision paraphrases instead of quoting, the winner may be any of
  them, and the verdict is NO-DATA naming the ambiguity rather than a count
  the check cannot defend; a bullet marking itself chosen is the decision in
  every form, not only in a table. Colon-terminated and bold section leads
  (`Criteria:`, `**Criteria**`, `Entities:`) are read as the headings they
  declare, in the ADR and the data model both. Proven by
  `a-faithful-madr-with-one-rejection-fails-the-floor`,
  `a-madr-with-two-real-rejections-passes-with-an-honest-count`,
  `an-in-bullet-chosen-marker-cannot-inflate-the-rejected-count`,
  `unmarked-options-with-an-unnamed-winner-are-nodata-not-a-count` and
  `a-colon-led-hurried-adr-is-read-as-its-sections`.
- Every report line prints through one flattening choke point (`say()`), so no
  interpolated value of any kind can move the cursor or open a second verdict
  line, and the source lint that enforces it derives its file set from the
  tool walk rather than a filename list: a tool added later is linted on
  arrival, the reviewed exceptions are named with reasons and reconciled on
  every run, and `sbe_decide.py` (whose Recommendation and Alternatives lines
  a table's option name could forge) is inside it. Proven by
  `a-receipt-field-cannot-move-the-cursor-in-the-rendered-report` and the
  report-print lint in `evals/test_no_data_class.py`, whose dead-exemption
  reconciliation is itself a failure path.
- `verify-install.sh` names UNWALKABLE directories and discloses that it
  verifies content, not modes; a shipped doc sentence asserting tool behavior
  is falsified against the source by the doc-behavior guard, whose liveness
  predicates read the MECHANISM (comment-stripped source for the removed
  re-root; the raw marker text for the waiver marker, which is itself
  comment-shaped) and never prose, and one dead claim family is a failure by
  itself. Proven by `no-shipped-doc-widens-the-receipt-lookup-past-what-the-gate-does`.
- Autosave snapshots cover the worktree the ref names (never the hook's cwd
  subtree), the skip-and-save decisions are logged, the tick counter and
  runaway warning serialize on a lock so the printed count is the measured
  one, every superseded snapshot stays reachable through the ref's reflog,
  the per-worktree id is git's own hash of the path (CRC-32 collided across
  ordinary paths), and recover's empty-ref sentence describes the whole
  namespace with legacy-id fallbacks. Proven by TestAutosaveCoversTheWorktree
  in `tools/test_sbe.py`.
- Telemetry writers serialize on an exclusive lock with per-process temp
  paths, and the migrate loss guard recounts the real post-rename file under
  that lock, so a live session's appended row survives a concurrent migrate
  and a maintenance collision cannot report a migration that reached no file.
  Proven by TestTelemetryWriterSerialization in `tools/test_sbe.py`, which
  runs the writers concurrently for real.
- The diagrams FAIL names what it examined (declared entities, components,
  states and systems of record) instead of asserting a whole-dossier absence,
  and a system of record an entity names is a traceable declaration. Proven
  by `a-system-of-record-named-in-the-data-model-traces-a-diagram-node`.
- Guide 01's planted-drift demonstration is replayed from the guide's own
  fenced steps by the suite, and the intake's printed override teaching names
  all three edits while the mismatch FAIL names the completing edit. Proven
  by `guide-01s-drift-demonstration-replays-from-its-own-steps` and the
  override evals in `evals/run_evals.py`.
- An exemption key resolves against the fixture leaf or heading it names, the
  access and legacy axes are non-exemptible by construction, and a waiver that
  excuses no PASS is a meta-test failure. Proven by the `gd_exempt*` guards and
  the dead-waiver guard in `evals/run_evals.py`.
- `verify-install.sh` enumerates every directory entry regardless of type, so a
  symlinked planted module is named rather than invisible, and an eval gates
  `CHECKSUMS.sha256` against the tracked tree so a stale manifest is a red
  suite. Proven by `a-symlinked-planted-module-fails-the-install-check` and
  `the-tracked-manifest-matches-the-tree-it-ships-with`.

- `tools/sbe_telemetry.py`'s `data-show`, `data-export` and `data-purge` had no help path: their
  argv scanning only ever matched the flags each already knew, so `data-export --help` fell through
  unconsumed and ran a real export, and a mistyped flag such as `--catgory` on `data-purge` was
  silently ignored rather than refused, which would have purged every category instead of the one
  named. `-h`/`--help` anywhere in a data command's argv now prints that command's usage (what it
  reads, what it writes, its flags) and exits 0 before anything under the vault is touched; any
  other unrecognized flag is refused with usage and a nonzero exit instead of running past it.
  Proof: `tools/test_sbe.py`'s `test_help_on_each_data_command_prints_usage_and_never_creates_the_vault`,
  `test_help_on_a_populated_vault_reports_and_changes_nothing_in_it` and
  `test_an_unrecognized_flag_refuses_nonzero_instead_of_running_live`, each hashing the vault
  directory's listing before and after rather than trusting the command's own claim. Calibrated red
  against the pre-fix dispatch (all three fixtures failed, one on a real `data-show` run appearing
  where usage was expected, one on a vault the command was only asked to describe getting created,
  one on a returncode of 0 for a rejected flag) before the fix restored it, the restore verified
  byte-identical to the pre-recorded `git hash-object` of the fixed file rather than by `git checkout`.

- `tools/sbe_gate.py::_canonical_email` folded case and a `+tag` for every
  approval-identity comparison but never a gmail dot, so an Approved-by trailer
  of `first.last@gmail.com` against an author of `firstlast@gmail.com`, ONE
  real gmail mailbox, fell through the self-approval check unmatched and
  reached the approval gate's strongest sentence: PASS, "proven different." The
  fix folds the local part's dots too, but ONLY for `gmail.com` and
  `googlemail.com`, because dot-insensitivity is a property of those hosts'
  own mail routing, not of email addressing in general; folding it everywhere
  would merge genuinely distinct mailboxes on a host where the dot is
  significant and turn a real second approver into a false self-approval
  refusal. A same-mailbox pair now FAILs by name, and the sentence quotes
  both recorded addresses and says they reach one mailbox under that host's
  own aliasing, not two identities; a genuinely different dotted pair on a
  non-gmail host still reaches PASS, "proven different," unchanged. Proof:
  `evals/run_evals.py`'s `a-gmail-dot-alias-is-not-proven-different` (reads
  the FAIL sentence itself, not just the verdict), the control
  `a-different-dotted-pair-on-a-non-gmail-host-stays-proven-different`, and
  `gmail-plus-tag-and-case-fold-still-collapse-with-the-dot-fold` (the new
  fold stacked with the two it sits beside on the same address), each
  calibrated red against the dot fold commented out and restored to the
  pre-recorded `git hash-object` of the fixed file before being counted
  green. `scripts/derive_refusal_table.py` now also re-runs every unproven
  pair with a gmail dot-alias standing in for the refusal sentence's own
  remedy ("record an email address that differs from the author's"), and
  publishes how many of those the gate still correctly declines, so the
  published table's escape column can no longer be read as "any two
  different-looking addresses close the remainder." `docs/KNOWN-LIMITS.md`
  states the host-dependence and carries that script's fresh output.

- `tools/sbe_checks.py::could_render_same` and `::name_sets_could_collide`
  were safe only because their two callers (the approval check in
  `tools/sbe_gate.py`, reused by `scripts/derive_refusal_table.py`) always
  hand them text already run through `fold()`, which composes via NFKC
  before either function compares a single character. Called on raw text,
  a composed-versus-decomposed spelling of the SAME rendered identity read
  as "proven different": a decomposed Hangul jamo run counts more
  characters than its precomposed syllable and trips the length check that
  earns a certifying PASS its proof, and a precomposed accented letter
  missing from the curated confusable and Latin-name tables (Greek
  omega-with-tonos, outside both) reads as an unreadable letter that
  differs by code point from its own NFD form once the mark-stripping step
  has reduced that form to bare omega, a genuine proof of difference for
  one letter compared against itself. Both functions now run `plain_text`
  (fold()'s own NFKC-composition-and-invisible-strip step, the one
  normalizer this module has, never a second one) over their arguments at
  entry, idempotent for the two callers that already pre-normalize, so
  neither caller's behavior moves. Proof: `tools/test_sbe.py`'s new
  `TestRenderSameNormalizesRawText`, four fixtures (a raw NFC/NFD accent
  pair and a raw Hangul syllable/jamo pair, each through both functions
  directly) plus a control (two genuinely different Hangul syllables, one
  passed as raw jamo, still prove different), each calibrated red against
  the two `plain_text` calls removed and restored to the pre-recorded
  `git hash-object` of the fixed file before being counted green. The
  existing `sig`-class evals exercising both call sites
  (`a-homoglyph-does-not-make-a-second-person`,
  `a-confusable-outside-the-curated-table-is-refused-not-passed`,
  `a-same-script-lookalike-letter-is-refused-not-passed`,
  `a-bidi-override-cannot-render-an-approver-as-the-author`,
  `a-blank-rendering-joiner-does-not-weld-a-second-person`,
  `a-confusable-email-is-the-authors-own-mailbox`, and the gmail-dot-alias
  cases above) were re-run and are unchanged.
