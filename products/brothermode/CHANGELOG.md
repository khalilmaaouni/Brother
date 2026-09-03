# Changelog

## 3.4.4, released 2026-09-02 (via the Brother hub, git-subdir at tag v0.9.11)

Vault link expansion resolves by filename stem. Retrieval signal C joined the
note title to the link target, which meant a wikilink only expanded when a
note's title happened to equal its filename: of 1,270 distinct link targets,
855 name an eligible note and the old join reached 178 of them. Resolving by
stem reaches all 855. Proven by tools/test_bm_vault.py and the measurement
recorded in the same change.

The silent-failure lint debt closes: 273 sites across 97 files in this product
now carry either a real failure path or a visible reviewed waiver naming its
reason, with the write-site manifest and CHECKSUMS.sha256 regenerated. Nine
were real defects rather than reasoned suppressions.

A release defect from the previous cut is fixed: the 0.9.10 bump missed
pyproject.toml, so the package would have published 3.4.2 while every other
site said 3.4.3. That site is now part of this product's bump pass.

Also in this cut: the fence's enforced mode is driven for the first time (20
conditions in both modes, 19 PASS and one honest NO-DATA, with a positive
control proving enforcement is selective rather than blanket), and the one
enforcement gap it found is recorded in the register that claims the coverage
rather than left in a session note.

## 3.4.3, released 2026-08-31 (via the Brother hub, git-subdir at tag v0.9.10; the BrotherModeUp repository is in its archive soak and gets no new tag)

The session cap refusal now names a working file escape the stopped user can
reach: ~/.claude/bm_session_cap.override, honored by tools/bm_session_cap.py
and proven by tools/test_bm_session_cap.py. E2's first-contact work: the
refusal text speaks the user's language and every named escape is one that
actually works.

## 3.4.2, released 2026-08-24 (annotated tag v3.4.2, on the commit that alone carries VERSION 3.4.2 per rule 1)

The seam between the two products carried nothing, and now it carries.

- **The producer deposit names its own version, so the consumer can read it.**
  `tools/bm_passport.py` wrote `.sbe/passport.json` with three fields and no
  `schema` marker. BrotherSBE's consumer half reads NOTHING from a deposit whose
  version it cannot identify, which is correct and deliberate on its part: it
  will not guess field shapes. The result was a seam that silently carried zero
  fields while BOTH halves reported success about their own side. Measured on
  this estate before the fix, over the same file, in the same minute:

  ```
  producer:  whoDidIt: carried
  consumer:  2. WHO DID IT [NO-DATA]
  ```

  The producer already held the correct constant, `GENERATE_SCHEMA_MARKER =
  "change-passport/v1"`, and used it on its `generate` path. The deposit path,
  which is what the command line actually writes, never set it. One line.

  After, over the same project: fields 1, 2, 4 and 5 CARRIED, field 3 NO-DATA
  because it is owed by assurance's own receipts, which is the honest answer
  rather than an empty one.

- **The contract test now fails when the seam breaks.** `test_bm_passport.py`
  asserted the deposit's keys were exactly the three field names, so it actively
  enforced the missing marker. `schema` joins both the allowed set and the
  required set, and its value is asserted against the LITERAL `change-passport/v1`
  rather than against this module's own constant, so renaming the constant on
  either side cannot silently empty the seam again. Calibrated by hand: a wrong
  marker turns the suite red, the correct one turns it green.

### Why the existing contract proof did not catch this

The suite already carried a test proving BrotherSBE reports fields 2 and 5 as
CARRIED. It feeds the consumer the canonical five-field fixture produced by the
`generate` verb. The command line writes the three-field deposit instead. So the
proof exercised a path nobody runs, and stayed green across a seam that carried
nothing. A contract test has to consume what the default command actually emits.

## 3.4.1, released 2026-08-24 (annotated tag v3.4.1, on the commit that alone carries VERSION 3.4.1 per rule 1)

A hygiene release. No behaviour changed, no feature moved, and no test changed
its verdict. It exists because 3.4.0 shipped one file that was never product
documentation.

- **A closed session's working note is out of the released tree.**
  `docs/plan/OVERNIGHT-STATUS-SECOND-SESSION.md` was added to main at 07:50 on
  2026-08-24 by a scheduled overnight session, published inadvertently when a
  later commit landed on top of it, and then swept into the v3.4.0 tag. Line 15
  used a retired product name, which is what
  `test_no_current_page_uses_a_retired_name_as_the_product_name` exists to catch
  on any current page. It is removed rather than corrected: fixing the name
  would have kept a session note inside a public release that has no reason to
  carry one. It is preserved outside the repository, and it remains readable in
  the v3.4.0 tagged tree for anyone auditing what that release contained.

- **The manifest describes this tree.** `CHECKSUMS.sha256` regenerated last,
  after every other change in this commit, and staged by path. The documented
  step for it begins `git add -A`, which is a trap in any tree carrying
  untracked working files: `checksums.sh` enumerates with `git ls-files`, so
  `-A` tracks those files first and they enter the manifest. 3.4.0's own cut hit
  a version of this.

- **The install pin moves to v3.4.1** in `tools/bm_project_facts.py`, and byte
  identically in `README.md`, `docs/QUICKSTART.md`, `docs/SETUP.md` and the live
  pin sentence and clone example inside `docs/RELEASE.md`, per step 2b.

### Why a new release rather than a corrected v3.4.0

v3.4.0 was first tagged carrying a manifest that did not describe its own tree,
was deleted and re-cut once to fix that, and then this file was found still
inside it. The forward cut was chosen over a second re-cut so that anyone who
pulled v3.4.0 holds exactly what was published rather than something that
changed underneath them, and so that this changelog states plainly what was
removed instead of a rewritten tag erasing the disclosure.

### What rule 1 assumes, learned here

Rule 1 says the tag goes on the commit that alone carries the version. It is
correct and does not need weakening. What it assumes, and never said, is that
THE RELEASE COMMIT IS ITS OWN TIP. A cut landed directly on main satisfies that.
A release landed through a pull request cannot: the merge commit is the tip but
does not alone carry the version, so such a release must either tag the merge
and fail rule 1, or tag the version commit and ship a manifest describing a tree
that never shipped. v3.4.0 hit exactly that fork. This release is cut directly
on main, which is the shape that satisfies the rule without amending it.

## 3.4.0, released 2026-08-24 (annotated tag v3.4.0, on the commit that alone carries VERSION 3.4.0 per rule 1)

The release that landed work this project had already written and never shipped.
Five of the six items below existed, finished and tested, on branches that never
reached main. They were found during a worktree reconciliation, not by planning,
and the reconciliation tool is itself one of the items.

- **The delivery packet is an export, redacted by default (D2).**
  `tools/bm_project.py` wrote `DELIVERY-PACKET.md` with unredacted reads while
  the sibling export command offered a redaction contract. The README calls that
  file a packet you can hand to someone and SECURITY.md says the withholding
  policy governs every export, so it is now treated as one. `deliver --raw`
  restores the owner's own unredacted copy, mirroring the contract `export`
  already had. `render_canvas` deliberately keeps raw reads: it is the owner's
  working page and nothing invites handing it over.
  Proof: `python3 tools/test_bm_project.py`, Ran 70 tests, OK.

- **A worktree can be told whether it is safe to delete (M30).**
  `tools/bm_reconcile_worktrees.py` classifies every uncommitted file in a
  worktree as LANDED, UNLANDED or UNKNOWN, and refuses removal unless every file
  is confirmed LANDED. UNKNOWN counts as unsafe, never as a pass. This is not a
  tidiness feature: it was used on 72 worktrees across three repositories the day
  it landed, and six of them sat on branches git called fully merged while still
  holding uncommitted source, including a module and a tool that existed nowhere
  else. Branch merged does not mean worktree empty.
  Proof: `python3 tools/test_bm_reconcile_worktrees.py`, Ran 6 tests, OK.

- **A saved record stops being reported as a failure (R5).**
  `tools/bm_reality.py` could write successfully and still exit nonzero, because
  the string it printed afterward was not ASCII and the process locale was C or
  POSIX. That is worse than a plain failure: the exit code contradicted the
  store. Fixed at the stream, so the exit code reflects the commit and never the
  printing.
  Proof: `python3 tools/test_bm_reality.py`, Ran 43 tests, OK.

- **Authority is tested where the write actually happens (BR-20).**
  `tools/test_bm_authority_sink.py` covers twelve cases at the sink rather than
  at the announcement: eight refusals, a positive case so the suite cannot pass
  by denying everything, a missing session id resolving to NO-DATA or refusal,
  retry and queue unable to manufacture authority, and a changed command or path
  invalidating a prior approval. It also records, as a passing test rather than a
  footnote, the limit it cannot close: two actors sharing one session id are
  indistinguishable.
  Proof: `python3 tools/test_bm_authority_sink.py`, Ran 16 tests, OK.

- **Distributable bytes cannot ship under an unchanged version (new).**
  `tools/bm_release_invariant.py` fails when a distributable file changes in a
  release range and VERSION does not change in the same range, because a
  marketplace user is offered an update only when the version string moves, so
  those bytes would ship as if unchanged. Calibrated against real history: it
  flags the range containing pull requests 51 and 52, which is the failure it was
  built for. A FAIL exits nonzero with no flag required. NO-DATA exits zero,
  deliberately, because NO-DATA is never a pass and never a block.
  Proof: `python3 tools/test_bm_release_invariant.py`, Ran 11 tests, OK.

- **The write-site gate reasons about bindings, not spellings (D4).**
  `TestPreWriteGate` replaced a nine entry regex list with a standard library
  `ast` walk that tracks handles bound by `with open(...) as X`. That catches
  `json.dump(obj, fh)`, the miss three earlier widenings did not, because what
  makes `fh` a file handle is the binding and not the spelling. A file the walk
  cannot parse raises rather than being dropped from the count.
  Proof: `python3 -W error::ResourceWarning -m unittest tools.test_bm.TestPreWriteGate`, Ran 6 tests, OK.

- **The installer migration never deletes what it could not read (M24).**
  `scripts/migrate_install.py` pins an explicit encoding and errors policy on
  every subprocess call, and removal of a stranded directory now happens only
  after a preflight has proven a real reinstall can run end to end. A decode
  failure refuses the apply and leaves the directory in place.
  Proof: `python3 scripts/test_migrate_install_encoding.py`, Ran 2 tests, OK.

Known limits, stated because this file must not claim more certainty than
docs/KNOWN-LIMITS.md admits: `bm_store.py`'s claim command still allows a claim
with an empty session id, which is a caller-side gap outside the sink this
release hardened. Two actors sharing one session id remain indistinguishable.

## 3.3.2, released 2026-08-20 (annotated tag v3.3.2, on the commit that alone carries VERSION 3.3.2 per rule 1)

The release that closes the two open ends of the chain this project is built
around. Until now BrotherMode could show you everything that happened up to
the moment a change was handed to review, and nothing at all after it. A
passport now leaves the execution side carrying who did the work and where it
came from, and a record now comes back the other way saying whether the
release actually worked. Neither existed at 3.3.1.

- **The change passport, produced for the first time.** `tools/bm_passport.py`
  writes the deposit that the assurance side consumes: what changed, who is
  accountable, which method was used, what evidence exists, and what could not
  be established. Two of its fields read NO-DATA on the consuming side in
  every earlier run, because nothing on this side ever delivered them. A
  standalone validator, `tools/bm_passport_validator.py`, ships in the install
  and refuses a hollow deposit: an empty string reads as absence on the far
  side, so depositing one fills nothing while looking filled.
- **The verified-reality record.** `tools/bm_reality.py` records what happened
  after a release: who accepted it, when, against which passport, and any
  reopen, rollback or incident that links back to that acceptance. Three
  refusals carry the weight. Nobody accepts a release anonymously, and a name
  made of invisible characters does not count as a name. An incident that
  links back to no accepted release is refused, because it cannot be audited.
  And a defect must name the new piece of work it opened, which is the return
  edge: a defect the project cannot route back into its own queue leaves the
  loop open, and a record of reality that never feeds the next intent is a log
  rather than a loop. The table is insert only and enforced as such by the
  database itself, so a later and more flattering judgement cannot rewrite
  what was recorded at the time, not even through plain SQL.

  STATED PLAINLY, because a pre-tag adversarial review found it and narrowing
  the claim is cheaper than discovering it later: the command line creates the
  new piece of work and the record carries its identifier, but the record
  cannot itself confirm that identifier still resolves. A caller reaching the
  storage layer directly can supply one that never existed. The queue write
  and the record are also two writes rather than one, so a crash between them
  can leave a piece of work with no record beside it. Both are named backlog
  items rather than silent gaps.
- **The trust surface, swept.** The live install line is pinned rather than
  described, `scripts/doctor.py` now detects a stranded install (a copy whose
  history was orphaned by a rewrite, which reports healthy while running code
  nobody can trace), the fence hook answers when it is asked what it holds
  instead of only speaking in refusals, and several degrade messages stopped
  claiming more than they had checked.
- **Every engineer complaint carried into this release is closed with a test
  that fails on the old shape**, rather than closed by prose. A separate
  correction landed in the same batch: a guard that refused a legitimate
  partial delivery now distinguishes certifying that something succeeded from
  declaring that it is incomplete, and lets the second proceed with its holes
  printed.
- **Every backlog item now names the stage of the chain it serves**, and
  `tools/bm_idle.py` refuses an item that names a stage the chain does not
  have.

Unchanged and still true: the largest gap in `docs/KNOWN-LIMITS.md` stands.
On a machine where this project is installed as a directory copy under
`~/.claude/skills/`, none of its own hooks load, so the controls described
here are the ones a session runs deliberately, not ones the harness enforces
for it. That swap is a founder decision and has not been made.

## 3.3.1, released 2026-08-18 (annotated tag v3.3.1, on the commit that alone carries VERSION 3.3.1 per rule 1)

A corrective release. `v3.3.0` shipped a `CHECKSUMS.sha256` that lists a file
the tag does not contain
(`docs/program/absolute-lead/evidence/BENCH/20260807T140548Z-v2/H7/B/transcript.txt`),
so `scripts/verify-install.sh`, the one command this project offers a user for
answering "is my copy the copy you published", reported a failure on a
correct install of v3.3.0. Nothing in the engine was wrong; the manifest was
generated against a tree that still held that path. This release exists to
give the integrity check a manifest it can pass, and the defect is the reason
the version moved at all.

The failure was invisible to earlier sessions because the release-truth test
SKIPS when the documented tag is absent, and a shallow clone has no tags. It
was found on 2026-08-18 when the full battery ran on a checkout that had them.

- **The manifest describes the tree it ships with.** Regenerated last, after
  every tracked-file change in this release, which is the ordering
  `scripts/doctor.py` check 9 exists to hold. `scripts/verify-install.sh`
  reports 1080 files matching, 0 mismatched, 0 missing at the release commit.
- **Cursor compatibility mode** arrives as a shipped feature (detail below).
  It was carried on a branch since 2026-08-10 and had never reached a
  release.
- **The battery fence and the gate lock.** `tools/bm_fence_hook.py` gains a
  test-gate lock that refuses edits while a battery is running, which closes
  the class of false signal this project has recorded before: a suite
  measured against a tree the session was still editing. Its no-network
  posture is declared per file and per module in `SECURITY.md`, which now
  counts eight subprocess-using tools rather than seven.
- **The published surface carries no operator identity.** Absolute home
  paths and a named vault are out of every tracked file outside dated
  `docs/` material, `BROTHERMODE_VAULT` replaces the hard-coded path, and
  `tools/test_bm_docs.py` gains two rules that fail a page reintroducing
  either. Dated evidence under `docs/` is deliberately untouched, because
  rewriting verbatim command output falsifies it and the same bytes remain
  in git history regardless.
- **The Cursor lifecycle is declared in the effect registry.**
  `tools/bm_effects.py` carries all fourteen verbs, with `cancel` and
  `worktree-remove` classed `destructive_external_action` because both reach
  `git worktree remove --force`, which discards uncommitted work.
- Still unproven, and still stated: nobody outside this machine has run the
  two-command install, the Cursor fence is ADVISORY rather than refusing
  until a live canary is recorded, and `docs/KNOWN-LIMITS.md` continues to
  carry the open items this entry does not re-argue.

### Cursor compatibility mode (2026-08-10)

- Independent Cursor install at `~/.cursor/brothermode` via
  `scripts/install_cursor.py`, with native `hooks.json` wiring through
  `tools/bm_cursor_hook.py`, manage CLI `tools/bm_cursor.py` (`bm-cursor`),
  and clean removal via `scripts/uninstall_cursor.py`.
- Local Fable-to-Cursor harness: dispatch / claim / record-result / adopt
  over `.brothermode/cursor-mailbox/`, optional git worktrees, and
  `CursorMailboxWorker` for the controller seam.
- Skills `cursor-dispatch` (planner) and `cursor-execute` (executor), docs
  in `docs/CURSOR-COMPAT.md`. Fence under Cursor is ADVISORY until a live
  canary is recorded; capability register marks the mode experimental.

## 3.3.0, released 2026-08-15 (annotated tag v3.3.0, on the commit that alone carries VERSION 3.3.0 per rule 1)

Bitbucket support, the competitive position stated with evidence, and the
finalization roadmap. Engine unchanged: no schema migration, no new command,
no new subsystem; the store remains at schema 20 where v3.2.1 left it.

- Bitbucket Cloud support lands as code and documentation.
  `bitbucket-pipelines.yml` runs the documented full gate on Python 3.9 and
  the newest Python 3 with a full-depth clone; `docs/BITBUCKET.md` covers
  install from a mirror (Claude Code's marketplace command accepts full git
  URLs, per its own documentation), remotes, the pull request flow through
  Bitbucket's REST API with Atlassian API tokens (app passwords were
  removed by Atlassian on 2026-07-28), and continuous integration. The
  engine needed no change because it speaks plain git; the page quotes the
  command that proves it, and labels UNVERIFIED the three claims only a
  live mirror can prove, because none exists yet.
- The fair comparison and the road to finished:
  `docs/plan/FINALIZATION-ROADMAP-2026-08-15.md` profiles the
  orchestration-layer field as read live on 2026-08-15 (Ruflo, superpowers,
  BMAD-METHOD, spec-kit and three smaller projects) and states the result
  in both directions: no project in the category ships enforced per-file
  ownership, a refusing handover ceremony, forecast calibration with a
  NO-DATA floor, or a delivered progress page; every project in the
  category beats this one on reach, and the recorded external install
  count is zero. Five phases order the answer.
- Still unproven, so still stated: nobody outside this machine has run the
  two-command install; the tester pack exists to measure exactly that, and
  `docs/KNOWN-LIMITS.md` continues to carry the open items this entry does
  not re-argue.

## 3.2.1, released 2026-08-12 (annotated tag v3.2.1, on the commit that alone carries VERSION 3.2.1 per rule 1)

A release-identity fix, recorded here after the fact: this entry was added
with 3.3.0 because 3.2.1 shipped without a changelog entry of its own.
Content identical to 3.2.0 plus that day's landed work; the number moved
because a history rewrite left the `v3.2.0` tag pointing at a commit that
was no longer the one setting VERSION, the release-truth check went red,
and moving a published tag was rejected in favor of cutting the next
number. `docs/plan/DEFECT-tag-version-disagreement-2026-08-12.md` carries
the decision with its alternatives.

## 3.2.0, released 2026-08-12 (annotated tag v3.2.0, on the commit that alone carries VERSION 3.2.0 per rule 1)

V3 Final. A repositioning release plus a surface cut plus two small
lifecycle items, landed on an engine that does not change shape: no schema
migration, no new command category, no new subsystem. The category is a
verified delivery layer for AI agents.

- The public command surface lands at six names, down from seven. `start`,
  `status`, `deliver` and `doctor` work today. `verify` is a name for the
  review and deliver flow that already exists, with no folder and no entry
  point of its own, and the README says so rather than implying a command.
  `toolkit` is named and not built, with no stub and no silently failing
  entry point. Everything that left the public list keeps working exactly
  as before: the documented surface shrank, the working surface did not.
- `purge` gained a dry run, so you can see what would be removed before
  anything is.
- A plain-language answer to where your data lives, checked by `doctor`
  rather than asserted in prose.
- A guide to using this alongside its sibling product, which governs a
  change as it passes between people while this one governs one person's
  session.
- A team adoption guide and a first-day pack, so a small engineering team
  can install, run one governed change, and know what to write down.
- Fixed: creating a work record through the store API left the generated
  `STATE.md` view stale, which surfaced as `doctor` check 7 and
  `test_brothermode_cli.py` both failing on somebody's first substantial
  task. The refresh now belongs to the mutation rather than to whichever
  caller happens to know it is owed. Found by running the product.

## 3.1.0, released 2026-08-11 (annotated tag v3.1.0, on the commit that alone carries VERSION 3.1.0 per rule 1)

The truth-hardening release. One main branch, and every claim the tree
makes about itself is now either machine-checked or published as a limit.

- Effect classes: every public command declares one of five effect
  classes in one registry (`tools/bm_effects.py`), and a gate proves every
  `pure_read` command changes zero bytes. The eleven audit findings
  against the registry were closed RED first.
- The active stall detector (`tools/bm_stall.py`): a pure-read sweep over
  fences, claims and provisional records that names dead owners and
  prints the exact clearing command beside each finding, wired into
  session start, never executing anything itself.
- The live deny canary: unattended preflight now fires the real fence
  hook and classifies on returncode, so an absent hook can no longer read
  as a demonstrated defect.
- The wall-clock lint (`tools/bm_lint_walltime.py`): absolute wall-time
  assertions in tests are refused at CI, closing a defect class fixed
  seven times in ten days, and the lint records unreadable files as
  violations instead of exiting clean.
- A machine-wide session cap hook and the continuity relay brake
  (generation cap, spend gate, deadline), so successor chains cannot run
  unbounded.
- Truth repairs: the README single-writer claim narrowed to what the
  shipped default performs, the phantom findings-ledger citation
  corrected, install docs collapsed to one source of truth
  (`tools/bm_project_facts.py`), and a cross-family read-only audit of
  the four authority files filed verbatim with every finding triaged.

## 3.0.0, released 2026-08-08 (annotated tag v3.0.0, on the commit that alone carries VERSION 3.0.0 per rule 1)

The identity release. BrotherMode is now one product name everywhere a
person reads it, and `brothermode` is the one technical namespace: plugin
id, marketplace, command boundary and skill invocations all agree. This is
a BREAKING change for anyone running v2: uninstall the old plugin before
installing this one, because the plugin identity itself changed.

- The nine public actions ship as proper Claude Code skills invoked as
  `/brothermode:start`, `status`, `next`, `review`, `deliver`, `view`,
  `help`, `doctor` and `update`. The fifteen legacy `/brotherme-*` command
  files remain as labelled compatibility shims, each naming its
  replacement and its removal condition, and the internal actions are
  hidden from the public menu rather than deleted.
- One deterministic command boundary (`tools/brothermode_cli.py`) is what
  the skills call, instead of each skill reaching into a different
  internal script. Its load-bearing contract, proven by a hermetic test,
  is that its status output is byte identical to the internal tool it
  dispatches to.
- Five worker profiles ship as native plugin agents (fast-worker, builder,
  researcher, navigator, reviewer) with their models, effort and read-only
  status declared. The two writer profiles deliberately do NOT declare
  worktree isolation: the file fence cannot resolve claims across
  worktrees, so shipping isolation as their default would have made the
  unfenced path the normal one. That limit is published.
- The installed product is now proven, not assumed: an eighteen step
  lifecycle canary installs the plugin the shipped way, upgrades from
  v2.1.1, proves a single hook chain, exercises the public actions,
  proves an incomplete delivery is REFUSED, and leaves a clean
  configuration behind. A separate canary proves the fence hook denies a
  cross-fence write in a real headless session.
- Current limitations live in `docs/limits/CURRENT.md` and contain only
  what is true today: recovery covers compaction boundaries and not a hard
  kill; the fence does not follow claims across worktrees and carries
  three further parser and path gaps found by an outside-family review;
  two database and input-output edges are named for later; and the hook
  performance target of 40 percent was missed, with the measured numbers
  published rather than a flattering estimate.
- The comparative benchmark ran its first installed-arm protocol: 26 real
  cells, both arms authenticated. The mechanism result is robust (the
  plain arm violated the file fence both attempts, the installed arm was
  blocked by the live hook both attempts). The judgment-graded cells do
  not survive two different grader families, so no quality claim is made
  from them. Internal evidence, one machine, one run.

Two reviews attacked this release before it was cut, one inside the Claude
family and one from OpenAI Codex; sixteen findings were reproduced and the
release-critical ones fixed, including a documented install pairing that
would have failed for a stranger, and a project store that was ignored
only through a private file on the author's machine.

## 2.1.1, released 2026-08-07 (annotated tag v2.1.1 at commit 748e1f7, which alone carried VERSION 2.1.1 per rule 1)

The tester release, cut overnight under the founder's recorded grant. What
it adds over v2.1.0, every line landed through a green-gated pull request
the same night:

- Installation became the boring part: two plain shell commands through
  Claude Code's own plugin manager, generated by the facts tool, proven end
  to end by scripts/release-smoke-install.sh (marketplace add, install,
  version match, every hook group, clean uninstall), and release law step
  4b makes that smoke non-skippable. The register's plugin row is certified
  on that named evidence; the desktop app surface limit is stated where a
  user meets it.
- The documentation truth system caught up with its own promise: three
  stale current-state claims fixed and pinned by regression fixtures, a
  seven phrase detector added, honest status banners on fourteen unguarded
  pages, and the fence capability narrowed everywhere to exactly what the
  hook enforces, with the no-sandbox line said plainly.
- The full-auto page opens with the warning an unattended run needs today,
  until the strict controller posture ships through the update channel.
- The five-user pilot protocol and its privacy-safe evidence template
  landed under docs/beta/.
- The comparative benchmark ran complete for the first time: twelve of
  twelve cells, both arms, every deterministic check passing in both, and
  the two blind judgment cells graded with the arm mapping sealed: both go
  to the BrotherMode arm, 15 to 14 each. One run, one grader per cell,
  labeled suggestive, INTERNAL EVIDENCE.
- The telemetry ledger lock from the parallel session, and its refusal-help
  fix, merged under a recorded one-time trailer waiver.

## 2.2.0, unreleased (development identity 2.2.0.dev1, opened 2026-08-06)

The 2.2 line: the tester release. Planned per the plan of record, second
resequencing of 2026-08-06: the progress surface, the status line and footer
links, the structural debt sweep, and a visual surface hardened by
adversarial review. The drift watchdog follows in the release after, by
founder decision, reaching installs through the update command.

## 2.1.0, released 2026-08-06 (annotated tag v2.1.0 at commit 77e7678)

The founder cut and pushed `v2.1.0` on 2026-08-06. The pinned install on the
onboarding pages now delivers this release. One recorded deviation: the
tagged commit carries the development identity `2.1.0.dev1` rather than
`2.1.0`; docs/RELEASE.md states the cause and why the tag stands. The body
below describes what landed on `main` since `v2.0.0`, as written before the
cut.

Gate after the last edit of the release run, on a quiet machine: `test_all: 2442
tests across 20 suites, 6 skipped, 358.7s wall. ALL GREEN`, exit 0, with the
checksum manifest regenerated afterwards and `verify-install` passing on the
restored tree.

WHAT IS NEW FOR YOU

A live project view. BrotherMode can now generate one self-contained HTML page
that shows where your project stands, drawn from the project's own records:
the pipeline, the gates, who holds which work, the open decision, and a
standing offer to take the work back into your own hands. It is a snapshot, not
a live dashboard, it makes no network call when it opens, and publishing it as
a private page is a separate step that needs a signed-in session; every one of
those limits is stated in `docs/KNOWN-LIMITS.md`. The onboarding flow was
rewritten around it: a first run now shows something to look at at each step
instead of a wall of text.

The one-writer fence now bites Codex writes. Under the Codex CLI every file
write arrives as a Bash `apply_patch` command, which the old fence never saw;
it now parses those and refuses a write to a file another session owns, the
same as it does for an Edit. This was hardened against an adversary who found
three ways it wrongly refused legitimate work, all fixed. Enforcement is proven
in process; a live wired Codex rehearsal is still owed and is marked so.

Three doors closed in the autonomy contract. A contract can no longer authorise
a write to BrotherMode's own machinery (its store, `.git`, or your `.claude`
settings files); the controller refuses a session that tries to drive a run it
does not own; and signing refuses work that declares no place it is allowed to
write. Each was reproduced by an adversarial refuter before and after the fix.
Driver ownership, like the fence, is coordination between cooperating sessions,
not a defence against a hostile local process, and `docs/KNOWN-LIMITS.md` now
says that plainly.

A comparative benchmark harness ships, frozen before any run. It is not yet run:
the numbers are the founder's to produce in an authenticated session, and the
protocol in `docs/BENCHMARK-COMPARATIVE.md` says every result it produces is
internal evidence, never a market claim.

## 2.0.0, 2026-08-04: the first public release, from one clean branch

The release-closure program is complete. Records were corrected against the
tree and proven append-only (Loop 2), the beginner install path was proven by
two independent probes given only the repository URL and then re-proven
against the corrected page (Loop 3), every open defect was triaged and the
blocks-a-user bucket is empty (Loop 5), the telemetry audit closed eleven
findings test-first and then survived an adversarial second review that
forced a generalization wave of seven more fixes (Loop 6 and the R-6
report), and the repository now carries exactly one branch, main, with every
contained branch deleted after its containment was proven twice.

Gate on the closure tree after its last edit: `test_all: 1573 tests across
15 suites, 6 skipped, 177.2s wall. ALL GREEN`, exit 0, at `91bc596`, which
`main` merges unchanged as `ef25c1f`. CI run `30906411852` on `91bc596`
concluded success with all eleven jobs green individually, read directly.

WHAT IS NEW FOR YOU

A schema skew is no longer called corruption. A store older or newer than
the running BrotherMode now gets `refused (schema-behind)` or
`refused (schema-ahead)` with plain instructions and the reassurance that
nothing was touched; STORE CORRUPT is reserved for stores that are actually
damaged.

Telemetry can no longer be taken down by one bad ledger line. A null, a bare
number, a string or an array on any line of any ledger is counted and
disclosed like other unusable lines instead of collapsing the scorecard, the
speed report and both repair commands into one swallowed-error line.
Unusable token fields, unreadable timestamps, future-dated rows and invalid
ratings are excluded from the numbers AND disclosed with counts, instead of
being silently coerced or silently dropped.

The install pages pin `v2.0.0`, and the documented beginner path is the one
proven by a probe forbidden from being cleverer than the page.

WHAT BROKE

The exit code for the two schema-skew refusals moved from 1 to 2, the code
every other refusal already uses. A script keying on exit 1 for store
problems should also treat exit 2 as a refusal. Genuine corruption still
exits 1 and still says CORRUPT.

WHAT IS STILL UNPROVEN, and this list is the point

The shell refusal is a NARROWING, not containment: a literal matcher, not a
shell parser; no operating-system sandbox; fail-open when tools/bm_store.py
cannot be imported. Packaging is a narrowing: tools/bm_project_facts.py and
everything in scripts/ stay unwired on purpose, because they read files that
do not ship in a wheel.

Real use exists and MEASUREMENT does not. The founder's daily use is
testimony, not a measurement. Register items X-01 to X-06 stay open and are
not closable by writing code: X-01 second runtime conformance, X-02 an
external user study, X-03 a benchmark corpus, X-04 measured dogfood, X-05
ecosystem thresholds, X-06 fault-injection reliability. Nothing in this
release changes that, and no scorecard should read as though it did.

## 2.0.0-rc.13, 2026-08-04: the closure register closes, and two of the fixes are narrowings that say so

Every one of the eleven machine-closable items in `docs/closure/CLOSURE_REGISTER.md`
now reads CLOSED. Gate on the released tree: `test_all: 1518 tests across 14
suites, 6 skipped, ALL GREEN`, exit 0, up from 1494 across 13. CI run
`30874562002` on `060a47a` concluded success with all eleven jobs green
individually.

WHAT IS NEW FOR YOU

`BM_FENCE_MODE=enforced` now refuses an obvious destructive shell command aimed
at BrotherMode's own store or fence directory, inside a BrotherMode project.
Before this, one `rm` through Bash silently disabled the whole enforcement
boundary and left no trace. Detection is also announced BEFORE it is recorded,
because a detection whose store has just been deleted used to be announced by
nothing at all.

A pip install now exposes twelve console scripts instead of nine, and actually
works: a packaged `bm-project` previously crashed at import time. `bm-sentinel`
and `bm-ledger` are wired for the first time.

The write-site review gate widened from three constructs in one directory to
eight constructs across `tools/`, `scripts/`, `mcp/` and `brotherme/`, so 193
mutation sites across 22 files are now under review rather than 68 across 14.

The whole suite runs on the declared Python 3.9 floor in CI, not just three
files of it. Release branches get push-triggered runs. `STATE.md` renders
byte-stable. Quarantine directories are owner-only.

WHAT BROKE

Nothing intentionally. The 12 to 13 store schema migration is additive only and
the runner backs the store up through SQLite's own backup API first, so an
existing install upgrades in place. The one real consequence is direction: once
a store is on schema 13, an OLDER install refuses to open it and says so,
deliberately, rather than quarantining it.

WHAT IS STILL UNPROVEN, and this list is the point

The shell refusal is a NARROWING, not containment. It is a literal matcher, not
a shell parser: a path assembled at runtime or held in a variable is not caught.
There is no operating-system sandbox and there will not be one here. And when
`tools/bm_store.py` cannot be imported at all, nothing is refused even under
enforced mode, which is a deliberate fail-open chosen because the alternative
refuses every shell command in every directory on the machine. Anyone who can
break that import can disable the refusal. `SECURITY.md` and
`docs/KNOWN-LIMITS.md` carry all three in these words.

Packaging is also a narrowing: `tools/bm_project_facts.py` and everything in
`scripts/` stay unwired on purpose, because they read files that do not ship in
a wheel.

Real use exists and MEASUREMENT does not. There are no counted projects, no
recorded failure or rework rate, no observed outside participant, and no
comparison against working without the tool. Register items X-01 to X-06 stay
open and are not closable by writing code: they need paid credits, outside
participants, or calendar time. Nothing in this release changes that, and no
scorecard should read as though it did.

## 2.0.0-rc.12.dev1, unreleased development identity, 2026-08-01: the release-closure program opens, rc.10 and rc.11 are retired

The tree carried `2.0.0-rc.11` (and, briefly the same day, `2.0.0-rc.10`)
while neither commit was ever tagged: rc.10's release-cut commit (`8aa6dd1`)
and rc.11's (`54cb898`) both landed on `main` and were pushed, but the
founder had not cut either tag before the next commit moved past it. An
external review of the resulting release-closure plan ratified amendment A1:
cutting a tag for either commit now would recreate the two-trees ambiguity
this project has already reproduced once (`v2.0.0-rc.1`), so both `rc.10` and
`rc.11` are marked SUPERSEDED, NEVER TAGGED and no tag will ever be cut for
them.

`VERSION` moves to `2.0.0-rc.12.dev1`, a development identity, so the tree
stops claiming an untagged release name. The public install target stays
pinned at `v2.0.0-rc.9`, the last tag actually cut and known to resolve
(`python3 tools/bm_project_facts.py --field install_target_tag`), independent
of what `VERSION` says. `tools/bm_project_facts.py` gains `is_development`
and `install_target_tag`; `install_command_pinned` is now built from
`install_target_tag`, never from `release_tag`, and `release_tag` itself is
`None` for a development identity rather than naming a tag that must never
exist. This opens the release-closure program's feature freeze: nothing
lands unless it closes a blocker named in
`docs/evidence/2026-08-01-source-BrotherME_Final_Release_Closure_Plan_Fable_Governed.md`.

## Unreleased on main, 2026-08-01 evening: the documentation earns its keep, after a 1 of 5

The founder rated the freshly shipped summary and book 1 of 5: weak, and
complicated where it should be practical. The diagnosis, confirmed by three
researchers on fetched sources and two red-team readers with measurements:
every gate checked truth and none checked usefulness, so both artifacts were
accurate walls of prose organized around the product instead of the reader
(summary: 4,942 words with the install command at 90 percent depth and
nothing ever shown; book: the seven guided commands first appearing at
chapter thirteen, 4,100 words before first success, two early exercises
broken as printed).

Both were rebuilt against a ratified spec
(docs/superpowers/specs/2026-08-01-docs-rebuild-after-1of5-design.md). The
summary is 771 body words: a falsifiable hero (the plugin that refuses to
say done), the two-line install ten lines from the top, one REAL captured
refusal transcript as the first evidence, one dated status box replacing
nine scattered disclaimers, the eight mechanisms once. The book inverted
from product tour to task-first: cheat sheet, then DO, SOLVE, UNDERSTAND,
LOOK UP, 217 words to first proven success, the broken exercises fixed and
re-run, every original chapter preserved with its anchors (chapter six
byte-identical for its replay suite). A usefulness gate now stands in front
of the founder's eyes: mechanical pins (a 1,600-word cap on the summary,
install-line depth, the cheat sheet and task index anchors) plus a fresh
hostile red reader who must score any documentation artifact 3 of 5 or
better before the founder is asked to look.

## 2.0.0-rc.11, 2026-08-01 (SUPERSEDED, NEVER TAGGED): the explainer earns its excitement, help grows a deep tour, and updating becomes one command

SECOND LATE ADDITION, same day: the founder accepted both artifacts and named
the missing organ: a big, visible statement of the philosophy and the laws.
Both now carry it: the explainer gains "The philosophy, and the laws" (the
founding belief that a rule in a prompt is not a control, then the ten laws,
each with the failure it kills), placed before the walkthrough; the book gains
"The philosophy, before the chapters" with the fuller ten-laws treatment, each
law naming the chapters that enforce and demonstrate it, plus the three-shape
honesty (machinery, handover, or discipline, and which is which). New test
pins hold both sections in place.

LATE ADDITION, same day (no rc.11 tag was ever cut, or ever will be, per
amendment A1; this addition landed while the tree still carried the rc.11
name): the founder judged the explainer the
summary and asked for the book behind it, and the July 30 adoption book at
docs/book/ turned out to have been disconnected from every session since it was
written. It gains part two, dummies to experts: chapter thirteen (the guided
layer and the deep tour), fourteen (the vault and Obsidian in depth), fifteen
(a composed multi-week product build with real fence transcripts run against a
throwaway store), and sixteen (analytics under scrutiny, every code block run
for real). Chapter two now teaches the two-line store install first, the stale
Windows-CI limit carries its dated correction, and an opus refuter that ran the
chapters' own code returned seven findings (one disqualifying: an output block
labeled unedited containing lines its code could not print), all fixed, one
cascading into SECURITY.md, whose no-network-calls sentence is now precise
about the single user-invoked version check. The explainer links the book; new
pins in the book's test class make this rot loud if it ever happens again.

Cut the same day as rc.10, which is SUPERSEDED WITHOUT EVER BEING TAGGED: its
release-cut commit (8aa6dd1) landed and was pushed, but no tag had been cut
when this work arrived, so none ever will be (docs/RELEASE.md records why a
late tag would recreate the rc.8 two-commits-one-name ambiguity).

The explainer (docs/brotherme-explained.html) was rebuilt after the founder
judged it too thin to convey what the tool actually does. It now anchors on a
complete worked example, a bakery website told as a conversation from idea to
delivered, jumpable by stage, with the label that keeps it honest: an
illustration with real command shapes, not a recording. Around it: practical
advice on using the guided loops, a good-at column and a not-good-at column
given equal design weight (the not-good-at list includes the unproven and the
deliberately refused), six use-case cards each carrying a mini tutorial, and
updating taught next to installing. A founder review the same day pushed it
further: a small-ask-versus-big-build section arguing where the machinery
actually pays for itself, a features section (seven mechanisms, each with
what it does, how to use it, when it matters) that the page and
docs/RELEASE.md now bind to a same-change update rule, and three personas
taken deep, from first-time founder to seasoned builder, all labeled as
designed-for stories rather than track records. Before any of that shipped,
a three-lens adversarial panel (command truth, overclaim, register) was sent
at the page and returned eighteen findings including three Critical
overclaims in the walkthrough itself (a site claimed live, an invented
delivery benchmark, a caption that retracted the illustration label); all
eighteen were applied. The stage stepper is plain anchor links
with CSS scrolling, chosen over a script after a script-stripped preview
proved buttons can die where anchors cannot.

A second founder review, minutes after the cut, asked the page to carry
ambition. The small-ask-versus-big-build section now runs two concrete
timelines side by side (a rename finished in minutes against a three-week,
three-language booking product) and names what wakes up at each size. The
features section grows to eight mechanisms: the founder-gate law (risky
moments come back to you, credentials never held) is promoted to a card of
its own. The personas go from three to seven, adding the data engineer, the
backend engineer, the infrastructure engineer and the engineering lead, each
with a deep, stack-shaped case, all still labeled designed-for stories. Same
page, same design system, same honesty labels; the drift tests that pin the
labels and the same-change update rule are untouched. The ratified spec
carries this as its amendment section.

/brotherme-help now offers a deep tour: the session runs the existing docs
engine (tools/bm_docs.py generate) and builds one HTML view of where the
project stands: state, process diagrams, data model, decisions, code map, and
a co-build section showing developers the house conventions. Verified honest
limit: a fresh project generates no technical pages at all, so every section
whose source folder is absent says "not yet recorded", never invented.

/brotherme-update is the seventh command: it reads the installed version,
checks the newest release, and gives the exact lines for the user's own
install path (the plugin lines verified against claude plugin --help on
2026-08-01; the clone lines per docs/RELEASE.md). Updating never touches a
user's projects or records, and now says so everywhere installing is taught.

Process note, recorded because the method is the product: this cut ran as one
wave of three fenced sonnet builders against a founder-ratified spec
(docs/superpowers/specs/2026-08-01-help-artifact-and-explainer-v2-design.md),
with the orchestrator writing the walkthrough copy, holding every test file,
re-running every done-check, and sending three adversarial reviewers at the
finished page before release. New drift suite pins the seven commands, the
verified update lines, both sides of the deep-tour flow, and the explainer's
honesty label.

## 2.0.0-rc.10, 2026-08-01 (SUPERSEDED, NEVER TAGGED): the beginner layer lands in main, and the repository becomes its own store

The layer that made this usable by a person with no technical background
existed for two days on a local branch nobody could install from. This cut
merges it: the six guided commands (/brotherme-start, -status, -next, -review,
-deliver, -help), the plain-language conductor skill, the Project Canvas and
Delivery Packet templates, the plugin and marketplace manifests, hooks.json,
and the canonical schema with its drift suite. The repository is now its own
Claude Code plugin marketplace: `/plugin marketplace add
khalilmaaouni/BrotherModeUp` then `/plugin install brotherme` is the whole
install, and README.md leads with it, honestly labeled (installed exactly
once, on the author's machine, from a local copy; never yet from GitHub).

The guided loop became written, tested law rather than a habit:
`references/delegation.md` now states that guidance and judgment sit on the
strongest capability profile while execution routes down to the cheapest
profile that passes the done-check, with an escalation rule after two failed
checks and a de-escalation rule for shapes that have succeeded twice. A new
suite class in `tools/test_bm.py` pins the law in its three homes (the
delegation reference, the beginner conductor, the profiles reference) so any
of them drifting turns CI red. This cut was itself run that way: three
read-only audits and the stale-doc corrections executed on a lower grade
against briefs and done-checks written and re-run by the orchestrating
session.

Honesty repairs in the same cut: the merge audit's one blocking defect (the
next-step command entered a flow the conductor skill never defined) is fixed
by defining the flow; two pages cited a release evidence file (rc.6) that a
later cut had renamed forward, and now say so instead of pointing at nothing;
`docs/REMAINING.md` carried three claims that stopped being true in July (CI
never ran, no tagged release, the learning system is law text rather than
code) and now corrects them in place, dated, with the old text preserved;
`docs/NOT-FINALIZED.md` item 11 no longer calls the public install "not
started" when an installer, an uninstaller, and their test suite shipped.
And the release runbook's own verify step caught a real drift while cutting
this release: `Documentation/` was gitignored as machine-generated on
2026-07-30, but the exclusion lists in `scripts/verify-install.sh` and
`scripts/checksums.sh` (documented as kept in sync with it) never learned
that, so a user who ran `bm_docs.py generate` in an installed copy would be
told their install could not be trusted. Both lists now also exclude
`Documentation/` and `.claude/`; `build/` stays deliberately unexcluded, since
a forgotten build directory failing the verify is the tamper check working.
What did NOT change: no real multi-day dogfood window has run, the fence hook
still does not see Bash writes, and no external user has ever installed this.
Those stay open in docs/NOT-FINALIZED.md, which is why this is rc.10 and not
2.0.0.

## 2.0.0-rc.9, 2026-07-31 (SUPERSEDED): one commit is the release, because the release-truth test refused two

Same content as rc.8 plus the linearity fix, cut again so that the tag and VERSION
name the SAME commit.

rc.8 was tagged at 284d786, two commits past the commit that set VERSION to
2.0.0-rc.8 (d9410c7), on the reasoning that the tag should carry the flake fix rather
than predate it. That reasoning was right about the fix and wrong about the tag: it
left TWO commits both claiming to be 2.0.0-rc.8, which is the ambiguity a version
number exists to prevent.

`test_the_tag_points_at_the_intended_release_commit`, added in Loop 1 that morning,
refused it in exactly those words: "the tag and VERSION disagree about which commit
is the release". The test was right. The published rc.8 tag stays where it is,
because deleting a published tag is the failure this project refuses on principle;
it is SUPERSEDED, and rc.9 is the cut where the version bump and the fix travel
together.

## 2.0.0-rc.8, 2026-07-31 (SUPERSEDED): the loop estimate ledger lands, and the gate finds a privacy hole in it

`tools/bm_ledger.py` and its suite arrive from the live installed skill, where another
session built them to a founder directive: declare a loop's expected minutes and
tokens BEFORE the work, close with measured actuals and a why sentence, graded on a
named 25 percent band. Three laws enforced rather than remembered: a close against an
undeclared loop is refused because an estimate written afterwards is a prediction of
the past; history is append-only, so changing your mind appends rather than edits; and
actual tokens are optional, absent reading NO-DATA rather than a number somebody
invented.

It was not copied in unread, and that mattered. The repository's own pre-write gate
refused the file: "bm_ledger.py writes files but is not in the reviewed inventory.
Review whether every text it writes passes through redaction." THE REVIEW FOUND IT DID
NOT. The tool stores a founder-written why sentence and a loop name, which is exactly
the shape a secret gets pasted into ("slow because the key was wrong"), and it wrote
both to disk raw while all ten sibling tools route such text through `redact_text`.

A `_scrub_row` step now runs before every append: the free-text fields pass through
the store's redactor, while numbers, verdicts, timestamps and the run identifier the
tool generates itself are left alone because they are not founder prose. `bm_store` is
imported lazily by path so the file stays a standalone script, and a failed import
REFUSES the write rather than falling back to writing unscrubbed. Proved on the real
binary: a declare whose loop name carried an `sk-` shaped key left zero occurrences of
it in the ledger file. Three tests, including a calibration that removes the scrub and
confirms the secret does land, so the green can go red.

Two further gates caught the landing rather than anyone remembering: the packaging
manifest (a pipx install would have shipped without the tool) and the CI inventory
check (a suite in the gate that no workflow step executed). Both closed. The gate is
eight suites and 1215 tests now.

## 2.0.0-rc.7, 2026-07-31: the first fully green CI, and the defect it took to get there

Cut so that the tagged bytes and the green continuous-integration run are the same
bytes. Run `30564943060` for commit `f751f9f` concluded SUCCESS across all nine jobs:
the serial gate, both suite legs, and all six store legs (three platforms, both
supported Python versions). This project had never before observed a fully green run.

Getting there needed a real defect fixed, and finding it needed a tool built for the
purpose. The Windows store legs had been failing in every run checked, and the reason
was unreadable: public annotations carried only "exit code 1", and the run log that
names the failing test needs an authenticated gh, which is founder-only here. So
.github/run_with_annotations.py now re-emits unittest FAIL and ERROR headers as
GitHub annotations, which a public repository serves to anyone with no login at all.
On its first live run it named both failures.

They were pointing at shipped instruction text, not at themselves. invocation()
quoted every user-facing command with shlex.quote, which is POSIX-only, so a Windows
path came back wrapped in single quotes and the remedy read

    python3 'C:\Users\...\bm_store.py'

which neither cmd.exe nor PowerShell will run. Every instruction in bm_docs,
bm_packs and bm_learn flows through that function. Fixed by quoting for the shell the
reader actually holds, with the property asserted on every platform rather than only
where it bites.

rc.6 is SUPERSEDED, not withdrawn: it is sound, its manifest describes its tree, and
a clean install verifies. It was simply tagged two commits before the Windows fix, so
its tagged tree still has red CI and it cannot honestly point at the green run.

## 2.0.0-rc.6, 2026-07-31 (SUPERSEDED): the first-rank loops, re-cut after rc.5 was withdrawn

Identical work to `rc.5` plus one corrected file. `v2.0.0-rc.5` is WITHDRAWN: its
`CHECKSUMS.sha256` omitted eight shipped files, including `tools/bm_docs.py`,
`tools/bm_docs_export.py` and `tools/bm_packs.py`, so `verify-install.sh` against
that tag reports three real tools as unknown. Operator error, stated plainly: the
release-cut session ran `scripts/checksums.sh` with its output redirected to
`/dev/null` rather than passing the documented output path, so the manifest was
never rewritten while the commit message claimed it had been. The tag object stays
on the remote; withdrawal is a statement, not a deletion, because deleting a
published tag is the failure this project refuses on principle.

The defect was caught by `test_the_checksum_manifest_matches_the_tagged_tree`, added
hours earlier in Loop 1, which skipped with a stated reason while no tag existed and
turned mandatory the moment one was pushed. It failed on its first live run against a
real tag and named all eight files.

## 2.0.0-rc.5, 2026-07-31 (WITHDRAWN): the first-rank loops, cut at the close of loops 0 through 5

One session, six commits, every one behind its own full-gate run. The test count
moved 1057 to 1194 across seven suites, and the store schema moved 8 to 11 in three
additive migrations with a durable pre-migration backup taken before the first. The
dated entries below this heading are that session's log, newest first, each with its
gate line. Loops 6 through 9 of the execution plan remain open: CI execution
evidence needs this cut pushed, and the dogfood window, outside-family audit,
external beta and public benchmark cannot be produced by the machine that built the
code.

Unreleased, 2026-07-30: the adoption book, twelve chapters with every command executed against a throwaway project, at `docs/book/brothermode-for-dummies.html` plus a 56 page PDF export (phase D of the documentation and gate-packs spec).

Unreleased, 2026-07-31: first-rank execution plan, Loop 5. Gate: `test_all: 1194 tests across 7 suites, 1 skipped, 110.7s wall. ALL GREEN`, up from 1181. Schema unchanged at 11.

- Default `apply` no longer stores the founder's verbatim query. New application
  rows keep a bounded, stopword-free term set (the primitive retrieval runs already
  used) plus the non-reversible task fingerprint; `--store-excerpt` is the explicit
  opt-in for a readable excerpt. Historical rows are untouched: what changed is what
  NEW rows store, never a rewrite of the founder's existing data. Verified at the
  byte level: a canaried task prompt does not appear in the sqlite file at all.

- Path masking handles quoted paths, escaped spaces, Windows drive paths, UNC paths,
  Unicode segments, several paths in one field, and paths glued to a word at the six
  known roots. Two honest remainders are in KNOWN-LIMITS: bare unquoted spaces still
  truncate the mask, and a glued single-letter drive stays unmasked because masking
  it provably swallows https URLs.

- Hand-typed session labels no longer export. The shape gate is an allowlist of the
  four real generated id shapes now, so a codename a human typed is withheld while
  generated ids keep their joins. The plan's literal design, a schema-level split of
  internal uuid from optional label, was NOT built and is recorded as open.

- Two surfaces were printing raw rows outside the withholding policy and now route
  through it: `applications` and `disposition` (text and `--json`). And `verify()`
  itself scrubbed nothing on its problem lines; a corrupted store could echo record
  names and paths raw. Its problems now pass through the same redact and mask pair,
  which broke the one test that deliberately staged the old leak as its calibration
  precondition; that test now pins the source fix AND still calibrates the MCP
  funnel by injecting a leak through the server's own private bm_store module.

- Nine-field canary suite: objective, task prompt, session label, path, correction
  text, approval answer, rule reason, disposition reason, outcome reference, seeded
  through the real CLI and asserted absent from dump, applications, disposition and
  the MCP status surface, including the corrupted-store error path. One disclosed
  survivor, by design pending a founder ruling: an approved rule's own because_text
  in `applications` output, which is founder-promoted rule content rather than
  incidental capture.

Unreleased, 2026-07-30: first-rank execution plan, Loop 4. Gate: `test_all: 1181 tests across 7 suites, 1 skipped, 112.2s wall. ALL GREEN`, up from 1149. Store schema 10 to 11.

- Session plus query text cannot tell two tasks apart, so `apply` no longer accepts
  session identity alone. It requires exactly one of `--record <existing-uuid>`,
  `--new-record <name>` (creates a provisional work record atomically with the
  application), or the active record already in the environment, and the refusal
  names all three ways forward. `lookup` is unchanged: it writes nothing and needs
  no identity. Proved on the real CLI: two applies with IDENTICAL wording in ONE
  session produced two distinct work records and two distinct retrieval runs.

- Provisional records: durable UUID, visible in project status, promotable,
  cancellable, linked applications preserved either way, and two identically named
  provisionals are distinct records by construction, so tasks can never silently
  merge. The new `promote` and `cancel` commands were classified by Loop 2's
  enumeration test the day they were born: both are allowlisted with stated reasons
  (they move a work record's lifecycle and touch no learning_rules row), and the
  reasons were verified against the code, not assumed.

- Retrieval runs now store complete membership, not just a count.
  `learning_retrieval_membership` records the identity AND version of every eligible
  rule per run, closing the case where one eligible rule vanishes and another appears
  while the total stays identical. The swap case is a permanent test, and
  `eligible_count` keeps its old meaning beside the new table.

- Link protection: relinking an application to a different record, applying against
  a nonexistent record, applying against a closed record, and cross-session reuse of
  an unpromoted provisional all refuse, each with its own reason.

- One constitutional collision surfaced by the full gate and settled by the founder,
  not by an agent: SKILL.md promised "the rules are printed on every failing path",
  and the new identity refusal prints none. Founder ruling: identity wins. The
  SKILL.md promise is now scoped to paths that REACHED retrieval, and exit 2 is
  documented as a usage refusal that happens before retrieval and says nothing about
  whether rules matched. Five tests encoding the old contract were rewritten to the
  new one, each keeping its old premise in its docstring, and the disclosure branch
  for ambiguous bare applies is left in place but believed unreachable from `apply`,
  unconfirmed for the deprecated `relevant` alias.

- `record_uuid` stays NULLABLE in the DDL, deliberately: the founder's live store
  holds historical rows with NULL there, and a test now asserts the column never
  becomes NOT NULL, so no future migration can rewrite his history to satisfy a
  constraint. Enforcement lives at the command layer where new work arrives.

Unreleased, 2026-07-30: first-rank execution plan, Loop 3. Gate: `test_all: 1149 tests across 7 suites, 1 skipped, 110.8s wall. ALL GREEN`, up from 1132. Store schema 9 to 10.

- Mandatory gates were safe in one direction and unsafe in the other. A limit could
  never hide a gate, which is right, but the cost was that every applicable gate
  printed in FULL on every query: twenty approved gates produced twenty full
  trigger, action and why blocks at 5722 characters, most at relevance 0.0. A model
  handed twenty irrelevant mandatory rules ignores all twenty, so unbounded
  visibility is its own failure mode.

  Every applicable gate now appears in a compact manifest, one bounded line each,
  sorted by rule identity so it is query-independent and byte-identical across a
  relevant and an irrelevant query, with a hash over the whole gate set so adding,
  removing or editing a gate moves it. Full text is expanded only when the trigger
  matches, the scope is narrow and matched, the query reaches the gate's own action
  text, or the caller names the gate by ID. Ambient expansions are capped at
  `GATE_EXPANSION_CAP = 5`; an explicit `--expand <id>` is never capped, because
  withholding a gate the caller named by name would be the same hiding this project
  already refuses to do for `limit`. Twenty gates: 5722 characters before, 1849
  after.

  Nothing is dropped from the result set. `presentation` decides what a caller SHOWS
  for a row, never what retrieval RETURNS, and `gates_returned` and `gates_total`
  keep the meanings they already had.

  Short IDs are `"G" + rule_uuid[:8]`. The uuid is minted once at approval and never
  reassigned, even across supersede, deprecate or forget, so an ID a human wrote
  down does not renumber when the corpus changes. A counter would have.

  Honest cost, measured and not buried: at ONE gate the manifest's fixed header and
  footer make the output LARGER than before, 524 characters against 454. The saving
  begins past roughly two gates and then grows at about 70 characters per gate
  instead of 270.

- Application records can now distinguish presentation from disposition.
  `disposition` already carried followed, ignored, not_relevant and unknown; the new
  `presentation` and `action_reached` columns add whether a gate was seen compact or
  in full, and whether the query's own wording reached the rule's do-clause. Additive,
  defaulting to 'unknown' for pre-migration rows, with no backfill, because a value
  computed today would falsely claim to describe a retrieval that happened before the
  column existed.

Unreleased, 2026-07-30: first-rank execution plan, Loop 2. Gate: `test_all: 1132 tests across 7 suites, 1 skipped, 111.9s wall. ALL GREEN`, up from 1082. Store schema 8 to 9.

- Five commands could alter the live rule set with no human answer anywhere, and
  only one of them was gated. `supersede`, `deprecate` and `forget` deactivated or
  replaced a rule for free; `resolve-conflict` could stand down a GATE rule, the
  strongest kind, for free; and resolving a critical alert unblocked the approval it
  was blocking, for free. All five now require a one-time receipt minted from a real
  answer, through ONE shared lane (`learning_state_change_receipts`,
  `mint_state_change_receipt`, `_require_state_change_receipt`), not five bespoke
  checks. Per-call-site implementation of a cross-cutting concern is this project's
  named root cause behind four earlier bugs, so the shape was chosen deliberately.

  Receipts are kind-discriminated and cannot cross-spend: a deprecate receipt cannot
  spend as a forget, a state-change receipt cannot spend as an approval, and neither
  direction works. Verified through the real CLI against a throwaway store, not only
  in tests: `forget --yes` with no receipt refuses `no-state-change-receipt` at exit
  2, with a forged 48-character token refuses `bad-state-change-receipt` at exit 2,
  and the gate rule survives every attempt. Note that `forget` reaches its `--yes`
  confirmation guard BEFORE the receipt check, so a probe that stops at the first
  refusal never tests the receipt at all.

- A mechanical stop, so this cannot silently regress. An enumeration test reads
  `bm_learn.py`'s own `COMMANDS` dict (38 entries, discovered, never hand-typed) and
  requires every command to be either receipt-gated or on an explicit allowlist with
  a stated reason. A sixth rule-altering command added later fails the suite until it
  is gated or reasoned about. Calibrated by adding a fake command to the real dict
  and confirming the test names it.

- The forged-token approval test mutated the last character to "0", which is not a
  mutation one time in sixteen. It now flips to a guaranteed different value, and ran
  100 consecutive times with no flake.

- Approval wording now matches the mechanism at six sites. The receipt proves an
  answer was supplied for this exact proposed rule and has not already been used. It
  does NOT prove which human supplied it, and no shipped page says otherwise now.

- `capture` with no arguments stored an EMPTY candidate while the same CLI correctly
  refused unknown flags. It prints usage and exits non-zero. Fixed at the CLI layer
  deliberately, not in `capture_learning_candidate`, which has roughly thirty
  legitimate call shapes across the suites.

Unreleased, 2026-07-30: first-rank execution plan, Loop 0 and Loop 1. Gate after both: `test_all: 1082 tests across 7 suites, 1 skipped, 107.8s wall. ALL GREEN`, up from 1057.

- Loop 0, the flag-as-a-name defect swept everywhere it lived rather than only where
  it was reported. Register item 16 named `claim --help`; the same read of `argv[0]`
  before flag rejection sat in four code sites covering seven commands. One
  `_require_positional` helper closes all of them. `fence-lint` now recognises the
  fence format the store actually renders, instead of only the hand-written V1 lines
  containing the word `agent`, so it no longer reports "no live fences found" against
  a STATE.md holding live records. `--handover "<heading>"`, previously the whole
  person-to-person handover mechanism and absent from every usage line, is documented
  in the four commands that accept it. STATE.md backups prune to the five most recent
  at the moment each one is written, logged per deletion, and a test proves the
  pruner cannot match a file outside the exact pattern it produces itself.

- Loop 1, the public install stops being a moving branch. README, QUICKSTART and
  SETUP told a new reader to clone `main`, which then auto-installs five hooks that
  run in all their later sessions; a moving branch is not an acceptable default for
  auto-run code. The pinned command is now GENERATED from one release fact
  (`install_command_pinned` in `tools/bm_project_facts.py`) rather than typed, with
  the development clone kept separate and labelled, and a drift test asserting the
  pages equal the generated fact exactly. `docs/RELEASE.md` claimed the rc.4 tag had
  not been cut and "will not resolve" while the tag was cut, pushed, annotated and
  pointing at `6cc94bc`; that contradiction is gone and a test now refuses it. The
  doc-status guard matched the historical marker anywhere in a page header, so a
  sentence merely describing the convention failed the suite; it now anchors at line
  start. `pyproject.toml`'s `2.0.0rc4` against `VERSION`'s `2.0.0-rc.4` was checked
  and is NOT drift: it is PEP 440's required spelling, and a normalizing test now
  pins that they agree.

## 2.0.0-rc.4, 2026-07-29: four parallel lanes merged, fifteen loops landed

The release cut for the work done on 2026-07-29. Everything below already ran
its own gate on its own branch; this entry is the merge, the version bump, and
the honest list of what is still open.

WHAT LANDED TONIGHT, with the commit that carries it. Each loop closed with a
full-gate run and a refutation round, and several were reopened by their own
refuters and fixed in a second commit before landing.

- Loop P2, one-command installer, safe uninstall, fence wired by default:
  `06b9c03`, fix round `7afa110`.
- Loop 3, approval needs a receipt from a real answer: `1fdf2c3`, fix round
  `05441e7`.
- Loop P4, a result limit could hide an applicable gate rule: `c78e5ea`, fix
  round `bc25e06`.
- Loop P5, substantial work no longer depends on remembering a flag: `fe0497b`,
  fix round `03c0dab`.
- Loop P6, persist complete retrieval-run context: `d3c24a3`, fix round
  `358e00d`.
- Loop P7, an optional FTS5 fast path with the lexical path intact behind it:
  `085cb0b`, fix round `e2112f0`.
- Loop P8, doctor proves the fence refuses, and the Bash boundary gets a
  policy: `ee50996`, fix round `7fd0ddb`.
- Loop 9, make the local and CI gates the same gate: `3aac9df`, fix round
  `33f96d5`.
- Loop P10, generated project facts and documentation that stops pinning stale
  ones: `9bb5daa`, fix round `68f2515`.
- Loop 11, one withholding policy for every export: `b3203b2`, fix round
  `7175250`.
- Loop P12, a parked thread could lose its handover: `4c37cdc`, fix round
  `1098af8`.
- Loop P16, cross-runtime adapters generated from one verified registry:
  `f1ad260`, fix round `07fdb8f`.
- Loop P17, BrotherMode installs from a package manager: `078e653`, fix round
  `4332a90`.
- Loop P18, the thirteen ratified benchmark scenarios run for real: `68eb4d8`,
  fix round `e8836a9`.
- Loop P19, a beta kit an outside founder can follow: `4084b4a`, fix round
  `eec5327`.

The four lane merges are `1c64e39` (install), `17c5183` (CI), `d2c7bec`
(privacy) and `823640a` (ecosystem). Their commit messages carry every conflict
resolution, including the one design pair that could not both be true.

OPEN BY NATURE, not by neglect. Three things cannot be closed by writing code,
and this release does not pretend otherwise.

- Loop 13, founder dogfooding, has not happened. Nothing in this repository has
  been through a real working day of someone's actual work. Every benchmark
  scenario runs against a store built seconds earlier.
- Loop 14, an independent adversarial audit by a different model, has not been
  run. Every finding here was found by the same family of model that wrote the
  code.
- The beta kit shipped (Loop P19) and no outside founder has run it. The kit is
  unexercised prose until the first real report comes back.

STILL A RELEASE CANDIDATE. `docs/KNOWN-LIMITS.md` and `docs/NOT-FINALIZED.md`
are the registers to believe over this entry, and `docs/RELEASE.md` states what
promoting this to a plain `2.0.0` would require. No tag was created by this
commit.

## 2026-07-29: installation is now one command (Loop P2)

- `scripts/install.py` copies the checkout, merges the hook configuration into
  `~/.claude/settings.json` without touching any hook it did not write,
  validates the result, and runs the fence hook end to end before reporting
  success. `scripts/uninstall.py` removes only BrotherMode-owned entries and
  never deletes a vault. FIVE hooks are installed, not four: `PreToolUse` (the
  fence, `docs/HOOKS.md`) was documented and was in no install instruction, so
  the one-writer-per-file promise was OFF by default on every installation that
  followed the docs. `--dry-run`, `--upgrade`, `--no-hooks`, `--target`,
  `--settings`. Windows is refused with the reason (two of the five hook
  commands are POSIX shell) rather than half-installed. New suite
  `tools/test_install.py` is in `tools/test_all.py`'s SUITES.
- `docs/QUICKSTART.md` and `docs/SETUP.md` lead with the installer; the
  hand-edited JSON block stays as a documented fallback.
- Hook ownership is decided by the command string naming this installation's
  path. The fix round replaced a substring match with a path-token match, kept
  a symlinked settings file symlinked, and made the uninstaller honest about
  what it left behind.

## 2026-07-29: the fence is proven to REFUSE, and the Bash boundary gets a policy (Loop P8)

- `scripts/doctor.py` proves the fence hook REFUSES, not merely that it runs.
  It reads the wired `PreToolUse` command out of `settings.json` and runs a
  blocked-write simulation against that exact command string in a throwaway
  project: one file claimed by one session, an Edit requested by another (deny
  expected), then the same edit by the owner (allow expected). It detects a
  fence that is absent, points at a missing file, has a matcher leaving write
  tools ungated, refuses nothing, or refuses everything. It prints what a green
  result does not prove.
- `scripts/bm_shell.py` is a declared-path wrapper for an unavoidable generated
  shell write. Every declared path is judged by `bm_fence_hook.decide()` itself,
  in strict mode, and the wrapper refuses on a fail-open, the deliberate inverse
  of the hook's rule. `--declare-none` is screened against a short, explicitly
  incomplete list of obvious write forms.
- `scripts/install.py` now points at doctor after its smoke test, because the
  smoke test proves the hook runs and not that it refuses. `docs/HOOKS.md` gains
  an Installation section and a Bash-boundary section.

## 2026-07-29: local and CI gates made equivalent (Loop 9)

`tools/test_bm_fence_hook.py` now runs in CI on Linux and macOS. A new serial
`gate` job runs `tools/test_all.py` itself, the same command a loop close runs
locally, and uploads every suite's full output as an artifact when it fails.
`tools/test_all.py` gained a CI inventory check (a suite cannot be in the local
gate and absent from CI, or run by CI and unknown to the gate), an interprocess
lock keyed to the checkout so two gate runs cannot corrupt each other, per-suite
timeouts with hung-suite diagnostics, and `--artifacts DIR`. The fix round made
the inventory check measure real execution rather than a mention, read job-level
`if:` as well as step-level, and stopped a leftover lock file from silently
disabling mutual exclusion.

## 2026-07-29: documentation stopped pinning facts that move (Loop P10)

`tools/bm_project_facts.py` prints version, release tag, schema version, hook
events, suite files, retrieval modes and the Python floor read out of the tree.
It deliberately prints NO test count: counts change with every test that lands,
so active pages now say to run `python3 tools/test_all.py` and expect ALL GREEN,
and exact counts stay in dated evidence. `tools/test_bm_docs.py` compares the
pages against those facts. Fixed with it: QUICKSTART told installers to expect a
test count that no longer matched and called the mismatch a broken install;
README and RELEASE counted four hooks while five ship, and neither hand-wiring
block wired the `PreToolUse` fence; README declared the adopt defect open two
days after it closed; RELEASE pinned `rc.2`; the README safety grep promised no
output while matching two documentation URLs; the README inventory omitted
`bm_fence_hook.py`, the newer suites, `scripts/` and `docs/HOOKS.md`; the
uninstall steps deleted the skill folder before running the unwire script that
lives in it. Dated documents now carry a HISTORICAL banner with superseded-by
pointers.

## 2026-07-29: ONE withholding policy now governs every export (Loop 11)

- `dump`, its JSON output, and the MCP server's responses WITHHOLD founder prose
  instead of scrubbing it (objectives, evidence, digest bodies and next intents,
  transition notes, decision text, directive text, claimed paths, captured
  corrections), because the redactor only removes secret SHAPES and ordinary
  sentences carry none. Structural data (identifiers, states, versions, hashes,
  counts, timestamps) still passes, and the record name and tier stay readable,
  scrubbed and absolute-path masked, so an export is still usable. Absolute
  POSIX, Windows drive and UNC paths are masked, including in the MCP server's
  `verify` problem lines. `dump --raw` still returns everything and now warns on
  stderr that it includes prose an ordinary dump withholds.
- `bm_threads send` regenerates `inbox.md` from authoritative store rows instead
  of `dump()`. A local view the founder reads is not an export.
- MCP copy-first is now covered by committed tests: real store bytes and
  sidecars byte-identical across four read-only tools, no snapshot directory
  surviving a call, and a cleanup failure reported by name rather than swallowed.
- Secret-scan hardening, and no Windows ACL support is claimed anywhere: every
  published owner-only sentence is scoped to POSIX or marked best-effort,
  enforced by a test.
- The fix round replaced the path-masking body class with a denylist, because
  the old ASCII allowlist stopped at the first non-ASCII byte and left the
  SENSITIVE TAIL of a path standing, and it shape-gated session ids, which are
  caller-supplied free text.

## 2026-07-29: cross-runtime adapters (Loop P16)

`tools/bm_runtimes.py` generates the instruction file that wires BrotherMode
into another AI coding runtime: OpenAI Codex CLI, GitHub Copilot, Google
Antigravity, Qwen Code, iFlow CLI, and a generic AGENTS.md. `list`, `emit` and
`check` are the three commands. Every destination path was read off the vendor's
own documentation page on 2026-07-29, and the URL plus the date travels into
each generated file's header and into `docs/RUNTIMES.md`, so a stale claim is
visible rather than inherited. Emission is non-destructive. The adapters name
subcommands only and tell the agent to run `--help`, so no flag name can be
invented or go stale. Gated by `tools/test_bm_runtimes.py`.

## 2026-07-29: packaging (Loop P17)

- `pyproject.toml` (PEP 621, setuptools backend, zero dependencies, Python 3.9
  floor) makes BrotherMode installable with pipx, uv, or pip. It ships the
  `bm_*` modules and puts six commands on PATH. Nothing is published; publishing
  is a founder decision and the runbook is `docs/PACKAGING.md`.
- `bm_learn.py`, `bm_runtimes.py` and `bm_score.py` gained a `cli()` entry
  point. The first two have `main(argv)` with a required argument, which an
  entry point calls with none, so a console script pointed straight at `main`
  would have installed a command that raised TypeError on first use.
  `bm_score`'s never-block guard lived only in its `__main__` block, which a
  console script bypasses, taking the `--strict` honesty rule with it.
- Tests hold the packaging manifest to the repository: every non-test module in
  `tools/` is in `py-modules` and nothing else is, every console script target
  imports and is callable with no arguments, the published version matches
  `VERSION`, and the dependency list is empty.
- `scripts/bootstrap.sh` finds a Python 3.9 or later and hands off to
  `scripts/install.py`.
- The fix round made every instruction string resolve to a command that exists
  in the reader's layout, because there is no `tools/` directory in a packaged
  install and the first refusal such a user saw named a file that does not
  exist.

## 2026-07-29: a beta kit an outside founder can follow (Loop P19)

`docs/beta/BETA-KIT.md` is the handout for an outside founder who has already
agreed to a beta: what they are being asked to do, a first hour split into
install, one real task, and one captured correction, five honest expectations
for the first two weeks, a weekly report template whose five headings mirror the
dogfood measurement categories, and a privacy note with two greps they can run
themselves. Every command in it was executed against a throwaway store before
publication. Recruiting is out of scope and the page says so.

## 2026-07-29 (no version bump): the handover dedupe was deleting handovers

Fix round on Loop P12. The entry below promised that a park and its handover
both land or neither does, and closed the audit item that asked for it. That
promise was false in one specific, reachable case, and this round found it by
attacking the boundary the loop's own Fable prompt named. Reproduced on a real
store before a line was changed.

- THE DEDUPE WAS SCOPED TO THE LIFECYCLE FOR ALL TIME, NOT TO A RETRY. The
  uniqueness key was the lifecycle plus the handover's content fingerprint,
  across every row that lifecycle had ever produced, delivered or not. The
  fingerprint covers the objective, the files, the owner, the tier, the check,
  the evidence, the latest digest and the decisions. It does not cover the
  state, the version, the transition, the heading or the sessions. So a record
  parked, acknowledged, resumed and parked again with nothing new checkpointed
  produced the identical fingerprint, the second insert lost, and the loss was
  swallowed. Result: `threads off` printed success, `handovers` said none,
  STATE.md had no handover section, and `verify` called the store healthy. The
  handover did not exist and nothing could recover it.
- ADOPTION HAD IT WORSE. `threads adopt` writes no digest of its own before it
  transitions, so its payload is almost always unchanged since the park that
  preceded it. Its handover lost the same way, and STATE.md kept rendering the
  park's heading, with a body reading "parked", for a record the store had
  already moved to "adopted". The CLI said the adoption's handover was in the
  store in the same breath.
- THE KEY IS NOW THE TEXT A FOUNDER CAN STILL SEE. Schema 6 replaces that index
  with the lifecycle, the fingerprint and the heading, restricted to
  UNDELIVERED rows. An acknowledged handover has been read and dismissed and no
  longer renders, so it can no longer suppress anything. A park heading you
  typed and the adoption heading that follows it are two different handovers,
  because they are two different things to tell the next session. A genuine
  retry, same text while the first copy is still on your screen, still stores
  and renders exactly once.
- AND THE SWALLOW NOW HAS TO PROVE ITSELF. When an insert loses on that index,
  the store re-reads for the undelivered copy that justifies staying quiet. If
  it is not there, the transition is refused and rolled back rather than moving
  the record: "parked with no handover" is now unreachable twice over, once by
  the key and once by the check.
- The migration drops an index and creates another. No row is read or rewritten,
  and the new key is strictly weaker than the old one, so nothing that was legal
  a moment ago can fail it. An existing store upgrades on the first command that
  writes; a read-only command refuses with the same clear message it has always
  used, and touches nothing.

## 2026-07-29 (no version bump): a parked thread could lose its handover, and now cannot

Loop P12. Until this change, a handover was TEXT APPENDED to the project's
STATE.md, while the same file was independently read, rebuilt and replaced by
the code that generates it. Those two writers were never atomic with respect to
each other, and the file said so in a comment. Reproduced on a real store
before anything was changed: park a thread, deliver its handover, let the
generated view land on the snapshot it had already taken, and the record reads
"parked" while the handover text is gone from disk, with no second copy
anywhere. That is a whole session's context lost at exit 0.

- A HANDOVER IS NOW A ROW, NOT AN APPEND. The store has a `handovers` table
  (schema 5), and the row is written inside the SAME sqlite transaction as the
  lifecycle transition that produced it. Park-with-handover and
  adopt-with-handover are one atomic act: both land or neither does. A refused
  adoption writes nothing at all, and a handover that cannot be built (the
  redactor is unavailable) rolls the park back rather than parking a thread
  whose context is gone.
- STATE.md IS NOW ONLY A VIEW. Undelivered handovers are rendered inside the
  generated markers, so nothing appends to that file anywhere in the project
  any more. Delete STATE.md after a park and the next command puts the handover
  back: a crash between the commit and the render costs nothing, because
  rendering is not delivering. The architectural guard that used to require
  exactly one appender now requires zero.
- RETRY CANNOT DUPLICATE. A uniqueness constraint on the lifecycle plus the
  handover's own content fingerprint is the dedupe. The old version simulated
  that by scanning a text file for an HTML comment marker, which is also why a
  concurrent rewrite could make the same handover land twice.
- THE LOCK IS DELETED. The directory lock that serialized the append, and the
  four functions around it, are gone rather than shimmed. A lock protecting a
  writer that no longer exists is one more thing to keep correct.
- Handovers that older versions already appended into a STATE.md stay exactly
  where they are, as human prose. Nothing parses them back in: those blocks
  have no end marker reliable enough to round-trip, and a parser guessing would
  either truncate a founder's handover or swallow the prose around it. Only new
  handovers use the database.
- Acknowledge a handover with `bm_store.py handover-ack --handover <uuid>`, and
  list what is outstanding with `bm_store.py handovers`. Acknowledging is
  idempotent and never deletes the row.
- One thing found while writing this: the new fingerprint column was originally
  named so that the dump redactor's digest rule did not cover it, and it dumped
  in cleartext. Caught by this loop's own dump test and renamed to match the
  convention that rule reads.

## 2026-07-29 (no version bump): the search index can no longer destroy the store, nor answer from deleted text

Fix round on Loop P7. Three findings, each one reproduced on a real store
before a line was changed, and each one a case of the previous entry's promises
being true about the code and false about the behaviour.

- AN OPTIONAL ACCELERATOR TOOK THE WHOLE DATABASE. Index statements ran through
  the same routing point as every other query, and that point treats "no such
  table" as structural damage: it MOVES store.sqlite3 and its sidecars into a
  quarantine directory and raises. So an index table dropped mid-session did not
  cost you speed, it cost you the store, the approval in flight and every rule
  in it. The `except sqlite3.Error` guards written to keep the failure direction
  lexical could never fire, because the quarantine exception is not a sqlite
  error. Reproduced: drop the table, approve a candidate, database gone. Index
  statements now have their own routing point with no quarantine in it, and the
  test asserts the exception classes directly so the guard cannot go decorative
  again. Statements against the real tables are unchanged: damage there is still
  damage.
- THE STATUS COMMAND DID THE SAME THING. `index-status` describes itself as read
  only and safe on a store with no index, and on a dropped index it quarantined
  the store; the drift check then failed on the connection it had just had
  closed underneath it. Both now report the condition and leave the file alone.
- A STALE INDEX WAS ANSWERING QUERIES. The fast path is a per-process switch, so
  one ordinary shell that approves or edits a rule without it leaves the index
  behind silently, and the index was only ever populated when it was first
  created. The next run with the fast path on trusted it: a rule was returned
  for a task its current text shares no word with, on the strength of a version
  the founder had deleted, and the explanation line called it a stem match. In
  the milder shape, an indexed rule sharing one common word outranked an
  unindexed rule with three times its exact overlap, and `--limit` then dropped
  the right rule entirely. The index is now reconciled against the rules at the
  point it is CONSUMED: it is rewritten if it disagrees, and if it cannot be
  rewritten the fast path switches off and the run says `mode=lexical`.
  `verify` and `index-status` still show drift rather than repairing it.
- WHAT DID NOT CHANGE. No schema change, no new dependency, no new environment
  variable, and the lexical path is byte for byte what it was. `rebuild-index`
  remains the explicit repair, and it now reports a failure instead of raising
  through the CLI.

## 2026-07-29 (no version bump): an optional FTS5 fast path, with the lexical path untouched behind it

Loop P7. The headline is not the index, it is what happens without it: retrieval
stays lexical by default, and every claim the tool makes about how it ranked is
now checkable against the mode it actually used.

- THE INDEX IS OFF UNTIL YOU ASK. `BROTHERMODE_FTS5=1` turns the fast path on,
  `BROTHERMODE_NO_FTS5=1` forces it back off and wins over the first. Nothing
  else changes: with no environment set, a store has no index table, retrieval
  reports `mode=lexical`, and the ranking is byte for byte the order it produced
  before this loop (there is a test that rebuilds the old sort key by hand and
  compares).
- IT IS NOT PART OF THE SCHEMA. No schema version bump, no new required table,
  no migration. A SQLite build with no FTS5 module opens, writes, verifies and
  retrieves exactly as before. Deleting the index by hand costs you speed and
  nothing else.
- WHAT IT BUYS. English stemming: a rule written about "pushing" is now found by
  a task that says "pushed", which the exact-token floor was throwing away.
  Measured on the real CLI against a throwaway store, both ways round: lexical
  returns nothing for that query, fts5 returns the rule.
- RANKING NAMES ITS PARTS. Gates first (structural, unchanged), then scope
  specificity, then rule state, then BM25, then exact lexical overlap, then a
  stable uuid tie break. Every result carries `mode`, `bm25` and
  `lexical_bonus` beside the terms it matched, and the screen prints bm25 ONLY
  when a real index answered.
- THE INDEX HOLDS ONLY WHAT WAS ALREADY BEING SHOWN. Trigger, action, because,
  domain and scope key of the CURRENT rule version. Raw founder corrections,
  evidence excerpts and rejected candidate text are never indexed, and a test
  reads the index back and fails if a marker from the founder's own words
  appears in it.
- DRIFT IS CHECKED FOR REAL. `bm_learn.py verify` used to print a note saying
  there was no index to drift from. It now compares the index against the rules
  and reports four disagreements: a rule with no row, a row with no rule, a row
  pinned to an old version, and text that does not match the version it names.
  Each shape is fed back in as a deliberate corruption in the suite and must be
  caught, then repaired.
- `bm_learn.py rebuild-index` rebuilds into a separate table and swaps it in
  inside one transaction, so a reader sees the whole old index or the whole new
  one. `bm_learn.py index-status` says what mode you are in and why.
- FAILURE DIRECTION IS ALWAYS LEXICAL. A broken or missing index switches the
  fast path off mid-session and the run reports `mode=lexical` from that moment;
  it never refuses a retrieval and it never costs you a gate. A hostile query
  (`NEAR/2`, `*`, `^`, column filters) is quoted token by token before it
  reaches FTS5, and the one query that could still raise owns its own except so
  a founder's punctuation can never quarantine a healthy store.

## 2026-07-29 (no version bump): a retrieval miss is now graded against the run that ran

Fix round P6, on top of Loop P6 (d3c24a3). The run row landed and the miss
count still moved for reasons that had nothing to do with the retrieval. Seven
findings, every one reproduced on a throwaway store through the real CLI before
anything was changed.

- THE GRADED RUN IS NOW JUDGED BY ITS OWN ROWS. `seen` was built from every
  application row in the session, while the run being graded was the earliest
  one. So the ordinary founder workflow erased the loop's flagship finding:
  `apply --limit 0` recorded a genuine limit miss, the founder raised the limit
  and re-ran, and the first run's miss disappeared. Rows now count only for the
  run that wrote them.
- A CHANGED CORPUS IS REFUSED, NOT RE-RANKED. The miss split was decided by
  re-ranking TODAY's rules while citing the stored run as its authority.
  Deprecating one unrelated rule flipped a stored `retrieval_limit_miss` into a
  `retrieval_miss`, which is the difference between "your page size was too
  small" and "your ranking is wrong": opposite fixes, on facts that did not
  change. The reconstructed corpus is now counted against the eligible count
  the run recorded, rules approved after the retrieval are dropped from the
  ranking instead of inflating positions, and a mismatch is reported as
  `corpus_changed_since_retrieval` and graded no further.
- THE PRINTED DENOMINATOR IS THE STORED ONE. A reason line saying "ranks 1 of 1"
  beside a run row recording 2 eligible rules cannot both be true.
- THE PROMPT IS NO LONGER STORED. `apply` defaulted the run's excerpt to the
  query itself, so up to 500 characters of verbatim task text went into the new
  table on the default path with nothing justified and no way out. The run now
  keeps the task's TERMS: sorted, deduplicated, stopword-free, order destroyed,
  secret-scrubbed. That set is exactly what the ranker reads, so past
  retrievals still re-rank faithfully, and the sentence the founder typed is
  not recoverable from the row. A task with more than 200 distinct terms keeps
  none and is reported as `no_task_text` rather than re-ranked from a truncated
  set.
- SCOPE KEYS ARE STORED WHOLE. They were written through the 200-character
  display cap, so a longer legal project key came back with an ellipsis,
  stopped matching its own rule, and the miss vanished with no refusal either.
- WHICH RUN IS THE AUTHORITY IS NO LONGER A COIN FLIP. `created_at` has
  one-second resolution and the tie was broken by the random retrieval uuid, so
  identical command sequences graded different runs. Insertion order decides.

## 2026-07-29 (no version bump, schema 3 to 4): a retrieval miss now has a recorded denominator

Loop P6. A miss is a statement about what was NOT returned, and the rules that
were not returned have no row to hang context on. The classifier used to rebuild
the retrieval context from the scope_match of the rows that DID land, which is
circular. Reproduced on a throwaway store at 03c0dab: one global gate and one
project-scoped rule in scope, `apply --project Acme --limit 0`, and `classify`
reported no misses at all, because nothing project-scoped came back so the
rebuilt context had no project key and the cut rule could not even be found to
be missing.

- NEW TABLE `learning_retrieval_runs`, and `learning_applications` gains
  `retrieval_uuid`. One row per recorded retrieval, written in the same
  transaction as the application rows, holding the scope context, the requested
  limit, the retrieval mode, the eligible and returned counts, a bounded
  scrubbed task excerpt and a NON-REVERSIBLE query hash. The raw query is never
  stored. Schema 4; the migration is additive, atomic, backed up first, and a
  failure leaves the previous store untouched.
- MISSES ARE SPLIT BY CAUSE. `retrieval_miss` now means the rule ranked inside
  the limit the caller asked for and still never reached the model;
  `retrieval_limit_miss` means the limit cut it. Different fixes, and one number
  covering both is how a limit set to 1 stays invisible while retrieval quality
  looks bad. A gate is never a limit miss: gates are exempt from the limit by
  construction, so a missing gate is always the harder finding.
- THE MISS PASS WALKS RUNS, NOT ROWS. A retrieval that returned nothing has no
  application row, and it is the retrieval most worth grading. It is now graded.
- FOUR REFUSALS, each reported rather than guessed: an application row that
  predates this table is `legacy` (incomplete evidence, never backfilled with an
  invented run), a task with no kept text is `no_task_text`, a rule whose current
  wording was written after the retrieval is `rule_changed_since_retrieval`, and
  a rule approved after the task ran is not a miss at all.

## 2026-07-29 (no version bump): a second unit of work now gets its own application row

Fix round P5-fix, on top of Loop P5 (fe0497b). P5 moved recording out of a
forgettable flag and into the `apply` verb. Three ways a run could still end
with substantial work and no row that could grade it survived that change, all
reproduced first on a throwaway store at fe0497b.

- THE IDEMPOTENCE KEY WAS BLIND TO THE WORK. It was (task fingerprint, rule,
  version, session), and the fingerprint comes from the query alone. Two
  different pieces of substantial work in one session worded the same way
  collapsed onto ONE row: the second printed `status: recorded.`, exited 0, and
  wrote nothing, so "was this rule followed" was unanswerable for it while the
  run read as a clean success. Naming the second unit's work record with
  `--record` did not help either: that path exited 3 with
  `already belongs to work record`, and still wrote nothing. The key now
  includes the work record. Each unit of work gets its own row, an unclaimed row
  is still adopted by the first `--record` that arrives, and a row belonging to
  another work record is never selected for update, so a link is still never
  moved. That last property is now a consequence of the lookup rather than a
  refusal bolted on after it.
- A BAD `--record` SWALLOWED THE FOUNDER RULES. Resolving the prefix ran before
  retrieval and outside the block that turns write failures into a partial
  status, so a stale or mistyped work id exited 2 with EMPTY stdout. SKILL.md
  ships the sentence "never read a nonzero exit as 'no rules'", so an agent
  following the law it was given would read that as partial success and do the
  work with zero founder rules surfaced. Resolving `--record` is part of the
  WRITE now: the rules print, `record_error` carries the reason, and the exit is
  3 like every other bookkeeping failure.
- THE PARTIAL STATUS PRESCRIBED A REMEDY THAT NEVER CONVERGED. It told every
  caller to "re-run the identical apply", which is right for a busy database and
  provably wrong for an argument that does not resolve: that fails the same way
  forever. `record_error_kind` now separates the two, and a bad `--record` is
  told to fix or drop the flag instead.
- `already recorded` no longer reads as `recorded for this work`. With no
  `--record` there is nothing to key on beyond shared task wording, so `apply`
  names the work record the row it found belongs to and leaves the decision with
  the caller rather than guessing in the direction that looks like success.
- On a failed write, the already-recorded count is no longer reset to 0. Those
  rows pre-date the call and survive the rollback; reporting 0 understated what
  the database holds.

Calibrated by reinjection, each restored after: making `_prior_application`
record-blind again fails the second-unit-of-work test with "the second unit of
work recorded nothing"; resolving `--record` before retrieval again turns exit 3
into exit 2 with no rules; collapsing the two remedies back into one fails the
converging-remedy assertion; dropping the disclosure fails the ambiguity test.
Suite after the last edit: 656 tests across 4 suites, 2 skipped, with one
pre-existing unrelated flake recorded in `docs/KNOWN-LIMITS.md`.

## 2026-07-29 (no version bump): substantial work no longer depends on remembering a flag

Loop P5. `relevant` did both jobs: it retrieved founder rules, and it recorded
that they had been surfaced only if the caller passed `--record-applications`.
SKILL.md, the law an agent actually follows, named the command WITHOUT the flag.
So the documented substantial-work path wrote nothing, "was this rule followed"
was unanswerable for every task that took it, and no output anywhere said so.
Reproduced at bc25e06 on a throwaway store: `relevant --query "..."` exited 0,
printed the rules, and `learning_applications` stayed empty.

A better default would not have fixed it, because the ambiguity was the point of
failure. The choice moved out of a flag and into the verb.

- `lookup` retrieves and writes NOTHING, ever. `--session`, `--record` and
  `--not-shown` are refused by name with a pointer to `apply`, rather than by
  the generic unknown-flag line.
- `apply` retrieves AND records, with no flag in between, and REFUSES without
  `--session`: an application row with no session identity cannot be tied back
  to the work it belongs to. It stays idempotent per (task, rule, version,
  session), and re-running it once a work record exists links the rows already
  written.
- A failed recording is no longer a line buried under a run that exits 0. The
  rules still print, because the retrieval genuinely succeeded, but the output
  carries `STATUS: PARTIAL. RULES RETRIEVED, APPLICATION NOT RECORDED.`, the
  JSON carries `recording_status`, and the process exits 3.
- `relevant` survives as a deprecated alias with its old behaviour intact, and
  says on every run that it is deprecated and which verb to use instead. It goes
  away in the next major version.
- SKILL.md's founder-rules law now names `apply --session`, and a test parses
  that section and fails if it ever again names a command that records nothing.

Calibrated by reinjection: forcing `recording_flag` back to opt-in fails 4 of
the 7 new tests; removing the `--session` requirement fails 1; restoring the old
SKILL.md line fails the law test. Suite after the last edit: 652 tests across 4
suites, 2 skipped, ALL GREEN.

## 2026-07-29 (no version bump): the zero-result path told you nothing was omitted

Fix round P4-fix, on top of Loop P4 (c78e5ea). Loop P4 promised that `relevant`
"explains soft omissions separately from gate delivery". It put that two
sentence footer at the BOTTOM of the command, and the zero-result branch
returns before reaching it. So with one live soft rule that DID match the query
and a limit that cut it, the founder read:

```
no founder rules apply here (1 in scope, none matched; mode=lexical)
0 of your 0 applicable gate rules were held back; a result limit cannot hide one.
```

while `--json` on the identical call reported `eligible: 1, omitted: 1,
soft_omitted: 1`. Two wrong things at once: a rule that matched was reported as
not matching, and the omission was never mentioned. The new gate sentence made
the screen read as a complete, clean answer. Reproduced on a throwaway store at
c78e5ea with `--limit 0` and again with `--limit -1`, both named in the loop
spec as required edge cases.

The gate guarantee itself was never broken. `gates_total` is necessarily zero
whenever the result is empty, because gates are no longer subject to the limit,
so no gate was hidden. What was broken is the disclosure the loop exists to
provide.

- The footer moved into `_delivery_footer`, called by every path out of
  `relevant` that prints for a human. It is a function precisely so a path
  cannot go quiet again without deleting the call, and a test fails if either
  sentence gets a second inline copy.
- The zero-result line now distinguishes its two causes. Nothing matched still
  reads "none matched". Rules matched and the limit cut all of them now reads
  "no founder rules SHOWN here (N in scope). Rules matched. The result limit
  cut every one of them."
- `_soft_omitted` gives the one definition of that count, with the pre-P4
  `omitted` key as the fallback.

Four tests, driven through the real binary: the reproduction at `--limit 0` and
`--limit -1` cross-checked against the JSON from the same call, a genuinely
empty result still saying "none matched" so the fix cannot pass by shouting
"omitted" at everything, the structural one-definition check, and a calibration
test that reinjects the pre-fix footer onto the real product symbol in-process.
Both behavioural tests fail against c78e5ea's `bm_learn.py`.

## 2026-07-29 (no version bump): a result limit can no longer hide a gate rule

Loop P4, on top of fix round P3 (05441e7). The defect was already written down
as the open bullet of `docs/NOT-FINALIZED.md` item 19, and it was reproduced
again on the real CLI before anything changed: two live global rules, one of
them a gate whose trigger shared no vocabulary with the task, `--limit 1`, and
the gate never reached the model.

The relevance floor already said a gate must appear even when the founder did
not use its words. The result slice then cut it anyway, which made that
exemption decorative. The fix is structural rather than a nudge to the ranking,
because promoting gates in the ranking would change Loop 5 retrieval order and
that is the founder's decision, not this loop's:

- `limit` now caps SOFT rules only. Every applicable live gate rule is returned
  whatever the caller passed. Ranking is untouched, so a gate does not jump the
  queue, it simply cannot be cut from it.
- `--limit 0` means gates only. A negative limit clamps to zero and means the
  same, instead of slicing from the end of the list the way a bare Python slice
  would.
- New diagnostics `gates_returned`, `gates_total`, `soft_returned` and
  `soft_omitted`. The old single `omitted` count is kept and now covers soft
  rules only, because after the split it can never mean anything else.
  `gates_returned` is counted off the rows actually returned, not off the
  intention above it, so a later edit that drops a gate makes the two disagree
  and fails a test.
- `bm_learn.py relevant` prints the two as two sentences: gate delivery as the
  guarantee it is, soft omission as the tuning knob it is. An empty result also
  says how many applicable gates were held back, which is always zero.
- `bm_learning.py` gained `GATE_SEVERITY`, `is_gate` and `split_gates`, so the
  three places that must agree on what a gate is read one definition. It stays
  pure.

Retiring a gate still works: a deprecated or forgotten gate is not live, so it
is not delivered, and there is a test for exactly that. Seven tests cover the
reproduction, limit zero, negative limits, many gates against a small limit,
dead gates, a conflicting gate counterpart, and a calibration test that
reinstates the old slice. Six of the seven fail with the old behaviour
reinjected. Suite: 641 tests, ALL GREEN.

## 2026-07-29 (no version bump): three holes in the receipt work, closed

Fix round P3 on top of the LOOP 3 commit (1fdf2c3). Each of the three was
reproduced against a throwaway store before anything was changed, and each has
a test that fails when the fix is removed.

1. `dump` printed the founder's answer, in effect. The receipt table stores
   `founder_response_hash`, an unsalted sha256 of what he said, and
   `nonce_hash`. The redactor that protects `dump` works by PATTERN, and a hex
   digest matches no pattern, so both came out in cleartext. Real answers are
   short ("oui", "yes"), so a ten-word wordlist turned the digest back into the
   word; identical answers also showed as identical digests. `dump` now
   withholds every `*_hash` and `*_fingerprint` column by name-shape, read from
   the live schema, so the next digest column anyone adds is covered the day it
   exists. `--raw` still returns everything.

2. Editing a rule needed no receipt. Approval was gated; rewriting an
   already-approved rule was not. An imported call turned an approved gate rule
   saying "never force push to main" into "always force push to main, skip
   review", kept its gate severity, and stamped the new version
   `approved_by='founder'`. Editing rule text is the same act as creating it,
   because the text is what gets injected, so it now takes its own one-time
   receipt: one rule, one version bump, one exact new text, one use. An
   approval receipt cannot be spent as an edit, or the reverse.

3. The overrides were not part of the question. `--override-reason` and
   `--override-conflict` existed only at approve time and were absent from the
   receipt fingerprint, and `grant-approval` never mentioned that a candidate
   contradicted an existing rule. So a receipt minted for a clean question was
   spent with `--override-conflict` attached and forced in a rule contradicting
   an approved gate rule, on an answer given about a question that never
   mentioned the conflict. The conflict, duplicate and not-atomic guards now run
   at MINT as well, so the question cannot be asked without naming what it
   overrides, and both flags are in the fingerprint, so a clean receipt dies if
   an override is added to it. `grant-approval` prints what is being overridden
   beside the token.

## 2026-07-29 (no version bump): approving a rule now needs a receipt from a real answer

Post-audit LOOP 3, founder decision the same day: Model A. No version bump,
because a release is founder-gated (`docs/RELEASE.md`) and this is a fix plus
its tests.

The thing that was wrong. The product said approval was founder-only and the
code did not enforce it. Reproduced against d88abcc in a throwaway store on
2026-07-29: `bm_learn.py approve <id> --gate`, with no reference and nothing
from a human, exited 0 and created gate rule 61de7eb9. The command line
manufactured the approval evidence itself, filling in the words "run by the
founder" whether or not anyone was there, while the help text three lines from
the top of the same file said approval refuses without that evidence.

What you do now, in two steps instead of one:

1. You are asked about one candidate. Whoever asked runs
   `bm_learn.py grant-approval <id> --answer "<what you said>"`, which prints a
   one-time token, good for fifteen minutes, for that candidate only, tied to
   the exact rule text you were shown.
2. `bm_learn.py approve <id> --receipt <token>` spends it, once. The token can
   also arrive in `BM_APPROVAL_RECEIPT` so it stays out of your shell history.

Change the candidate or the rule text after the question and the token dies.
Use it twice and the second try refuses. Let it go stale and it refuses. There
is no override and no break-glass: with no token, no rule is created, by any
path, including an imported function call.

What this does NOT claim. Nothing here authenticates WHICH human answered. The
token proves an answer was given about this exact thing and has not been spent.
It is not an identity check and no wording in this product may say it is. What
it removes is the real hole: a background process can no longer produce an
approved rule, because there is no default and nothing a hook can read.

Store schema goes 2 to 3, additively. The migration adds one table and one
index and touches no existing row. Rules approved before receipts existed keep
their old, weaker provenance and are NOT rewritten to look receipt-backed; a
schema-2 store migrates rather than being quarantined.

Four guards were calibrated by reinjecting the bypass and confirming the right
test fails: the receipt requirement, the fingerprint binding, the expiry check,
and the conditional claim that makes consumption and rule creation one atomic
act. That fourth calibration found a real gap: removing the claim guard failed
nothing, because the ordinary replay was being caught earlier, so the race the
guard exists for now has its own test.

## 2026-07-29 v2.0.0-rc.3: V2 becomes the public product, by founder decision

The founder decided, recorded the same day: ship V2 as a release candidate now.
The repository root previously showed the pre-V2 product; after this release the
default branch carries V2, so the clone command in the README installs what the
README describes. The STABLE claim still waits for the real dogfood window and an
independent audit; this is a release candidate and says so.

What changed in this cut:

- `v2` merged into `main` (fast forward; pre-merge main preserved in a backup
  branch). One product, one branch, one README.
- `CHECKSUMS.sha256` regenerated at the tagged commit. The post-audit baseline
  (docs/POST-AUDIT-BASELINE-2026-07-29.md) records that the previous manifest
  had drifted 12 commits behind the tree and the verifier correctly refused it.
- The post-audit execution plan and its frozen baseline ship in `docs/`, with
  founder decisions recorded inline: Model A approval receipts chosen, four new
  loops ratified (cross-runtime adapters, packaging, ecosystem launch kit,
  external beta evidence).
- Everything in the rc.2 entry below (the correction memory, 598-test gate)
  is included unchanged.

## 2026-07-29 (still v2.0.0-rc.2, no version bump): a founder-approved correction memory, documented honestly

No version bump: a release is founder-gated (`docs/RELEASE.md`), and this
entry is documentation plus a working feature, not a release decision. Test
count went from 419 (2 skipped) to 598 (2 skipped), all green, run with
`python3 tools/test_all.py`.

What actually changed for you:

- You can now capture a correction, approve it into a rule yourself, and ask
  what rules apply to a piece of work with the reason shown. Approval is
  founder-only: nothing in this system, including the parts that watch for
  corrections automatically, can approve its own candidate. Plain-language
  walkthrough with real, hand-run command output: `docs/CORRECTION-LEARNING.md`.
- Correction capture now understands English, French and Japanese phrasing,
  and a long correction is excerpted with the omitted character count
  recorded, instead of the old English-only, 400-character filter that
  silently dropped a founder's French corrections and long ones alike.
- Approving a rule that plainly reverses a live one is refused unless you
  override it, and the override is recorded rather than silent. A conflict
  the detector cannot see (a phrasing it does not recognise as a reversal)
  can be declared by hand and counts exactly as much downstream.
- The store can now say, for a given piece of work, which rules were
  retrieved, shown, followed, ignored, and why, and can link a rule to a
  piece of rework or an escaped defect it failed to prevent. A correction
  round on that grading (found the same way every serious defect in this
  project has been found: driving the real CLI against a throwaway store
  while the suite stayed green) fixed the same outcome being counted twice,
  a rule being blamed for work it was never part of, and rework being
  miscounted as the founder repeating an instruction he only said once.
  `docs/NOT-FINALIZED.md` item 20 has the detail.
- The secret scrubber used to miss a secret glued to a word character on
  either side (an API key immediately after an underscore, for example), and
  every JSON-emitting learning command now withholds a founder's raw text and
  evidence excerpts through one shared rule, rather than a rule a particular
  command could forget to apply. `docs/NOT-FINALIZED.md` item 15 has the
  detail.

What is deliberately unbuilt, and stays that way until its stated reopening
condition:

- **Loops 9 and 10 were never built, by the founder's own ratified decision**
  (`docs/superpowers/specs/2026-07-28-correction-learning-program.md`, section
  3.1): evaluation partitions (Loop 9) and generated knowledge views (Loop 10)
  would both be measuring or generating over a corpus of maybe twenty to forty
  rules belonging to one person, too small for either to produce a number that
  can support a decision. Building the machinery anyway would make an
  unsupportable number look rigorous, which is exactly the failure this
  program's own principle forbids. Loop 9 reopens when the rule corpus is
  large enough for a partition to decide anything; Loop 10 reopens when
  hand-curating `docs/knowledge/LESSONS.md` and `TOOLBOX.md` actually becomes
  the bottleneck.
- **Loop 11B, the optional automatic-retrieval hook, is gated on the real
  dogfood window, not built.** The skill-driven retrieval that already ships
  (Stage A, Loop 11A) has to prove itself in real use first; a hook that
  pushes the wrong rule into every prompt is worse than the opt-in retrieval
  shipping today.

Still true and the single most important line in this entry: **this system has
never run on a real day of the founder's actual work.** Every count above came
from a test suite or a hand-driven probe against a throwaway store. That is
`docs/NOT-FINALIZED.md` item 1, and it stays UNPROVEN, ranked the highest-harm
open item in the whole project, until Loop 14a (a real dogfood window) closes
it. No amount of further testing can close it.

Also still true: Windows behaviour is asserted by continuous integration only;
no Windows machine has ever run this code by hand. And `docs/NOT-FINALIZED.md`
item 12, an independent second-model adversarial re-audit, remains open. The
privacy and security fixes in this wave (the secret scrubber, withholding raw
text) do NOT close item 12: they were written and verified by the same model
family that built the feature they review, which is exactly the blind spot an
independent re-audit exists to catch. A different model family has never
looked at this code.

## 2026-07-27 (v2.0.0-rc.2): the external audit closed, and rc.1 withdrawn

A second external adversarial audit returned NO-GO on rc.1: 8 release blockers
and 9 high-risk defects. All 17 are now closed, and CI is green on Linux, macOS
and Windows across both supported Python versions with the recovery suite
included, which rc.1 never was.

WITHDRAWN: do not install `v2.0.0-rc.1`. Its tag points at a commit that FAILED
on Windows, and the branch then moved 14 commits past it while the VERSION file
still read `2.0.0-rc.1`. An immutable tag and a moving branch claimed one
identity while holding different code. That is the defect this entry exists to
close, and it is worse than an ordinary bug: a version number is the promise that
two people naming it are discussing the same bytes.

What actually changed for a user:

- Your recovery tool no longer lies to you. It used to print "your files are
  autosaved" whenever this session had EVER snapshotted; it now verifies the
  snapshot still resolves and says plainly when your newest work may not be
  captured. It has a third answer that claims nothing when the truth is unknown.
- Files can no longer be written outside your project through a symlink. One
  containment funnel now covers STATE.md, its backups, thread directories and the
  project server, which previously had four separate holes.
- The project server can no longer be tricked into copying another project's
  database and answering with its contents.
- The install verifier now catches a planted symlink backdoor it used to pass.
- A read-only health check can no longer move your database.
- The store refuses to open where git could commit it, so raw objectives and
  decisions cannot be published by a routine `git add -A`.
- A thread whose NAME looked secret-shaped used to become permanently unreachable
  with its fence stranded. Identity now comes from the table, not a redacted view.
- The CLI no longer guesses between two threads with the same name; it refuses
  and lists them.
- The single-writer promise has a mechanical gate for the first time: a
  PreToolUse hook that blocks a write outside an active claim. It is shipped but
  NOT installed by default, because it sits in front of every edit.

Still true and stated rather than buried: this has never run on a real project,
Bash writes are not gated by the hook, session identity is harder to forge but
not unforgeable, recovered work is owner-only on POSIX only, and handovers are
serialized by a lock rather than stored transactionally. `docs/KNOWN-LIMITS.md`
carries the full list.

## 2026-07-26 (later still): the final gate's two blockers, closed structurally

A four-lens final gate aimed at the code from the entry below found two release
blockers and three further gates, all reproduced by hand before this entry's
fixes were written (`docs/superpowers/specs/2026-07-26-final-blockers.md`).
This entry closes all of them. No version bump: still `2.0.0-rc.1`.

**BLOCKER 1, the worst finding: the server documented as read-only moved the
founder's database.** One `bm_status` call against a store whose header bytes
had been corrupted quarantined it, renaming `store.sqlite3` aside and
reporting `isError: false`; a HEALTHY store's own read-only open also created
`-shm`/`-wal` sidecars that were not there before. Reproduced, then fixed
structurally rather than by a stronger promise: `mcp/bm_mcp_server.py` now
copies `.brothermode/` and `STATE.md` into a private temporary directory
before every tool runs, and deletes the copy afterward, success or failure
alike. Proven against the real server: a corrupted store's directory listing
(names and content hashes) is byte-identical before and after all four tools
are called, and a healthy store with its sidecars removed gains none back. A
read-only sqlite URI was considered and rejected on purpose, to avoid
reopening the exact `%`-path defect this project's own GATE A (fix-round 6)
already closed.

**BLOCKER 2, security: `verify-install.sh` reported PASSED with a planted
backdoor present.** It only ever asked whether every file the manifest NAMES
matches on disk, so a file that was ADDED (`tools/bm_helper.py`, containing a
shell-out) was invisible to it. Reproduced (88 hashes, plant, still PASSED at
exit 0), then fixed: the script now also enumerates the installed tree and
fails, naming the file, when anything exists on disk that the manifest does
not name. Verified against a clean tracked-file copy of this repository
(PASSED, 0 extra) and the same copy with the backdoor planted (FAILED, exit 1,
`EXTRA: tools/bm_helper.py`).

**GATE 3: the server leaked founder text every other exit redacts.**
`bm_store.verify()` returns raw, unredacted rows (it is an invariant checker,
not a rendering funnel), and the server used to print its problem strings
straight through; a secret-shaped record name reached a client here
unredacted while the CLI showed `[REDACTED]` for the same value. Fixed: every
founder-typed value this server returns now passes through
`bm_store._protect_text`, the exact function the CLI's own output funnel
uses. The round-7 output-funnel structural scan, previously scoped to
`bm_store.py` alone, is now also applied to `mcp/bm_mcp_server.py`
(`tools/test_bm.py`), together with a calibrated test that reproduces the
secret-leak precondition and asserts the live secret never reaches the tool's
returned text. Not widened further: `bm_threads.py`, `bm_autosave.py`,
`bm_telemetry.py`, and `bm_score.py` were never brought under this funnel
discipline and are outside this fix's scope; stated here rather than silently
skipped.

**GATE 4: `project_root` was not authoritative.** A relative path resolved
against the SERVER PROCESS's own working directory, and `BROTHERMODE_ROOT`
silently overrode an explicit argument, so a call naming one project could
answer with a different project's record. Fixed: `project_root` must now be
an absolute path (a relative one is refused outright), the server never
consults `BROTHERMODE_ROOT`, and every tool prints the project root it
actually resolved as the first line of its own output. Verified: with
`BROTHERMODE_ROOT` pointed at one project, an absolute `project_root` naming
a second project answers correctly about the second project, with its root
printed; a relative `project_root` is refused with `isError: true`.

**GATE 5: `checksums.sh` silently dropped any git-quoted path.** A tracked
path containing a quote, a backslash, or a non-ASCII character is printed
quoted by plain `git ls-files`, is not a real path on disk, and the manifest
loop skipped it with no message at exit 0. Fixed: the manifest is now built
from `git ls-files -z` (null-delimited, never quoted), and the script fails
loudly, naming the file, if any listed path is not a regular file on disk.
Verified in a throwaway repository with a file named containing both a quote
and an accented character: the old approach silently hashed 3 of 4 tracked
files; the fixed script hashes all 4, and correctly fails loudly when a
tracked file is genuinely missing from disk.

**SOFT 6: the decisions filter compared against the REDACTED name.**
Filtering `bm_decisions` by a record's real name returned a false "no
decisions recorded" whenever that name happened to look secret-shaped,
indistinguishable from a project that genuinely recorded none. Fixed: the
filter now matches against the record's real name, read from a second,
separately-opened raw dump used only to compute matching identifiers; only
those identifiers, never the raw name, reach the tool's returned (still
redacted) text. Verified: filtering by a secret-shaped name now returns its
decision instead of a false negative.

`docs/RELEASE.md` and this file both now describe the two-directional
`verify-install.sh` check accurately; the previous wording promised the
behavior BLOCKER 2 found missing.

## 2026-07-26 (v2.0.0-rc.1): the first tagged version, release discipline, and an honest ledger of what shipped today

This is the first version of this project to carry a version number
(`VERSION`, `2.0.0-rc.1`) and, once the founder-gated tagging step in
`docs/RELEASE.md` actually runs, the first tagged release. Read
`docs/RELEASE.md` for why it is `2.0.0-rc.1` and not a bare `2.0.0`: the
storage engine changed underneath every command (a major bump), but this has
never run on a real project and CI has never executed once (`docs/KNOWN-LIMITS.md`),
so it ships as a release candidate rather than a claim of proven stability.

**What changed for you, if you install this today:**

- **The storage engine is now the thing every tool actually uses.**
  `tools/bm_store.py` (a SQLite-backed store) is imported by
  `bm_autosave.py`, `bm_sessionstart.sh`, `bm_telemetry.py`, and
  `bm_threads.py`. `bm_registry.py`, the old JSON registry, is deleted
  outright, not kept as a shim. If your own scripts imported
  `bm_registry.py` directly, that import will now fail; nothing in this
  project does that any more.
- **A recovery path for the new engine.** The autosave/recovery mechanism was
  rebuilt against the store rather than the old registry, with a real,
  test-covered recovery flow instead of a manual sqlite surgery.
- **Two security fixes, both re-verified today.** A recovered autosave
  worktree used to come back world-readable (`drwxr-xr-x`); it now comes
  back owner-only (`drwx------`). Turning thread mode off and resuming a
  thread later from a different session used to be wrongly refused; it now
  succeeds and transfers ownership correctly. Both are described in full,
  with the exact reproduction steps, in `docs/KNOWN-LIMITS.md`.
- **Several features were removed, not just refactored.** Checkpoint clash
  detection has no equivalent in the new engine and is gone. The 320-line
  generative test bound to the old registry was deleted along with it; a
  store-level replacement is being built separately and is not part of this
  release. Neither removal is quietly absorbed into "cleanup" language:
  they are feature losses, named as such in `docs/REMAINING.md`.
- **Release discipline exists for the first time.** `scripts/checksums.sh`
  generates a SHA256 manifest of every git-tracked file; `scripts/verify-install.sh`
  checks an installed copy against that manifest and names anything that
  differs. This is the direct answer to a problem the original external
  audit called the weakest link in the whole design: the install
  instruction clones a git ref into a location whose code then runs
  automatically on every Claude Code session, with previously no way to
  confirm what actually landed on disk.
- **A guided path to the memory setup**, `docs/OBSIDIAN.md`: what the vault
  is, how to install Obsidian, how to point it at the vault, and how to work
  with the same vault using nothing but a plain text editor. Obsidian
  previously appeared nowhere outside a design document.
- **A minimal, read-only MCP server**, `mcp/`, so a session can ask what
  work is active, what fences are live, what decisions were recorded, and
  whether the store is healthy, without reading files directly. It is
  read-only by construction (it only opens `bm_store.ReadOnlyStore` or calls
  `bm_store.verify()`) and implements the subset of the Model Context
  Protocol a read-only query tool actually needs, not the full
  specification; `mcp/README.md` states exactly what was and was not
  verified.

**What is still open, so nobody mistakes this for finished:**

- **One confirmed defect remains.** A refused `adopt` attempt (one session
  tries to take over another session's live, active thread without the
  explicit override, and is correctly told no) still permanently writes an
  "Adopted from dead/stalled thread" block into `STATE.md` anyway. The
  refusal itself is correct; the side effect it leaves behind is misleading.
  See `docs/KNOWN-LIMITS.md` for the exact reproduction.
- **Never run on a real project.** Every claim in this release rests on test
  suites and adversarial review, not on a week of real founder work going
  through the V2 store.
- **CI has never executed.** The workflow is configured for Linux, macOS,
  and Windows; nothing has been pushed to trigger it, so the first real push
  is also the first real test of that configuration.
- **Windows is designed for, not proven.** Windows behavior was proxied by
  substituting the path module and platform identifier, which caught one
  real defect but is not the same as running on Windows.
- **The self-learning mechanism (Section 8 loops) is designed, not built.**
  The redesign is approved and written up; nothing implements it yet, and
  the old scorecard still prints at least one number (`collisions=0, baton
  drops=0`) that cannot move because nothing behind it is measured.

No test count is restated here. The previous entry claimed 116; this session
could not re-run `tools/test_bm.py` to confirm that number still holds
(other work landed in `tools/` in parallel with this entry being written),
and `docs/KNOWN-LIMITS.md` already warns that any test count in this
repository is a claim to re-verify, not a certificate. State the actual
number the next time this file is edited, taken from a command run in that
same session, not copied forward from here.

---

## 2026-07-26: handover delivery gets an owner, and the pattern gets a stop

A reviewer found that the retry marker added in the previous entry was keyed on
the thread NAME, and a thread name is reusable. Reproduced: adopt "payments",
start "payments" again for new work, adopt again, and the second lifecycle's
handover was silently discarded while the tool printed "Nothing is duplicated".

The fix is not another marker. Delivering a handover into the project
`STATE.md` now has exactly ONE owner, `deliver_handover`, used by both `absorb`
and `adopt`:

- **Identity is per lifecycle, not per name.** Every record carries a
  `lifecycle` number, incremented when a closed id is reused, so a second
  `payments` thread has its own delivery identity.
- **The proof of delivery lives inside the delivered text**, as a
  `<!-- brothermode-handover:id#n -->` tag, not in a side file. A separate
  marker can survive content a crash destroyed, which is the worse direction;
  a tag cannot outlive the bytes it is written with.
- **The append is durable.** `durable_append` flushes and fsyncs before
  returning, so a crash cannot leave a delivery proof more durable than the
  handover it vouches for.
- **`absorb` is now idempotent too.** It appended and then saved the registry;
  if the save failed, the retry appended the same handover again. It now
  recognises its own tag.

**The part that matters most.** Four cross-cutting concerns in this project have
now followed the identical arc: locking, redaction, durable writes, and handover
delivery were each implemented per call site, diverged, and were unified into a
primitive only after someone found the divergence. A fifth was always going to
happen, because nothing stopped the next call site improvising.

So this release adds the stop, not just the primitive: a test asserts that
`durable_append` has exactly one caller and that no other code appends a
handover by hand. A future writer that invents its own delivery fails the suite
instead of shipping and waiting to be caught.

116 tests.

---

## 2026-07-26: durable writes, closed as a class rather than case by case

The previous three entries each fixed one reported instance of the same defect.
This entry closes the class, by audit rather than by report.

**Crash atomicity.** State was written with a plain truncating `open(path, "w")`,
so a crash, a full disk or a killed process between truncate and write could
leave `registry.json` or `thread-mode.json` empty. An empty registry reads as
"no work in progress", which would let a second writer claim files someone is
already editing. There is now ONE primitive, `bm_telemetry.atomic_write`: temp
file in the same directory, flush, fsync, `os.replace`, then fsync the
directory. The registry and every thread file go through it. Proven: a failed
write leaves the previous file byte-identical and no temp file behind.

**Idempotent adoption.** `adopt` has three writes and can fail between any two,
so a retry is expected. A marker records that the handover already reached
`STATE.md`, and a retry resumes the unfinished steps instead of appending the
handover a second time. Proven by forcing a mid-transaction failure and retrying.

**The audit.** Rather than wait for the next report, every durable write in
`tools/` was enumerated and checked for a discarded return value. That found ten
more instances the reviews had not reached: `claim`, `decide` and `set_digest`
all reported success without checking their save; `start` ignored all four
thread-file writes and its mode write; `on`, `checkpoint`, `send` and
`attribute` the same. All now report honestly, and `start` releases its fence
when its files cannot be written so no paths stay claimed by a thread that does
not exist. The audit was repeated until two consecutive rounds turned up nothing
new; the four remaining unchecked calls are documented as deliberate.

One consequence worth knowing: because replacement is now atomic, a read-only
target FILE no longer blocks a write (rename needs directory permission, not
file permission). Tests that simulate a failed write make the DIRECTORY
read-only instead.

113 tests.

---

## 2026-07-26: a failed write can no longer be reported as success

The concurrency work was done, so the next review looked at failure paths and
found the worst remaining case. `adopt` ignored the return value of every write
it made and always printed success. With an unwritable project `STATE.md`:

- the handover never landed,
- the registry record was closed anyway, so `off` would never drain it,
- the thread was marked adopted,
- and the command printed "Nothing is orphaned".

Total silent context loss, reported as a clean adoption. Reproduced before the
fix, and the message was exactly that.

`adopt` now writes the handover FIRST, so a failure there changes nothing and
the thread stays adoptable, then checks every subsequent write and names the
exact partial state if one fails. `off` checks its final mode-file write for the
same reason. `bm_registry.close()` was returning True regardless of whether its
save reached disk, which would have made adopt's new check meaningless; it now
returns what the save did.

This is the same defect this project already fixed once inside `absorb()`, where
the bug had moved one call later. It is the third appearance of the class.

110 tests.

---

## 2026-07-26 (final): the off transition is now atomic

A follow-up review found a real transactional race between `start` and `off`,
and it also corrected a mistake in the previous entry.

`off` drained the registry OUTSIDE the mode lock and only took that lock
afterwards. A `start` running concurrently could be granted in the gap, so its
record stayed ACTIVE while the mode file recorded the thread as parked, and its
digest was never absorbed. That is silent context loss, the exact thing thread
mode exists to prevent. Reproduced before fixing: **28 of 30 trials**. After:
**0 of 40**.

`off` and `adopt` are now each ONE mode-locked transaction, and `start`
rechecks the mode inside the lock so a start that queued behind an `off` refuses
instead of creating a thread nobody will drain.

The previous entry claimed holding the mode lock across `absorb` would invert
the lock order and risk deadlock. That was wrong. `cmd_start` calls
`reg.claim()` INSIDE the mode lock, so the order has always been mode then
registry, and `off` now matches it rather than inverting it. The wrong comment
had been used to justify leaving the gap open.

The concurrency test now asserts STATE, not only liveness: an OFF system must
hold no active persistent record, and every thread in the mode file must have a
record. It runs repeatedly, because a race that reproduces sometimes proves
nothing when it happens to pass once.

108 tests.

---

## 2026-07-26 (later still): one lock for the whole system

A follow-up review found the locking fix from the previous entry was applied in
one place and not the others. Three silent paths remained, all confirmed by
reading the code before changing it.

- `bm_threads.py` carried its OWN mode-file lock that swallowed every failure,
  so on a platform without `fcntl` the registry warned that coordination was
  degraded while thread-mode updates raced on quietly. Two half-truths instead
  of one behaviour.
- `bm_registry.with_lock` proceeded unlocked and silent when the lock directory
  could not be created, and again when the lock file could not be opened.

Locking is now a single primitive, `bm_registry.locked_call`, used by both
files. Every way it can fail to acquire still runs the work, because never-block
outranks coordination, and every one of them warns once per process.

Found while fixing this, and not in the review: three of the four writes to
`thread-mode.json` were not locked at all. Only thread creation was. The one
that mattered was `off` racing a `start`, which loses a thread. All four are now
inside the lock, in a consistent registry-then-mode order so the two locks
cannot deadlock against each other. That ordering is covered by a test that runs
four starts and two offs at once and fails on a hang.

107 tests.

---

## 2026-07-26 (later): the CI gate could pass on a crashed checker

Three findings from an external source review, each confirmed by running the
code before it was changed.

- **`bm_score.py --strict` exited 0 when the checker itself crashed.** The
  top-level handler caught every exception and exited 0, so any bug inside the
  checker turned into a green build that had verified nothing. Reproduced by
  injecting a crash and watching `--strict` report success. Strict mode now
  exits nonzero and says the checker failed; local runs still degrade quietly,
  because never-block is a promise to the session, not to CI.
- **Missing file locking was silent.** `fcntl` is POSIX only, so on Windows the
  registry ran with no lock at all while callers believed concurrent claims were
  serialized. Work still proceeds, but it now says once per process that
  coordination is degraded. Tested by shadowing `fcntl` with a module that
  refuses to import.
- **The collision claim was stronger than the code.** The README said collisions
  "stop being possible". They do not: the guarantee is exactly as good as the
  declaration, and a file an agent never declared is not protected. Both the
  README and the design doc now say that plainly.

104 tests.

---

## 2026-07-26: one work record for threads and fences

BrotherMode was keeping two separate records of the same fact. Threads lived in
`thread-mode.json`. Single-writer fences lived as prose inside `STATE.md`. Both
answered the question "who owns this file right now", and nothing stopped them
from answering it differently.

This release makes them one object. A thread and a fence are now the same
record, and `lifetime` (persistent or ephemeral) is the only thing that tells
them apart.

The practical effect: because a record's declared files are a real list instead
of a sentence, an overlap between two claims is now **computed and refused by
name**. Before, the registry could only be read by a human who happened to
check. That was the single largest hole in the single-writer law, and closing
it is the point of this release.

---

## What existed before

Everything below already worked and still works. This release did not remove
any of it.

### The law

- `SKILL.md`: 16 numbered sections covering classification, role assignment,
  the delegation ladder, token budgets, fences, research doctrine, circuit
  breakers, self-improvement loops, context hygiene, honesty, founder gates,
  memory, a known-mistakes ledger, the founder model, and scoring.
- `DIGEST.md`: a short compression of the law, injected at session start so the
  rules survive a context loss.
- `RUBRIC.md`: the frozen scoring rubric the weekly review grades against.
- `STATE.template.md`: the per-project state file, copied into your own repos.

### The tools

- `bm_telemetry.py`: session outcomes, the scorecard, felt-outcome ratings,
  review marks, session-start nags, stop warnings, registry and fence linting,
  write-ahead intent, the pre-compaction resume brief, the compaction hint, the
  update check, team handoff export, correction purging, prediction audit,
  speed stats, and deduplication.
- `bm_threads.py`: persistent feature threads with a chief orchestrator.
  Commands `recommend`, `on`, `start`, `checkpoint`, `send`, `dashboard`,
  `off`, `adopt`. Nothing ever flips mode automatically, the active-thread cap
  is enforced, and switching off is lossless.
- `bm_score.py`: the nine rubric metrics, with a `--strict` mode for CI.
- `bm_autosave.py`: snapshots the whole tree, including untracked files, into a
  private local git ref before every context compaction. Never pushes, and
  excludes secret-shaped files.
- `bm_sessionstart.sh`: injects the active-laws digest at session start.
- `WEEKLY-REVIEW.md`: the weekly scoring and amendment ritual.

### The safety properties

- No network calls, no analytics, no account, no server.
- Redaction of secret-shaped text before it reaches disk.
- Owner-only permissions on the files that carry your words.
- Founder gates: credentials are never typed, destructive actions are confirmed.

### Test coverage at that point

12 tests.

---

## What was added

### A new module: `tools/bm_registry.py`

The single owner of the work record and of the three operations that are
genuinely hard to get right.

| Function | What it does |
|---|---|
| `claim` | Registers work. Refuses when declared files overlap another active record, and names the record it collided with |
| `paths_overlap` | Computes overlap across exact paths, nested directories, globs, and absolute versus relative forms of the same file |
| `decide` | Records a decision under a topic tag and raises a clash when another live record already decided that topic |
| `set_digest` | Keeps an always-current handover, so nothing depends on a thread still being alive |
| `absorb` | Drains every digest into your project `STATE.md`, then parks records rather than deleting them |
| `close` | Releases a record and its file claim |
| `render` | Regenerates the human-readable view of the registry |
| `unguarded_count` | Reports when a record is guarding fewer paths than it declared |

The record itself carries: an id, a lifetime, an owner, an objective, a real
file list, an effort tier, a lease with a time to live, a state, a done-check,
an evidence block, tagged decisions, a digest, attributed spend, and a schema
version so a later change can migrate rather than break.

### A pre-write redaction gate

`tools/write_sites.json` plus a test that inventories every write site in
`tools/`. When a write site is added or removed, the test fails and a human has
to decide whether the new one needs redaction.

Be clear about what this is: a **review-forcing inventory, not proof**. It
cannot see inside a call graph. Its value is that it makes a new write site
impossible to add silently.

### Spend attribution

`bm_telemetry.py attribute` adds a session's output tokens to a specific
record, so a token budget becomes measured rather than advisory. Cross-process
locking is proven: forty concurrent attributions produce exactly the expected
total with no lost updates.

### Documentation

- `docs/BrotherMode-One-Page.pdf`: a single designed sheet covering purpose,
  target user, philosophy, all 16 laws, features, and how to use it well.
- `docs/one-pager.src.html`: the source that generates it, so the sheet can be
  regenerated and audited instead of trusted as an opaque binary.
- The design spec and implementation plan for this release, under
  `docs/superpowers/`.

---

## What changed in existing tools

- `bm_threads.py` keeps exactly the same eight commands. Nothing new appears on
  the surface. Underneath, claim, decide, absorb, and render are delegated to
  the registry instead of reimplemented, and its duplicate copy of the
  redaction fallback is gone.
- `bm_telemetry.py` gained the `attribute` subcommand. Three code paths that
  wrote text to disk without redaction were fixed. Rating and review files are
  now owner-only.
- `SECURITY.md` had two claims that had stopped being true and are now
  corrected: the registry writes inside your project directory as well as your
  vault, and the audit line count is now checked by a test so it cannot rot
  again unnoticed.

---

## Fixed

Three Critical defects, each found by adversarial review, reproduced before the
fix and again after it.

1. **Two writers could be granted the same file.** Overlap detection did not
   match an absolute path against a relative one, so the same file written two
   different ways looked like two different files.
2. **A corrupt path entry silently destroyed a handover.** One malformed value
   made the drain throw. The digest never reached `STATE.md`, it failed
   quietly, and it failed identically on every retry.
3. **Thread adoption wrote text to disk unredacted.** Adopting a stalled thread
   copied its notes into your project `STATE.md` with no redaction, and the
   autosave then committed that file into a git ref.

Also fixed: `off` reported success when a handover had actually failed; `adopt`
never closed its registry record, so digests duplicated and file claims were
never released; a race at the thread cap could leave an invisible orphan
record; the redactor masked only the first line of a private key and let the
key body through; and note files were world-readable.

---

## Known limits

Stated here rather than left for you to discover.

- The pre-write gate cannot see non-Python files, `pathlib.write_text`,
  `json.dump(fh)`, `print(file=fh)`, or a read-one-file-then-append-to-another
  shape. It stops the problem growing. It does not retire what already exists.
- Clash detection matches topic tags. Two records making incompatible decisions
  under different topic names will not be caught.
- Overlap detection still under-blocks on symlinks and on unicode paths that
  differ only by normalization form.
- `off` drains every active record regardless of lifetime. Correct today,
  because threads are the only producer. A test fails the moment that stops
  being true.
- `bm_threads.py` grew from 470 to 540 lines. The design intended it to shrink,
  because logic moved out to the registry. The defensive guards and honest
  error reporting added back more than the move removed.
- **None of this has run on a real project yet.** Every claim here rests on
  tests, adversarial review, and simulated lifecycles.

A review on 2026-08-08 decides whether ephemeral fences migrate into the
registry, the migration is deferred with a stated reason, or the design is
reverted for not having moved the signals it named.

---

## Verifying this release yourself

```bash
python3 tools/test_bm.py
```

102 tests, up from 12.

CI runs the same suite on Linux. One test in the first cut of this release was
platform dependent (it relied on Python overflowing its stack at a given JSON
nesting depth, which macOS did and Linux did not) and it failed the public
build. It is split into two tests that each assert something true on every
platform, and the stderr warning path now has deterministic coverage it did not
have before.

Then watch the central behavior work, in a throwaway directory:

```bash
python3 tools/bm_threads.py on
python3 tools/bm_threads.py start pay "wire the webhook" --files api/pay.py
python3 tools/bm_threads.py start pay2 "second writer" --files api/pay.py
```

The second claim is refused by name and creates nothing at all.

Then confirm secrets do not reach disk:

```bash
python3 tools/bm_threads.py checkpoint pay --decision "use key AKIAIOSFODNN7EXAMPLE" --topic auth
grep -r "AKIAIOSFODNN7EXAMPLE" threads/
```

The grep finds nothing. The registry holds `[REDACTED]` in its place.
