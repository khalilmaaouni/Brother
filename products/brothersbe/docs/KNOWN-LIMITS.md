# Known limits

Every law already states what its machinery does not do, but those statements
live inside the digest and the law text, where the honest half of the product
is the hardest part to read. This page collects them: one heading per limit,
each naming the law it qualifies and the file where the full text lives. Three
laws (L6, L11, L14) live in `SKILL.md` itself; the other sixteen and the six
phases live in the `references/*.md` file its routing table names.
Nothing here is new; a limit stated only on this page and nowhere else would
be a bug in this page.

## The spine is a discipline, not a control

"Design before verification" and "install the check before writing the work"
are [human] lines: no tool computes whether you did. Full text: `SKILL.md`
(The spine), `DIGEST.md`.

## Nothing detects that a change needed an approval (L9)

The gate verifies an approval that was declared. It cannot notice a money-path
change that declared nothing, and nothing resolves a `Reviewed-in:` id, which
is why that path reports NO-DATA rather than an approval. Full text:
`references/laws-hard-gates.md` L9 and `LAWS-REFERENCE.md` (the hard gates).

## This repository's own merged pull requests carry no independent review (L9)

Applied to this repository's own history rather than to a change the gate is
checking, L9 reports NO-DATA, not PASS. Verified on 2026-08-04: all nine
merged pull requests in this repository (numbers 1 through 9) carry zero
submitted reviews, zero review requests, and zero issue comments (`gh pr
list --state merged --json reviews,reviewRequests,comments`, each field
empty on every one of the nine), and `git log origin/main --format='%h|%s|
%(trailers:key=Approved-by,valueonly)'` shows every `Approved-by` trailer on
every merge commit empty. L9 requires an approval naming somebody other than
the author and committer, and self-approval FAILs; nine merged pull requests
with zero reviews is the absence of that evidence, not a weaker form of it.
This page does not imply a review happened out of band, because none did.
The founder has chosen plain disclosure of this gap over turning on branch
protection for this repository. Full text: `references/laws-hard-gates.md`
L9, and the entry directly above.

## Only one tag is published on origin

Verified on 2026-08-04:

```
$ git ls-remote --tags origin | grep -v '\^{}'
9011cf4a2f12cde6f2a55b61047c1cb782897c79	refs/tags/v1.0.0-rc.1

$ git ls-remote --tags origin 'refs/tags/v1.0.0-rc.2' | wc -l
0
```

`v1.0.0-rc.1` is the only tag this repository has ever pushed to origin.
`v1.0.0-rc.2` was cut locally (`docs/RELEASE.md`, "What has actually been
executed") but never pushed, and the version this tree carries moves again
with every release. A pinning command that names a specific `vX.Y.Z` fails
at clone time the moment the named tag is not the one actually published,
which is why `docs/ROLLOUT.md` and `docs/RELEASE.md` now have the reader run
`git ls-remote --tags <repository-url>` and substitute the tag they see,
rather than a version typed into this page going stale the next time a
release is cut. Full text: `docs/ROLLOUT.md` (Upgrade and rollback),
`docs/RELEASE.md` (Pinning an install to a release, What has actually been
executed).

## The tier comes from answers about contracts that no checker reads

The intake asks whether the change alters a data model, an API contract, or a
file interface. Those answers are what set the tier, and the tier is what
decides which design artifacts are required. Nothing anywhere in this tool opens
a schema, a contract, an OpenAPI document, a protobuf file or a file format
specification, so nothing can confirm or contradict an answer: `sbe_intake.py`
records what it is told, `compute_tier` applies the rule to that record, and the
design checks then verify the artifacts a tier requires are present and carry
content. A wrong answer produces a lower tier, fewer required artifacts, and a
run whose every verdict is honest about what it read. The check on that answer
is a person.

## The forcing conditions are read by a person (L6)

Stop-on-ambiguity, contradiction, gate collision, and disproven assumption are
[human]: the checkpoint shape is prose the session follows or does not. Full
text: `SKILL.md` L6.

## The fence checks read registries, not the world (L13)

Fence hygiene and budget-vs-tier run only over registries named in
`BROTHERSBE_REGISTRIES`, and only over fence lines containing the word
"agent". Writing the fence, comparing scopes, and resuming after a kill are
human. Full text: `references/laws-parallel-writers.md` L13, `LAWS-REFERENCE.md`.

## The case-fold confirmation trusts one probe of the project's own volume

`tools/sbe_fence_hook.py::paths_overlap` closes the case-insensitive-filesystem
escape (`docs/BYPASS-COVERAGE.md` row 21) by retrying a missed comparison
case-folded and confirming the fold against the filesystem before trusting it,
never on the string match alone. When both spellings already exist, the
confirmation is `os.path.samefile`, which is definitive. When one or both do
not exist yet, there is nothing to `samefile`, so the confirmation instead
probes whether the PROJECT ROOT's own directory entry answers to a case-swapped
spelling, on the reasoning that case (in)sensitivity is a property of the
volume, not of any one file on it. Two edges follow from that reasoning and are
worth stating rather than assuming away: a root whose own directory name
carries no letters to swap (all digits or symbols) cannot be probed, and this
hook's fail-open bias means an inconclusive probe allows the write rather than
denying it; and a project split across two mounts with different case
sensitivity (a fenced file reached through a symlink onto a different volume
than `root`) is answered by `root`'s volume, not the target's. Neither edge is
fixtured, because neither can be constructed without a filesystem this suite
does not control.

## Blast radius revokes nothing (L14)

"No apply rights on production state" is a working rule plus whatever access
control your estate has. Nothing here can revoke a credential your shell
already holds. Full text: `SKILL.md` L14.

## The CI workflow guards nothing until you copy it (L16)

`--strict` blocks only in a repository that wired it. No CODEOWNERS and no
branch protection ships, so nothing makes editing the workflow require a
review; that is your repository's setting. Full text:
`references/laws-overrides-and-waivers.md` L16.

## Most of the close is human (L17)

Only the vault session log has a check, and only where `BROTHERSBE_VAULT`
points at a vault, which the shipped CI does not set: on a stock runner every
ledger check is NO-DATA at exit 0. Open items, the failures index, the
scorecard and the self-score cap are human review. Full text:
`references/laws-closing-and-review.md` L17.

## Telemetry observes, it never decides

The SessionEnd hook writes the ledger and decides nothing; no CI step reads
it. The checks fed by it are named on their own digest lines. Full text:
`DIGEST.md`, `LAWS-REFERENCE.md` (Telemetry).

## The UNVERIFIED label is the agent's to write

No tool applies it. A session that fails to label unverified output is not
caught by a check. Full text: `references/laws-hard-gates.md` L7,
`references/laws-overrides-and-waivers.md` L16, `DIGEST.md`.

## The doc-honesty guard reads proximity, not grammar

The guard that checks shipped prose against what the tools do now reads a
document the way a reader does, joining hard-wrapped lines into the block they
form, so a false sentence no longer escapes by wrapping. What it still cannot
do is decide which word a negator governs: a sentence carrying "no", "not" or
"never" within 24 characters before a claim is read as denying it, so an
assertion that happens to carry one ("there is no doubt the gate walks up to
the repository root") reads as honest. Requiring the negator to sit
immediately before the claim was tried and reverted, because it flags the
honest denials this project actually writes, where the negator is the clause's
subject. Full text: `evals/run_evals.py` (_SCOPE_DENIAL, _reader_blocks).

## Published figures are derived only where a page says so

A block marked `derived-by: <script>` is re-run by an eval on every suite
execution and the page fails when it disagrees with the script. That is the
whole mechanism, and its boundary is the marker: a number typed into ordinary
prose with no marker is checked by nothing here, exactly as it was before. The
eval-count guards and the lint-count guards cover their own numbers
separately. Full text: `evals/run_evals.py`
(every-derived-figure-in-a-shipped-doc-recomputes),
`scripts/derive_refusal_table.py`.

## The hollowing sweep is not a proof

The meta-test hollows each check's own declared worked example, prints its
coverage, skipped cases and exemptions, and claims nothing about inputs no
fixture plants. Full text: `INVARIANTS.md` (what the register does not claim),
`evals/README.md`.

## Never run in anyone else's CI

Every green run this project cites happened in its own repository or on the
estate it was built on. No external adoption, and no second estate, is
claimed anywhere.

## The ledger rewrite guard leaves one instant uncovered

A maintenance rewrite (migrate, dedup) re-measures the live ledger under the
writer lock immediately before its rename and carries any bytes appended
since its read into the rewrite, so an append that took the 15 second
unlocked fallback survives. What remains is the instant between that final
measurement and the rename itself: an append landing exactly there would be
in neither the rewritten file nor the rewrite's read. The window is
microseconds, not the fallback's 15-plus seconds, and it is covered post-hoc
rather than prevented: every unlocked append records itself in
`<ledger>.unlocked-appends`, and a rewrite that finds a record from inside
its own window says so after the rename and points at the per-run byte
backup. Full text: `tools/sbe_telemetry.py` (_rewrite_locked).

## The writer lock needs a filesystem that honors flock

The telemetry writer lock is an advisory `flock` on a sidecar file. On a
filesystem that does not honor it (a network mount is the ordinary case; the
vault is documented as a local directory for this reason), the lock cannot be
taken, and the degradation is the safe one rather than a silent loss: an
append proceeds unlocked so the row is never dropped and records itself in
`<ledger>.unlocked-appends`, and both maintenance rewrites (migrate, dedup)
REFUSE to rewrite and say so, naming the possibility that the platform has no
working lock. Executed by forcing `flock` to return the unsupported error on
this host: the appended row survived, the fallback recorded itself, and both
rewrites left the file byte-identical. What is lost on such a mount is
maintenance, not data: migrate and dedup will never run there until the vault
sits on a filesystem whose locks work. Full text: `tools/sbe_telemetry.py`
(_writer_lock).

## The approval identity proof has a measured refusal remainder (L9)

The approval gate certifies "the approver is not the author" only when the
difference is proven: by an email address differing at positions the host
reads, by name structure, by readable letters, or by code point within one
script. Two names of ONE script compare by code point (two different Ethiopic
or Devanagari letters are different glyphs, not look-alikes of each other),
which accepts near-identical glyph pairs WITHIN a script as a limit, exactly
as it already does for CJK ideographs. What remains refused is the soft
class: same-script name pairs whose every differing letter is one the
confusable tables fold to ASCII, where a certificate resting on the fold's
coverage would rest on a table this project's own history proves incomplete.
Every refused pair passes by recording an email address that differs from the
author's, which the gate accepts as proof of difference; the refusal sentence
names that escape, and the second-to-last column below exercises it rather
than asserting it. That escape is HOST-DEPENDENT, not a blanket "any two
different-looking addresses prove two people": on gmail.com and
googlemail.com, an address that differs from the author's only by a dot in
the local part reaches the SAME mailbox by the host's own aliasing, so the
gate declines to certify it as a second person, and the escape does not
close those pairs. The last column below exercises that case too, rather
than asserting it, and it is why the escape column must never be read on
its own. Full text: `tools/sbe_gate.py` (gate_approval, _canonical_email),
`tools/sbe_checks.py` (the four character kinds).

The figures below are not typed. They are the output of a script you can run,
over pools it publishes, and an eval re-runs that script and fails when this
page disagrees with it. An earlier edition of this section typed its numbers
by hand over pools it did not publish; the code underneath moved, and the page
went on reporting a measurement nobody could reproduce, which is the same
false assurance this project exists to refuse. Regenerate with
`python3 scripts/derive_refusal_table.py`.

<!-- derived-by: scripts/derive_refusal_table.py -->

```text
Recomputed by scripts/derive_refusal_table.py on 2026-07-27.
Pools of 10 real names per script, 45 unordered pairs each, name only,
no email address recorded. "Unproven" means the gate declines to certify
the two are different people and says so; it is never a silent pass.

script        pairs  unproven  percent  still unproven with distinct emails  still unproven with a gmail dot-alias
Amharic          45         0        0                                   0                                      0
Arabic           45         0        0                                   0                                      0
Armenian         45         0        0                                   0                                      0
Georgian         45         0        0                                   0                                      0
Greek            45         9       20                                   0                                      9
Hebrew           45         0        0                                   0                                      0
Hindi            45         0        0                                   0                                      0
Japanese         45         0        0                                   0                                      0
Korean           45         0        0                                   0                                      0
Russian          45        13       29                                   0                                     13
Thai             45         0        0                                   0                                      0
Vietnamese       45         0        0                                   0                                      0

Real names read as placeholders by the vacuity backstop: 0 of 240 names
across 12 scripts, two disjoint pools of 10 per script.
```

## The citation check never opens a page

`citation-inventory` proves that every external URL cited in README.md,
SKILL.md and docs/ has a `docs/CITATIONS.md` entry answering claim,
population, date and limit, and nothing more. It verifies structure and
coverage offline, makes no network call, and cannot prove a page still says
what its entry recorded; its own verdict sentence states that limit.
Re-checking content against the live page is a human job at review time. Full
text: `docs/CITATIONS.md` (preamble), `docs/HOW-IT-WORKS.md` (section 6).

Where the root ships a `CHECKSUMS.sha256`, that manifest is the shipped file
list and its markdown entries are the scanned set. A manifest naming files the
tree does not carry therefore leaves the scan scope unestablished, and the
verdict is NO-DATA naming that root, never FAIL: the manifest and the missing
files both belong to the named tree (for the default root, the installation
itself), so the gap is that tree's packaging defect and never a finding about
the repository being scored. NO-DATA is not a pass and never becomes one; a
file that EXISTS and cannot be opened is still a gate severity FAIL, because a
document this check cannot read can hide a URL the verdict would miss. Found
as row E29: the published plugin 3.7.3 shipped a manifest naming 291 markdown
files the install did not carry, and every user scoring their own clean
repository read a gate FAIL about the vendor's tree.

## One uncited URL turned six tests red across two suites (FIXED 2026-08-18)

Kept after the fix, in the same spirit as the Windows section below: the shape
of this one is worth more than the repair.

For a day, `python3 tools/test_sbe.py` ended `FAILED (failures=3)` and
`tools/test_sbe_verify_converge.py` ended `FAILED (failures=3)`, and the cause
was in none of the six tests. Commit 26ce4d9 replaced the progress-board URL in
`PROJECT.md` after the previous board link died, and `docs/CITATIONS.md` never
gained an entry for the new one. `citation-inventory` is gate severity, so
every `sbe_score.py --strict` run in this repository exited nonzero, and every
test that reaches score under `--strict` failed with it.

Bisected rather than guessed, in detached worktrees: 11 of 11 pass at de47597,
the parent; exactly those failures appear at 26ce4d9.

THE TRAP, and it is the part worth keeping. Adding the missing entry did not
clear the check. Nothing scanned still cited the PREVIOUS board URL, so its
entry became stale, and a stale entry fails the same check. Proven by adding a
probe entry in a scratch worktree and watching the complaint move rather than
disappear. Clearing it needed the RECORD, not the entry: the inventory's own
`bc992465` entry already said a dead link is "kept here because the identifier
is still referenced in the record of which links died", and no such record
existed for the newer death. `PROJECT.md` now carries one under "The published
links". Deleting the stale entry would have cleared the same check by throwing
away the thing the inventory says to keep.

Two lessons, neither about citations. A gate-severity check over repository
CONTENT fails every test that runs the gate, so one prose omission reads as six
unrelated test failures in two suites, and a handover can honestly report
"three known failures" while the real number is seven and the real cause is
one. And a check with more than one rule is not cleared by satisfying the rule
it happens to be reporting: the second rule is only visible once the first
stops firing.

## The Windows hook findings of 2026-08-17, and what they cost to learn

An engineer running this plugin on Windows 11 found three things a green CI
leg had been quiet about for months. All three are fixed; they are recorded
here because the SHAPE of the miss matters more than the fixes.

1. **The autosave hook timed out on any real-sized repository, silently.**
   The content scan forked several external processes per candidate file. A
   process spawn costs about 24 ms on that box against about 1.5 ms on
   Linux, so a 5,974 file repository projected to roughly 573 seconds, the
   harness killed the hook at its timeout, and the kill produced no
   snapshot, no log line, and no message: the control whose entire purpose
   is "never lose work" was doing nothing, and saying nothing about it.
   Fixed by porting to `tools/sbe_autosave.py` (the scan is in-process, zero
   forks per file, measured at 6.2 seconds wall on a 6,000 file fixture),
   and by giving the scan its own deadline: passing it writes a SKIPPED line
   naming how many files were reached and that nothing was saved. A control
   that cannot finish now says so, which is the NO-DATA rule applied to a
   hook rather than to a checker.
2. **Every `sh` hook was an undeclared hard dependency on Git Bash.** `sh`
   is not on the Windows PATH; the hooks ran only because Claude Code
   happened to spawn them through Git Bash, so on a Windows machine without
   Git for Windows every one of them died at session start. Fixed by
   porting both remaining shell hooks to Python;
   `tools/test_sbe_hooks.py::TestHookContract` now refuses any hook command
   that is not `python3`.
3. **`bash` on the Windows PATH is WSL, not Git Bash.**
   `C:\WINDOWS\system32\bash.exe` ships with Windows and drops into the WSL
   filesystem, where `${CLAUDE_PLUGIN_ROOT}` does not resolve. This one was
   latent (no hook used `bash`), and it is fenced by the same contract test
   rather than left as folklore.

**The lesson, worth more than the three fixes.** The `windows-latest` CI leg
skipped the two `sh` scripts by name, so it was green while the shipped
hooks were broken on Windows. Excluding a script from a platform's CI does
not make it work on that platform, it only stops the platform reporting on
it, and a green leg reads as coverage to everyone who did not write the
exclusion. Windows behavior on this estate stays UNVERIFIED until a Windows
run confirms it; the fixes above are proven on macOS only.

## Windows CI does NOT run, and has not since 2026-08-17

A `windows-latest` leg DID run the same battery, added 2026-08-05. It was REMOVED on
2026-08-17 under the founder law of 2026-08-16 forbidding a Windows or macOS runner on
any trigger: Windows bills at 2x Linux and macOS at 10x, and 886 workflow runs of an
eleven job matrix is where a free tier goes. Measured 2026-08-29: every job in every
workflow here runs on `ubuntu-latest` and nothing else.

THIS SECTION SAID THE OPPOSITE FOR TWELVE DAYS, in the present tense, in the document
whose entire purpose is to state limits honestly. The leg was removed and the page that
advertised it was not touched, so the estate's own limits document was overclaiming its
coverage. What follows is what the leg USED to skip, kept because the same gaps now
fall to the manual protocol. The two POSIX `sh` install and upgrade scripts are still
skipped there by name (they need a POSIX shell this leg does not carry; the
two `sh` HOOK scripts that were also skipped are gone, ported to Python on
2026-08-17, see the section above), and
`SECURITY.md`'s owner-only 0600 file modes remain a courtesy on a filesystem
that does not enforce POSIX permission bits.

Since 2026-08-11 this leg is a discovery leg, not a gate: `continue-on-error`
is set on the job, so it reports its discoveries without blocking a merge,
and only the Linux and macOS matrix blocks. This states in this file what the
workflow states beside the job: Windows is deferred out of V1 and explicitly
experimental, by the founder's ratified decision of 2026-08-09 (overturning
decision 30 of 2026-08-08), in his words "I will find someone to test on
windows when you really finish everything else". The 2026-08-11 program
packet's G02 line, which called Windows P0, is overturned by the same
decision; that packet's text is not in this repository, so the overturn is
recorded here and in the vault rather than by editing it.

The telemetry writer lock (migrate, dedup) no longer degrades unconditionally
on Windows: it is `fcntl.flock` where that exists and `msvcrt.locking` where
it does not, with the same refusal wording either way, so a Windows run can
actually take the lock and run a maintenance rewrite instead of always
falling into the no-lock path this page's next section describes. Full text:
`tools/sbe_telemetry.py` (`_writer_lock`). A round-2 read found the first
version of this lock had no test that could fail if its byte-range reset
(`os.lseek(fd, 0, os.SEEK_SET)` before `msvcrt.locking`) were deleted; a
calibration fixture now records the real file descriptor position at every
`msvcrt.locking` call under a fake `msvcrt` and asserts it is 0. Run against
a scratch copy of the source with the reset before the LOCK call deleted, the
fixture goes red, proving that reset load-bearing. The same fixture could not
be made to go red by deleting a second reset that used to sit before the
UNLOCK call: nothing between a successful lock and this function's own
unlock ever moves that file descriptor's position, so it is already 0 there.
That second reset was dead code and was removed rather than kept behind an
assertion that could never fail. Full text: `tools/test_sbe.py`
(`TestWriterLockByteRangeCalibration`).

Three gaps this leg's reads surfaced remain open, disclosed rather than
hidden:

- The honesty meta-test's ACCESS scenario axis (chmod, broken symlink,
  symlink loop, FIFO) genuinely does not run on Windows: `os.mkfifo` does not
  exist there and a chmod bit does not take read access away the way it does
  on POSIX. The suite already declares this by name in its own output rather
  than silently running fewer scenarios; a shipped doc's pasted summary line
  states the suite's scenario count as a platform-independent property of
  its checks and registries, not as what any one run's ACCESS axis actually
  exercised. Full text: `evals/test_no_data_class.py` (`ACCESS_APPLIES`,
  `access_cases`, `counts`).
- The tracked-manifest integrity eval read several files as stale on
  Windows's first CI reads, and this could not be reproduced from a POSIX
  machine. Rather than guess at a fix, the same eval now reads the git blob
  and the working-tree file as raw bytes for each stale path and reports the
  first offset where they disagree, so the next Windows run states what
  differs instead of only that something does. Checkout-time line-ending
  conversion is the leading theory (no `.gitattributes` in this repository
  pins the working tree's line endings on any platform), but it is stated
  as a theory, not a finding, until a Windows run's own byte-level output
  confirms or refutes it. Full text: `evals/run_evals.py`
  (`gd_manifest_fresh`, `the-tracked-manifest-matches-the-tree-it-ships-with`).
- The shipped-doc consistency eval (`dc2` in `evals/run_evals.py`) matched
  its own regex against a doc's pasted "N module(s)" figure and then never
  compared the number it matched to anything: any module count in a shipped
  doc read as consistent. It now derives the expected module count the same
  way `evals/test_no_data_class.py`'s own `main()` does, by counting what
  `load_tool_modules()` discovers when the suite runs, never from a number
  written into either file, and compares it. That correction has nothing to
  do with Windows: run on this POSIX machine, `README.md` and
  `docs/for-engineers/01-install-and-first-run.md` had both drifted to "35
  module(s)" while the suite discovered 43, as tool modules were added with
  no gate to catch the pasted figures falling behind. Both pasted figures
  are now updated to 43 (the count this branch's own live run produced) and
  `dc2` passes against them. Treat any module count pasted into a doc as a
  snapshot of one run, not a promise: the true figure is always whatever
  `load_tool_modules()` finds when `evals/test_no_data_class.py` runs next,
  and it will drift again the next time a tool module is added; `dc2`
  catching that drift, rather than the literal number staying 43 forever,
  is the actual guarantee here.

One of those gaps closed on 2026-08-07 and is recorded here as closed rather
than deleted, because the reason it was declared was wrong and that matters
more than the entry: two eval cases had been marked PLATFORM-GAP with the
reason "its control tree misbehaves under the Windows shell (run 31040612827,
an unnamed EXTRA file)". The Windows shell was not the cause. The fixtures
write their `CHECKSUMS.sha256` through Python text mode, which on Windows
emits CRLF, and `scripts/verify-install.sh` read the carriage return as part
of every path, so every file the manifest named reported MISSING and every
file on disk reported EXTRA. That was reproduced on POSIX by writing the same
manifest with `newline="\r\n"`, fixed at the root in
`scripts/verify-install.sh` (the trailing return is stripped and the count of
lines it was stripped from is printed, never removed in silence), and pinned
by the eval `a-crlf-manifest-verifies-instead-of-reporting-every-file-missing`,
which runs on every leg and goes red against a copy of the script with the
strip deleted. `verify-install-fails-over-source-in-an-excluded-path` declares
no platform gap any more. This also means an adopter verifying an installation
against a manifest that ever passed through a text-mode write or a
line-ending-normalising transport was told their install looked like a planted
backdoor, on any platform.

## The owner-only telemetry writes are advisory only on Windows

`tools/sbe_telemetry.py` writes several files it calls "owner-only" through
`os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` (or the append
equivalent), in `scan_corrections`, `atomic_append_text`, `_write_brief` and
`cmd_data_export`. Those four are named rather than numbered on purpose. This
entry cited lines 548, 1519, 1569 and 1957 until 2026-08-25, by which time all
four pointed at unrelated code: a secret-pattern loop, a print statement, a
bare return and another print. Nothing detected the drift because prose has no
gate, so the citation is now a symbol a reader can grep and
`TestTheOwnerOnlyWriteSitesStayNamed` fails if any of the four stops making an
owner-only write. On POSIX the requested mode
bits are what the file gets. On Windows, the mode argument to `os.open` sets
only `FILE_ATTRIBUTE_READONLY`; no ACL is written, so the file inherits
whatever permissions its containing directory already grants. A file that this
project's own printed messages once called owner-only landed world-readable
there, which is the exact defect class this project refuses to ship: a
sentence claiming enforcement that does not exist.

What that means in practice:

- The resume brief (`_write_brief`, line 1569) and the correction candidates
  (`atomic_append`, line 548, and `atomic_append_text`, line 1519) can hold
  redacted but still sensitive recent session context: your own recent
  messages, tool commands, and file paths. On Windows, anyone with read
  access to the directory holding them can read them, not only the account
  that wrote them.
- The `data-export` bundle (line 1957) is a copy of everything stored,
  written to the same advisory-only mode. It carries the same exposure and is
  meant to leave the vault, which is the whole point of the command, so treat
  the exported file as sensitive on Windows regardless of what its listed
  mode says.
- A user who points `BROTHERSBE_VAULT` outside their own profile directory
  (a shared drive, a synced folder with broader ACLs than `%USERPROFILE%`)
  gets no additional protection from this tool. Nothing here enforces where
  the vault lives.
- The printed messages that used to claim `(owner-only)` as an accomplished
  fact now report the mode the platform actually gave and say plainly that
  this does not promise enforcement on platforms that ignore POSIX modes,
  mirroring the wording `tools/sbe_autosave.py` already used for its recovery
  worktree's permissions. A true sentence on every platform was chosen over a
  sentence that was only ever true on POSIX.

**Rejected alternative:** setting a real Windows ACL (a DACL restricting the
file to the owning account) so `0o600` means the same thing on every
platform. That needs `pywin32` or shelling out to `icacls`, either of which
adds a new dependency with its own install and failure modes to a tool whose
zero-dependency, no-subprocess design is a stated property elsewhere in this
file and in `tools/sbe_telemetry.py`'s own module docstring. Documenting the
gap honestly was chosen over building around it.
Full text: `tools/sbe_telemetry.py` (`_write_brief`, `atomic_append`,
`atomic_append_text`, `cmd_data_export`), `tools/sbe_autosave.py` (the
`recover` permissions line), `tools/test_sbe.py`
(`TestResumeBrief.test_opt_in_writes_the_brief_and_still_redacts`,
`TestCaptureDefaultsAndAutosaveContentScan.test_show_export_and_purge_do_what_they_claim`).

## Windows: the install-checker family is CLOSED and proven; OWED-4's cause is FIXED, its Windows verification still UNVERIFIED

Rewritten 2026-08-08 with evidence from the windows-latest run itself, which is
what this entry spent three revisions lacking.

**All four install-checker defects are fixed AND GREEN ON WINDOWS.** Not
predicted, read. From run 31186212064, job 92891284093, head 3c9733f9 (rc.24),
each eliminated by its own log line:

```
a-crlf-manifest-verifies-instead-of-reporting-every-file-missing  got=read as paths ok
a-backslash-in-the-install-path-does-not-accuse-a-clean-tree      got=clean tree reads clean ok
no-checksum-tool-is-handed-a-filename-it-would-escape             got=both scripts hash from stdin ok
two-spellings-of-one-root-do-not-make-the-manifest-an-intruder    got=one spelling throughout ok
```

The four were: a CRLF manifest line; the install root spliced into a sed regular
expression; GNU coreutils escaping a backslash-bearing filename so `cut -c1-64`
returned a 63-character hash; and two spellings of one root so the manifest
failed to recognise itself. Every one was a path read as syntax rather than as
data, and every one made the tool tell a clean installation it looked
compromised.

**A note on how this entry got it wrong before.** rc.22 predicted the sed fix
would close both Windows regressions; the rc.23 run falsified that, and this
file recorded the falsification. What it then failed to do was re-read the log
after rc.24, which added the fourth fix. The family closed at rc.24 and nobody
looked. A prediction is worth writing down only if somebody goes back to check
it, and the checking is the part that was missing.

**The defect this entry tracked is FIXED and pinned; the record of what it was is kept below.** The
step that used to be red was the "Honesty meta-test" running
`python3 evals/test_no_data_class.py`, and the historical failure it produced was:

```
32 checks discovered from 6 registries in 60 module(s), 3588 scenarios run, 2 waived by declared exemption, 2 failure(s).
  FAIL sbe_plan.py freshness [full] want PASS got FAIL
  FAIL sbe_plan.py freshness: the declared full_fixture did not produce PASS, so nothing here proves the check body ran or that its worked example is a real one
```

**Mechanism, reproduced rather than reasoned about.** `evals/test_no_data_class.py`
wrote every fixture with `open(path, "w")`. Text mode on Windows translates
each newline into a carriage return plus newline, so the fixture's BYTES differed
from what the registry declared, its hash moved, and the freshness check in
`tools/sbe_plan.py` that pins that hash failed. It was reproduced on POSIX by
patching the writer to emulate the translation, which turned the same two lines
red here.

**The fix landed at the writer, not the fixture.** The eval's `write_file` opens
`"wb"` and writes bytes, so the platform's newline translation can no longer
change the bytes a fixture declares, and the eval pins that mechanism against
regression: it parses its own source and fails itself if `write_file`'s `open()`
mode is ever not `"wb"`. The two lines above cannot recur from that cause.

**The live run, after the fix and the 2026-08-18 honesty-lint work.** On macOS,
at commit 744e02e and later (this branch), `python3 evals/test_no_data_class.py`
reports and exits 0:

```
37 checks discovered from 6 registries in 104 module(s), 4222 scenarios run, 2 waived by declared exemption, 0 failure(s).
```

The check and module counts rose because commit 69cd4e2 recovered modules
(`sbe_gatelock.py`, `tools/sbe_discover.py`) that carry verdict-shaped helper
tuples; those three helpers are declared in the eval's `NOT_A_VERDICT` allowlist
with their reasons, the same remedy the verdict-returning helpers in
`sbe_autosave.py` and `sbe_gate.py` already use.

**What closure still requires, unchanged in kind.** A green POSIX run remains a
PREDICTION about Windows. The `gates-windows` Actions lane that first showed this
failure no longer runs at all: GitHub Actions is disabled estate-wide by founder
law of 2026-08-16, so there is no cloud Windows leg to re-observe and no
merge-gate framing left to fudge. Closure is `docs/WINDOWS-CHECK.md` run end to
end on a real Windows machine, with this eval's own output reading `0 failure(s)`
there. Until that run exists, the Windows half of this entry stays UNVERIFIED,
stated rather than assumed.

## Every threshold was measured on one estate

`tables/`, the RUBRIC baselines, and the lint's own numbers were measured
where this project was built. Re-measure on yours; NO-DATA is a legal score.
Full text: `README.md` (What this is not), `RUBRIC.md`, `INVARIANTS.md`.

## The impact scan proposes a floor, and reads paths more than it reads code

`sbe impact` derives the five intake answers from the git diff and runs them
through the SAME tier table a person's answers go through, so the two can never
drift apart. What it cannot do, stated where the behavior is:

- Two of the five answers are not derivable from a diff. `consumers` is assumed
  `none`, and `crosses_boundary` is inferred only from infrastructure-shaped
  files, so a service call added inside existing code is invisible to it. Both
  assumptions can only LOWER the proposal. The proposed tier is therefore a
  FLOOR: it can say a change is bigger than declared, never smaller.
- A PASS from it means "nothing in the diff contradicts the declared tier". It
  does not mean the declared tier is right.
- Detection is mostly path-shaped, with content patterns only for SQL data
  definition language, destructive operations, and personal-data field names.
  A payment path in a file named nothing like a payment is not detected.
- Content patterns read ADDED lines only, so removing a sensitive line is not
  classified as adding one. The reverse is also true: a deletion that IS the
  risky change (dropping a column in code rather than in a migration) is not
  caught by a content pattern.
- Every changed file no detector covers is reported under `unmeasured`, by name.
  A clean report over an unsupported language is not available from this tool.
- Maturity: INTERNAL-EVAL. It has been exercised on this repository's fixtures
  and on this repository's own diff, and on no other estate.
Full text: `src/brothersbe/impact.py`, `docs/CLI.md`.

## The evidence wrapper binds a run to a commit, and proves less than that sounds

`sbe evidence run` executes the command itself, so the duration, the exit code
and the output digests come from a run rather than from a keyboard, and
`sbe evidence verify` refuses a receipt whose commit or covered files have
moved. What that does NOT establish, stated where the behavior is:

- The `runId` seal is TAMPER EVIDENCE, not a signature. It catches a plausible
  receipt typed to satisfy the schema. It does not stop anybody who has read
  `src/brothersbe/evidence.py`, because the input is the receipt itself and
  there is no key. A locally generated receipt is therefore never more than
  LOCAL-ADVISORY, and `show` says so on every receipt rather than leaving it to
  the reader to remember.
- `CI-CLAIMED` is only as trustworthy as the environment that set
  `SBE_CI_RUN_ID`. Nothing here can tell a run id minted by a CI system from one
  an agent exported into its own shell. The label states where the value came
  from; it does not authenticate it. What makes it worth having is that a
  protected CI configuration is a thing a human controls and an agent in a
  worktree usually does not.
- Nothing checks that the command was the RIGHT command. `sbe evidence run --
  true` produces a flawless receipt for a run that tested nothing. The receipt
  records the argv it ran (redaction aside, see below) so a reader can see
  that; deciding whether that argv is the work the gate wanted is a person's
  job, and no field here does it.
- `argv` is recorded as text, on purpose, because a receipt whose command was
  paraphrased proves nothing about what happened. Before it is written, every
  token is checked against `SECRET_PATTERNS`, the same list
  `tools/sbe_telemetry.py` already uses to redact an operator's own messages
  (imported from there, not a second list kept here). A match becomes a named
  marker, `[REDACTED:<shape>]`, and the receipt's `argvRedactions` field says
  how many were found, so a reader never has to guess whether argv is verbatim.
  This is a NARROWING, not a fix: the pattern list is finite by nature, so a
  credential typed in a shape none of these patterns recognize (a bespoke
  internal token format, a password with no recognizable prefix) still reaches
  the receipt whole, and the digests-only policy that covers stdout and stderr
  does not cover argv either way. Pass secrets through the environment or a
  file, never as an argument, when the shape is anything you are not certain
  the pattern list would catch. Fixtures pin both halves: a planted
  pattern-matching secret comes out as the marker
  (`tools/test_sbe_evidence.py::test_a_secret_shaped_argv_token_is_redacted_not_recorded_verbatim`),
  and this residual limit stays a decision rather than a surprise.
- The digests prove the same bytes came back. They carry none of them, so a
  receipt cannot be used to audit what a command printed, only to detect that it
  printed something different.
- Coverage is what the caller named, or the diff between base and head. A change
  to a file the receipt does not cover is invisible to `verify`, and a receipt
  covering no file at all is NO-DATA rather than a pass, naming why. A diff
  cannot tell code under test from another evidence receipt that happened to
  land in the same range; see "Evidence covering evidence" below for the
  narrower limit that leaves open.
- The staleness check is deliberately strict in one direction: a covered file
  written after the run ended FAILs even when its bytes are unchanged, so a
  checkout or a formatter that rewrites a file identically invalidates the
  receipt. Regenerating is cheap; a receipt that speaks for a file it did not
  see is not.
- `verify` compares against the CURRENT head of the working directory it is
  given. A receipt made on a branch tip and verified at a merge commit FAILs,
  correctly and inconveniently: it is evidence for the commit it was made
  against and for no other.
- Writing a receipt INTO the repository it covers makes that tree dirty, so the
  next receipt generated there is advisory. Keep receipts outside the tree, or
  ignore them, or accept NO-DATA.
- Maturity: INTERNAL-EVAL. Exercised by 27 fixtures in
  `tools/test_sbe_evidence.py` that build real git repositories and run real
  commands, on this repository and on no other estate.
Full text: `src/brothersbe/evidence.py`, `docs/CLI.md`.

## Evidence covering evidence (T6)

A receipt's `coveredFiles`, when computed from a diff rather than an explicit
`--covers` list, is every file that changed in `base..head`, and that diff
cannot distinguish "the code this run tested" from "another evidence receipt
that happened to land in the same range". A receipt regenerated at a fixed
`--out` path is the ordinary shape of a CI re-run (the design, gate and score
checks all write to well-known paths on every push), not an edit to any code
under test. Reproduced: generate `design.json` with `--covers app.py`, commit
it; generate `gate.json` with the default diff-based coverage, which then
names `design.json` alongside `app.py` purely because of where it landed;
regenerate `design.json` in place. Before this fix, `gate.json` FAILed with
"covered file .sbe/evidence/design.json now hashes to ...: the code changed
after the evidence was made", for a check that never claimed to test
`design.json` and whose own covered code (`app.py`) never moved: the evidence
store poisoning itself.

The fix is scoped to interpretation, not to what a receipt records:
`evidence.verify` gained an `exclude_dirs` parameter (default: none, so every
existing caller keeps today's behavior unchanged) naming path prefixes whose
`coveredFiles` entries are still recorded and shown in the note, but never
hashed, timed, or allowed to fail or pass a verdict. `status.py`'s
`_scan_evidence`, the one place this repository reads every receipt in the
store to build BROKEN CLAIMS and COMPLETED EVIDENCE, passes the evidence
store itself. A receipt whose ENTIRE coverage sits under an excluded path
reads NO-DATA, never a silent PASS built on nothing.

What this does NOT close, stated where the behavior is:
- The exclusion is per-caller, not global. `src/brothersbe/decisions.py` and
  `src/brothersbe/work.py` also call `evidence.verify` (for a decision
  package's judged receipts and a task's close postcondition) and neither
  passes `exclude_dirs`; the same accidental coupling can still reach them
  through the identical diff-based mechanism. Closing every caller was out of
  this stage's scope, named here rather than silently left open.
- This does not touch `generate()`: a receipt's own `coveredFiles` field
  still lists an evidence-store path when the diff found one, faithfully, as
  a record of what the diff actually contained. Only what that record is
  allowed to PROVE changed.
- This does not touch the commit-binding check (`headCommit` must equal the
  current HEAD). A receipt that is itself committed to the repository is, by
  construction, generated before the commit that adds it exists, so its
  recorded `headCommit` can never equal that commit's own SHA; the very next
  commit of any kind, evidence or not, makes it stale under the rule two
  entries above ("`verify` compares against the CURRENT head..."). That is
  the pre-existing, already-tested behavior
  (`tools/test_sbe_status.py::TestBrokenClaims::test_a_stale_receipt_is_named_under_broken_claims_and_exits_1`),
  and this fix leaves it exactly as it was: a receipt that is committed and
  then followed by any further commit is still named under BROKEN CLAIMS for
  that separate, unrelated reason.
- The exclusion is a path-prefix match against `coveredFiles` entries as
  RECORDED (POSIX-style, relative to the repository root). A receipt covering
  an absolute path, or a path spelled with backslashes, is not matched and
  not excluded; this repository's own receipts never record either shape.
Full text: `src/brothersbe/evidence.py` (`_check_covered`, `verify`),
`src/brothersbe/status.py` (`_scan_evidence`),
`tools/test_sbe_status.py::TestEvidenceStoreSelfPoisoning`.

## The privacy controls are defaults and patterns, not guarantees

Capture is off by default per category, an organization switch can force it all
off, and the autosave reads file CONTENT before any git object is created. What
none of that does, stated where somebody deciding whether to install this can
read it:

- A file name exclusion has never prevented secret capture and this page will
  not say otherwise. A credential lives in a normally named source file at
  least as often as in a file called `.env`, and this project shipped a comment
  claiming the name patterns meant "credentials never enter the autosave ref".
  They never did. The content scan is what addresses that class, and it is
  pattern matching over the shapes it knows: a secret in a shape it does not
  know still enters the snapshot.
- A local git ref is not a private one. `refs/brothersbe/autosave/<id>` never
  leaves the machine by any action of this tool, and that is a statement about
  this tool only: a backup, a mirror, a sync client or anything else that copies
  `.git` carries the snapshot with it, including whatever a snapshot preserved
  before the content scan existed. Snapshots taken by an earlier version are
  still in your object database; `git reflog <ref>` lists them.
- Excluding a file loses work. An excluded file is left out of the snapshot
  entirely, so an unsaved edit to it is preserved nowhere. The scan is
  deliberately conservative, so it will sometimes exclude a file holding no
  secret at all. Both cases are named with their reason in
  `99-System/telemetry/autosave-exclusions.log`, which is the only reason this
  trade is visible rather than silent.
- Three of the scan's reject reasons are limits, not detections: a file past
  the size limit, a binary file, and a path git could not print literally were
  never scanned at all. They are excluded and recorded on exactly that basis,
  because a file the scanner could not read must not be treated as clean.
- The scan reads every candidate file on every snapshot, and the tick mode
  snapshots every N tool calls. On a very large worktree that cost is real and
  nothing here caps it. Past `BROTHERSBE_AUTOSAVE_MAX_EXCLUSIONS` (200) the
  snapshot is refused outright rather than truncated, because a `git add` whose
  argument list is too long produces an empty tree that would then be committed
  as though it were the work.
- Content already committed is not the autosave's doing and not its to withhold.
  The snapshot index is seeded from HEAD so tracked work is never dropped, so a
  secret that is already in a commit rides along in the snapshot tree. The
  control here is about what a snapshot ADDS to the object database.
- The organization telemetry override is a policy control on a cooperating
  machine. Root can put the file where an ordinary user cannot write it, and a
  user who runs a patched copy of the script is past it regardless. It fails
  closed on an unreadable or unrecognized policy, which is the strongest thing
  it can honestly do.
- Redaction is unchanged and still best effort. What changed is that nothing is
  read out of a transcript until a switch says so, which means the redactor is
  no longer the only thing between a session and a file on disk.
- `data-show` and `data-purge` see one vault. Copies made by a backup, a mirror
  or an export you took yourself are outside their reach, and `data-export`
  deliberately creates one such copy.
- The resume brief flip (founder, 2026-07-29) traded a standing placeholder
  for silence. `transcript` off used to still write a file naming the switch,
  so a resumed session read something even with capture off. Off now means no
  file at all: the switch is named once, on stderr, at the moment the
  `precompact-brief` hook declines to write. A resumed session's SessionStart
  hook has nothing on disk to relay, because there is nothing on disk, so
  anyone who was not watching that stderr line never sees it; SECURITY.md and
  `data-show` are where they find out afterward.
Full text: `tools/sbe_telemetry.py` (the capture policy block),
`tools/sbe_autosave.py` (the content scan block), `SECURITY.md`,
`docs/THREAT_MODEL.md`.

## A green bypass suite covers the scenarios it covers

An external review listed 35 ways a person or an agent could get past these
controls. `docs/BYPASS-COVERAGE.md` is the table: one row per scenario, each row
COVERED (with the fixture named), UNREACHABLE HERE (with the missing thing
named: a GitHub token, branch protection, a warehouse, a real second estate) or
UNCOVERED (with what covering it would take). As this file is written, 18 rows
are COVERED, 6 are UNREACHABLE HERE and 11 are UNCOVERED.

So: a green `python3 tools/test_sbe_bypass.py` means the COVERED scenarios were
tested. It is not a statement about the other 17, and it is not a claim that the
list of 35 is the whole space of bypasses. Some fixtures in that file pin a
bypass that WORKS, and carry `_is_a_limit` in their names for exactly that
reason; a limit fixture is a tripwire on this documentation, not coverage.

Two holes found while writing that table were fixed in a later wave, both in
`src/brothersbe/evidence.py`. `sbe evidence verify` used to open the receipt
path with no access check, so a FIFO where a receipt was expected hung the
command forever with no verdict in either mode; it now runs the same
`evidence_problem` access check the hard gates use before opening, and refuses
a FIFO, socket, device or unreadable file by name in bounded time instead
(`tools/test_sbe_evidence.py::TestAccessAndTimeout`). The evidence wrapper's
`subprocess.run` used to carry no timeout at all, so a command that hung, hung
the wrapper; `sbe evidence run` now takes an optional `--timeout SECONDS` that
kills the child and writes no receipt rather than one that could later verify
PASS. It has NO DEFAULT, on purpose: a silent one would kill a legitimate
long-running test suite, which is the exact false-positive shape this
project's own kill criteria warn against, so a command run with no `--timeout`
can still hang the wrapper forever, precisely as before. One more limit,
measured rather than assumed (a `sh -c "sleep 60 & sleep 60"` run under
`timeout=2` returned in 2.00 seconds and left both sleeps alive): expiry kills
the CHILD, not its descendants, because nothing here kills a process group. The
refusal comes back at the bound, and a grandchild the command spawned may keep
running, detached, after the wrapper has already said no. Both were row 35 of
the table, now COVERED with that residual stated on the row itself.

Full text: `docs/BYPASS-COVERAGE.md`, `tools/test_sbe_bypass.py`,
`docs/CLI.md` ("sbe evidence").

## The task registry only governs writers who register

`sbe task close` detects an out-of-scope write after the fact by reading the
diff, which is exactly why it survives Bash. But the postcondition runs at
close, and only a task that was OPENED can be closed: an actor who never runs
`sbe task open` never meets it, and reviewer separation orders roles inside
the registry only. In front of that actor stands only the fence hook, which
is advisory and fails open with a stated reason. Full text: `docs/CLI.md`
("sbe task"), `docs/HOW-IT-WORKS.md` (the two-layer scope model).

## The registry file itself has no lock

`.sbe/tasks.json` is rewritten atomically (write temp, rename), so it is never
half-written, but two simultaneous `sbe task open` calls are last-write-wins
and one of the two records can vanish. Concurrent writers of the REGISTRY
itself are out of scope by design: no service, no daemon, no lock. `sbe task
check` is the recovery tool, because it re-runs the overlap scan over whatever
the file now holds.

## The registry exempts its own file from the postcondition

Opening a task writes `.sbe/tasks.json`, so that one path, by exact name, is
excluded from the changed-path comparison at close; otherwise no single-writer
flow could ever close clean, which is this control's own kill criterion. The
exemption is one exact path, not the `.sbe/` directory: receipts under
`.sbe/evidence` still count, which is what the reviewer-receipt refusal reads.

## Task expiry is informational, and nothing writes "abandoned" yet

`expiry` is a date a human reads in `sbe task list`; nothing deletes or closes
a task on a clock, so a stale open task keeps refusing overlapping opens until
somebody closes it (with `--force` and a recorded who and why, if its work is
gone). "abandoned" is a legal status value in the schema and no command in
this wave writes it.

## The fence view is one-directional

`sbe task fence` renders markdown FROM the JSON registry for humans reading a
STATE.md style file. Nothing reads markdown fences back into the registry, so
a hand-edited fence line and the registry can disagree, and the registry is
the one the postcondition reads.
## The adoption kit proposes, and verifies only what a filesystem can answer

`sbe adopt` detects a repository's stack and proposes a policy file and a CODEOWNERS example;
`sbe init` installs BrotherSBE's own local footprint. What neither does, stated where the
behavior is:

- `sbe adopt`'s report names three protections that live on GitHub's code review platform, not
  on a filesystem: branch protection, required status checks, and whether review from a code
  owner is REQUIRED. None of the three can ever read PRESENT here. They report
  UNVERIFIABLE-HERE unconditionally, naming what checking them for real would take (a GitHub
  token with repo scope, plus admin rights on the repository), because this tool holds no
  GitHub credentials and asks for none. This is the kill criterion the plugin conversion plan
  states for this wave, made into a fixture:
  `tools/test_sbe_adopt.py::TestAdoptionReportNeverClaimsPresent`.
- A CODEOWNERS file merely existing in the tree IS a fact this tool can read, and it is reported
  separately, under `localFacts`, so it is never folded into a claim about whether GitHub
  actually requires that review. The file existing and GitHub requiring it are two different
  facts, one local and one not, and only one of them is checkable from a clone.
- The repository policy `sbe adopt` proposes (`.brothersbe/policy.json`) is NOT wave 3's
  eventual policy file and JSON schema, which had not shipped as this wave was written. It is a
  smaller, provisional shape built from what stack detection can already see, and the file says
  so on its own `note` field. Replacing it once wave 3 ships its schema is expected, not a
  regression.
- Stack detection walks the tree pruning conventional vendor and build directories by name
  (`.git`, `node_modules`, `vendor`, `venv`, `.venv`, `dist`, `build`, `__pycache__`, `.tox`,
  `.mypy_cache`, `.pytest_cache`, `target`), never by content. A project keeping first-party
  source inside a directory with one of those names is under-detected, and a very large
  unconventionally-named vendor directory is walked in full, which costs real time on a large
  repository with nothing here to cap it.
- Detection of a contract, migration or dbt-shaped path reuses the SAME path patterns
  `sbe impact` runs against a diff (`brothersbe.impact.DETECTORS`), applied here to a full tree
  walk instead. The same limits `sbe impact` already states about those patterns being
  path-shaped rather than content-read apply here too: a migrations directory named something
  this project's patterns do not recognize is not detected.
- The CODEOWNERS example this proposes carries the placeholder `@REPLACE-ME` on every line.
  Neither `sbe adopt` nor anything else in this project has repository membership to read a
  real username or team from, and a placeholder left in place protects nothing: GitHub will not
  resolve it to an owner. `docs/ADOPTION.md` says so before the checklist of what to click.
- `sbe init --with-consumer-ci` copies this INSTALLATION's own shipped
  `.github/workflows/consumer-check.yml` and `.github/actions/sbe-consumer/action.yml`. When
  those files cannot be read from the installation running the command, the copy is skipped and
  named under a warning rather than writing a partial or empty file in their place.
- Both `sbe adopt` and `sbe init` propose deterministic content (no timestamp, no run id) so a
  second `--apply` can recognize nothing changed. The one exception is `sbe init`'s own install
  receipt, which legitimately carries an install timestamp; it is written or refreshed only when
  something else was actually written this run, and left untouched on a no-op run, which is what
  keeps "running it twice changes nothing" true even though the receipt itself is not
  deterministic content.
- Maturity: INTERNAL-EVAL. Exercised by `tools/test_sbe_adopt.py` against real temporary git
  repositories built by the test itself, and against this repository's own diff, and on no other
  estate.
Full text: `src/brothersbe/adopt.py`, `src/brothersbe/initcmd.py`, `docs/ADOPTION.md`,
`docs/CLI.md`.

## The release candidate ships packaging, not a release

Wave 10 adds `.claude-plugin/marketplace.json` (so `claude plugin marketplace
add` has something to read), an install-artifact test, and an
upgrade-rollback test. None of that is a release. Four things stay blocked,
named here in this tool's own voice rather than left for a reader to infer
from what is absent:

- **Signed release.** No tag this project produces is signed. A signed
  release is blocked on a key the founder holds, not on anything this code
  could compute for itself, and nothing here claims otherwise. `git tag -a`
  (`docs/RELEASE.md`, `docs/ROLLOUT.md`) makes an annotated tag, which
  records who ran the command and when; it is not a signature, and this file
  does not call it one.
- **Branch protection.** Unchanged from the limit already stated above under
  "The adoption kit proposes, and verifies only what a filesystem can
  answer": branch protection, required status checks, and required code-owner
  review are GitHub platform settings, never `PRESENT` from a local read,
  always `UNVERIFIABLE-HERE`. Shipping a marketplace manifest changes nothing
  about that; there is still no GitHub token anywhere in this project.
- **`gh auth`.** Nothing in this wave runs `gh auth login`, stores a GitHub
  token, or automates a GitHub-side action on anyone's behalf. Every
  GitHub-side step `docs/ROLLOUT.md` and `PUBLISH-CHECKLIST.md` describe
  (opening a repository, protecting a branch, pushing a tag) is a human
  authorizing it in the GUI.
- **Real-estate maturity claims.** The install-artifact test and the
  upgrade-rollback test are exercised against THIS repository's own git
  history (`tools/test_sbe.py`'s new `TestMarketplaceManifest` class checks
  the manifest shape and re-runs the installed CLI's own validator; the two
  shell scripts are calibrated by breaking each fixture and watching it go
  red, then restoring it and watching it go green, against this
  repository and a disposable clone of it). None of that is evidence from a
  second, independent estate. Maturity: INTERNAL-EVAL, same word this file
  uses everywhere else, meaning the same thing everywhere else: proven here,
  claimed nowhere beyond here.

The upgrade-rollback script carries one limit of its own, stated where the
behavior is rather than only here. This paragraph used to say the repository
had cut no tag, so the script could only ever report NO-DATA. That stopped
being true and the text did not follow: `git tag -l` lists v1.0.0-rc.1 and
v1.0.0-rc.2, and `sh scripts/test-upgrade-rollback.sh` therefore takes its
REAL path, upgrading from the previous tag to HEAD and back. Two things
follow. First, the script's verdict is now a genuine PASS or FAIL about this
repository, not an absence. Second, it fails honestly when the working tree
disagrees with `CHECKSUMS.sha256`, which is what a stale manifest looks like
from the rollback side, so run `scripts/checksums.sh CHECKSUMS.sha256` last
in any change that moves shipped bytes. A NO-DATA verdict remains possible in
a clone with no tags at all, and there it still means the honest absence of
the one fixture the script needs, never a weaker pass.

Full text: `docs/ROLLOUT.md`, `scripts/test-install-artifact.sh`,
`scripts/test-upgrade-rollback.sh`, `tools/test_sbe.py`
(`TestMarketplaceManifest`).

## The recommended install path has an undo now, and here is exactly how much of one

NARROWED, not removed, and the narrowing is quoted rather than asserted.

What stood here before was an asymmetry between the two install paths. The
clone path could always go back (check out the older tag, re-run
`scripts/verify-install.sh`, which is what `scripts/test-upgrade-rollback.sh`
rehearses). The recommended plugin pair could only go forward: `claude plugin
update brothersbe` moves an installation to the newest version and `claude
plugin uninstall brothersbe` removes it, and neither one puts a machine back
on the version it was running an hour ago.

What is now true. `scripts/rollback-install.sh` gives the plugin path the
same move over the same tags, checked by the same verifier, and it finds the
installation by ASKING rather than by assuming a path. Both of these exist at
once on a real machine, which is the whole difficulty:
`~/.claude/plugins/cache/brothersbe/brothersbe/<version>` (the marketplace
install, the bytes Claude Code loads, carrying no `.git`) and
`~/.claude/skills/brothersbe` (the clone `install.sh`'s fallback branch
makes). A first draft of this script defaulted to the second, which would
have run to completion and reported success over an untouched installation, a
worse outcome than shipping no undo at all: it converts "I have no undo" into
"I ran the undo and I am fine." So the script reads Claude Code's own two
records, `installed_plugins.json` for the install path and
`known_marketplaces.json` for the repository that carries the release tags,
the same way `scripts/verify-install.sh` and
`src/brothersbe/__init__.py:repo_root()` compute their own root from their
own file location rather than from a path typed into a document. After
re-installing it RE-READS the first record, refuses to say the word if the
version the harness reports did not move, and verifies the bytes that are now
installed rather than the source they came from.

It previews by default (the install it found and how it found it, the source,
the version now, the version it would move to, and all four steps) and writes
nothing without `--apply`, matching `sbe adopt` and `sbe init` rather than
`install.sh`'s apply-by-default shape, because this command's default outcome
moves an installation BACKWARD. It carries ten refusals, every one evaluated
before the first write; the count in its header is checked against the code by
`tools/test_sbe_install.py`, so a refusal added or removed without updating
the header fails a test rather than leaving a number in a comment nobody
reads.

What it still does NOT do, stated here rather than left to be discovered:

- It cannot pin a version inside Claude Code's own plugin store directly, and
  it invents no way to. It moves the marketplace SOURCE to the earlier release
  tag and re-runs the same `claude plugin marketplace add` plus `claude plugin
  install` pair `install.sh` already uses. Whether the harness then loads the
  rolled-back copy after a restart is the same platform behavior already
  labeled DOCUMENTED CONTRACT on this page, not something a fixture here can
  watch; what a fixture CAN watch, and does, is that the harness's own record
  names the earlier version afterwards.
- An installation with no reachable release history (a marketplace whose
  source is gone, an extracted archive, a copied directory) is refused, not
  served: no history means no earlier version, and the refusal names the
  remedy (`--source-dir`).
- On this repository as published, the honest outcome on most machines today
  is a REFUSAL, not a rollback: "Only one tag is published on origin" above
  still holds, and an installation built from the only published tag has no
  earlier release to return to. The script says so in those words and changes
  nothing. That is the same NO-DATA-is-not-a-pass rule this project applies
  everywhere else, spelled as a refusal because a rollback with no previous
  version to name is a guess, not an absence.
- It never edits `~/.claude/settings.json` and removes nothing: coming forward
  again is the `git checkout` its own closing line prints.
- Maturity: INTERNAL-EVAL, the same word used everywhere else on this page.
  `tools/test_sbe_install.py::TestRollbackInstallScript` builds a disposable
  HOME laid out like a real one (marketplace install, both harness records,
  release source, AND the decoy clone at `~/.claude/skills/brothersbe`) with a
  stubbed `claude` and no network, and proves four things: the target
  resolution finds the marketplace install and never mentions the clone, a
  rollback to a previous release verifies clean through this repository's real
  `scripts/verify-install.sh` (`verify-install: PASSED` in the run's own
  output, not asserted by the test), an installation with no previous release
  refuses and names why, and a refused run leaves the install, the source and
  the clone byte for byte as they were. Each of those was calibrated by
  re-injecting the defect it exists to catch and watching it go red. No
  second, independent estate has run it.

Full text: `scripts/rollback-install.sh`, `docs/ROLLOUT.md` (Upgrade and
rollback), `tools/test_sbe_install.py` (`TestRollbackInstallScript`).

## A gate exemption cannot tell a real reason from a well-formed fake one

`tools/sbe_gate.py` now reads `.sbe-exempt` too, close to how `tools/sbe_design.py`
already does: a directory holding a gate artifact that is not live work (a
finished project's old receipts, a teaching example) names which of the four
hard gates it waives and why, and the report prints WAIVED with that reason on
every run instead of PASS, FAIL or NO-DATA. The mechanical part is real: a file
naming no gates, a file naming a gate and a blank or whitespace-only reason,
and `gates: *` are each refused as their own FAIL, by name, and the artifact
underneath is still checked rather than silently dropped. One check design's
own exemption has that this one does not: `tools/sbe_design.py::parse_exemption`
holds its reason to a minimum word and character count so a one-token reason
cannot pass; this channel checks only that a reason is present, not how short
it is, so `reason: x` waives a gate here where design would refuse it. What is
not mechanical either way, stated as plainly as design's own docstring states
it: no test here can tell a real reason from a well-formed fake one.
`reason: this is a finished project kept for history` waives a gate whether or
not that sentence is true; a blank-reason check only proves a reason was
written, never that it is accurate or long enough to say anything. The control
this buys is narrower than "the waiver is justified": it is "the waiver is
visible, names the gate it covers, and is not a blank switch", which is what
the WAIVED line, the per-gate waiver count, and `--strict-waivers` are for. A
waiver's only expiry is a human deleting the file or narrowing what it names;
nothing here reads a date, an owner, or an approver out of `.sbe-exempt`, the
same limit `tools/sbe_design.py`'s own exemption already carries and this
channel inherits rather than closes.

Full text: `tools/sbe_gate.py` (`parse_exemption`, `find`), `tools/sbe_design.py`
(`parse_exemption`), `evals/run_evals.py` (the `gx1`-`gx4` fixtures).

## A dossier's binding only resolves a commit held as a loose object

`00-intake.json` may carry an OPTIONAL `binding` block: the head commit a
dossier was written against, plus a sha256 per artifact it covers
(`docs/BYPASS-COVERAGE.md` row 23). Left out, nothing changes: no commit is
read, no digest is checked, and the design checks behave exactly as they did
before this block existed. Recorded, `tools/sbe_design.py::_binding_problem`
checks it by reading git's own on-disk files directly (`HEAD`, a ref, a loose
object's path) rather than by running git as a subprocess, which is also why
`tools/test_sbe_bypass.py::test_the_design_checks_never_read_a_commit_which_is_a_limit`
still passes unchanged: it pins the absence of a `subprocess` import and of a
`git log`/`rev-parse` call, and this stays true of a file that resolves a
commit by reading `.git` itself.

The gap that reading `.git` by hand carries and a real git binary would not:
confirming a bound commit id names a real object is checked only against
LOOSE objects under `.git/objects`; a commit folded into a pack by
housekeeping (`git gc`) is invisible to this check. HEAD itself, and any
commit still close enough to it that nothing has packed it, resolve
correctly, which covers the ordinary case a dossier's own author hits: bind
right after committing, and the bound commit is the loosest object there is.
A binding naming a commit from far enough back in history to have been
packed reads NO-DATA here rather than a confirmed FAIL or PASS, the same
"cannot resolve, so cannot vouch either way" answer this project already
gives a snapshot id or a rehearsal id it cannot look up. Row 23 stays
UNCOVERED for exactly this reason: an optional control only a dossier's own
author opts into is not a covered bypass, and this project does not round a
partial, honestly-limited check up to COVERED.

Full text: `docs/BYPASS-COVERAGE.md` row 23, `tools/sbe_design.py`
(`_binding_problem`, `_git_dir`, `_resolve_head`, `_object_exists`),
`tools/test_sbe.py` (`TestDossierBindingScenario23`).

Two more edges the fixtures pin: a bound artifact that no longer exists or cannot be read FAILs quoting the path (never a silent skip), and a bound path that resolves outside the repository FAILs as a broken claim even when the outside file exists and its digest is true, because a design artifact lives in the tree it binds.

## Two honest narrowings from the baseline repair (2026-07-30)

The book's replay check masks one declared-volatile substring, the live
merge-base diff line, in chapter 03's status block; every other byte of every
excerpt is still compared literally, and the calibration in
`tools/test_sbe_book.py::TestDeclaredVolatileLine` proves the mask cannot
widen silently. The private-name scan applies a stands-alone rule to exactly
one vendored minified file; a name planted standalone in that file is still
caught, and a letter-flanked substring of a generated identifier is not. Both
narrowings exist because the alternative was a control that cried wolf, and a
control that cries wolf gets ignored, which is worse than a narrow one.

## sbe plan derives structures, not intent

There is no LLM anywhere in `tools/sbe_plan.py`'s derivation or validation:
every task, citation and verdict comes from parsing a dossier and applying
the rules the spec names, never from reading intent prose beyond those
structures. That has a direct consequence at the point where a dossier's own
decision names no paths: the plan it derives has a first task that owns
nothing, the ownership check FAILs that task by id, and the remedy is a
better dossier, not a guess, because nothing here can infer ownership the
dossier never stated. Freshness is checked the same mechanical way: recorded
dossier digests are compared against the dossier files on disk, so a dossier
edited after planning in a way that changes its digest is caught and named,
but an edit that happens to keep the file's bytes identical is invisible to
this check, because a digest cannot see past its own bytes. Full text:
`docs/specs/2026-07-30-sbe-plan-derivation.md` (What this deliberately does
not do), `tools/sbe_plan.py`, `tools/test_sbe_plan.py`.

## sbe work isolates, it does not merge

`sbe work` gives a task its own branch and its own git worktree, and closes it
only on a postcondition plus a bound receipt, but nothing in this module ever
merges, rebases onto the default branch, pushes, or touches a production
system; that boundary is a source level fixture, not a policy note
(`TestNoMergeLaw::test_work_module_never_constructs_a_merge_rebase_or_push_argv`).
Landing a task's branch onto the default branch, deploying it, and applying
anything to production state stay human decisions this tool never automates,
the same [human] line the rest of this page already draws around merge,
deploy, and apply.

`check`'s scope comparison reads the diff between the worktree's current
state and the task's recorded base, the same postcondition machinery
`sbe task close` already runs. A change made and then reverted inside the
worktree, before `check` or `finish` ever run, therefore leaves no diff to
read: it is invisible to scope checking precisely because scope checking
only ever sees the diff, never a history of edits. This is the same shape of
limit stated above for the task registry's own postcondition, applied here
to a worktree instead of a shared tree.

Worktree isolation is a filesystem convenience, `git worktree add` giving one
task its own directory and branch, not a sandbox. Nothing here restricts
network access, process execution, environment variables, or reads and
writes outside the worktree's own path: an agent working inside a task's
worktree can still read or write anywhere its own OS permissions allow. The
worktree keeps two writers from colliding on the same files; it does not
confine what either writer's code can do while it runs.

Full text: `docs/specs/2026-07-30-sbe-work-lifecycle.md`,
`src/brothersbe/work.py`, `tools/test_sbe_work.py`.

## pr verify reads GitHub, it does not police it

There is no GitHub token on the reference machine, so `sbe pr verify`'s live
path is opt-in, not the default: without GITHUB_TOKEN, GH_TOKEN, or a working
`gh auth token`, every control that needs the network reports NO-DATA with a
remedy, never PASS, and the exit is nonzero
(`test_no_token_no_gh_is_no_data_everywhere_with_remedy_and_nonzero_exit`).
Branch protection and required checks are read from the GitHub API on that
call or reported UNVERIFIABLE; this tool never infers protection state from
local git config, hooks, or history, because a local guess is not the same
fact as what GitHub currently enforces. Approval state is re-fetched from the
API on every run and never cached across runs or within one, so the verdict
always reflects the request that just went out, not a stale copy. Because of
that, a force-push landing between the first fetch and the last one in the
same run is UNVERIFIABLE rather than a guess in either direction, naming both
shas the check saw, and the remedy is the same command again
(`test_a_force_pushed_head_between_first_and_last_fetch_is_unverifiable`).

Full text: `docs/specs/2026-07-30-sbe-pr-verify.md`,
`src/brothersbe/prverify.py`, `tools/test_sbe_prverify.py`.


## converge compares structures, not intent

`sbe converge` reads names, shapes, and receipts, nothing subtler. Contracts
are diffed only when the changed file parses as JSON OpenAPI at both commits;
YAML has no standard-library parser here, so a YAML contract is named
unmeasured and blocks a clean CONTRACTS verdict rather than passing unread.
The DATA dimension scans changed migrations for DROP TABLE and DROP COLUMN
statements against the names the data model documents, and nothing subtler:
a rename, a type change, or a semantic contradiction is beyond this scan.
ARCHITECTURE compares declared component names against new top-level
directories and everything deeper (technology choices, dependencies,
infrastructure, recovery) is NO-DATA by design: intent is not readable from
a diff. Scope compares path names against plan ownership and dossier-named
paths; it does not read file contents, and a changed file that is neither
source-shaped nor detector-matched is named unmeasured rather than counted
clean or flagged as unplanned noise. A FINAL PASS therefore means "nothing
this tool can read contradicts the dossier", and its own output names every
dimension that had nothing to read.


## status --team reads the estate, it does not phone anyone

Approval facts in the team view are only as fresh as the saved report; the
view never calls GitHub, so a review dismissed a minute ago still reads PASS
until `sbe pr verify` runs again, and the staleness line it CAN compute (a
report bound to a commit that is no longer head) is labeled derived. An
unreadable source is a visible unavailable finding, never a silent gap. One
repository per invocation: cross-repository estates are out of scope. And a
structural fact this view had to design around rather than fix: plan task
ids are per-change (every derived plan starts at T01) while the task
registry is one global table, so records are attributed to changes
best-effort by id, conflicts are computed globally over all open records so
no collision can hide, and two changes cannot hold an open task with the
same id at the same time. A team-profile designRoots entry that resolves
outside the repository is refused by name and never walked; discovery stays
inside the tree it was asked about.


## What the first external round taught converge and plan (2026-07-31)

Receipt matching is shlex-canonical on both sides now, and detector kinds
honor content patterns, both learned from foreign estates the hard way
(docs/EXTERNAL-PROOF-2026-07-31.md). Two limits stay: converge SCOPE matches
ownership by file path only, so a later range touching the same files for an
unrelated reason still reads in scope (the deeper diff-content comparison is
future work), and sbe plan does not re-check the tier's required-artifact
list because sbe design owns that gate; run design before plan and CI runs
both. sbe adopt still proposes this repository's own layout to foreign
trees; the existence-filtered proposal is designed and lands with its suite
rewrite, not before.


## The zero-network scan now walks the whole tree, not only tools/

`TestAuditableSurface.test_the_zero_network_property_holds_by_ast` in
`tools/test_sbe.py` used to parse only `tools/*.py` and `tools/*.sh`. It now
parses `src/brothersbe/*.py`, `hooks/**/*.py`, `scripts/**/*.py`, `bin/sbe`,
and `install.sh` too, the same surface `SECURITY.md`'s own suggested audit
grep names (`grep -rnE "urllib|requests|socket|http|curl|wget|subprocess"
tools/ src/ hooks/ scripts/ bin/`). Exact paths are allow-listed rather
than directories, so no sibling module can hide behind one. Two exist today,
and they are the two halves of the same command:
`src/brothersbe/prverify.py`, `sbe pr verify`'s own documented GitHub API
client (see "pr verify reads GitHub, it does not police it" above), and
`src/brothersbe/bbprverify.py`, its Bitbucket Cloud client, added 2026-08-17
so that command reaches a real verdict on Bitbucket instead of only refusing
on host grounds. Both are read-only, neither opens a socket without an
explicit credential, and both report NO-DATA naming the reason rather than
passing when they cannot look. Where the Bitbucket API cannot answer what
the GitHub one can, the verdict says so: a participant object carries no
commit reference, so approval staleness is NO-DATA there rather than
certified. The
shell side of the same test now also flags `nc`, not only `curl` and
`wget`. `install.sh`'s own documented `git` network calls (`git ls-remote`
at line 98, `git clone` or `git pull --ff-only` at lines 106 to 110) are not
what this scan bans; the property under test is the absence of a direct
`urllib`, `requests`, `socket` or `http` import and the absence of a
`curl`, `wget` or `nc` invocation, not the absence of `git` as a local
subprocess call, which the test's own docstring already names as one of the
three benign shapes a hit is allowed to be.

Full text: `SECURITY.md`, `docs/THREAT_MODEL.md`,
`tools/test_sbe.py::TestAuditableSurface`.


## Every suite runs in both gates as of 2026-08-26, after 39 ran in neither (twice corrected)

**Resolved. Both corrections are kept because the sequence is the lesson.**
Measured 2026-08-26, counting INVOCATIONS rather than name mentions: 74
`tools/test_sbe_*.py` suites exist, and `.github/workflows/brothersbe-gates.yml`
and `release-control/baseline/run-battery.sh` each invoke 77 suites, the same
77, with neither holding one the other lacks. The one deliberate exception is
`test_sbe_prverify_live.py`, in neither, for the credential reason the original
entry below already gives correctly.

The state this replaces, measured 2026-08-25: 39 suites invoked by no gate at
all, and separately 10 the workflow invoked while the battery did not. The drift
ran in both directions and only one direction had ever been counted.

Two things are worth more than the fix.

The first is that the check itself measured the wrong gate. The workflow is
`on: workflow_dispatch:` only and had not run since 2026-08-15; the battery is
what runs every release. A count of workflow wiring would have declared the
problem solved while the gate that actually fires stayed short.

The second is that a name is not an invocation. Counting mentions puts
`test_sbe_prverify_live.py` in the wired column because the workflow discusses
it in a comment. Every count here is now of `run:` and `run_step` lines.

`TestEverySuiteIsWiredIntoAGate` in `tools/test_sbe.py` asserts all three
properties, both gates and their parity, so this prose is a record rather than
the thing tracking it.

### The 2026-08-25 correction, superseded above

**This entry's original title and closing claim were true when written and are
false now, and the correction is placed at the top rather than the bottom
because a reader who stops after the first paragraph should stop on the truth.**
Measured 2026-08-25: 74 `tools/test_sbe_*.py` suites exist, 35 are named in a
workflow, and **39 are named in none**. The sentence below claiming exactly one
deliberate exception is off by 38.

Nothing detected the drift for the whole interval, and that is the part worth
keeping. The failure mode is silent by construction: adding a suite is a normal
act, wiring it is a separate act in a different file, and skipping the second
produces no red anywhere. The suite then passes locally on nobody's merge,
which is precisely the state the original entry was written to end.

Three of the 39 are `test_sbe_policy.py`, `test_sbe_check_registry.py` and
`test_sbe_bash_guard.py`, which `docs/HOSTILE-SCENARIO-COVERAGE.md` cites as
the mechanical coverage behind six of the ten red-team scenarios. Coverage
nothing runs is the same class of claim as a declared gate nothing computes.

`TestEverySuiteIsWiredIntoAGate` in `tools/test_sbe.py` now holds the count as
a dated debt register and fails in both directions, so this prose can never
again be the only thing tracking it. Wiring the workflow is a human edit under
this project's constitution and is not done by a session; the cost is measured
and is zero red, since all three suites above pass on their own.

What follows is the ORIGINAL entry, kept verbatim because it is accurate
history about a real pass that did happen, and deleting it would hide how the
claim came to be made.

### The original entry, 2026-08, superseded above

`.github/workflows/brothersbe-gates.yml` used to run a handful of suites by
name while thirteen others sat in `tools/` passing locally on nobody's
merge. It now runs all of them on both OS legs: `test_sbe_adopt.py`,
`test_sbe_book.py`, `test_sbe_bypass.py`, `test_sbe_converge.py`,
`test_sbe_decisions.py`, `test_sbe_evidence.py`, `test_sbe_install.py`,
`test_sbe_plan.py`, `test_sbe_prverify.py`, `test_sbe_status.py`,
`test_sbe_status_team.py`, `test_sbe_tasks.py`, and `test_sbe_work.py`,
alongside the suites already wired before this pass. Every
`tools/test_sbe_*.py` file now appears in the workflow exactly once, with
one deliberate exception: `test_sbe_prverify_live.py` stays unwired. It is
not the same suite as `test_sbe_prverify.py` (that one is canned and
offline, every GitHub API call routed through a fake fetch, and it is the
one that runs in CI); the live script needs both `SBE_LIVE_GH_REPO` and
`SBE_LIVE_GH_PR` set, plus a token discoverable the same way `sbe pr
verify` itself discovers one, none of which this workflow provides. Without
those, the live script already prints one NO-DATA line and exits 0 by its
own docstring, so wiring it in would either skip silently on every normal
run or force this repository to carry a GitHub token as a CI secret for a
script most runs would never exercise. The workflow carries a comment
stating this reasoning next to the `test_sbe_prverify.py` step it sits
beside. No strictness flag changed to get here; the diff is purely
additive.

Full text: `.github/workflows/brothersbe-gates.yml`.

## A hard-gate receipt with no headCommit still passes unbound

`tools/sbe_gate.py`'s `gate_numbers`, `gate_migration` and `gate_ran` now read
an optional `headCommit` field, the same field name and comparison
`src/brothersbe/evidence.py`'s own `sbe evidence` receipt store already
carries (`_check_commit`). When the field is present and names a commit that
is not the directory's current `git rev-parse HEAD`, the receipt is stale
evidence for the change that is actually checked out and the gate FAILs
rather than PASSing over it: a `numbers-manifest.json`,
`migration-receipt.json` or `ran-receipt.json` copied forward from an earlier
commit no longer clears the gate at a later one. Calibrated, in
`evals/run_evals.py`'s
`a-stale-headcommit-ran-receipt-no-longer-passes`,
`a-stale-headcommit-numbers-manifest-no-longer-passes` and
`a-stale-headcommit-migration-receipt-no-longer-passes`, each of which pins a
receipt sound in every other field to an old commit, moves HEAD on with a
second, unrelated commit, and asserts FAIL; with `_commit_problem` in
`tools/sbe_gate.py` neutralized to always return `None`, the same three cases
read `want=FAIL got=PASS REGRESSION`, and a fourth
(`a-non-string-headcommit-is-caught`) regresses the same way, confirming the
check is what makes each one red.

What this does NOT do BY DEFAULT, stated because a control that oversells
itself is worse than none: a receipt that records no `headCommit` at all is
not judged by this check either way, and still PASSes exactly as it did
before this field existed. This gate cannot tell "a receipt written before
this field existed" apart from "an operator who chose not to record one",
and every worked receipt this repository ships today is the first kind: the
shipped example receipts under `docs/for-engineers/examples/`, the worked
receipts `docs/guides/05-a-worked-engagement.md` writes and
`evals/replay_guide05.py` replays verbatim, and every eval case in
`evals/run_evals.py` written before this change all carry no `headCommit`
field, and none of those quoted PASS lines are files this change is scoped
to rewrite. Rewriting every shipped receipt and worked-engagement block to
carry `headCommit`, so binding could become the unconditional default, needs
those blocks updated together with the code in one pass, so the doc-quote
guards that replay them do not go stale on landing; that whole-tree pass is
still future work, named here rather than left implicit.

A second, OPT-IN flag, `--require-headcommit`, now closes this gap for any
caller who has already moved its own receipts past it: passed to
`sbe_gate.py`, `gate_numbers`, `gate_migration` and `gate_ran` each report
NO-DATA (never PASS, never a FAIL nothing here observed) for a receipt that
names no `headCommit` at all, through the new `_unbound_receipt_problem`.
OFF by default for exactly the reason above: turning it on unconditionally
would regress every shipped example and most of `evals/run_evals.py`'s own
cases from PASS to NO-DATA in the same change that added the flag, which is
the whole-tree rewrite named above, not this one's job. The mismatch case
the earlier change closed, and the unconditional-default case this one still
leaves open, are both unchanged: a passing receipt copied forward from a
commit that is no longer HEAD still FAILs regardless of the flag, and an
unbound receipt still PASSes by default, exactly as before.

A second, smaller side effect of the same change: adding new fixture-backed
eval cases moved `evals/run_evals.py`'s own case count, and a handful of
shipped docs outside this change's scope (`README.md`, `docs/SETUP.md`,
`docs/guides/01-quickstart.md`, `docs/guides/04-teams-and-evolution.md`)
quote that exact count the way `CHECKSUMS.sha256` quotes tracked file hashes.
Regenerating those counts is the same kind of whole-tree pass as regenerating
`CHECKSUMS.sha256`, and is left to it rather than forced through a file this
change was not scoped to touch.

Full text: `tools/sbe_gate.py` (`_current_head`, `_commit_problem`,
`_unbound_receipt_problem`, `REQUIRE_HEADCOMMIT`, `gate_numbers`,
`gate_migration`, `gate_ran`), `src/brothersbe/evidence.py`
(`_check_commit`), `evals/run_evals.py` (the commit-binding cases),
`tools/test_sbe_receipt_shapes.py` (the `--require-headcommit` cases).

## check-update follows a linked worktree, not a broken one

`tools/sbe_telemetry.py::_resolve_git_dirs` follows a linked worktree's
`.git` file (`gitdir: <path>`) to the per-worktree directory git created for
it, then reads that directory's own `commondir` file to find the COMMON
directory where refs/heads and refs/remotes actually live (a linked
worktree does not duplicate them). This covers every worktree `git
worktree add` produces and every one `git worktree prune` has not yet torn
down: the case reproduced and fixed. It does not cover a worktree whose
per-worktree directory survives but whose `commondir` file is itself
missing, unreadable, or points at a directory that no longer exists, the
shape of a hand-edited or partially corrupted `.git/worktrees/<name>`
rather than anything an ordinary `git worktree` command leaves behind. In
that narrower case the helper falls back to treating the per-worktree
directory as the refs source, refs/heads is normally empty there, the
branch ref fails to resolve, and `cmd_check_update` exits at its existing
`if not local: return` exactly as before this change, silently. That is a
narrower silence than the one this change closes (a per-worktree directory
that is itself intact but missing its link to the common dir, not any
linked worktree git actually creates), and is named here rather than
guarded against speculatively for a shape not reproduced. Full text:
`tools/sbe_telemetry.py` (`_resolve_git_dirs`, `cmd_check_update`),
`tools/test_sbe.py` (`TestCheckUpdateFindsAWorktreeGitdir`).

## The authority-file guard cannot always tell a worker from a human (LT-402)

`tools/sbe_authority_hook.py` refuses an UNDECLARED write to an authority-bearing
file (CLAUDE.md, `.claude/**`, `.mcp.json`, `.claude-plugin/**`, `hooks/**`,
`agents/*.md`, `skills/*/SKILL.md`, `CODEOWNERS`, `.github/workflows/**`) only
when it can also tell it is running inside a dispatched worker's session, not a
human editing interactively. Refusing every undeclared authority-file edit
unconditionally would block the ordinary case this project runs in every day: a
human, in an interactive session, with no task registry in play at all, editing
CLAUDE.md by hand.

`_worker_context` answers the question with exactly two signals, either
sufficient on its own: at least one task is OPEN in `.sbe/tasks.json` right now
(regardless of whether it covers the file in question), or the working
directory's own `.git` entry is a FILE rather than a directory, the shape `git
worktree add` leaves behind for a linked worktree and the shape this project's
own dispatch model uses to isolate a worker (`sbe task open --worktree`).

Both are heuristics, not proof, and here is exactly where they fail. A worker
that shares the primary tree (no `--worktree` was used to open its task) and
whose own task has already been closed, or that never ran `sbe task open` at
all, is indistinguishable from an interactive human by either signal. In that
shape, an undeclared authority-file edit is ALLOWED, not refused: the hook
prints a note naming the file and saying no worker context was detected
(`tools/test_sbe_authority_hook.py::TestWorkerContextSignal::test_no_registry_and_no_linked_worktree_allows_the_undeclared_edit`
is the fixture that proves the allow, on purpose, in that shape), but nothing
stops the edit. Closing this gap needs a signal Claude Code's PreToolUse
payload does not currently carry: nothing in `tool_name`, `tool_input`,
`session_id`, `cwd`, or `project_dir` distinguishes a dispatched subagent
invocation from the operator's own primary session. Until such a signal
exists, this is a real, named remainder, not an oversight.

## The authority guard's case-fold confirmation covers named segments, not a powerset

`tools/sbe_authority_hook.py::confirmed_surface` closes the same
case-insensitive-filesystem hazard `tools/sbe_fence_hook.py::paths_overlap`
closes for fence scopes (see "The case-fold confirmation trusts one probe of
the project's own volume", above), reusing its confirmation function
(`_same_entry_case_insensitive`) rather than re-deriving it. But the CANDIDATE
spelling it confirms against is built from a fixed, named table of segments
(`_known_segments`: the literal tuples `tools/sbe_instruction_surface.py`'s
`_matched_surface` is itself built from, plus `CLAUDE.md`, `agents`, `skills`,
`SKILL.md`, `.github`, `workflows`), not an exhaustive case powerset of an
arbitrary path. A case-folded hazard in a path segment none of the nine
detected authority families ever names is out of scope by the same logic
`_matched_surface` itself uses to decide what counts as authority-bearing at
all, and is not a gap this file closes or claims to.

## Per-task worktree evidence under team status and converge (LT-501; both engine gaps fixed in rc.11, one true boundary remains)

The LT-501 golden scenario (`tools/test_sbe_golden_scenario.py`) runs the
whole team lifecycle for real: `sbe work start` opens a dedicated branch and
linked worktree per task, never merges, rebases onto, or pushes to the
repository root's own branch (the no-merge law, checked mechanically by the
same suite). Driving that real chain surfaced two engine gaps in rc.10,
recorded here at the time with red fixtures built to flip. Both were fixed
in rc.11 and the fixtures flipped exactly as designed.

First, fixed in rc.11: `sbe status --team`'s evidence scan
(`status._scan_evidence`) used to compare every receipt's `headCommit`
against the repository ROOT's own checked-out HEAD, so a finished task's
own receipt, generated on the task's own never-merged branch, always read
as a severity-1 broken claim. The scan now resolves a receipt CLAIMED BY A
TASK RECORD, meaning the record's `evidenceId` equals the receipt's
`runId`, against that record's declared worktree when the directory still
exists, and the resolution is disclosed on the entry itself (`verifiedIn`,
`task`, and the finding sentence naming the declared worktree). What
remains true, and is a boundary rather than a defect: a claimed receipt
whose worktree has been removed, an unclaimed receipt, and an unreadable
registry all still verify against root exactly as before, so linkage that
cannot be read never upgrades a verdict. Proven by
`tools/test_sbe_golden_scenario.py::TestTeamModeCIPostcondition`, which
asserts zero severity-1 findings after real task work while the legitimate
NO-DATA blockers (missing approval, missing convergence) stay named
exactly, and separately asserts the plain status JSON names the worktree
resolution, so a scan that silently dropped the receipt cannot pass either
assertion. Calibrated by suppressing the resolution in a scratch copy: the
suite goes red.

Second, fixed in rc.11: `sbe converge`'s VERIFICATION dimension
(`src/brothersbe/converge.py`, the block computing `missing_cover`) used to
compare a bare path string from a plan task's `owns` against
`receipt["coveredFiles"]`, which `sbe evidence run` always writes as a list
of `{path, sha256, note}` objects, so the membership test could never match
and a writer task's OWN receipt was misreported as not covering its own
owned path. The block now extracts `path` from each entry (tolerating a
legacy bare-string entry) before the membership test. Proven by
`tools/test_sbe_golden_scenario.py::TestFullChain`, which asserts the
sealed-receipt PASS finding for T02's own receipt and the absence of the
old does-not-cover text against it. Calibrated by reverting the extraction
in a scratch copy: the suite goes red.

What remains a real limit, unchanged and inherent: `sbe converge` assesses
one explicit `--head`, so receipts for the OTHER tasks, bound to their own
never-merged branch heads, still read as wrong-head stale evidence for the
assessed head until integration happens outside `sbe` (the no-merge law
says `sbe` itself never performs it). The golden scenario asserts
VERIFICATION still FAILs for exactly that reason and FINAL stays FAIL by
the documented rule.


## Plugin interoperability rests on platform behavior this repository cannot drive in a test (LT-502)

`docs/INTEROPERABILITY.md` names seven interoperability guarantees and labels
each one PROVEN BY TEST or DOCUMENTED CONTRACT, never implied. Four of the
seven carry a documented-contract half that no fixture here can turn into a
mechanical check, because the missing half is Claude Code's own runtime, not
this repository's code: whether the harness actually prefixes every skill
with `brothersbe:` at resolution time, whether it correctly merges two
installed plugins' `SessionStart` and `PreToolUse` hooks rather than only
running one, and whether a person who lacks a companion plugin actually
reaches `docs/CLI.md` are all platform or human behavior this repository has
no way to observe from inside a unit test. The one place a human action
substitutes for code, the manual `~/.claude/settings.json` paste
`docs/SETUP.md` step 3 and `docs/HOOKS.md` document for a standalone
(non-plugin) install, is proven only on the code side (nothing in the
install path names the file at all); that a person follows the documented
paste correctly is not something a test can watch.

`sbe doctor` (`src/brothersbe/cli.py::_doctor_checks`) gained no
interoperability row: it is a fixed, hand-written list of tuples, not a
discoverable check registry, and editing it sits outside LT-502's file
boundary (documentation and tests only) and would reopen review on the
doctor command for a change this task was not scoped to make. The contract
is documented instead, in `docs/INTEROPERABILITY.md`'s own "Doctor: branch
taken" section.

Full text: `docs/INTEROPERABILITY.md`, `tools/test_sbe_interop.py`.


## Evidence obligations under CR-06 multi-dossier status, fixed (LANE B-004)

`sbe status`'s (non---team) MISSING EVIDENCE check used to clear an
obligation by consulting `status._scan_evidence`'s own `kindsCovered`, a
single set computed ONCE over the whole `.sbe/evidence/` store with no
notion of which change a receipt belonged to. That was harmless on the
ordinary flat single-dossier layout (one change, one obligation, one
store), but on the CR-06 discovered-dossier path (`design/<name>/` with no
flat `00-intake.json` at root) it meant a gate receipt scoped to change A's
own owned file cleared change B's MISSING EVIDENCE obligation too, purely
because both dossiers, discovered in the same run, consulted the same
global set. Reproduced with a two-dossier fixture before any fix landed:
`chg-a`'s own gate receipt (covering only `src/a.py`, `chg-a`'s own plan
ownership) also cleared `chg-b`'s gate obligation, with nothing in the
output naming why.

Fixed: `status._dossier_evidence_attribution` computes per-dossier coverage
over `_scan_evidence`'s (now additionally returned) `receipts` list. A
receipt is attributable to a dossier when a registry task record that is
GENUINELY that dossier's own claims it by `evidenceId` (the record's `id`
is one of the dossier's own `08-plan.json` task ids AND the record's own
`ownedPaths` overlap a path that dossier's plan `owns` -- the id alone is
not enough, because every derived plan starts fresh at "T01", so sibling
dossiers routinely share task ids and an id-only match would reopen the
identical class of bug through the registry-claim path instead of the
coveredFiles one), or when every one of the receipt's `coveredFiles` falls
inside a path that dossier's plan owns (`sbe_fence_hook.paths_overlap`, the
same containment rule task ownership is judged by everywhere else in this
project). A receipt attributable to no dossier at all is UNSCOPED: it
clears nothing on the discovered-dossier path, and the MISSING EVIDENCE
finding says so by name ("a matching receipt exists but is unscoped", or
names the dossier it actually landed on) rather than staying silent about a
receipt that plainly exists in the store. The flat single-dossier layout
never calls this function at all (`dossier_kinds_covered` stays `{}`, and
every read of it falls back to the original global `ev["kindsCovered"]`
unchanged), so that layout's output stays byte-identical to every earlier
version of this file, per the CR-06 law `status.py`'s own module docstring
states.

What remains a real, honestly-scoped limit: attribution depends entirely on
a dossier's own `08-plan.json` actually declaring `owns` paths (or a
registry record actually declaring a matching `ownedPaths`); a discovered
dossier with no plan yet owns nothing in this scheme and so cannot be
attributed a receipt by either path, which means every one of its
obligations reads as MISSING even for evidence a human would recognize as
"obviously theirs" -- the honest state before `sbe plan --write` has ever
run for that change, not a regression. Two sibling dossiers whose plans
BOTH declare `owns` over genuinely overlapping paths (a plan-level version
of the same ambiguity task-registry fencing exists to prevent, but plan
`owns` values are not deduplicated against each other the way registry
`ownedPaths` claims are checked for overlap) can both be attributed the
same receipt; that is read as intentional here (a receipt over shared,
declared territory clears both obligations), not policed further.

Proven by `tools/test_sbe_status_team.py::TestPerChangeEvidenceScoping`:
one fixture reproduces the original bug (red before the fix, green after,
per this project's own before-any-fix discipline), one pins the UNSCOPED
wording by name, and one pins the T01-collision guard specifically (a
receipt claimed by a registry record whose `ownedPaths` name one dossier's
file must never clear a sibling dossier's obligation merely because that
sibling's own plan also derived a task called "T01"; calibrated by removing
the `ownedPaths` overlap requirement in an rsync scratch copy, which turns
that one fixture red). `tools/test_sbe_status.py::TestDossierDiscovery`
and its sibling classes, unmodified by this change, stay green and continue
to pin the flat layout's byte-identical output.


## The no-server promise is amended to no-remote-server (2026-08-05, gate LP-0301)

`SECURITY.md` promised "no analytics, no account, and no server" since before
1.0. The founder ratified keeping that promise twice, most recently
2026-08-04 (`docs/release-1.0/FABLE-PLAN-REVIEW.md` section 8,
`docs/plans/2026-08-04-parity-triage-verdict.md`), choosing PT-3 (a generated
static map) over a server for the visual surface. Gate LP-0301, opened
2026-08-05, asked for something a generated-and-reloaded page cannot be: a
workspace a person leaves open across a session. The founder's recorded
2026-08-05 decision amends the promise for that one narrow case:
`docs/adr/2026-08-05-gui-server-amendment.md` is the ADR, and it is worth
reading in full for the two alternatives it rejects (the generated page
alone, and a cloud or remote UI) and why. The boundary that survives: no
REMOTE server, ever; a loopback-only workspace binding `127.0.0.1` and
nothing else is authorized for one reserved, not-yet-built module,
`src/brothersbe/gui/server.py`.

What changed on disk in this lane, and what did not. `SECURITY.md`'s promise
paragraph and audit-grep prose are rewritten to state the amended boundary
and point at the ADR; running the grep the document itself gives you still
shows exactly the same single real hit it always has
(`src/brothersbe/prverify.py`), because the reserved GUI path does not exist
in this tree. `tools/test_sbe.py`'s zero-network AST scan
(`_zero_network_scan_paths`, `_zero_network_allowlist`,
`_banned_import_violations`, all above `TestAuditableSurface`) gains a
second exact-path allowlist entry for that same reserved path, and is
extended to walk `src/brothersbe/gui/` recursively rather than stop at the
top level of `src/brothersbe/`, so a sibling file placed in that directory
cannot ride along on the one allowed path's exemption.
`TestGuiNetworkAllowlistIsNarrow` proves both directions against a scratch
copy under `/tmp`, never against the real tree: a planted `import socket` in
a fake `gui/api.py` is caught, and the same import in the allow-listed
`gui/server.py` is not. No GUI code exists anywhere in the repository after
this lane; the scan's behavior against the real tree is provably unchanged
(the reserved glob pattern matches nothing on disk today), which is the
entire point of reserving a path ahead of building it.

The honest limit, named rather than left for a reader to discover:
`docs/THREAT_MODEL.md`, `README.md`, and
`design/final-release-program/01-purpose.md` all still state the
pre-amendment "no server" wording verbatim. None of the three is an owned
file of this lane, and none is silently reconciled here. Until a follow-up
lane updates them, a reader who opens `docs/THREAT_MODEL.md` instead of
`SECURITY.md` sees the old boundary, not the amended one; `SECURITY.md` is
the one this document, the ADR, and the amended scan all agree is current.
A second limit, equally deliberate: this lane authorizes the boundary and
reserves the path, and nothing more. It does not design authentication on
the loopback socket, CORS behavior, or defense against a browser-based CSRF
request reaching a listening local port; the ADR says plainly that the lane
which writes `src/brothersbe/gui/server.py` still owes that threat model of
its own, and inherits the boundary here rather than reopening it.

Full text: `SECURITY.md`, `docs/adr/2026-08-05-gui-server-amendment.md`,
`tools/test_sbe.py::TestAuditableSurface`,
`tools/test_sbe.py::TestGuiNetworkAllowlistIsNarrow`.


## A refused overlap on `sbe task open` leaves no durable record

`src/brothersbe/tasks.py::cmd_open` checks a new task's declared `owns` paths
against every OTHER open task's own `ownedPaths` (through the one shared
`claims_overlap` / `paths_overlap` rule `tools/sbe_fence_hook.py` also uses)
BEFORE it ever reaches the read-modify-write that appends a record and calls
`save_registry`. A refusal prints one line to stderr ("sbe task open:
refused. Owned path ... overlaps ...") and returns a nonzero exit code;
nothing is appended to `.sbe/tasks.json`, and nothing in this path calls
`tools/sbe_telemetry.py`, so no telemetry row, session log line, or registry
field records that the attempt happened at all. Once the calling shell's own
history or terminal scrollback is gone, so is every trace of the refusal.

The silence is intended, not an oversight: the registry's job is to record
STATE that exists (open tasks and the paths they own), never the attempts
that failed to change it, so a refusal that changed nothing durable has
nothing durable to write; the calling process's own exit code and stderr
line are the whole receipt, by design, the same way an ordinary shell
command that exits nonzero is not itself logged anywhere just because it
failed.

Full text: `src/brothersbe/tasks.py` (`cmd_open`, `claims_overlap`).


## Task identity becomes (change, taskId); the headline collision it closes, and what remains (card 1)

This AMENDS "status --team reads the estate, it does not phone anyone",
above: that entry's "structural fact this view had to design around rather
than fix" (plan task ids are per-change, every derived plan starts at T01,
while the task registry was one global table keyed by id alone) is now
partly closed at its root, in the registry itself, rather than only worked
around downstream. `src/brothersbe/tasks.py`'s record schema gains a
`change` field (schemaVersion 1.0 to 1.1); a task's identity is the pair
(`change`, `id`), not `id` alone. `sbe work start` stamps `change` from the
dossier basename (the same string its branch name already carried,
`sbe/<change>/<taskId>`), so T01 derived from one dossier no longer refuses
to open because an unrelated dossier's own T01 is open. `sbe task open`
gains `--change` directly for the same reason; omitted, a task's change is
the empty, unscoped string, colliding by id alone exactly as every task did
before this pair existed. A bare id given to `sbe task close` / `sbe work
check|finish|remove` still resolves unambiguously in the overwhelming common
case of one match; when it resolves to open (or recorded) tasks in more than
one change, `AmbiguousTaskId` refuses and names every colliding
(change, id) pair rather than guessing, and `--change` disambiguates (the
value given is STRIPPED before comparison, the same way `sbe task open
--change` strips it before storing, so a padded value that opened cleanly
stays addressable by the identical padded string on `close`/`check`/
`finish`/`remove`; a mismatch here was fixed in the same round this file's
own "does not close" list below was corrected). `sbe work start`'s
WORKTREE directory (not just the registry record) also closes the headline
collision under its own documented DEFAULT flags: see the second bullet
below.

What this does NOT close, stated here because a fix that oversells itself is
worse than none:

- **`sbe work brief`'s own dependency and already-claimed checks stay
  unscoped, by id alone**, the SAME collision class `sbe work start` closes.
  `tools/test_sbe_work_brief.py`, a separate suite outside this change's
  fence, pins that unscoped behavior with fixtures written before the
  `change` field existed; moving `cmd_brief` to scoped checks the way
  `cmd_start` moved is left for a follow-up that also extends that suite.
  `_dependency_problem` (`src/brothersbe/work.py`) takes an optional
  `change`; `cmd_brief` passes none, `cmd_start` passes its own `change_id`.
  Because it is unscoped, `brief`'s already-claimed lookup never resolves to
  more than a single, last-appended record by design; it uses
  `_last_record_any_change`, a dedicated lookup that NEVER raises
  `AmbiguousTaskId`, precisely because `brief` carries no `--change` flag to
  recover with if it did (an earlier build of this pair called the shared,
  change-aware `_find_record` here instead, which raised on an id spanning
  more than one change and told the operator to pass a flag `brief` does not
  have; fixed, `tools/test_sbe_work.py::TestBriefUnscopedLookupNeverRaises`).
- **`sbe work start`'s WORKTREE directory now closes the headline
  collision under its own documented DEFAULT flags.** The branch name
  already carried `change` before this fix (`sbe/<change>/<taskId>`) and
  never collided; the worktree path defaults to
  `<repository's parent>/<repo>-sbe-<taskId>`, unchanged from before, for a
  SINGLE dossier (the common case, and the only case every example in
  `docs/guides/00-sandbox.md` and `docs/book/` exercises). When that plain
  default path is ALREADY TAKEN (routinely true after upgrading, since
  every derived plan starts fresh at "T01" and a sibling dossier's own T01
  is often already running), `sbe work start` now falls back automatically,
  and says so out loud on stdout, to a subdirectory of the same parent named
  for THIS dossier's own change id, so a SECOND dossier's identical task id
  starts cleanly with no extra flag. What remains, by design, not by gap: an
  operator who passes an EXPLICIT `--worktree-dir` and points two dossiers
  at the identical directory by hand still collides there; that is the
  operator's own choice, never a default trap, and `--worktree-dir` never
  falls back. Proven by
  `tools/test_sbe_work.py::TestStartTwoDossiersUnderDefaultFlags` (a REAL
  second `sbe work start`, two real derived plans, both under true default
  flags with no `--worktree-dir` at all, alongside a companion test proving
  the explicit-shared-directory case still refuses).
- **A pre-migration (schema 1.0) record has no recoverable dossier.**
  Nothing it carries says which change produced it, so `migrate_registry`
  does not guess one: every migrated record adopts the empty, unscoped
  change, stated as a fact about old data, not a promise that migrated
  records are disambiguated from each other. Migration is explicit,
  versioned, and runs ONLY on the first WRITE after this build lands
  (`cmd_open`, `cmd_close`); `sbe task list|fence|check` read a 1.0 registry
  as-is and never persist a migrated copy themselves. A 1.0 record already
  carrying a non-string `change` (a hand-edit) refuses the whole migration
  by name; nothing is silently coerced or rewritten. **The flip side of the
  same fact, not stated by the round this migration landed in:** identity is
  now the `(change, id)` pair, so a migrated OPEN record's empty change also
  no longer collides with a DIFFERENT, non-empty change stamped onto a fresh
  `sbe task open` / `sbe work start` for the SAME id. An operator's first
  change-scoped start after upgrading, for an id that already carries a
  legacy OPEN record, SUCCEEDS rather than refusing (before this pair
  existed, one global OPEN-by-id-alone check would have refused it
  outright), leaving TWO OPEN records for one id; a bare
  `close`/`check`/`finish`/`remove` on that id becomes ambiguous, and
  `--change ''` is the working escape hatch that still addresses the legacy
  record on its own. Proven by
  `tools/test_sbe_tasks.py::TestSchemaMigration::
  test_a_migrated_open_legacy_record_no_longer_blocks_a_change_scoped_reopen`.
- **`status --team` and `sbe handover`, both outside this change's fence,
  are unchanged.** The registry now CARRIES the data that would let
  `status.py`'s "attributed to changes best-effort by id" and
  `handover.py`'s own per-id registry scan read the pair instead of
  guessing by id; neither reads it yet. The "structural fact" sentence in
  the entry above still describes their behavior accurately today, only no
  longer the registry's own.

Full text: `src/brothersbe/tasks.py` (`RECORD_FIELDS`, `migrate_registry`,
`_find_open`, `AmbiguousTaskId`, `_normalize_change`), `src/brothersbe/work.py`
(`_find_record`, `_last_record_any_change`, `_dependency_problem`, `cmd_start`,
`cmd_brief`, `_worktree_path`), `docs/CLI.md` (`sbe task`, `sbe work`),
`tools/test_sbe_tasks.py` (`TestChangeScopedIdentity`, `TestSchemaMigration`),
`tools/test_sbe_work.py` (`TestChangeScopedStart`,
`TestStartTwoDossiersUnderDefaultFlags`, `TestBriefUnscopedLookupNeverRaises`).
## A registered check binds a command, and cannot say the command is a good check (BR-1012)

`.sbe/checks.yml` and `src/brothersbe/checks.py` close a real hole: `sbe
evidence run --check <id>` resolves the executable, the argument vector, the
working directory, the covered globs and the runner files out of the registry,
takes no substitution from the caller, and seals `checkId`, `checkKind`,
`checkSpecSha256` and the whole binding into the receipt, so `sbe evidence
verify` can prove afterwards that the REGISTERED command ran, with the
registered arguments, over the files that check covers, against runner bytes
that still hash the same. A redefined check, a renamed or edited runner, a
changed argument vector, or a receipt whose coverage sits outside the check's
own globs, each invalidate the old receipt by name.

What none of that proves is that the registered command performs a real check.
A registry entry pointing `migration-rehearsal` at a script that prints nothing
and exits zero would produce receipts that verify perfectly, and this module
cannot read the script's intent any more than the wrapper under it can read a
free-form command's. The only defence is that `.sbe/checks.yml` sits in the
control-plane rule of `.sbe/policy.yml`, so changing it owes control-plane
evidence and a protected approval from somebody who is not the agent proposing
the change. That is a review control, not a computed one, and it is stated here
rather than implied by the strength of the hashes around it.

Two narrower residuals, named rather than folded into the sentence above. An
executable resolved from PATH (`python3`) is NOT hashed, because a digest of one
machine's interpreter is not reproducible on another, so the binding covers the
runner FILES and not the interpreter that reads them. And a check this
repository owes but has not built (`migration-rehearsal`,
`migration-reconciliation`, `claude-plugin-e2e`, `numerical-reconciliation`,
`security-policy`) is listed under `unregistered:` with its reason instead of
being registered against a script that does not exist: the policy engine reports
MISSING for those, which blocks, and that is the honest state and not a gap the
registry papers over.

Full text: `src/brothersbe/checks.py`, `.sbe/checks.yml`,
`src/brothersbe/evidence.py` (`trust_level`, `_check_registered`).

## There is no protected evidence level in this release

`PROTECTED-CI` used to be minted whenever `SBE_CI_RUN_ID` was set. Any local
process can export that variable, and the receipt seal is a checksum over the
receipt's own fields, so it proves the receipt was not edited afterwards and
proves nothing at all about who produced it. A label must never be stronger
than its evidence, so the label was removed rather than explained away.

Two consequences, both stated where they bite rather than only here:

1. The strongest level this release mints is `CI-CLAIMED`, whose own sentence
   is the whole claim: CI shaped metadata was recorded but no protected
   identity was verified.
2. `.sbe/policy.yml` ships with `protectedEvidenceRequired: false`. The
   mechanism is intact and still refuses both `LOCAL-ADVISORY` and
   `CI-CLAIMED` when a policy turns it on, which
   `tools/test_sbe_check_registry.py` asserts directly. It is off here because
   turning it on while no protected level exists would not make this
   repository strict, it would make it stuck: every governed change would
   report UNPROTECTED forever, demanding a proof nothing can currently issue.

Both flip in the same change that lands cryptographic attestation binding a
receipt to its repository, workflow, commit and run. Until then, any document
claiming this project can prove where a receipt came from is wrong, and this
entry is the correction.

### The same hole exists on the WAIVER surface, and it is the one that turns BLOCKED into PASS (2026-08-25)

The entry above is about receipts. An exception carries a second, separate
claim of protection, and that one is self-asserted too. `waiver_defects` in
`src/brothersbe/policy.py` refuses an exception that names no reason, owner or
expiry, refuses an expired one, and refuses one whose approval is not marked
protected. Its own comment states the principle exactly: "an exception a local
process can mint for itself is not an exception". What the check actually reads
is `approval.get("protected") is not True`, a boolean the exception file
asserts about itself, so a local process satisfies it by writing `true`.

This is not a reading of the code. It was run, against the repository's own
policy and one of its own commit ranges, with everything identical except the
presence of a waiver file this session wrote for itself:

    WITHOUT the self-minted waiver:  verdict: BLOCKED
    WITH it:                         verdict: PASS

and the control printed the reason it accepted the exception, in its own words:

    check:contract-compatibility was MISSING and is WAIVED, never passed:
    minted by this local process to see whether the control notices, owned by
    the process that wrote this file, expiring 2099-01-01, under protected
    approval by the same process

Three things are worth separating, because two of them are the design working.

First, the requirement never reads PASS. It reads WAIVED, and the sentence
carries "never passed" inside it. The vocabulary holds.

Second, an incomplete exception is genuinely refused: drop the reason, the
owner, the expiry or the approval object and `waiver_defects` returns the
defect by name and the requirement KEEPS its blocking state. Scenario B8 of
`TEST-PROTOCOL.md` asks for exactly that and gets it.

Third, and this is the hole: the OVERALL verdict flips from BLOCKED to PASS,
because `api-or-event-contract` is the one rule in `.sbe/policy.yml` that
declares `strictWaivers: false`, so a waived requirement on it does not join
the blocking set. That combination, an exception whose protection nothing
verifies plus a rule where an accepted exception does not block, is a
self-service route from BLOCKED to PASS on a rule that governs published
surfaces including `src/brothersbe/cli.py` and `bin/sbe`.

What is NOT claimed: that this is reachable without write access to the tree.
Anyone who can write a waiver file can already write the code, so this is not
privilege escalation. It matters because the point of the control is to make a
governed change state its exception in a way a reviewer can weigh, and an
exception that names itself as its own approver gives a reviewer nothing to
weigh while still clearing the gate.

No fix is attempted here, and the reason is the same sentence the entry above
ends with: the honest repair is attestation that binds an approval to an
identity, which nothing in this release can issue. Two smaller moves exist and
both are the founder's call rather than a session's, because both change what
real changes are allowed to do: setting `strictWaivers: true` on
`api-or-event-contract`, which makes an accepted exception block there as it
does on the other four rules, or running with `--strict-waivers`, which does
the same globally per invocation and is already implemented. Neither closes the
provenance hole; both remove the route from BLOCKED to PASS that depends on it.

Reproduce it with: `bin/sbe policy evaluate --base dbe0408 --head 45e0fa1
--intake <a T2 intake> --waivers <a file asserting its own protected approval>`,
against and without the waivers file.


## The install verifier reads no file contents, so configuration is reported rather than judged

`scripts/verify-install.sh` compares bytes against a manifest. It never opens a
file to understand what that file does. Inside the paths it excludes by design
(`.claude/` most of all, which holds harness-written local state), that leaves a
real gap: `.claude/settings.json` can declare a SessionStart hook, and the
harness executes that hook on every session.

The verifier now NAMES every configuration file it finds inside an excluded path
and counts them separately, so their presence is visible rather than silent. It
does not fail the run on their presence, and that is deliberate: a
`.claude/settings.json` is ordinary state a correct installation has, and this
script has already shipped four defects that told a clean installation it looked
compromised. Failing on presence would have made a fifth.

So the honest statement of what this control gives you: it proves the bytes of
every manifest-named file, it tells you what unmanifested configuration exists
where it cannot vouch for it, and it does not read that configuration. If you
need to know what a hook will run, read the file it names.

Rejected alternative, recorded: parse `.claude/settings.json` and judge the hooks
it declares. That turns a byte-comparison tool into a policy engine for a schema
this project does not own and cannot pin, and its verdicts would go stale the
moment the harness changed the format.


## Cold-start usability measurement

The cold-start receipt measures a MODEL playing a beginner, not a human. Every
row carries `proxy: model-as-beginner`, and `coldstart-receipt` FAILs when that
label is missing or wrong. It is a proxy for the north star's own check, never
evidence about humans, and the founder-gated human study is not satisfied by it.

Thresholds are undeclared until a baseline run exists, so `coldstart-thresholds`
reports NO-DATA by construction and cannot yet fail anything. Setting the bars is
a separate decision that belongs after the first real measurement, because a
threshold chosen before a baseline is an appetite rather than a measurement.

The runner is never run by CI. It costs real money and it is nondeterministic, so
it runs on a schedule and before a release, and CI grades only the receipt it
leaves behind. The runner refuses to start without an explicit `--max-budget-usd`
ceiling, which the CLI itself enforces.

Not implemented yet, and named here so its absence is not mistaken for coverage:
the surface-based staleness rule from the design. A receipt is currently graded
regardless of how old it is. Once a baseline exists, `coldstart-receipt` gains a
stale branch keyed to whether `skills/`, `SKILL.md`, `references/`, `hooks/` or
`DIGEST.md` changed since the sha it measured.

## The cross-project execution check knows only three project markers, and only reads command text (ADR 2026-08-12, BR-1014)

`tools/sbe_bash_write_guard.py` now refuses a Bash command that EXECUTES a tool
under a different project's root, the control the ADR names as change 3: "no
session in either project runs the other's tools." What follows is what that
control does not cover, stated the same way every other entry on this page is.

A directory counts as a project root only when it directly carries ALL THREE
markers together: a `.git` entry, `.claude-plugin/plugin.json`, AND
`PROJECT.md` (amendment 2026-08-29: originally any ONE was enough; measured
against every plugin installed on this machine, that caught 237 unrelated
installed products this ADR was never about, and only the two real
companions carry all three, so all three is now required). A folder missing
even one of the three, such as a plain archive or handover snapshot that was
never a git repository and never got its own `PROJECT.md`, of which several
already exist on this machine's Documents folder, OR a plugin installed from
a git-backed marketplace that carries a stray `.git` but no `PROJECT.md`, is
invisible to this check. Running a tool out of such a folder is not refused;
it is undeterminable, and this guard's whole design is to allow what it
cannot tell, because a check that refuses what it does not understand gets
turned off. `evals/test_no_data_class.py`'s honesty sweep is what keeps that
path labeled NO-DATA in the guard's own notes rather than silently passed
over; see `_project_marker_root`'s docstring and the `no_data_note` it
produces.

The refusal is also no longer unconditional per tool. `bm_project.py`'s
`status` and `next` are exempted (`_READ_ONLY_VERBS_BY_BASENAME`, amendment
2026-08-29): both are proven read-only in the tool's own source
(`_read_store()` builds a `bs.ReadOnlyStore`), so blocking them defended
against nothing while breaking brotherme's own SKILL.md-documented
guided-loop flow whenever both products are installed together. Every write
verb of `bm_project.py`, and every verb of every OTHER tool this check has
never been taught about, stays refused exactly as before: the exemption is a
two-entry allowlist keyed by tool basename, not a general read/write
classifier of another project's CLI, so a companion tool this file does not
name is refused categorically regardless of what its own arguments say. The
allowlist entry also names the `.claude-plugin/plugin.json` name the marker
root must declare (amendment 2026-08-29, second pass): checking the
executed file's basename and its directory's marker shape alone let this
file's own test prove a file merely NAMED `bm_project.py` under a marked
root got exempted with nothing checked about what it actually was. A read
failure on that manifest refuses, never exempts. Not closed by this check,
named rather than hidden: a genuine but STALE copy of brothermode (predating
the 2026-08-11 read-only fix this exemption rests on) still passes the name
check, because nothing here confirms the copy's version, only its identity.

A second, unrelated exemption in the same file, `_installed_plugin_roots()`,
used to also exempt the ENTIRE plugin cache directory
(`$CLAUDE_CONFIG_DIR/plugins/cache`) for every verb of every tool under it,
checked before the marker walk or the read-only-verb table above ever ran.
Since brotherme's own SKILL.md documents invoking `bm_project.py` via
exactly that cache path, this meant every write verb of the real companion
was allowed unconditionally the normal way a plugin is invoked, independent
of and defeating everything two paragraphs above. Live-confirmed, not
inferred: `bm_project.py start` from a foreign project returned no deny
payload before this was narrowed. `_installed_plugin_roots()` now exempts
only this guard's own installation root; a sibling plugin that is a genuine
companion goes back through the ordinary marker walk and verb table.

Named plainly rather than left for a reader to discover: this file's own
`.git` plus `PROJECT.md`, no `.claude-plugin/plugin.json`, is two of three
markers, not all three, so the umbrella `Brother` repository is itself
outside this check's detectable set. On the evidence available this is a net
improvement, not only a cost: under the old any-one rule, `Brother`'s `.git`
alone marked it as another project, which meant `scripts/run_evidence.py`
(the command this machine's own standing rules require for long-running
work) was itself refused as a cross-project execution from a different
guarded root. Full reasoning and the accepted trade: the ADR amendment.

Also named plainly: this guard's own module docstring defers to
`tools/sbe_session_reconcile.py` as the authoritative control behind its
early warning, and that is true for a write landing inside this project. A
cross-project execution writes into the OTHER repository, which the Stop
reconciler never inspects, on either side. For this one family of check,
this guard is the only line there is, not an early warning in front of a
backstop.

Two smaller, accepted costs of the read-only-verb exemption, both raised by
an independent review and neither fixed: a bare `bm_project.py` invocation
or one with `-h`/`--help` carries no recognized subcommand, so it is refused
like a write verb rather than exempted, even though it only prints usage;
fail-closed, so left as is. And the exemption's correctness rests entirely
on `bm_project.py`'s own current source (which verbs use `_read_store()`),
verified here but pinned by nothing in either repository, so a future change
to that companion's CLI shape could silently invalidate the assumption
without either project's own tests noticing.

What counts as EXECUTING is text matching only, the identical limit the rest of
this file already states about itself: the command name is a path, or a known
interpreter is given a script argument. A command reached only through a PATH
lookup with no separator in its own name, or wrapped in a launcher this file
does not parse (`env python3 tool.py`, a Makefile target, a CI runner that
resolves the path itself), is outside what text can show and is not caught.
Neither is a target computed at run time, read from an environment variable, or
built by string concatenation, for the same reason `_code_payload_candidates`
already states it about write detection: this file reads command TEXT, not
what a script or a compiled tool will do once it runs.

The check does not recurse into a `sh -c` payload the way the write-family scan
does; only the top-level simple commands of a compound line are examined for a
cross-project execution. A command that reaches another project's tool only
through a nested `sh -c '...'` payload is not caught by this half of the guard,
though the write it might perform once running is still subject to the
ordinary write-family scan if that write lands inside this project.

Full text: `tools/sbe_bash_write_guard.py` (the "Cross-project execution
detection" section), `tools/test_sbe_bash_guard.py`
(`TestCrossProjectExecutionIsRefused`, `TestOwnToolExecutionIsUnaffected`,
`TestReadingAnotherProjectStaysOrdinary`, `TestUndeterminablePathsAllow`,
`TestInstalledPluginRootsExemptsOnlyThisGuardsOwnTool`),
`docs/adr/2026-08-12-where-the-shared-machinery-lives.md`.

## The book replay's two reds, both resolved, and what neither of them was (2026-08-24)

This entry replaced a longer one written earlier the same day that stated both
of these as limits to live with. That earlier text was true when it was written
and is false now, and a limits file that keeps a resolved finding phrased as an
open one is worse than one that never mentioned it, so what stands here is what
actually happened.

The symptom was that `python3 evals/replay_book.py` reported `compared 124
output blocks, 2 differ` and exited 1, which made two of the 36 baseline
battery steps red: `04-regression-evals` through the eval
`the-books-terminal-blocks-are-what-the-tools-print`, and `10-book-estate`
through `test_a_bare_machine_skips_the_declared_chapters_by_name`. Two blocks,
two unrelated causes, and the first diagnosis of each was wrong in a way worth
recording.

The first was block 3 of `docs/book/05-the-first-loop.md`, which pinned
`bin/sbe impact . --base 6b24001cd57e --head 02d9d2a`. The initial reading was
that the container's clone was shallow. It was, so the clone was unshallowed
and the question asked again of a complete object database: 977 commits across
37 refs, and `git rev-parse --disambiguate` returning nothing for either
prefix. The objects are ABSENT from the published repository, not unreachable
from a branch, so they were rewritten out of the history that was published and
survive only in whatever local clone recorded the page. Meanwhile the paragraph
above the block promised "the same answer on any clone on any day". The
instinct was right and the pin was what broke it. Re-pinned to
`f0be9e20893d..25b6f2c650bc`, chosen against four criteria: reachable from both
`main` and the `v3.4.1` tag, only 8 commits back so an ordinary clone resolves
it, a 2 file diff including `CHECKSUMS.sha256` so the recorded UNMEASURED lines
still appear, and proposing T0 against the chapter's declared T2 so every word
of the surrounding prose stays true. The prose now carries the qualification it
needed, and says plainly that a clone too shallow to hold either object still
gets NO-DATA rather than a hidden pass.

The second was block 1 of `docs/book/04-install-day.md`, and the first
diagnosis of it was flatly wrong. It was read as a recording artifact that
could never match off the machine that captured it, because `install.sh` echoes
`$origin_url` and a `$HOME` derived clone path into its dry run line and the
committed transcript carried `/Users/...` paths and a `.git` suffixed origin.
That reading came from the tool's printed diff, which shows RAW `want` against
RAW `got`. The comparison it actually performs is `stable(want) != stable(got)`,
so the printed diff is not the residual and reading it as one is a trap. Asking
the control's own `stable()` for the residual instead left exactly one token:

    book (stabilized):  git clone http<path> <repo-root>/skills/brothersbe
    live (stabilized):  git clone http<path> /root/.claude/skills/brothersbe

Everything else folded, the `.git` suffix included. `VOLATILE_REPO_ROOT`
anchored on `/(?:Users|home)/` and `/root` was missing from it, so the RECORDED
side folded to a placeholder while the LIVE side stayed literal. That is a ONE
SIDED mask, which is a worse failure than a lenient one, because no version of
the tool could make the page match on such a host. `root` now sits in that
anchor set, on the ground that it is where uid 0's home lives on Linux and so
is the same category as `/Users/<name>` and `/home/<name>` rather than a new
one. The shape difference is what hid it: those two carry a name segment after
the anchor and `/root` carries none, because `/root` IS the home directory.

That edit touches a control, so it was mutation tested rather than asserted.
The book was mutated twice and the control re-run on each: the tail after the
placeholder (`skills/brothersbe` to `skills/brothersbeWRONG`) and a relative
path in the same block (`.sbe/team-profile.json` to `.sbe/other.json`). Both
came back as `1 differ`, so the added anchor swallows nothing. In this case it
swallows nothing at all, because a repo-named entry follows the placeholder and
the regex's precise branch preserves the tail for byte comparison, which is
what the first mutation demonstrates.

What is worth keeping from all of this, beyond the two fixes. A mask that
normalizes one side of a comparison and not the other does not read as
brokenness, it reads as a stubborn content difference, and it will be argued
about as though the recorded page were at fault. The tell was that the
RECORDED side had been rewritten while the LIVE side had not, and the only way
to see it was to call the comparison's own normalizer instead of trusting the
diff it prints for humans. Both first diagnoses here were confident and wrong,
and in both cases what corrected them was running something rather than reading
something: unshallowing the clone, and calling `stable()` directly.

`python3 evals/replay_book.py` now reports `compared 124 output blocks, 0
differ` and exits 0.

Full text: `evals/replay_book.py` (`stable()`, `VOLATILE_REPO_ROOT` and its
comment), `tools/test_sbe_book.py`
(`TestChapterCapabilities.test_a_bare_machine_skips_the_declared_chapters_by_name`),
`docs/book/05-the-first-loop.md`, `docs/book/04-install-day.md`,
`install.sh` line 165.
