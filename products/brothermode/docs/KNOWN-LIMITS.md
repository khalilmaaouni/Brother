# Known limits, stated plainly (2026-07-26)

What this project does NOT do, has NOT proven, or has only partly checked. This
file exists because an unstated gap is a failure even when it is small, and because
the single most useful thing a handover can contain is the list of things the last
person was not sure about.

## The install shape people actually use loads NONE of this product's own hooks (measured 2026-08-15)

The largest gap on the list, and it makes several other entries here
academic, because a control that never loads cannot fail either.

MEASURED, on the founder's own machine, every line run rather than reasoned:

    ~/.claude/plugins/installed_plugins.json     68 plugins, brothermode ABSENT
                                                 (the sibling brothersbe IS present)
    ~/.claude/skills/brothermode/hooks/hooks.json declares SIX events:
                                                 SessionStart, SessionEnd, Stop,
                                                 PreCompact, PreToolUse, PostToolUse
    grep bm_fence_hook <every settings file>      ZERO hits
    ~/.claude/agents/                             one file, and it is not ours

BrotherMode is installed here as a DIRECTORY COPY under `~/.claude/skills/`.
It carries `hooks/hooks.json` and a plugin manifest, and nothing reads either,
because a skill copy is not a plugin install. Hooks are loaded from a settings
file or from a registered plugin, and this product appears in neither.

WHAT THAT MEANS, CONCRETELY. The one-writer fence hook is not running. During
the session that measured this, a write WAS refused across a live claim, and
that refusal came from `sbe_fence_hook.py`, the SIBLING's hook, which is a
registered plugin. The protection was real and it was not ours. Any document
of ours that credits the fence hook for a refusal observed on this machine is
crediting the wrong product, and one of ours did until this entry was written.

WHAT DOES RUN, so this entry is not read as "nothing works": the vault's own
session-start and session-end scripts, `spend_guard.py`, and
`bm_session_cap.py`, all wired individually in `~/.claude/settings.json`.
The session-start digest arrives through that path, not through our manifest.

WHAT IS NOT AFFECTED: every tool invoked directly on the command line
(`bm_store.py`, `bm_project.py`, `bm_idle.py`, `bm_escalate.py` and the rest)
runs exactly as documented, because a command is not a hook. The gate, the
suites and the queue checks are all unaffected. It is the AUTOMATIC half,
everything that was supposed to fire without being remembered, that is inert.

CORRECTED 2026-08-19, and the correction narrows this entry sharply. The
PACKAGING IS NOT THE PROBLEM and this product is not broken. Compared against
the sibling, whose hooks demonstrably fired on this machine tonight by
refusing a write by name:

    ours   .claude-plugin/plugin.json   declares NO hooks field, ships hooks/hooks.json
    theirs .claude-plugin/plugin.json   declares NO hooks field, ships hooks/hooks.json

Identical shape. `hooks/hooks.json` is discovered by convention, not declared
in the manifest, so a proper plugin install of this product WOULD load its
hooks exactly as the sibling's does. What differs is only how each arrived
here: the sibling is a plugin install, this product is a skill-directory copy.

So the honest statement is about THIS MACHINE'S INSTALL SHAPE, not about the
product. The supported install is documented in docs/SETUP.md and README.md:

    claude plugin marketplace add khalilmaaouni/BrotherModeUp@v3.3.1
    claude plugin install brothermode@brothermode-marketplace

ONE TRAP BEFORE ANYONE RUNS THAT, from docs/SETUP.md's own troubleshooting
table, row 6: with BOTH a plugin install and a clone install wired, every hook
fires TWICE, because both wire into the same settings.json. This machine
currently has the clone. Remove one first (`python3 scripts/uninstall.py` for
the clone, or `/plugin uninstall` for the plugin) rather than stacking them.

And one thing to know about the pin: `@v3.3.1` installs the tagged release, so
any fix landed after that tag is not in what gets installed until the tag
moves.

FILED as M1 in docs/plan/QUEUE.json. Not fixed here on purpose: registering
the plugin changes machine configuration that other live sessions depend on,
and a config change belongs to the founder and to a session that is not
sharing the machine with three others.

## rc.4 merge: what the merged tree does NOT prove

- **A gate corpus is not bounded by your limit, and since 2026-07-30 (Loop 3) its
  full TEXT is.** Every applicable gate is still returned at every limit,
  deliberately, so a limit can never hide a safety rule. What changed is that gates
  are no longer all printed in full: every applicable gate appears in a compact,
  deterministic, hashed manifest, and full text is expanded only when the trigger
  matches, the scope is narrow and matched, the query reaches the gate's own action
  text, or the caller names the gate by ID. Ambient expansions are capped at
  `GATE_EXPANSION_CAP = 5` per call; an explicit `--expand <id>` is never capped,
  because withholding a gate the caller named would be the same hiding this project
  refuses to do for `limit`. Measured on a 20-gate store: 5722 characters before,
  1849 after.

  Two honest costs. At ONE gate the manifest's fixed header and footer make the
  output slightly LARGER than the old behaviour (524 characters against 454); the
  saving only begins past roughly two gates. And `GATE_EXPANSION_CAP` is a hardcoded
  constant, tunable by neither flag nor environment variable, so a founder who wants
  a different bound has to edit the source.
- **The installer is verified on macOS only** (Python 3.9.6). It is
  stdlib-only and POSIX-path-only and nothing in it is macOS-specific, but no
  Linux run has happened, so Linux is expected-to-work rather than tested.
  Windows is refused, not supported; WSL works.
- **The installer adds and overwrites files; it never prunes.** A file deleted
  upstream since your last install stays behind after `--upgrade`.
  `scripts/verify-install.sh` reports exactly those as EXTRA. Deleting was
  rejected on purpose: an installer that removes files it did not put there is
  the failure mode that loop existed to prevent.
- **Hook ownership is decided by the command string naming this installation's
  path.** Move the install directory by hand without re-running the installer
  and the old entries are no longer recognized as ours; pass `--target <old
  path>` to remove them.
- **The pinned install command is checked, not self-updating.** Since 2026-07-30
  (Loop 1) README, QUICKSTART and SETUP carry a tag-pinned clone generated from
  `install_command_pinned` in `tools/bm_project_facts.py`, and a drift test refuses
  any page that disagrees with that fact or that clones a moving branch. What the
  test CANNOT do is edit the pages for you: cutting a new tag makes the suite go red
  until the generated command is re-pasted. That is deliberate, because a docs
  rewrite nobody read is how the stale command got there in the first place. It does
  mean a release cut is a two-step act, and the second step is enforced only by the
  gate.
- **`scripts/doctor.py` now runs TEN checks, not one, since 2026-08-01 (Loop
  3 design D-3, docs/superpowers/specs/2026-08-01-loop3-consent-install-
  design.md): fence liveness (check 1, unchanged), VERSION-vs-manifest
  version identity, python3 and git availability, consent config presence,
  vault path writability, plugin-vs-clone duplicate-hook detection, project
  store health (`bm_store.py verify`), hook wiring matched to the consented
  installation_mode, a CHECKSUMS.sha256 self-check, and settings.json
  validity.** Each prints PASS, FAIL with a one-sentence fix, or SKIP with
  the reason nothing could be checked yet; `--json` for machines. What was
  already true of check 1 alone still holds for the whole surface: it checks
  the settings FILE and the CODE, not whether Claude Code has actually
  LOADED any of it (hooks are read at session start, so a mid-session
  correction is live at the next session), and the fence hook still fails
  open by design (missing, empty, or corrupt store, or any internal error),
  so a green doctor is a statement about the files and the code right now,
  not a promise about every future run.
- **By default the fence refuses a write across ANOTHER active claim, and
  nothing more: an UNCLAIMED path is ALLOWED.** "Claim before you edit" is the
  working discipline this project asks for, not the guarantee the default
  ships, and the two are easy to read as one. `tools/bm_fence_hook.py` fails
  OPEN on every condition that stops it reaching a decision (no store, corrupt
  store, zero active claims, malformed payload, unimportable store module,
  underivable session identity): it allows the write and prints the reason to
  stderr, so a refusal always means a real ownership conflict rather than a
  broken hook. Two opt-in variables tighten that, both off by default and both
  documented in docs/HOOKS.md: `BM_FENCE_MODE=enforced` makes a failure to
  check refuse instead of allow, and `BM_FENCE_STRICT=1` additionally requires
  a claim before editing any project path. Making either one the default was
  considered and REJECTED (founder decision 2026-08-10): it would start
  refusing edits to unclaimed files for every existing user on their next
  update. Outside a BrotherMode project the hook reaches no decision at all,
  because it installs user-globally and fences a project rather than a
  machine. The Bash leg is a separate matter, covered by the entry below.
- **The fence covers Edit, Write, MultiEdit, NotebookEdit, and since L06
  (2026-08-06) exactly one Bash shape, for PREVENTION. Every other Bash write is
  DETECTED, since 2026-08-01 (Loop 6, D-1), and still not prevented.** The one
  prevented Bash shape is an apply_patch envelope (the form every Codex CLI file
  write takes): `tools/bm_fence_hook.py` now parses its file directives and
  refuses a foreign-fenced path before the command runs, proven in process
  against a captured Codex payload; a live wired Codex rehearsal has not run
  yet, so under Codex itself that enforcement stays UNVERIFIED until rehearsed.
  All other Bash writes (redirection, `sed -i`, `tee`, `git checkout`, inline
  interpreter scripts, any subprocess) still reach the filesystem without
  passing a hook that can refuse them: no reliable parse of arbitrary shell
  exists, so nothing blocks those the way an Edit or Write
  can be blocked. What changed on 2026-08-01 is that a fenced file changed BY a Bash call FROM a
  session that does not own that fence is no longer invisible: `tools/
  bm_bash_audit.py` snapshots every fenced, existing file before a Bash call and
  re-hashes it after, raising a high-severity fence-breach alert (through the
  store, requiring a human) when the hash changed and the acting session is not
  the owner. Detection, not prevention, stated in as many words in docs/HOOKS.md's
  own "Bash-write detection hook" section, which also states what it cannot see: a
  write that restores the original bytes before the check runs, a Bash call that
  removes its own snapshot along with the evidence, a concurrent process racing
  the same window, and a claim on a directory or glob that was never expanded into
  the files it covers. `scripts/bm_shell.py` still mitigates only the writes a
  caller chooses to declare: it is a declaration channel, not a sandbox, and its
  `--declare-none` screen is a short list of obvious write forms, not a shell
  parser. CORRECTED 2026-08-01 (loop6 refuter finding A1/A8c): this row used to say
  the new hook was wired into the plugin manifest (`hooks/hooks.json`) but NOT YET
  into `scripts/install.py`'s clone-install path, so a clone install got the fence
  hook only. That gap is closed: `scripts/install.py` now wires both the
  `PreToolUse` and `PostToolUse` halves of the Bash-audit pair too, on the same
  terms as the plugin manifest, so both install paths carry Bash-write detection.
  What is still true either way: the snapshot only covers a claimed path that
  resolves to a REAL, EXISTING FILE at the moment the Bash call starts, not a
  directory or glob-shaped claim expanded into the files it would cover.
  EXTENDED 2026-08-03 (closure item C-02), and this is the honest shape of
  what is and is not contained.
  WHAT IS CONTAINED: nothing, in the operating-system sense. No file this
  project writes is protected from a shell command by anything except a hook
  that Claude Code chooses to run.
  WHAT IS NOW REFUSED: with `BM_FENCE_MODE=enforced` set, AND ONLY WHEN THE
  BASH CALL'S CWD RESOLVES TO A BROTHERMODE PROJECT, a `Bash` command whose
  TEXT matches a small literal list of destructive forms (`rm`, `>`, `>>`,
  `tee`, `sed -i`, `truncate`, `mv`, `cp`, `chmod`, an inline `python3 -c`, a
  rewriting `git` subcommand, `find -delete`, and a few more) while also
  containing the literal string `.brothermode` or `store.sqlite3`, plus
  exactly two whole-directory forms that name nothing (`git clean` with
  `-x`, and `rm -r` aimed at `.` or `*`). The project check is load-bearing,
  not decoration: this hook installs at USER-GLOBAL scope
  (`~/.claude/settings.json`), so it runs on every Bash call in every Claude
  Code session on the machine, and without the project check enforced mode
  would refuse commands in every unrelated, non-BrotherMode directory too.
  Outside a BrotherMode project the refusal check is inert.
  THE DELIBERATE LIMIT THIS CREATES: when `tools/bm_store.py` cannot be
  imported at all, the project check itself cannot run, so nothing is
  refused, even under enforced mode, anywhere. That is a fail-open path
  inside a fail-closed feature, chosen on purpose: the only alternative is
  refusing every Bash command in every directory on the machine, which is
  not shippable. Someone who can break that import can therefore disable the
  refusal.
  WHAT IS NOW DETECTED, in both modes and in every BrotherMode project: the
  store file disappearing, becoming zero bytes, or ceasing to begin with the
  SQLite file header, and any session token file disappearing, between the
  start and the end of a Bash call. Each one prints a sentence on stderr and
  raises a high-severity `fence-control-loss` alert; when the store is the
  thing that went missing the alert cannot be written and the hook says so
  rather than falling silent.
  WHAT IS NOT CAUGHT, stated in full because a partial check presented as a
  complete one is the failure this file exists to prevent: a path assembled
  at runtime or held in a variable; a destructive command inside a script
  file, a Makefile target, or any program the command merely starts; any form
  not on the list; a write that returns a fenced file to its original bytes
  before the check runs; a Bash call that deletes its own snapshot; every
  write by a process that never passed through a hook at all (a second
  terminal, an editor, a background job); and, as stated above, anything at
  all once `tools/bm_store.py` cannot be imported. A project with NO active
  claim is not snapshotted at all, so nothing is detected there either,
  though enforced mode still refuses. And the refusal over-refuses by
  design, inside a BrotherMode project: `ls .brothermode > /tmp/x` is
  refused, and so is `git clean -xfd` anywhere in the tree. Full
  operating-system containment (a sandbox profile, a container, a FUSE write
  mediator) was considered and is explicitly OUT of scope: it sits outside
  "Python 3.9, standard library only" and would be a second product rather
  than a fix.
- **OBSERVED GREEN 2026-07-31, on all nine jobs.** This entry used to say no real
  Actions run had ever been observed. Run `30564943060` for commit `f751f9f`
  concluded success across the serial `gate` job, both `suite` legs, and all six
  `store` legs (three platforms, both Python versions). Getting there required
  fixing a real defect the Windows legs had been failing on: `invocation()` quoted
  user-facing commands with POSIX-only `shlex.quote`, so a Windows path came back
  single-quoted and the printed remedy was unrunnable in cmd and PowerShell.
  What is still NOT proven: the failure-artifact upload path, which needs a
  deliberately broken build on a temporary branch; and that green stays green,
  since the Windows fix was verified by CI rather than on a Windows machine here.
  Full per-job table: the current release evidence file in `docs/evidence/`
  (the rc.6 file this line used to cite was renamed forward at each
  release cut; `docs/evidence/RELEASE-CANDIDATE-2.0.0-rc.9.md` carries it now).
- **The remaining CI-equivalence gap.** Every refusal in `tools/test_all.py` was
  proven by running it on one machine. The first push to a branch CI watches is what turns
  that from designed to demonstrated.
- **The fence hook suite runs in CI on Linux and macOS only.** It contains
  deliberate win32 skips, so it is written to be Windows-aware, but it has never
  been run on Windows. The store and recovery suites do cover Windows.
- **The documentation consistency suite checks the pages listed in
  `tools/test_bm_docs.py` ACTIVE_DOCS.** Everything else, including
  `docs/DESIGN.md`, `docs/WHITEPAPER.md`, `docs/OBSIDIAN.md`, `docs/SUNSET.md`,
  `docs/REMAINING.md`, the PDFs and the one-pager HTML source, is unchecked. The
  suite catches stale FACTS it can generate, not a stale claim written in prose.
- **Path masking, narrowed 2026-07-31 (Loop 5) but not closed.** Quoted paths,
  escaped spaces, Windows drive paths, UNC paths, Unicode segments, multiple paths
  in one field, and paths glued to a word at the six known roots (`Users`, `home`,
  `root`, `Volumes`, `private`, `cygdrive`) now mask correctly. Still true: an
  UNQUOTED path containing a bare space masks only up to the space, and a
  single-letter Windows drive glued to a word stays unmasked, deliberately,
  because masking it provably swallows `https://` URLs. The known-root list can
  also OVER-mask a relative path that happens to share one of those six segment
  names. Withheld columns are unaffected: they reproduce nothing at all.
- **Session labels are gated by an allowlist of generated shapes, not by a schema
  split.** Since 2026-07-31 (Loop 5) only the four real generated id shapes
  export; any hand-typed label, hyphenated or not, is withheld. This is
  deliberately NOT the plan's literal design (a separate internal session UUID
  column beside an optional human label); that split remains open as a schema-12
  candidate, recorded in `docs/NOT-FINALIZED.md`.
- **An approved rule's own `because_text` appears in `applications` output, and
  that is settled.** Founder ruling 2026-07-31: it is a feature, not a leak. The
  founder wrote it, explicitly promoted it into a rule, and retrieval exists to
  show rules with their reasons, so `applications` keeps explaining WHY a rule
  applied. Recorded here so the canary suite's "rule reason" field is read as
  "must not leak from surfaces that withhold founder prose", not as "must never
  render anywhere".
- **Windows owner-only file modes and ACLs.** Nothing in this project configures
  a Windows ACL. Stdlib only, with no subprocess in the shipping tools, rules
  out both `icacls` and `pywin32`, so `os.chmod` is best-effort there and the
  real control is your user profile. No Windows behavior is proven by these
  suites.
- **BrotherMode's own hooks are verified in Claude Code only.**
  `docs/RUNTIMES.md` reports whether each runtime has hook points at all; that
  is not the same claim as "BrotherMode's hooks run there". No runtime was
  driven end to end: the instruction-file conventions are documentation-verified,
  not behavior-verified.
- **The packaged modules install at the top level of site-packages**, and a
  package install wires no hooks. Building with the macOS system pip (21.2.4)
  silently produces an empty `UNKNOWN-0.0.0` wheel; build with `uv build` or a
  pip new enough to read PEP 621 metadata.
- **The public benchmark has been measured on ONE machine and ONE platform**
  (macOS, Python 3.9.6, 2026-07-29) and is not in CI. Scenarios 1, 2, 3 and 6
  exercise lexical word-overlap retrieval, which names itself `mode=lexical`; a
  green benchmark is not evidence of semantic retrieval, which this project does
  not have.
- **README's "verify the safety claims yourself" grep is stale.** As published
  it greps for the bare words http, socket, curl and wget across `tools/*.py`
  and `tools/*.sh` and says "Expected: no output", but it returns hits on
  documentation URLs inside comments. The underlying claim still holds, and an
  import-scoped grep proves it:
  `grep -rnE "import (urllib|socket|http|ftplib|smtplib|requests)" tools/*.py |
  grep -v "^tools/test_"` returns nothing. `docs/beta/BETA-KIT.md` ships the
  corrected grep.

## Session identity is harder to forge, not unforgeable

MOVED HERE 2026-08-04 (N-5 bucket 3,
docs/closure/reports/2026-08-04-N-5-open-defect-triage.md row 15), from
`docs/NOT-FINALIZED.md` item 3, an honest limit code cannot fix rather than
backlog work. The entry it replaces there stays in place, pointing here,
per this project's rule against editing a dated entry to agree with today.

Was: the owning value was printed in plaintext into the file every session
reads, so the ownership check compared a public value against itself.

Now: a per-session secret, stored owner-only, with only a hash on the
claim. Copying the label from `STATE.md` gains an attacker nothing
(proven).

Still: any process running as your user can read the token file and
impersonate fully. Perfect unforgeability is not reachable on one machine,
one user, no network. Documented in `docs/HOOKS.md` rather than overclaimed;
nothing further for code to do here.

## P7: what the optional search index does NOT do

- IT IS ENGLISH STEMMING. The tokenizer is `porter unicode61`. unicode61 folds
  case and accents, so French and Japanese text is stored and searchable, but
  porter stems ENGLISH only: "pousser" and "poussé" do not stem together, and
  Japanese has no word boundaries for it to find, so a Japanese query matches on
  whole runs of text or not at all. The fast path is therefore a real gain in
  English and roughly neutral elsewhere. Nothing in the output claims otherwise.
- IT IS NOT SEMANTIC. BM25 is term frequency arithmetic. There are no
  embeddings, and a task that shares no words with a rule still finds nothing
  unless the rule is a gate. That is a deliberate limit of this loop.
- DRIFT IS ONLY CHECKED WHEN THE INDEX IS ON. With `BROTHERMODE_FTS5` unset,
  `verify` says so in its note and compares nothing.
  CORRECTED 2026-07-29: this entry used to add that a stale index left by a
  previous session is "a reporting limit rather than a correctness one". That
  was wrong, and the fix round proved it on the real CLI. The switch is a
  per-process variable, so one ordinary shell that approved or edited a rule
  left the index behind with no error anywhere, and the next run with the fast
  path on consumed the stale index: a rule came back for a task its current text
  shares no word with, matched on text the founder had deleted, and the CLI
  explained the hit as a stem match no live word could produce. The index is now
  reconciled against the rules at the moment it is CONSUMED, so it cannot answer
  a query while it disagrees with them.
- THE INDEX IS RECONCILED BEFORE IT IS READ, NOT ON OPEN. It is built and filled
  the first time a store is opened with the fast path on, and maintained inside
  the same transaction as every approval and edit made by a connection that has
  the fast path on. Because another process can write between your open and your
  query, the check that matters runs at retrieval: an index that disagrees with
  the rules is rewritten from them first, and if it cannot be rewritten (another
  writer holds the lock, or the caller has no write authority) the fast path
  switches off and the run reports `mode=lexical`. `verify` and `index-status`
  still SHOW drift rather than repairing it, which is what keeps them worth
  running, and `rebuild-index` is still the explicit repair.
- THE INDEX CANNOT TAKE THE STORE WITH IT. Until 2026-07-29 it could: index SQL
  ran through the same routing point as everything else, and that point reads
  "no such table" as structural damage and quarantines the database file. An
  index table dropped mid-session therefore cost the whole store, the approval
  in flight, and every rule in it, and the status command did the same thing
  while claiming to be read only. Index statements now have their own routing
  point with no quarantine in it, so the worst an unreadable index can do is
  turn the fast path off.
- FORGOTTEN RULES STAY INDEXED. The index mirrors the current version of every
  rule row, including forgotten ones, exactly as `learning_rule_versions` does.
  They can never be retrieved (the state filter runs before ranking), so this
  costs index size, not privacy: the same text is in the versions table either
  way. Un-indexing them is not done.
- THE MEASURED GAIN RESTS ON ONE LABELLED FIXTURE. COMPLETED HERE 2026-08-04
  (N-5 bucket 3, docs/closure/reports/2026-08-04-N-5-open-defect-triage.md
  row 5, completing what N-5 called the partial coverage of this claim by
  the public-benchmark caveat above). `docs/NOT-FINALIZED.md`'s entry on
  this stays in place, pointing here. The improvement claim rests on the
  stemming case, reproduced on the real CLI both ways round: a rule written
  about "pushing", a task that says "pushed", found under fts5 and not
  under lexical. That is a demonstration, not a benchmark. No labelled
  corpus of founder rules with graded relevance exists yet, so "FTS5
  improves measured retrieval" is true of the fixture and unproven at
  scale. Proving the gain at scale needs a labelled corpus, which is data
  to gather, not code to write; the register's X-03 item names the same
  gap.

## P6: what the retrieval run still cannot tell you

The run row makes the denominator a stored fact, and these things are still out
of its reach. Stated here rather than discovered later.

- ONE RUN IS THE AUTHORITY PER TASK. When the same task text is retrieved more
  than once in one session with DIFFERENT limits or contexts, the earliest run
  is used and `retrieval_runs` reports how many there were. Earliest now means
  insertion order, so it is the same run on every run of the classifier (fix
  round P6 closed a random uuid tie-break on the one-second timestamp). The
  later runs are still not merged and not compared. A reader can see the
  ambiguity; nothing resolves it.
- THE CORPUS CHECK COUNTS, IT DOES NOT IDENTIFY (fix round P6). A task is graded
  only when the rules that existed at retrieval time still number exactly what
  the run recorded as eligible; otherwise it is `corpus_changed_since_retrieval`
  and nothing is attributed. The run stores HOW MANY rules were eligible, not
  WHICH, so a removal and an addition that exactly cancel out still read as
  equal and the task is graded against a corpus that is not the one that ran.
  Narrower than the old behaviour, which re-ranked today's rules unconditionally,
  and not the same thing as reconstruction.
- A RELEVANCE MISS NOW MEANS ONE THING: the rule was eligible, ranked inside the
  requested limit, and the ledger holds no application row for that run. With
  the corpus pinned, that is a bookkeeping hole rather than a ranking verdict.
  Ranking quality itself is not measured by this number.
- MIXED-ERA TASKS ARE NOT SPECIAL-CASED. A task with both legacy application
  rows (no run) and a run row counts only the run's own rows as surfaced. The
  corpus count usually refuses such a task anyway, but that is a consequence,
  not a check.
- TIMESTAMPS ARE WHOLE SECONDS. A rule rewritten inside the same second as the
  retrieval reads as unchanged. Nothing in this design can separate them.
- LEGACY ROWS STAY LEGACY. An application recorded before schema 4 has no run,
  is reported as incomplete evidence, and is never backfilled. The misses those
  tasks had are not zero; they are unknown, and the reports say so.

## P5-fix: without `--record`, two units of work still cannot be told apart

The idempotence key now includes the work record, so naming one with `--record`
gives each unit of work its own application row. WITHOUT `--record` there is
still nothing to key on beyond the task fingerprint, which comes from the query
alone. Two different pieces of substantial work in one session phrased the same
way therefore still land on one row. That is now DISCLOSED rather than hidden:
`apply` prints which work record the row it found already belongs to and says it
cannot tell a re-read from different work. It is disclosure, not detection, and
the exit code is still 0. The founder has to act on the note.

Also not closed by this round: nothing forces `--record` to be passed at all,
and nothing verifies that the work record named is the work actually being done.
A caller can pass a real but wrong work-record id and the row will link to it.

## P5-fix: one pre-existing unrelated test is flaky on this machine

RESOLVED 2026-07-29 by Loop P12, which deleted the test rather than stabilised
it. `test_bm.py`
`TestFinding12HandoverDeliveryIsSerializedAndVerified.test_calibrated_without_the_lock_the_same_pair_duplicates_the_handover`
failed roughly 4 runs in 5 with "the reproduction did not reproduce": a timing
race in a CALIBRATION test for the handover lock, not in the guard it
calibrated. The lock and the append it guarded no longer exist, so neither does
the test. What replaced it is a concurrency test that races two parks through
the store's own transaction, which has no barrier to time out.

## Loop P5 left the older documentation naming the deprecated verb

`SKILL.md` now names `bm_learn.py apply --session` as the substantial-work path,
and a test enforces that. The narrative docs were NOT swept in the same change:
`docs/CORRECTION-LEARNING.md`, `docs/HANDOVER-2026-07-29.md` and the plan under
`docs/superpowers/plans/` still show `relevant`. Those examples still WORK, since
`relevant` is a live alias, but they teach the deprecated verb and a reader
following them would take the read-only path for work that should be recorded.
The law file is the one an agent follows, so this is a documentation debt rather
than a live hole, and it is scheduled for the Documentation Agent's pass.

Also unproven at P5: nothing forces an agent to run `apply` at all. The command
contract makes the recorded path the obvious one and removes the forgettable
flag; it does not detect a substantial task that skipped retrieval entirely.
`should-retrieve` answers that question only when someone asks it.

## The biggest one, updated: the engine IS connected now, and that surfaced new defects

Phase 3 landed 2026-07-26: `tools/bm_store.py` is now imported by
`bm_autosave.py`, `bm_sessionstart.sh`, `bm_telemetry.py`, and `bm_threads.py`
(43 references across those four files, measured the same day), and
`bm_registry.py`, the old JSON registry, is deleted rather than shimmed. The
defects the original audit found in that registry (silent name takeover, two
registries minted for one project, a truncated fingerprint dropping a
handover) go away with the file.

They were replaced by five real defects in the rewired thread commands,
written up in `docs/superpowers/specs/2026-07-26-release-blockers.md`. This
project's code changes fast: of the five, four were already fixed by the
time this file was corrected, in the same session, some within the hour.
Each line states what was found, and what direct re-execution just before
this edit actually showed:

- **Recovered work was world-readable. FIXED, re-verified 2026-07-26.**
  `bm_autosave.py recover` used to leave its worktree `drwxr-xr-x` with
  `-rw-r--r--` files inside (reproduced independently on this machine's own
  macOS `/tmp`, not only the Linux case that found it first). Re-run just
  now: the recovered directory comes back `drwx------` and the tool prints
  the mode it achieved.
- **The reversibility promise was broken. FIXED, re-verified 2026-07-26.**
  Turning thread mode off and then resuming a thread from a different
  session used to be refused with `not-owner`, breaking the founder's
  ratified requirement that thread mode be reversible mid-project with
  every thread resumable. Re-run just now: `resume` from a different
  session on a parked thread succeeds and transfers ownership.
- **`verify` reported a false problem after any thread command. FIXED,
  re-verified 2026-07-26.** Used to report "1 problem(s) found" on an
  otherwise healthy project and name an unresolvable relative path. Re-run
  just now: `verify` reports "healthy, 0 problem(s)" after a thread `off`.
- **Neither CLI validated flag names. FIXED, re-verified 2026-07-26.**
  `start X --file f` (the wrong, singular flag) used to be accepted at exit
  0 with no fence. Re-run just now: both `bm_store.py` and `bm_threads.py`
  refuse an unrecognized flag by name at exit 2, for `start` and for
  `checkpoint`.
- **A refused adoption attempt wrote its handover into `STATE.md`. FIXED,
  re-verified 2026-07-26 by the orchestrator after this file first recorded
  it as open.** The delivery write happened before the ownership check, so a
  refusal still recorded a live thread as "Adopted from dead/stalled thread".
  The order is now transition first, deliver second. Re-run just now: a
  different session attempting `adopt` without the override exits 2, the
  checksum of `STATE.md` is IDENTICAL before and after, and the false block
  appears zero times. This entry is kept rather than deleted because the
  sequence, found open and then closed within the same session, is the
  clearest example of why this file states dates and re-verification rather
  than conclusions.

Practical consequence: all five defects the rewire introduced are now closed
and each was re-verified by direct execution rather than accepted from a
report. This project's code changes fast; re-run the
reproduction steps in the release-blockers spec yourself rather than trust
this file's dates once more time has passed. The general operating
restrictions from the original audit still apply: run commands from the
repository root, avoid glob fences, and never restore an autosave snapshot
in place without inspecting it first in a separate worktree. The audit's
further restriction against running two worktrees of one repo in parallel
sessions was lifted in the finalization run of 2026-08-08: the fence now
resolves a claim and a write to the same logical file across a worktree
boundary (the H1 fix, bm_store.strip_worktree_segments), which was the
whole reason for that restriction.

## Used for real, but never MEASURED on a real project

CORRECTED 2026-08-03, and this correction runs in the opposite direction from
most on this page: the limit was overstated, not understated. Every earlier
version said flatly that nothing here had ever run on a real project and that
everything rested on tests, adversarial execution and simulated lifecycles.
That was true of the RECORD and false of the world. The founder reports weeks
of his own daily use, plus other people using it on their own machines,
installed by pointing them at this repository.

What remains true, and is the actual limit: none of that use was MEASURED.
There is no counted set of projects, no recorded failure and rework rate, no
comparison against working without the tool, and no outside participant whose
experience was observed rather than reported. So the honest statement is that
real use exists and graded outcomes do not, and a score that wants the second
cannot borrow it from the first.

The older, stronger claim survives in the dated documents that made it
(docs/SCORECARD-2026-07-27.md, docs/ACCOUNTING-2026-07-26.md). Those were
accurate on their dates and are deliberately NOT rewritten: editing a dated
record to match today is the failure this project refuses on principle.

## Continuous integration HAS executed, and it FAILED on Windows

CORRECTED 2026-07-26. An earlier version of this file said continuous integration
had never executed. That was FALSE and it was never checked: the workflow has run
18 times, three of them on branch v2, and the record is public in the repository's
Actions tab. Assuming instead of looking, in a project whose whole point is not
doing that, is worth recording rather than quietly fixing.

The result on the tagged release commit (run 18, commit 7c2e0ec) is FAILURE. The
job `store (windows-latest, 3.x)` exited 1; the other matrix legs were cancelled by
fail-fast, so the Windows 3.9 leg and one macOS leg remain UNKNOWN rather than
passing.

## Recovered work is owner-only on POSIX ONLY, not on Windows

Found 2026-07-27 by putting the recovery suite into CI for the first time (audit
finding 14). It failed on both Windows legs and passed on all four POSIX legs.

The guarantee "a recovered worktree, and any private untracked file inside it, is
readable only by you" rests on a POSIX file mode of 0700. Windows governs access
by ACLs instead, and there `os.chmod` can only toggle a read-only bit. So on
Windows the recovered directory inherits whatever the parent temp directory
grants, and this project does not currently set an ACL to narrow it.

What this does and does not mean:

- The tool does NOT lie about it. It prints the mode it actually achieved rather
  than the mode it wanted, so a Windows user sees the real value.
- The POSIX guarantee is unchanged and still asserted at full strength.
- A Windows user on a shared machine should treat recovered work as readable by
  other local accounts until this is closed with a real ACL call.

Not fixed yet because doing it properly needs a Windows ACL API rather than a
weaker assertion, and a security property is worth stating honestly while it is
missing rather than quietly skipping the test that revealed it.

## Windows was BROKEN, was fixed, and is now GREEN (the full arc, kept on purpose)

CORRECTED 2026-07-26. An earlier version said Windows was "designed for, not
proven". The stronger truth: it failed. Verbatim from the run:

    PermissionError: [WinError 32] The process cannot access the file because it is
    being used by another process: '...\.brothermode\store.sqlite3'

Cause: sqlite connections were opened and never closed. POSIX allows deleting a
file that still has an open handle, so every macOS and Linux leg passed and the
leak was invisible; Windows refuses, which is the only reason it surfaced. That
makes it a real API gap on every platform (a long-lived process leaked a handle per
store) that only one platform reported.

RESOLVED 2026-07-27, CI run on commit ba4eca2: all eight jobs pass, including
BOTH Windows legs (3.x and 3.9). This is the first green Windows run this
project has ever had. The fix was an idempotent close() plus context-manager
support on Store and ReadOnlyStore, twelve call sites closed, and four
Windows-only test bugs that only surfaced once the suite got far enough to run:
a mock-call string comparison that repr-doubled backslashes, a write handle
opened on a memory-mapped -shm file, and a deliberate locker connection that was
rolled back but never closed.

The arc is kept here rather than collapsed into "Windows works", because the
useful part is not the outcome. The founder OVERRODE a recommendation to declare
Windows unsupported and required it as scope. That override is the only reason
any of this was found: the defect was a real API gap on every platform (a
long-lived process leaked a database handle per store) that POSIX silently
tolerates. Narrowing the supported platforms would have hidden it, not avoided
it.

What is now guarded mechanically: every test that opens a store and does not
close it FAILS, on every platform, naming the test. That check was calibrated in
both directions before it was trusted, and enabling it immediately found ten
further undisciplined sites. Two earlier attempts at that check were discarded
for being incapable of failing, which is written up in the commit.
Symlink and hardlink tests skip on Windows entirely. Read-only database behavior,
file permission semantics, and the worktree layout are unverified there.

## Paths exercised only partly

- The backup that should be written before a status file is rewritten has not been
  exercised, because in every test the tool correctly refused before reaching it.
  Refusing is the safer behavior, but the backup code path is unproven.
- The `deliveries` table and the full-length handover fingerprint shipped with no
  writer. RESOLVED 2026-07-29 (Loop P12): `deliveries` was deleted earlier as
  decoration, and the fingerprint now has a real writer. It is the dedupe behind
  the unique index on the `handovers` table, which is what stops a retried
  handover being stored or rendered twice. CORRECTED the same day (P12 fix
  round): that index was UNIQUE(lifecycle_uuid, payload_fingerprint) over every
  row for all time, and a key that wide does not deduplicate handovers, it
  deletes them. The fingerprint covers the objective, files, owner, tier, check,
  evidence, latest digest and decisions, and NOT the state, version, transition,
  heading or sessions, so a second park after an acknowledged one wrote nothing
  at all. Schema 6 makes it UNIQUE(lifecycle_uuid, payload_fingerprint, heading)
  WHERE delivered_at IS NULL. What that still means, stated rather than hidden:
  two parks with the SAME heading while the first is unacknowledged remain one
  row, so the second transition has no handover row of its own. The founder sees
  the same text either way; the one-transition-one-handover link is exact only
  up to identical text.
- `send()` takes no expected version, making directives the one mutation without
  optimistic concurrency. Phase 3 owns the directive experience.

## The test suite's honest shape

Eight rounds of adversarial review plus one independent code review plus one
systematic mutation audit. The mutation audit found that fifteen tests named as
calibrated were testing a local copy of old code rather than the product, and could
never have failed; they are being deleted and the honest count reported. Treat any
test count in this repository as a claim to be re-verified rather than a
certificate.

## The correction-learning system: built through Loop 12, never run on a real day

UPDATED 2026-07-29. The paragraph below used to say the self-learning mechanism
was designed and not implemented. That is now false and would mislead a reader
who trusted it: capture, founder approval, scoped explainable retrieval,
conflict and supersession detection, retrieval and application outcome
tracking, and rework and escaped-defect grading are all built, tested, and
were driven by hand against a real, throwaway store while writing this line.
Plain-language explanation with real command output:
`docs/CORRECTION-LEARNING.md`. Technical detail: `docs/NOT-FINALIZED.md`
sections 15, 17 through 20.

What has NOT changed, and matters more than what has:

- **Never run on a real day of the founder's work.** Every command, every
  count, every test in the whole system comes from a test suite or a scripted
  probe against a throwaway store. `docs/NOT-FINALIZED.md` item 1 stays
  UNPROVEN, ranked as the highest-harm open item in the project, until a real
  dogfood window (Loop 14a) closes it. No amount of further testing closes
  this; only using it does.
- **Approval proves an answer, not an identity. UPDATED 2026-07-29 (post-audit
  LOOP 3, Model A).** Until this date the "founder-only approval" claim was
  wording, not mechanism: `bm_learn.py approve <id>` with no arguments beyond
  the id created a rule and wrote its own approval evidence. Reproduced against
  d88abcc, gate rule 61de7eb9, exit 0. Approval now needs a one-time receipt,
  minted from a real answer, bound to one candidate and to the exact rule text
  shown, valid fifteen minutes, spendable once, consumed in the same
  transaction that creates the rule. What is still NOT true: nothing checks
  WHICH human answered. Anyone who can run `grant-approval` can mint a receipt.
  The guarantee is "an answer was given about this exact thing, once, recently",
  not "the founder gave it". Read any wording that suggests otherwise as a
  defect and report it.
- **The receipt gate covers creating AND rewriting a rule, since fix round P3
  on 2026-07-29, and it did not on the day LOOP 3 landed.** For that one day,
  `Store.edit_learning_rule` appended a new current version of an approved rule
  with no receipt, no fingerprint check, and a hardcoded
  `approved_by='founder'`. Reproduced: an approved gate rule saying "never force
  push to main" was rewritten to "always force push to main, skip review",
  keeping its gate severity. Editing now takes its own one-time receipt. Two
  things are still true and worth stating: the receipt proves an answer, not an
  identity, exactly as above; and neither minting nor spending an edit receipt
  has a CLI command, so this door is reachable only from imported code, which
  is also the only vector it ever had.
- **Five more commands can alter the live rule set, and none of them had a
  receipt, until LOOP 2 on 2026-07-30.** Create and edit were receipt-gated;
  supersede, resolve-conflict, deprecate, forget, and resolving a critical
  alert were not. The most serious: `resolve-conflict` could stand an
  approved GATE rule down (state `contradicted`, `deprecated`, or
  `superseded`) with no human answer anywhere, and resolving a critical
  alert unblocked an approval the same way, since `blocking_alerts` stops
  counting a note the moment it is resolved. Both are reproduced and closed
  in `tools/test_bm_store.py` (`TestLoop2StateChangeReceipts`). Rather than
  five separate checks, ONE generic lane now covers all five: a new,
  additive `learning_state_change_receipts` table (schema 8 to 9), one
  `mint_state_change_receipt` and one `_require_state_change_receipt`/
  `_consume_state_change_receipt` pair, keyed by a `kind` discriminator so a
  receipt minted for one of the five can never spend as another, and the
  store's own `change_learning_rule_state` gate is now UNCONDITIONAL for
  supersede/deprecate/forget: a caller cannot opt out by simply omitting the
  receipt kind, which closes the direct-Python-import vector the same way
  approval and edit already were closed. An ordinary note, and a
  non-critical alert, still resolve with no receipt, exactly as before:
  only a critical alert (kind `alert`, severity `critical`) is gated.
  `bm_learn.py capture` with no arguments also used to store an EMPTY
  candidate instead of printing usage (item D6 of the Loop 0 sweep); it now
  refuses at the CLI layer, leaving `capture_learning_candidate` itself
  untouched. And the forged-token test's mutation
  (`tools/test_bm_store.py`) was probabilistic, flaking roughly one run in
  sixteen when the real token's last character happened to be `0`; it is
  now a guaranteed-different substitution.
- **A result limit caps soft rules only, since loop P4 on 2026-07-29, and it
  capped gates too before that.** Reproduced on the real CLI against 05441e7:
  two live global rules, a gate whose trigger shared no vocabulary with the
  task, `--limit 1`, and the gate never appeared. The relevance floor already
  exempted gates from being filtered for irrelevance, and the slice then cut
  them anyway, which made the exemption decorative. `retrieve_learning_rules`
  now splits eligible rules into gates and soft, returns every applicable live
  gate regardless of the limit, and applies the limit to soft rules only.
  Ranking is unchanged, so a gate does not jump the queue, it simply cannot be
  cut from it. `--limit 0` and negative limits both mean "gates only". What is
  still NOT claimed: applicability is scope plus lifecycle state, so a gate
  scoped to a project you are not in, or one you deprecated or forgot, is
  correctly absent, and a gate you never approved was never a gate. Relevance
  is still lexical word matching, so the ranking a gate sits at inside the
  result can still be poor even though its presence is guaranteed. And the
  numbers are checkable rather than trusted: `gates_returned` is counted off
  the rows actually returned, `gates_total` off the applicable set, and a test
  fails if they ever differ.

  Amended the same day by fix round P4-fix, because the loop shipped the
  guarantee and half the disclosure. The two sentences were printed at the
  bottom of `relevant`, and the zero-result branch returns before reaching
  them, so `--limit 0` and `--limit -1` in a store holding one matching soft
  rule printed "none matched" and no omission count while the JSON from the
  identical call reported `omitted: 1`. No gate was hidden by this, since an
  empty result means `gates_total` was zero, but the founder was told all the
  rules had been considered when one had been cut. The footer is now one
  function called from every human-output path, and the zero-result line names
  which of its two causes applied. What is still NOT claimed: the count of
  rules "in scope" on that line is scope and lifecycle eligibility, not a
  promise that each of them is a good match, and "none matched" remains a
  statement about lexical overlap, which is the only relevance this tool has.
- **A digest in the database is not a promise, it is a delay.**
  `founder_response_hash` is an unsalted sha256 of a short answer. Until fix
  round P3 it was printed verbatim by `dump`, and a ten-word wordlist recovered
  the answer. `dump` now withholds digests, but the column is still an unsalted
  hash of a low-entropy string, and `--raw` and the database file itself expose
  it. `SECURITY.md` already treats the file as sensitive; treat that column as
  the answer itself, not as protection.
- **Rules approved before 2026-07-29 carry the old, weaker provenance.** The
  schema 2 to 3 migration deliberately does not annotate or invalidate them.
  If a store predates receipts, its existing rules were approved under the
  mechanism described above as broken, and their approval evidence should be
  read that way.
- **No independent second-model review.** `docs/NOT-FINALIZED.md` item 12
  stays open. The privacy and security fixes that landed in Loop 12 (the
  secret scrubber and the withholding of raw founder text, described in item
  15) were written and verified by the same model family that built the
  feature. That is real evidence, but it is not the independent adversarial
  pass item 12 asks for.
- **Two loops of the program were deliberately not built.** Evaluation
  partitions (Loop 9) and generated knowledge views (Loop 10) are both
  unbuilt on purpose, with stated reopening conditions, because at the
  current size of one founder's rule corpus (twenty to forty rules) neither
  would produce a number large enough to support a decision. Building the
  machinery anyway would make an unsupportable number look rigorous, which
  is the exact failure this program's own principle forbids. See
  `docs/CORRECTION-LEARNING.md` for the reopening conditions.
- **The optional automatic-retrieval hook (Loop 11B) is gated on the dogfood
  window, not built yet.** The skill-driven retrieval that already ships
  (Stage A, Loop 11A) has to prove itself in real use first. A hook that
  pushes the wrong rule into every prompt is worse than the opt-in retrieval
  this project ships today.
- **The old weekly scorecard's unflattering audit is still true where it was
  never addressed.**
  `docs/superpowers/specs/2026-07-26-self-learning-redesign.md` found a
  hardcoded metric that could never move, five of nine scored metrics with no
  mechanical number, thirteen law amendments against one weekly review, and
  ratings the graded party could write itself. The correction-learning system
  now gives several of those metrics a real, row-backed number
  (`bm_learn.py loop-failures`, `bm_learn.py rule-outcomes`, see
  `docs/CORRECTION-LEARNING.md`'s scorecard section), but `RUBRIC.md` itself
  is a founder-frozen template that changes only by the founder's own
  decision, so it has not been rewritten here. Do not cite the OLD scorecard
  metric 1 as evidence of anything until the founder ratifies pointing it at
  the new commands.

## What was checked by class rather than individually

The original external audit contained 63 findings. Twenty-two were reproduced by
execution. The remainder were triaged into phases by class rather than each being
re-proven. If one of them matters to a decision, re-verify it rather than trusting
this file.

## Orchestration practice did not improve, only the outcome did

MOVED HERE 2026-08-04 (N-5 bucket 3,
docs/closure/reports/2026-08-04-N-5-open-defect-triage.md row 25), from
`docs/NOT-FINALIZED.md` item 13. A process observation, not a code defect,
so there is no fix to build, only practice to change: `docs/NOT-FINALIZED.md`
stays in place with this pointer, per this project's rule against editing a
dated entry to agree with today.

Fences were written AFTER dispatch three times. Two agents were given the
same file under an "add-only" fence, which is not a safe concurrency
primitive on a text file. No collision resulted, because the write sets
happened to be disjoint. Scored flat rather than up, because scoring a
lucky outcome is how a scorecard becomes flattery.

## The two oldest published release tags are lightweight, not annotated

MOVED HERE 2026-08-04 (N-5 bucket 3,
docs/closure/reports/2026-08-04-N-5-open-defect-triage.md row 37), from
`docs/NOT-FINALIZED.md` item 23. Not closable by design: fixing it means
force-updating two already-published refs, which this project refuses to
do on its own initiative, so it belongs here as a standing limit rather than
in a backlog. `docs/NOT-FINALIZED.md` stays in place with this pointer, per
this project's rule against editing a dated entry to agree with today.

`docs/RELEASE.md` requires release tags to be annotated ("annotated, not
lightweight, as the steps below require"), and the two OLDEST published
tags do not meet that rule:

    remote v2.0.0-rc.1 -> 7c2e0ec   (no ^{} line: LIGHTWEIGHT)
    local  v2.0.0-rc.1 -> tag object ea0ca74 -> commit 7c2e0ec  (ANNOTATED)
    remote v2.0.0-rc.2 -> 2aef6a4   (no ^{} line: LIGHTWEIGHT)
    local  v2.0.0-rc.2 -> tag object 09224c7 -> commit 2aef6a4  (ANNOTATED)

Both names resolve to the SAME COMMIT on both sides; the local copies carry
more information (an annotation object) than the remote does, so re-pointing
the local tags to match the remote would lose information for nothing, and
`git fetch --tags` already refuses that with "would clobber existing tag".
`rc.4`, `rc.6` and `rc.7` are all annotated on the remote (each shows a
`^{}` line). Impact is low: `rc.1` is withdrawn and `rc.2` superseded, so
nothing current depends on either, and a lightweight tag still names the
right commit. N-5 re-checked this fresh with a read-only
`git ls-remote --tags origin`: v2.0.0-rc.1 and v2.0.0-rc.2 still show no
peeled `^{}` line while v2.0.0-rc.13 does.

## The two handover flakes were not reproduced; a third was, and is fixed

MOVED HERE 2026-08-04 (N-5 bucket 3,
docs/closure/reports/2026-08-04-N-5-open-defect-triage.md row 38), from
`docs/NOT-FINALIZED.md` item 24. Two flakes are UNDECIDABLE rather than
open work: code cannot fix what cannot be reproduced, and the CI annotation
wrapper already captures any recurrence, which is what a code fix would
have bought here anyway. `docs/NOT-FINALIZED.md` stays in place with this
pointer, per this project's rule against editing a dated entry to agree
with today.

**Handover item 9, "the store suite fails when it runs slowly."** The
recorded correlate was 71 seconds for a failing run against 12 to 13 for a
passing one. Re-run three times under deliberate CPU load: about 32 seconds
each time, all green, well short of the 71 recorded. Evidence of absence at
that load on that machine, not proof the defect is gone.

**Handover item 10**, a named test asserting a deliberate two-thread race
on the handover lock, does not exist in the tree under that name and the
suite that would hold it contains no `threading.Thread` at all. Either
renamed or removed between the handover and today; recorded as
UNDECIDABLE rather than closed.

**A third load-sensitive failure, in `test_bm.py`, WAS reproduced and IS
fixed.** A bare-stopwatch timing test with no baseline measured the machine
rather than the algorithm, and a first fix attempt (deriving the ceiling
from a small-input measurement) was shown wrong by calibration: a
deliberately reinjected quadratic redactor passed it, because a quadratic
inflates the small measurement too. The assertion now lives on the RATIO of
large to small timing, which no defect can inflate the same way, and it was
calibrated both directions (linear passes, reinjected quadratic fails) and
confirmed green three times under the load that produced the original
failure. C-11 (closure register) later moved the underlying timer itself to
a minimum-of-five-samples helper shared by both timing tests, which is
outside the scope of this move and is not restated here.

## The plugin install path and the beginner layer: brand new, installed once

Added 2026-07-31 on the beginner-first branch. The facts, stated so the
QUICKSTART honesty label has a register entry behind it:

- The Claude Code plugin packaging (`.claude-plugin/plugin.json`, the
  repository marketplace manifest, `hooks/hooks.json`, the six `/brotherme`
  commands, and the guided skill at `skills/brotherme/`) is new. It has been installed exactly once: on the
  author's machine on 2026-07-31, from a local copy of the repository, full
  cycle in `docs/evidence/2026-07-31-first-plugin-install.md` (in the public
  repository). No install from GitHub or on any other machine yet. Two
  defects that first install surfaced: a name collision (a development copy
  carrying the same manifest name is refused loading while the plugin is
  installed; the development copy is now named differently) and double
  wiring (the plugin auto-wires the same six hook events scripts/install.py
  wires into settings.json, so a machine carrying both runs every hook twice
  while the plugin is installed; pick one wiring, not both). UPDATED
  2026-08-01 (Loop 3 design D-3): this double-fire state is no longer
  silent. `scripts/doctor.py` check 6 detects it mechanically, both a
  plugin named in settings.json's `enabledPlugins` and a clone-managed
  `PreToolUse` entry wired at the same time, and FAILs naming which one to
  remove (`/plugin uninstall <name>` or `python3 scripts/uninstall.py`).
  What is still NOT true: nothing prevents the double install from
  happening in the first place, only from staying unnoticed once doctor is
  run.
- The marketplace install command only works once these files exist on the
  branch or tag the marketplace add fetches. If your copy predates them, the
  command fails; that is a missing-files condition, not a broken machine.
- `hooks/hooks.json` mirrors the same six hook events `scripts/install.py`
  wires into `settings.json`, but the plugin-managed wiring has never been
  exercised end to end. The `scripts/install.py` path is the exercised one.
- How the guided layer loads is only partly proven. On this project's own
  machine, a working copy at `~/.claude/skills/brothermode` that contains the
  plugin manifest, the commands, and the guided skill DID register all six
  `/brotherme` commands and the guided skill in a live Claude Code session on
  2026-07-31 (one observation, one machine, one Claude Code version). No
  fresh-machine install of either path has demonstrated the guided layer yet;
  treat reachability as promising, not verified.
- UPDATED 2026-08-01 (Loop 3 design D-1/D-2, docs/superpowers/specs/
  2026-08-01-loop3-consent-install-design.md): what this entry used to say
  is now false and would mislead a reader who trusted it. There IS a
  first-run setup now, `python3 scripts/setup.py`, runnable interactively
  (question by question, plain words) or flag-driven
  (`--vault PATH --mode plugin|clone --accept-notice`) for scripted runs and
  tests, and it is the ONLY code path in the project allowed to create
  `~/.brotherme/config.json`. Every write-capable hook entry point
  (`bm_sessionstart.sh`, `bm_autosave.py`, and all three hook-wired commands
  in `bm_telemetry.py`: `outcomes-append`, `precompact-brief` and
  `stop-warn`) checks that config BEFORE writing anything and, when setup has
  not run, writes NOTHING and prints one sentence naming `scripts/setup.py`:
  proven directly by walking both the HOME tree and the project tree before
  and after, a fresh HOME stays at zero files (`tools/test_bm_consent.py`,
  the suite this loop added).
  CORRECTED 2026-08-02, and the correction is the useful part: this sentence
  named only two entry points because only two had been gated, and the two it
  omitted were writing. `precompact-brief` wrote the founder's last message
  VERBATIM into `~/BrotherModeVault/99-System/telemetry/last-resume-*.md`
  pre-consent, and `stop-warn` created the vault tree to hold a marker file.
  Both were found by an independent Loop 9 review and reproduced from scratch
  in a throwaway HOME before being fixed. The escape route matters more than
  either bug: the PreCompact hook line runs TWO programs off one payload, the
  earlier fix gated the first, and every check that existed drove hook EVENTS
  rather than every PROGRAM on each line, so nothing could see the second.
  The gate now sits on each command, and an inventory test reads
  `hooks/hooks.json` and fails if any hook-wired `bm_telemetry.py` command
  lacks a consent check, so the next hook cannot reopen the class. The
  automatic session hooks no longer default the vault to
  `~/BrotherModeVault` on their own the first time they fire; they simply do
  not write until `scripts/setup.py` has recorded a vault path, and setup
  itself never creates the vault directory, only records the path a founder
  chose. What is still NOT true: the guided `/brotherme-start` skill flow
  and this CLI-based setup are two separate entry points that have not been
  unified into one first-run experience, and `docs/specs/canonical-project-
  protocol.md` remains the longer-term direction, not what shipped here.

## The rc.9 install gap, found by the Loop 9 preliminary refuter pass (2026-08-02)

Three related facts, all true at once, all resolved only by the founder
cutting the next release tag at program end:

- **The pinned install commands install the pre-fix tree.** Every install
  page pins `v2.0.0`, the last resolvable tag, per the Loop 0
  version law. But rc.9 predates every commit of the release-closure
  program, so a user installing today gets the acknowledged-broken
  five-event hand-wiring blocks, no Bash-write detection, and none of
  loops 1 through 8. The release branch's own install path does not
  install the release branch. This is the designed cost of refusing to
  tag mid-program (a late rc.10 or rc.11 tag would have recreated the
  rc.8 two-trees ambiguity); it stops being true the moment the next tag
  is cut and the three doc pins move to it.
- **rc.9 cannot read a store this branch has touched.** rc.9 code
  understands store schema 11; the program's Loop 1 migrated the schema
  to 12, and the Memory Sentinel (Full-Auto Phase 1, 2026-08-02) moved it
  again to 13. An rc.9 install pointed at that store refuses loudly at
  session start ("store schema_version is 13 but this BrotherMode
  understands at most 11. Upgrade BrotherMode; do not downgrade the
  store.") and touches nothing, so the mismatch is safe but noisy. The
  founder's own machine shows this warning every session for exactly
  this reason. It ends when the live install is upgraded past the
  schema-13 migration.
- **doctor's wiring check reads 1 of 7 hook groups.** Check 8's healthy
  verdict confirms the PreToolUse fence entry and nothing else: an
  install missing SessionEnd, Stop, PreCompact, PostToolUse and both
  Bash-audit groups (telemetry, autosave and the Loop 6 detection all
  dead) still prints "All 11 checks passed". Widening the check to all
  seven groups is a post-freeze fix; until then, the mechanical
  cross-check is `python3 tools/test_install.py`, whose shape assertions
  do read all seven.

## U1 autonomy contract: the signer check, one concurrency note, and one stale number (2026-08-05)

- **The `sign --signed-by` check is a denylist, not an authentication
  check.** Full account and the plain-language version of this limit:
  `docs/AUTONOMY.md`. Stated here in the same terms the code carries: it
  refuses roughly thirty model-name tokens, case-folded and Unicode
  normalized, and it does NOT catch a model deliberately told to sign as
  a real person's name (an initial like `K.` reads no differently from
  any other short token, so the check does not try to tell them apart), a
  vendor or model name not yet on the list, a name written in a
  non-Latin script, or a deliberate misspelling (`cl4ude` does not fold
  to `claude`). A stricter, version-suffix-shaped regex (matching a
  pattern like `claude-opus-5`) was drafted during implementation and
  DISCARDED: it also matched `K. Maaouni`, which normalizes to
  `k-maaouni`, a word-hyphen-word shape the regex could not tell apart
  from a model name, and it would have wrongly refused a real person's
  own name. Plain token splitting against the fixed list, with no
  shape-based pattern, is what ships, and it is the intentionally weaker,
  more honest check.
- **Two concurrent signers do not corrupt anything, but not for the
  reason first assumed.** The design sketch this loop implemented from
  predicted a race resolved by `UNIQUE(project_id, revision)`: the
  SECOND concurrent signer would collide on that constraint and have to
  retry. What the store actually does is re-read the latest revision
  INSIDE the write lock it already holds, so the second signer never
  collides at all; it simply waits for the lock, then lands on the next
  free revision in turn. Both signers succeed, on two different
  consecutive revisions, and exactly one highest revision exists when
  both are done. This is a stronger property than the one the design
  sketch predicted (no wasted attempt, no retry), not a weaker one, but
  it is a different mechanism, and a reader who goes looking for the
  collision-and-retry behavior in the code will not find it.
- **`sign --allowed-path` and `gate-check --path` are always resolved
  against the project root, never against the directory the command was
  run from.** An earlier design sketch assumed the CLI would pass the
  caller's current directory through to path resolution; the store
  methods that actually shipped (`Store.sign_contract`,
  `Store.gate_check`) take no such argument at all, so there is nowhere
  for the CLI to pass one. A path typed from a project subdirectory is
  therefore interpreted the same way it would be from the project root,
  which is simpler than the original plan but worth stating plainly
  rather than leaving a founder to discover it by surprise.
- **This very file's rc.9 entry above still quotes a stale schema
  number.** The bullet dated 2026-08-02, two entries up, quotes a
  verbatim console message naming the schema number that was live at
  the time (thirteen); the U1 loop that added this section moved the
  live schema forward by one. The quoted sentence is left untouched on
  purpose, per this file's own rule against editing a dated entry to
  agree with today, so `tools/test_bm_docs.py`'s mechanical schema
  check currently reports that one line as a mismatch against today's
  number. Disclosed here rather than silently patched: fixing it needs
  either a founder-authorized edit to that specific historical quote,
  or the same current-claim-versus-dated-evidence exemption
  `tools/test_bm_docs.py` already gives its sibling version-number
  check, neither of which is this loop's file to make.

## L03: what the Full-Auto controller does NOT yet do (2026-08-05)

The durable controller (docs/FULL-AUTO.md, tools/bm_controller.py) resumes a
run by the SAME controller identity after a crash, proven end to end by the E4
fixture, and it refuses a second LIVE controller for one project through the
one-writer fence. What it does NOT do yet: automatically ADOPT a DEAD
controller's run under a new identity. The store primitive for that adoption
exists and is tested (a fresh session is blocked without an explicit displace
flag, and adopts with it), but the controller does not wire it into its own
start path, so recovering a genuinely abandoned run today needs a later store
method or a founder passing the displace flag by hand. Recorded here rather
than left implicit.

The at-most-once external side-effect guarantee holds only where the unit's
operation is idempotent or its worker is the record-intent kind that BrotherMode
ships. A unit that runs a non-idempotent external command through a custom
worker can repeat that command if the process dies between the command and its
checkpoint; the controller records the accepted result exactly once, but it
cannot make an arbitrary external command idempotent. The founder-gated and
production surfaces stay behind the contract's floors regardless.

What the second hardening round (2026-08-05) narrowed but did not close:

- A contract that allows a path PATTERN is no longer a limit at all. It is a
  RULE, decided by the founder on 2026-08-05 after both alternatives were
  measured and rejected. THE RULE: **a plain path grants its whole subtree,
  and a pattern grants the FILES it matches at its own depth.** `api/*.py`
  grants `api/pay.py`. It refuses `api/notes.md`, `api` itself, anything
  deeper such as `api/sub/deep/secrets.env`, and, this last one is what the
  decision added, any DIRECTORY the pattern happens to match at its own
  depth: `src/*` no longer grants `src/app`. That refusal is the point of
  the rule. A fence over a directory covers its whole subtree, so granting
  `src/app` made `src/app/deep/keys.pem` writable although the same contract
  refuses that file when it is named directly. Name the plain directory when
  the subtree is what you mean; that spelling has always granted a subtree
  and still does. Naming the PARENT of an allowed path widens nothing, which
  is the case round 2 fixed and this decision left alone. This entry stays
  under a heading that now contradicts it rather than being deleted,
  because the record of what was once open is the point of this page; the
  correction sections below carry the rest of that history.
- Reading the name is how the rule tells a file from a directory: a name
  carrying an EXTENSION is a file, an extensionless name is a directory.
  That is the only file signal a path comparison has without touching the
  disk. So a pattern also refuses `Makefile`, `LICENSE` and `.env`, and
  there is one case the reading cannot see, a directory whose name carries
  an extension (`src/*` grants `src/data.bak` as though it were a file, and
  a fence over it would cover what is inside it). Both directions have the
  same answer: name the path literally when you mean it.
- A contract revoked in the instant between the controller reading it and
  recording spend against it still raises out of that one call. The next
  `step` resumes the pending result and settles it correctly, so the window is
  two adjacent store calls wide and self healing, not the minutes-wide window
  it used to be.
- `run_to_completion` returns instead of spinning when a pass achieves nothing
  and nothing is in flight. The one shape it still spins on is a run held at a
  soft spend stop with no work in flight, where every pass reports the same
  soft-stop note; `bm-controller step` and `status` show the same state without
  looping.
- The deadline check that abandons a hung dispatch is an engine method a
  scheduler or SDK caller drives. It is still not wired to a subcommand of
  `bm-controller`, so nothing abandons a hung dispatch automatically today.
- Concurrency between two controller processes against one store was exercised
  in a single process with a delegating wrapper, never as two real operating
  system processes contending for the same SQLite file.

## L03 round 4: what the third adversarial pass closed, and the six things it deliberately did not (2026-08-05)

Three independent refuters attacked the round-3 controller fixes (state
machine, authorisation, liveness) and raised twenty-six findings. The
hardening round that followed closed twenty-three of them. This section
records the three it deliberately DEFERRED, with the reason for each, plus
three bounds that survive by design, plus the two statements this page made
after round 2 that are now WRONG and are corrected here rather than left
standing.

### Corrections to what this page said after round 2

- **The glob claim above is superseded.** The round-2 entry says a contract
  allowing `api/*.py` also authorises `api/notes.md`. That was true, and it
  understated the blast radius: a leading wildcard such as `*.py` authorised
  the entire project, at any depth, because the pattern's literal prefix was
  empty. Both are now closed. THE RULE, in one sentence a founder can hold
  the system to, in the form the founder's 2026-08-05 decision left it:
  **a plain path grants its whole subtree, and a pattern grants the FILES it
  matches at its own depth.** `api` grants `api/pay.py` and
  `api/sub/deep/secrets.env`. `api/*.py` grants `api/pay.py` and refuses
  `api`, `api/notes.md` and `api/sub/deep/secrets.env`. `**` is NOT
  recursive: `api/**` grants the direct children of `api` that are files,
  and nothing below them, and the recursive spelling is the plain directory.
  Naming the PARENT of an allowed path still widens nothing, which round 2
  fixed and this round left alone. Round 4 wrote this sentence as "grants
  exactly what it matches at its own depth", which admitted a directory at
  that depth and therefore, through the fence over it, a subtree; the word
  FILES is the 2026-08-05 correction and it only ever narrows.
- **The soft-spend-stop spin is closed, and it was not the only one.** The
  round-2 entry says `run_to_completion` still spins at a soft spend stop.
  Every pass now reports one machine-readable reason out of a fixed set and
  both loop drivers stop on it, so the soft stop, a failing done-definition
  (which used to re-run the founder's whole test suite once per wasted pass,
  up to 500 times in one call), a provider outage, transient fence
  contention and the ordinary parked dispatch all stop after ONE pass. The
  reason is printed as a `reason:` line and carried in `--json` as
  `stop_reason`; `docs/FULL-AUTO.md` lists all eight words and says for each
  whether a founder needs to act.

### Closed 2026-08-06 (L09), each ratified by the founder on 2026-08-05

- **The path floor is CLOSED.** A sixth safety floor, `governance-write`,
  makes writes to BrotherMode's own `.brothermode` store directory, to
  `.git` (config included) and to `.claude/settings.json` un-authorisable
  by any contract wording. `sign` refuses an `allowed_paths` entry that
  names one of those surfaces (`path-is-floor`), and `gate_check` refuses a
  candidate path inside one (`REFUSED-FLOOR`) BEFORE the `allowed_paths`
  comparison, so the floor holds under `.`, `*`, `**`, any covering glob,
  and any hand-written row. A `.` contract stays signable; the floor bites
  the protected path, never the broad allowance. What remains open by
  design: a fence claimed over `.` still covers the protected files at the
  fence layer; the gate refuses any attempt to authorise them as a write
  path, and the fence hook's own git-containment check is the second
  defence there.
- **Controller ownership on `step`, `plan`, `record-result` and `stop`
  is CLOSED.** The run's recorded session id is the driver identity; a
  caller whose session does not match is refused `not-driver`, with the
  refusal naming the owning session and the one deliberate takeover path:
  `bm-controller adopt`, which records the handover durably (attribution
  event `controller.run.adopted`), mirroring STATE.md fence adoption.
  `plan` was added to the guarded set on 2026-08-06 after an adversarial
  refuter showed it was the sharpest bypass of exactly this gap (a
  foreign session could inject the unit graph the honest driver then
  dispatched); `record-result` now refuses a foreign session even on a
  terminal run. Consequence a founder must know: a multi-process CLI flow
  must carry the SAME `--session-id` across invocations (the store's own
  documented session convention), or run `adopt` once per takeover. A run
  row with no recorded session (a store from before this law) is not
  guarded, the same rule the fence store applies. Still not
  ownership-guarded: the unwired `check_timeouts` (no shipped CLI door).
- **Driver ownership and the fence are COORDINATION, not access control
  against a malicious local actor (unchanged trust model, stated plainly
  after a refuter reproduced it).** The driver identity is a session id
  the caller supplies with `--session-id` and that `status --json --raw`
  prints, so a process on the same machine that reads and re-asserts it
  can drive a run without going through `adopt`, leaving no handover
  event. This is the same self-asserted-identity model the STATE.md fence
  has always had: it keeps two COOPERATING sessions from colliding and
  records who did what, and it is not a defence against a hostile local
  process, which already has the founder's filesystem. Authenticating a
  session would need a per-session secret store, a different product than
  this one. Runs that need a real trust boundary belong in separate OS
  accounts or machines, not separate BrotherMode sessions.
- **An empty `allowed_paths` no longer authorises writing work.** `sign`
  refuses (`no-write-scope`) a contract that grants any writing risk class
  with no declared `allowed_paths`. Genuinely read-only work stays
  expressible the one way the schema has: grant only `read-only-inspect`
  and/or `browser-read` (there is no explicit read-only marker column),
  and the refusal message names that route.

### Bounds that survive by design, stated rather than implied

- **The run-state read and the result handler are not atomic.**
  `record-result` reads the run state and then branches on it, and a
  concurrent writer moving the run between those two puts the call on the
  wrong side of the branch. This round did NOT close that race, and says so
  plainly: closing it needs the whole result path inside one store
  transaction, which means a store method that runs a founder's own
  subprocess, and the harness seam exists precisely to forbid that. What the
  round DID remove is both consequences a refuter measured. A stopped run
  gaining newly selectable work with no founder step is closed by the
  unconditional founder step and by the dispatch-source rule; a run wedged
  in `EXECUTING` with its only unit blocked is closed by the empty-wave
  unwind and the two-way lane reconcile. The race survives; its damage does
  not.
- **Controller unit ids are ONE GLOBAL NAMESPACE.** Two projects in one
  store cannot both use a unit called `u1`. The symptom is closed (a clean
  refusal naming the colliding id, the project that already holds it, and
  the fix, instead of a raw database traceback, with the plan rolled back
  cleanly so a re-plan with fresh ids recovers), but the underlying limit is
  a composite-key table rebuild, which is not an additive change. Prefix
  unit ids per project.
- **A path containing a NUL byte is still accepted.** A write scope entry
  that is a number, a list or an object is now refused with `bad-path`
  naming the entry and its type, rather than crashing. A NUL byte inside a
  string path is not refused; that is a separate policy decision about path
  bytes which this round did not take.
- **A founder step gates its lane PROJECT WIDE, across runs.** The human
  steps table has no run id, so a step left open by an EARLIER run gates the
  same lane in a new one. Narrowing it needs a schema column. The behaviour
  is fully recoverable by resolving the step, and `bm-controller status`
  shows the open count.
- **Timeouts still have no subcommand.** The deadline check that abandons a
  hung dispatch remains an engine method a scheduler or SDK caller drives.
  It now refuses to act at all on a PAUSED run, and it reads dispatch rows
  rather than unit statuses so a dropped unit's stale dispatch can no longer
  hide from it, but nothing abandons a hung dispatch automatically today.
- **Two real controller processes contending for one store file were still
  not exercised.** Every concurrency probe in this round, as in the last
  one, ran in a single process with a delegating wrapper firing the
  competing write. That is a faithful simulation of the interleaving and it
  is NOT a test of two operating system processes against one SQLite file.

## L03 round 5, the STORE half: what the fourth adversarial pass closed here, and the three things it did not (2026-08-05)

This section is written by the store writer of round 5 and covers ONLY the
store (`tools/bm_store.py`). The controller half of the same round is
disclosed separately, by the writer who owns that file. Nothing above this
heading was edited to add it.

### Closed in this round

- **A write scope is now a literal path, never a pattern.** A unit that
  declared `write_scope ['*.py']` used to be authorised, fenced and rolled
  back over the WHOLE project, because everything downstream of the
  authorisation check reduces a pattern to the directory before its first
  wildcard, and for a leading wildcard that directory is the project root.
  The engine's own rollback (`git restore -- '*.py'`, and git's pathspec
  globbing IS recursive) then destroyed uncommitted work at depths the rule
  said the pattern never granted. Declaring a pattern as a write scope is
  now refused outright (`glob-write-scope`), and the refusal says what to do
  instead: name the files, or name the directory they live in, which grants
  its whole subtree. Patterns stay legal in a contract's `allowed_paths`,
  where the founder is drawing the boundary rather than a worker naming its
  own.
- **A path that escapes the project can no longer be re-spelled to get in.**
  Only the literal part of a pattern was ever resolved on disk, so
  `src/[a]pp` (which matches exactly one path, `src/app`) was accepted where
  naming `src/app` was refused for resolving outside the project through a
  symlink. Both spellings refuse now, because the pattern spelling cannot be
  declared at all.
- **A command naming one project can no longer write another project's
  run.** Every store write that resolves a run from an id the caller supplied
  now accepts the project the caller believes it is working on and refuses
  `run-not-in-project` before touching anything. Eleven entry points, listed
  in the round-5 store fix report.

### Not closed, and what stands in the way

- **CLOSED on 2026-08-05, by the founder decision this entry was waiting
  for.** This entry said a pattern in `allowed_paths` still granted a
  subtree it did not itself match: `src/*` authorised the directory
  `src/app`, a fence over a directory covers everything under it, and
  `src/app/deep/keys.pem` was reachable even though asking about that file
  by name was refused. Round 5 stopped here because closing it meant moving
  a verdict this project's own tests pinned, and both directions it could
  measure broke something (a pattern granting the subtree of what it matches
  turns `['*']` into a whole-project grant again; a pattern granting nothing
  breaks `api/*.py`). The founder chose a third rule, which narrows only: a
  pattern grants the FILES it matches at its own depth and no directory. The
  superseded test row was moved in the same change, the property that had 35
  violations over 55440 triples now sweeps to ZERO, and the evidence is
  `docs/program/absolute-lead/evidence/L03/FIX-glob-rule-report.md` beside
  the round-5 measurement it closes. The practical advice is unchanged and
  is now enforced rather than advised: grant the directory you mean, not a
  pattern that happens to match it.
- **A unit that declares NO write scope is still dispatched, under any
  contract, and claims an empty fence.** The page already deferred this as a
  question about what an empty `allowed_paths` MEANS; the missing data now
  exists and is worse than the deferral assumed. Under a contract granting
  only `docs`, a unit with `write_scope []` is authorised on its risk class
  alone, dispatched, and fenced over nothing at all, and in the default
  (non-strict) fence mode a fence holding nothing refuses no write anywhere.
  Half of the recommendation on file landed on 2026-08-06: `sign` now
  refuses a contract that grants writing work with no `allowed_paths` at
  all (`no-write-scope`, see the closed section above). The UNIT-level
  half is still open: under a legitimately signed contract, a unit whose
  own `write_scope` is `[]` is still judged on its risk class alone and
  claims an empty fence. Until that is decided, give every unit a write
  scope.
- **Two earlier deferrals are now CLOSED (2026-08-06)**: the path floor
  and the driver-ownership check both landed; the "Closed 2026-08-06
  (L09)" section above carries what each refusal says and the bounds each
  one keeps.

### One bound this round's own fix introduces

- **The foreign-run refusal only fires when the caller says which project it
  means.** The check is an argument a caller passes, not something the store
  can infer, so a caller that omits it gets exactly the previous behaviour.
  Every store write that can be reached with a run id now accepts that
  argument; wiring the shipped commands to pass it is the controller's own
  change, and until that lands the refusal protects callers that opt in
  rather than every route into the store.

## L03 round 5, the CONTROLLER half: what the fourth adversarial pass closed here, and the five things it did not (2026-08-05)

This section is written by the controller writer of round 5 and covers ONLY
`tools/bm_controller.py` and its suite. The store half of the same round is
disclosed in the section immediately above, by the writer who owns that
file. Nothing above this heading was edited to add it.

### Closed in this round

- **The kill switch now stops COMMANDS, not just new work.** After
  `bm-autonomy stop` or `revoke`, one `bm-controller record-result` still
  executed the unit's own model-authored done-check and then a `git restore`
  over the founder's working tree, and one plain `bm-controller step` did
  the same through the crash-resume branch, which by design runs before the
  contract is read. Every command the engine can run now goes through one
  call site that re-reads the contract immediately before running anything
  and refuses to run at all unless it is live. A result arriving under a
  dead contract is recorded, rejected, its fence parked and a founder step
  queued naming the unit and the contract state, with zero commands
  executed.
- **A pause no longer destroys a real answer.** The hold that design round 4
  added only worked for a run paused by a route no shipped command has. On
  the route the CLI actually takes, the contract being paused, the answer
  was rejected as stale on resume, rolled back on disk and charged a retry.
  A pause and a resume copy every authorisation column forward unchanged, so
  they no longer count as the authorisation moving, and the same held answer
  is accepted afterwards. A real amend still rejects.
- **A paused contract says which command clears it.** The note pointed only
  at `bm-controller resume`, which cannot clear a contract pause: the next
  pass re-reads the contract and pauses the run again, forever. Both
  commands are named now, in the order they must be run, and the machine
  readable reason distinguishes a contract pause from a run pause.
- **A run blocked by a founder step in an UPSTREAM lane now says so.** It
  reported "nothing founder-gated, inspect the graph" and left the run
  READY, for a run that one `resolve` unwedges. It parks as founder-waiting
  now, moves the run to WAITING_HUMAN, and the note names the lane and the
  step.
- **A delivered run names what is still founder-gated.** The settle path,
  which is every synchronous wave and every `record-result`, computed the
  stop reason, the note and the founder-gated remainder and then threw all
  three away. `bm-controller start` on a delivered run with a failed unit
  never mentioned it. The wave that delivers now carries its own verdict,
  and `record-result` prints it.
- **Four more, smaller:** a run left mid-verification with an open dispatch
  now names `record-result`, the one command that recovers it, instead of
  three that cannot; a unit stranded behind an already-closed dispatch (a
  crash window in five places) is recovered and its fence parked instead of
  held over the founder's files forever; a unit id the fence store would
  refuse is refused at plan time instead of wedging the run permanently; and
  a unit's read scope is canonicalised and kept inside the project exactly
  as its write scope is, so a brief can no longer hand a worker `/etc` or a
  path under the home directory.
- **A command naming one project can no longer write another project's run.**
  `plan --project p1 --run <p2's run id>` un-paused p2, cancelled its open
  dispatch, parked its fence and replaced its unit graph. Every store write
  this file makes now names the project the caller said it was working on,
  so the store refuses before touching anything.

### Not closed, and what stands in the way

- **A held result's meter cannot be charged while the contract is paused.**
  Spend is only recordable against a live authorisation, which is the right
  rule for a revoked contract and the wrong one for a reversible pause, and
  the cost is not carried on the recorded result, so it cannot be charged
  later either. Closing it properly needs a store change (either spend
  recordable against a paused contract, or the cost stored with the result
  so the charge can be deferred to the resume), and that is still not this
  file's to make.

  **Round 6 corrects the size of this and blocks its consequence.** The
  earlier wording here, "a breaker reading low by one unit's cost", was
  wrong: the sequence is `bm-autonomy pause`, `bm-controller record-result
  --tokens N`, `bm-autonomy resume`, `bm-controller resume`,
  `bm-controller step`, and it repeats once per unit, so the under-count is
  EVERY result recorded during a pause, without limit. A whole run was
  driven to `DELIVERABLE_READY` with 270 claimed tokens against a 100 token
  ceiling, metered 0, verdict `ok`, and zero open founder steps.

  What is fixed is the consequence rather than the charge. Each uncharged
  disclosure now also queues a founder step in the reserved lane
  `spend-reconciliation`, naming the exact tokens and minutes, the
  `bm-autonomy spend` command that charges them and the `bm-autonomy
  human-steps --resolve` command that closes it, and **no run reaches
  `DELIVERABLE_READY` while one of those steps is open**. So the meter can
  still read low mid-run, and a run whose meter reads low can no longer be
  declared deliverable. The reserved lane holds no units, so it gates no
  work; the way out is the two shipped commands, not a wedge.
- **A unit with NO write scope is still dispatched and fences nothing.**
  Unchanged from the store half's disclosure above, and it is a contract
  layer decision rather than a controller one. Give every unit a write
  scope.
- **There is still no path floor, and the duplicate-driver refusal still
  only fires on `start`.** Both are unchanged from earlier sections of this
  page; round 5 touched neither.
- **One retirement note in the controller suite claims one property more
  than its replacement pins.** A test retired in round 4 was superseded by
  one that asserts two of the three properties the retirement note claims;
  the third holds in fact and is asserted by nothing. Round 5 did not fix
  the wording or add the assertion.
- **The controller suite is one test below a numeric floor an earlier design
  set for itself.** That floor counted tests before an authorised retirement
  and was never adjusted for it. The retirement is recorded in the file with
  its argument, so nothing is silently missing, but a later round reading
  that floor literally would flag the number.

### Bounds round 6's own fixes introduce

- **The spend breaker judges one already-paid unit past the ceiling.** Once
  the breaker trips, no command runs: not a done-check, not a verifier, not
  a rollback, not the founder's whole done-definition. The single carve-out
  is the unit whose OWN reported cost is what pushed the meter over: the
  engine charges the meter before it judges a result, so refusing that unit
  its own check would destroy an answer already paid for without saving a
  token. Subtracting the spend row just written puts the meters back under
  their ceilings, and only then is the command allowed. The bound, stated
  plainly: a caller that self-reports an enormous cost buys the judgement
  of the one unit already in flight. It buys nothing else, it pays the
  meter in full to do it, the run drains on the next step, and no
  deliverable is declared.
- **A rollback is now refused rather than run when the write scope is not a
  list of plain relative paths.** That is the point (a `git restore` built
  from a git pathspec restored the whole working tree), but it means the
  write scope is left exactly as the worker left it. A founder step says so
  in those words and names the unit; nothing cleans it up automatically.
- **The engine's write-scope rule duplicates the store's on purpose.** The
  store refuses a bad entry where it ENTERS the store, and the engine
  refuses one where it ACTS on it. Two refusals for one bad unit graph is
  the intended cost of the fix holding even for a row that reached the
  engine by another route.
- **The uncharged-spend founder step uses a reserved lane name,
  `spend-reconciliation`.** Round 6 called that lane reserved and reserved
  nothing: `plan` accepted a unit into it, and the delivery block selected
  every open step in the lane, so an unrelated step there blocked a whole
  run's delivery citing spend that was never uncharged. Round 7 closed both
  halves. `bm-controller plan` now REFUSES a unit whose lane is that name,
  by name, before anything is written, and the delivery block selects only
  steps the disclosure itself marked. A unit graph that wants that lane
  name gets a refusal naming it, not a silent gate.
- **The kill switch is re-asked at every window on the judging path.**
  Round 6 said "after every command" and asked once, after the done_check.
  The complete set is now: after the unit's done_check, after its verifier,
  once more immediately before anything is written about the unit's fate,
  and once after the founder's whole done-definition and before
  `DELIVERABLE_READY` is declared. A rollback has no window after it,
  because nothing is accepted after a rollback and the rollback is itself
  refused before it runs. The cost is three extra store reads per window
  (contract, spend totals, and the unit's own gate check) and it is
  deliberate: the read is what makes the moment of execution the moment of
  authorisation. A unit with a verifier therefore pays for two adjacent
  windows today, which is kept on purpose so that a future statement
  inserted between them cannot reopen the gap.
- **The sentence that says what ran is scoped to the CALL, not to the
  unit.** The controller derives "what ran" from a ledger of the commands
  it actually executed since the current command started, rather than from
  which branch is writing the sentence, which is what stopped it claiming
  that nothing ran in a call that ran three things. On `bm-controller
  record-result`, which handles exactly one result, the call and the result
  are the same set. On a `bm-controller step` wave that judges several
  units, the sentence names every command the WAVE ran, which is coarser
  than per-unit and still true.

### Bounds round 7's own fixes introduce

- **The structural guard over execution primitives is not total, and here
  is what it does and does not cover.** It reads the controller's source
  and refuses: any import that is not one of the eight whole modules the
  file already imports, any `from X import Y` at all, any aliased import,
  any reference (called or merely bound) to `subprocess` outside the one
  runner method or to a listed process-starting or name-resolving call, any
  of the builtins that turn a string into an object, either builtins
  namespace, and the `checker` attribute anywhere except where it is bound
  and where it is called behind the gate. Names are resolved through the
  file's own imports before any of that is tested, so a rebinding is read
  as what it really names. What it does NOT cover: the module's own `_load`
  helper, which is how every tool in this repository loads
  `tools/bm_store.py`, executes a sibling `.py` file by path. A future edit
  could reach a command through a sibling module rather than through a
  primitive named above, and this guard would not see it. That is a
  boundary of reading source rather than running it, and it is written down
  here rather than left to be discovered.
- **A write scope that is not a list of paths is refused on the judging
  path by SHAPE, not by entry.** A scalar or a bare string is refused
  before any path is judged, with a founder step naming the unit, because
  without a container there is no list to judge at all. An entry that IS a
  string but is not a plain relative path (a git pathspec, a glob, an
  absolute path) is still refused where it is ACTED on, in the rollback,
  which composes no command and tells the founder the scope was left as the
  worker left it. That split is deliberate: the unit's answer is already in
  hand by then, and refusing its done-check would destroy an answer over a
  fault in a field the check does not use.

### Bounds this round's own fixes introduce

- **The pause-is-not-an-amend rule reads the contract's revision chain.** It
  asks whether every revision since a dispatch was stamped was a pure
  lifecycle change (pause, resume, stop, revoke), which by construction
  cannot alter what was authorised. It reads a bounded window of that chain
  and, on any doubt at all, including a chain longer than the window,
  reports that the authorisation moved, which is the older and stricter
  answer. A project with thousands of contract revisions therefore gets the
  round-4 behaviour back rather than a wrong answer.
- **The read-scope check is a project-root containment check, not an
  authorisation check.** A read scope still is not measured against the
  contract's allowed paths, deliberately: those are a WRITE boundary, and
  gating reads on them would refuse units the contract permits. What is new
  is only that a read scope cannot name a path outside the project.
- **The founder step queued when a result lands under a dead contract gates
  that unit's lane.** That is the intended mechanism, and on a run that is
  about to drain it costs nothing, but it is a real gate: the lane stays
  unselectable until the step is resolved.

## L03 round 6, the STORE half (DECLARATION side): what the fifth adversarial pass closed here, and the four things it did not (2026-08-05)

This section is written by the store writer of round 6 and covers ONLY the
store (`tools/bm_store.py`). The EXECUTION side of the same finding, what the
engine does with an entry once it is stored, is closed in the same round by
the writer who owns `tools/bm_controller.py` and is disclosed separately.
Nothing above this heading was edited to add it.

### Closed in this round

- **A write scope entry is now a plain relative path inside the project, and
  git pathspec magic is refused by name.** Round 5 made a write scope a
  literal path by refusing three pattern characters. Git has a second way to
  mean more than one file and it uses none of them: pathspec magic, which
  always begins with a colon. `:/` and `:(top)` mean the whole repository,
  `:!x` and `:(exclude)x` mean everything EXCEPT x, `:(icase)` matches a name
  the entry does not spell. Those spellings passed the round-5 gate, were
  stored, were fenced, and were handed to the engine's own
  `git restore -- <entry>`, which reverted files the unit never declared;
  with `:/` the rollback exited 0, so the founder was told only that a
  dispatch was rejected. Every spelling now refuses `pathspec-write-scope`,
  naming the entry, the unit and the remedy. Two shapes go with it for the
  same reason: an ABSOLUTE entry refuses `absolute-write-scope` (it used to
  be accepted and silently rewritten to its relative form, so the plan the
  founder wrote and the plan the store held were different strings), and an
  EMPTY or whitespace-only entry refuses `empty-write-scope` (it used to
  reach the resolver as a bare `ValueError('empty path')`, with no reason
  code, no unit id and no remedy).
- **A refused spelling can no longer be re-spelled past the gate.** The
  declaration rules read what the caller wrote, and the resolver then
  collapses `.` and `..` segments, so `./:!keep.txt` was stored as
  `:!keep.txt` and `sub/../:` as `:`. The gate now looks twice, at what was
  declared and at what will actually be stored, and the refusal names both
  forms. A property sweep over 2954 generated spellings is pinned as a test;
  it is what found this, and two further families with it (`./a:b` stored as
  `a:b`, which a Windows caller reads as drive-qualified, and `./ /` stored
  as a single space).
- **A scope is a LIST of path strings, and the container is checked before
  anything is iterated.** `write_scope: "a.py"` used to declare four scopes,
  one per character, one of them `.`, the project root: the worker's brief
  said it could write the whole project and the unit's fence held the root,
  silently. `write_scope: 7` used to leave the shipped `plan` command as an
  uncaught `TypeError`. Both now refuse `bad-scope-container`, naming the
  field, the actual type and what the old behaviour would have done with it.
  `read_scope` gets the same container check, where it previously had none
  at all.
- **Every exception from path handling at that boundary is now a named
  refusal.** The resolver ends in a syscall, so it can raise `OSError`,
  `ValueError` or whatever a hostile path proxy chooses, and each of those
  used to leave the method that validates a whole plan as itself, past the
  command line's handler, as a traceback. They land on
  `unreadable-scope-path` now; a refusal that already has a name, such as
  `path-escape`, is passed through unchanged.

### Not closed, and what stands in the way

- **A read scope ENTRY is still not put through the literal-path rule in the
  store.** The store checks the container and that each entry is a path
  string; it does not refuse a pattern, a pathspec or an absolute path there,
  and it does not canonicalise. That is deliberate rather than forgotten: a
  read scope never reaches `git restore --`, the engine canonicalises it
  separately, and a founder-authored pattern over files to READ is a
  reasonable thing to write. The consequence to know is that the store alone
  does not keep a read scope inside the project; the controller's own check
  does.
- **A bare-string read scope can still be exploded by the CALLER before the
  store sees it.** The store refuses `read_scope: "src"` from a direct
  caller, but the engine canonicalises a read scope before it calls the
  store, and a bare string iterated there arrives as a list of single
  characters that the store's container check cannot tell from a real
  declaration. Closing that is the controller half's item, in the same
  round, by design: neither half assumes the other landed.
- **`expected_artifacts` has no container check at all.** A bare string is
  stored as a bare string and a number is stored as a number. It is not a
  path the fence, the coverage check or the rollback ever reads, so the
  damage of the scope defect does not follow, but it is the same shape and
  this round did not take it. (`dependencies` has the same missing check and
  fails LOUDLY instead: a bare string becomes one dependency per character
  and refuses `dangling-dependency`.)
- **A path containing a NUL byte is still accepted, and `.` is still a legal
  write scope.** Both are unchanged from earlier sections of this page. A
  write scope of `.` grants and fences the whole project, which is the same
  disclosure as a unit with no write scope at all.

### One bound this round's own fix introduces

- **An explicit `null` scope now refuses where it used to mean "none".** An
  ABSENT `write_scope` or `read_scope` key still means "no scope" and hashes
  exactly as it did before, so no persisted unit is redefined by the upgrade.
  A key that is PRESENT and null is a declaration of the wrong type and
  refuses `bad-scope-container` naming `NoneType`, with `[]` given as the
  spelling for "no scope on purpose". A plan file that wrote `null` there
  gets a refusal it can act on rather than a silent empty scope.

## Cross-family refuter, the STORE half: what closed, and what it costs (2026-08-05)

Findings 1, 4, 5 and 6 of
`BrotherModeUp-handovers/2026-08-05-codex-crossfamily-findings.md`, raised by a
different model family against the shipped tools. Findings 2 and 3 were the
controller writer's and are recorded separately.

### Closed in this round

- **A unit's numeric fields are type-checked where they enter the store.**
  `retry_ceiling`, `token_budget`, `minute_budget` and `done_check_expect_exit`
  each refuse `bad-numeric-field`, naming the field, the type that arrived and
  what is required, before a single row is written. Nothing is coerced, so a
  well-typed graph hashes exactly as it did before.
- **The read-only store opens the database file read-only.** It used to open a
  read-WRITE connection and set `PRAGMA query_only=ON` afterwards, which left
  the OPEN itself able to write: measured, a plain connect to a cleanly-closed
  WAL store whose sidecars had been removed created both `-wal` and `-shm` and
  left them behind, and in a directory that forbids that it reported a
  perfectly healthy store as `StoreCorrupt`.
- **A claim cannot land on a unit whose status has moved.** `claim_unit`
  refuses `unit-not-claimable` rather than overwriting a concurrent re-plan's
  decision, and the controller treats that refusal as it treats a fence
  overlap: deferred to a later wave, no retry burned, no drain, the fence
  released.
- **A dispatch gets at most one verdict.** `record_verification` refuses
  `already-verified` for a dispatch that already carries one, so the loser of
  two concurrent verifications can no longer re-open a unit the winner
  completed.

### Bounds this round's own fixes introduce

- **A read-only open of a store with NO write-ahead log uses sqlite's
  `immutable=1`, and there is a race it does not close.** With no `-wal` there
  is nothing pending, so `immutable=1` is accurate and is the only open that
  creates no file at all. A writer that appears between the check and the open
  is caught by re-checking for the `-wal` and retaking the connection
  WAL-aware; the window that survives is a writer that opens, commits,
  checkpoints AND closes inside it, which would leave that one read seeing a
  file changing underneath it. Closing that from this side means taking the
  write lock a read-only diagnostic must not take.
- **A store whose `-wal` exists but whose directory forbids the `-shm` cannot
  be read at all**, and now says so by name (`store-unreadable`) rather than
  being reported as corrupt. Reading it with `immutable=1` was rejected
  deliberately: that ignores the log, so the diagnostic would report a stale
  snapshot of a live store as healthy.
- **An explicit `null` `retry_ceiling` now refuses.** An ABSENT key still means
  the documented default of 1. A key present and null used to reach sqlite as
  an `IntegrityError` out of the shipped CLI, so this replaces a traceback with
  a refusal; the three nullable numeric fields still accept null.
- **`claim_unit`'s predicate is `READY` or `PENDING`, not `READY` alone.** That
  is the set `select_ready_units` hands out (a PENDING unit whose dependencies
  are all DONE is selectable, and the claim is what flips it), so the literal
  `status='READY'` predicate the finding proposed would have refused every
  dependent unit in the product. Measured before shipping.

### Not closed, and what stands in the way

- **SQLite STRICT tables were considered and not used.** STRICT would enforce
  column types in the engine rather than in Python, but it requires a full
  table rebuild for every existing store (SQLite cannot make a table STRICT
  in place) and a schema-version bump, which is not an additive migration.
  Boundary validation gives the same protection with a founder-readable
  refusal instead of an `sqlite3.IntegrityError`, and STRICT remains the right
  answer for the next migration that rebuilds these tables anyway.
- **Only the numeric columns of `controller_units` are type-checked at their
  boundary.** `controller_dispatches.contract_revision` and `done_check_exit`
  are engine-supplied and never compared (only formatted into messages), so
  they cannot reproduce the defect today, and they were left alone rather than
  widened into.
- **`records.version` and `learning_rules.current_version` still compare a
  caller's `expected_version` untyped.** A wrong-typed one fails the
  optimistic-concurrency check and refuses `StaleIdentity`, so the failure
  direction is safe (a refusal, never a wrong accept) and the sweep left it
  listed rather than changed.
- **Everything here was measured on darwin, Python 3.9.6, sqlite 3.51.0.**
  sqlite's read-only WAL behaviour is not documented as platform specific, but
  the sidecar creation, the `immutable=1` behaviour and the non-writable
  directory results were observed on this machine only.

## Platform-blind tests: what the 2026-08-05 Windows audit closed, and the four gaps it leaves standing (2026-08-05)

Context, because it is the reason this section is not simply a bug list. CI run
30980039674 failed on both Windows legs at the FIRST step of the `store` job, and
GitHub stops a job at its first failing step, so `tools/test_bm_controller.py` has
never executed on a Windows runner even once. An audit
(`docs/program/absolute-lead/evidence/AUDIT-windows-blind-assumptions.md`) read the
new tests for platform assumptions and found eleven. Five would have turned the
Windows leg red the first time it got far enough to run them, three passed while
proving nothing there, two depend on the runner image, and one lives in a suite
Windows never runs.

What was fixed is written up in
`docs/program/absolute-lead/evidence/FIX-windows-blind-report.md`. What remains is
here.

- **Every Windows claim in that work is UNVERIFIED LOCALLY. There is no Windows
  machine in this loop.** The probes behind it were executed on macOS against
  `ntpath`, `shlex` and the shipped `_quote_path_for_local_shell` with
  `sys.platform` forced to `win32`, which gives exact Windows LEXICAL semantics and
  says nothing about syscall behaviour or about what `cmd.exe` then does with the
  string. CI is the instrument that settles it.
- **On Windows, nothing proves that a read-only diagnostic survives a store
  directory it cannot write.** `os.chmod(dir, 0o500)` cannot deny a directory write
  there (Python documents that only the read-only flag is settable and that all
  other bits are ignored), so the two tests that pose that question now MEASURE
  whether the denial works before relying on it and skip with a named reason where
  it does not. The real denial needs a directory ACL through `icacls`, which is not
  in the standard library. The same skip fires on any POSIX machine running the
  suite as root, which is a second vacuous pass this closes. What still runs
  everywhere: a read-only open creates no sidecar in a WRITABLE directory, and the
  read-only connection refuses a write.
- **The symlink-escape refusals depend on a Windows privilege the runner currently
  grants.** Creating a symbolic link needs `SeCreateSymbolicLinkPrivilege`. Both
  symlink tests ran and passed on both Windows legs of run 30980039674, so the
  privilege exists on today's image; the guard is on the PRIVILEGE rather than on
  the platform, so the coverage keeps running wherever it exists and degrades to a
  named skip rather than to an unexplained `OSError [WinError 1314]` in a test about
  path escapes.
- **`tools/bm_controller.py` still applies `shlex.quote` to each write-scope entry
  when composing the rollback** (the `git restore -- <paths>` half, not the
  `--git-dir/--work-tree` half, which was fixed). Ordinary entries such as `a.py`
  come back bare and are safe on both platforms, which is why the rollback tests are
  correct as they stand. An entry containing a SPACE comes back POSIX
  single-quoted, and `cmd.exe` reads those quotes as filename characters, so on
  Windows that rollback would fail to restore rather than restore the wrong thing.
  The failure direction is the safe one (a non-zero exit is the dirty-write-scope
  warning path, so the founder hears about it), no test exercises a spaced entry on
  the CLI path, and the one-line remedy is the same helper swap the `_git_prefix`
  call site just took. Left for a founder call rather than folded into an audit
  closure loop.
- **`tools/bm_controller.py` defaults an empty `done_check` to the string `true`.**
  That is a POSIX shell builtin. `cmd.exe` has no `true` and there is no `true.exe`
  in `System32`; it resolves on a GitHub-hosted Windows runner only because that
  image puts Git for Windows' `usr/bin` on PATH. The test literals that depended on
  the same accident were replaced with an interpreter-based command; the PRODUCT
  default was not, because changing it changes shipped behaviour for every unit that
  declares no done-check. If that image ever drops those Unix tools, a Windows unit
  with no done-check flips to REJECTED and reads like a controller bug.
- **`tools/test_bm_runtimes.py` still never runs on Windows.** Its `shlex.split`
  ordering bug is fixed anyway because the fix was one line, but the `suite` job is
  ubuntu and macos only, so that file has no Windows evidence behind it at all.

## L04: what founder mode, the ledger and the watchdog do NOT do (2026-08-05)

L04 added the founder-facing command surface, the record of decisions and
catch-ups behind it, the handback, the handover pages, and the half-hour
watchdog. The design is `docs/program/absolute-lead/DESIGN-L04.md`, and every
gap below is named in its section 16 with the same reason, so this page and that
design cannot drift into two different lists. Nothing here is a defect report:
each one is a thing the loop decided not to close, with what closing it would
take.

- **The watchdog is a due check on the Stop hook, so it cannot fire inside a
  turn that never ends.** It runs once per model turn, which means a single turn
  that works for two hours produces no catch-up until it finishes. Closing this
  means running the due check on a tool-use hook instead, which would run it on
  every command in every session for a catch-up that is due at most twice an
  hour. That trade is a measurement this loop did not make, so it was not taken.
  Until it is, `/brotherme-brief` is the manual path and it is one command.
- **The five-minute gap ceiling in the active-work clock is a chosen number, not
  a measured one.** The clock sums the gaps between recorded actions and counts
  each gap up to a ceiling, so an idle stretch stops adding time. Five minutes is
  argued from the two behaviours the founder asked for (a working session earns a
  catch-up in about half an hour, an idle one does not spam), and it is not
  derived from measured session history, because none is recorded. It is a named
  constant in `tools/bm_store.py`, so moving it is one edit, and any move should
  come with the measurement this loop could not make. This is the same honesty
  `tools/bm_controller.py` already prints beside its own dispatch timeout.
- **The ledger records the coordinator's judgement; your project's records stay
  the truth.** An entry in the ledger is a claim ABOUT the records, never a
  replacement for them. Where the two disagree, what the status view prints is
  what the records hold, and the disagreement is itself appended as a risk entry
  rather than resolved quietly. That means a wrong entry can sit in the ledger,
  visibly, until something supersedes it. Nothing edits or deletes an entry, so a
  correction is a new entry naming the one it corrects, and a reader has to read
  both. That is the cost of an append-only record and it was accepted on purpose.
- **The separate controller event table is deferred, and a test decides whether
  it is needed at all.** The store already appends an event for every controller
  move through its attribution trail, so a second table would be a second record
  of the same events. What lands instead is a replay test in
  `tools/test_bm_store.py`, `TestControllerEventsReplayFromAttribution`, which
  reconstructs a run's state sequence and each unit's status sequence from those
  events alone. Green means the table is redundant and the deferral was right.
  Red means it names the exact transition that leaves no event, and that output
  is the specification for the table in the next loop. The verdict of that run
  belongs beside this paragraph, written by whoever lands the test, and is not
  claimed here in advance of it.
- **The store and project command lines are not gated on consent, and never
  were.** A person who types `python3 tools/bm_project.py start` before running
  setup writes rows today. L04 did not change that and does not close it. The
  consent law this project states in `SECURITY.md` is about UNATTENDED writes,
  which is what a hook is, and the watchdog is gated for exactly that reason.
  This is recorded so the watchdog's gate is not read as a claim about the whole
  toolchain.
- **Handing control back does not cancel work already in flight.** Work
  dispatched before control changed hands stays dispatched. The handover page
  lists it by name with its age, rather than the system cancelling it, because
  cancelling would mean writing controller state from outside the controller.
  The disclosure costs a paragraph; the alternative costs a second writer of a
  state machine that is deliberately single-writer.
- **The handover pages cover one project at a time.** A folder holding several
  projects generates one set of pages per project. There is no cross-project
  rollup, and no page implies one.
- **Every capability L04 registers is beta, and none of them is certified.** The
  register `capabilities.status.json` is what states that, and `beta` there means
  real with a named gap. The gap these six share is not the code and not the
  tests: it is that nobody outside this project has run any of them. What
  certification would take is written down already, in `docs/ROADMAP.md` section
  1 and in `docs/closure/CLOSURE_REGISTER.md` items X-01 to X-06, and none of it
  closes by writing more code here.

## L05: what the live project view, the drawings and the alerts do NOT do (2026-08-05)

L05 added the page that shows where a project stands, the drawn vocabulary
behind it, the four levels of alert, the designed empty states of a project that
has barely started, and the offer to take the work back made visible on screen.
The design is `docs/program/absolute-lead/DESIGN-visual-surface.md`, and the ten
limits below are its section 10, in its own words, so this page and that design
cannot drift into two different lists. Every one of them was known before a line
was written, and each is here because a visual feature that over claims is worse
than the text it replaces.

- **The page is not live.** It is a snapshot. It cannot read your project
  records when you open it. Freshness comes from the page being written again,
  which happens on command and when a session stops, and from Claude
  republishing it, which is a request and not a guarantee. The page therefore
  states the newest record it was built from and a short code that changes when
  your records change, rather than the time it was drawn: two writes with
  nothing recorded in between produce the same file, and a tab open on an older
  code is visibly older.
- **The published page may be unavailable to you entirely.** Publishing needs a
  Pro, Max, Team or Enterprise plan, a session signed in with `/login`, the
  Anthropic API as the model provider, an organisation without CMEK, HIPAA or
  Zero Data Retention, and Claude Code 2.1.183 or later. It is off by default in
  Agent SDK, GitHub Action and MCP server contexts. Sessions using an API key, a
  gateway token or a cloud provider credential cannot publish at all. When any
  of these fails, you still get the file on disk, and the command says which one
  you got. The file is what this product promises; the published page is an
  addition.
- **The page holds no state.** Nothing you do on it is saved. There is no
  storage capability in Claude Code artifacts; the roster measured on this
  machine is `downloads` and `mcp` only. This is a feature: it makes it
  impossible for the view to become a second truth beside your records.
- **Nothing on the page can act on your project.** There is no path from a
  button back into the running session. Taking a decision back is a control that
  copies the exact words for you and a paste you make yourself, which keeps you
  in the loop by construction rather than by good intentions.
- **There are no pictures in the chat in the Claude Code terminal.** There is no
  surface for a drawing inside the conversation there, and no wording makes one
  appear. What exists instead is the page one click away, plus the same facts as
  plain text on the same turn. If you work in the Claude desktop chat or in
  Cowork rather than in the terminal, pictures inside the conversation may
  additionally be available there, which is a fact about where you are working
  and not something this product installs.
- **BrotherMode cannot install the status line or the clickable footer links.**
  Those are your settings, not a plugin's. Both are offered as a one line change
  you make yourself, and neither ships.
- **BrotherMode cannot show you your application running.** It does not run it,
  has no browser, and will not claim otherwise. There are no screenshots and no
  video of your product as proof. What stands in for it is the evidence line
  beside each claim, saying how that claim was checked.
- **Mermaid rendering inside a Claude Code artifact is unconfirmed** and this
  design does not rely on it. The drawings are plain inline shapes, which is
  unambiguously supported. The model behind them can emit mermaid later in one
  function if that changes.
- **Sending the page to your phone is not promised.** It needs Remote Control
  connected or a managed cloud session, so the page reaching you while you are
  away from the machine is not something this can offer.
- **Two smaller unknowns, recorded rather than hidden.** Whether the structured
  findings list can be driven outside code review, which would be the only
  native structured list renderer in the harness, and what content types the one
  hook that alters rendered output accepts. Neither is used here, and both are
  written down as probes worth five minutes each rather than left as folklore.

One more, and it belongs beside these because it is the same class of honesty.
Every capability this loop registers is beta, and none is certified. What `beta`
means in `capabilities.status.json` is real with a named gap, and the gap these
four share is the same one the founder mode rows above carry: nobody outside
this project has run any of them. The first fifteen minutes in particular is a
target rather than a measurement, and no first run by a person who had never
used this has been timed.

## The session cap reaches plugin installs only (2026-08-09)

The machine-wide session cap (`tools/bm_session_cap.py`, docs/HOOKS.md) is wired
in the plugin manifest `hooks/hooks.json` and not in `scripts/install.py`'s
clone-install path, so a clone install today gets seven hook commands and no
cap. The installer and `tools/test_install.py` are two hand-maintained mirrors
that must move together, and both sat outside the session-cap change's fence,
the same situation the Bash-audit hook was born into on 2026-08-01 and the same
closure applies: a later loop updates both together, and this entry is deleted
in that loop's commit. Until then, a clone user who wants the cap wires it
themselves in their own settings, the exact shape docs/HOOKS.md shows.

## The client name is in this machine's filesystem metadata, not only in files

Found 2026-08-17 by reading an ordinary `ls -la` line, not by a scan:

    -rwxr-xr-x@ 1 khalil.maaouni  <CLIENT>\Domain Users  3669  scripts/gates-when-quiet.sh

This Mac is domain-joined, so the client name is the GROUP on every file in
every repository here. Git stores no group ownership, which is why the
working-tree scrub and the history rewrite both read clean and were both right:
the name is not in any file and not in any commit.

MEASURED, so this is a latent vector and not a live leak: `Domain Users` appears
0 times in tracked files and 0 times in history in both repositories, no
delivery path uses `tar`, and the raw bytes of the newest handover archive carry
0 occurrences of either client token or of Domain Users. ZIP stores numeric
ids and no
POSIX group NAME, so `zipfile` cannot leak it.

WHERE IT WOULD LEAK, and the four delivery scans do not look at any of these:
- a `tar` archive, which unlike zip DOES record group names
- terminal output pasted into a document, an issue or a commit message
- a screenshot of a file listing
- anything preserving extended attributes or ACLs

The existing private-terms scan already catches the string in file CONTENTS,
because a client token is a substring of the group name. It does not read binary
archive metadata or images, and that is the actual gap.

CHEAPEST MITIGATION if a tar is ever added to a delivery path: pass
`--group=staff --owner=$(id -un)` (GNU tar) or pipe through a normalising step,
so the archive records a neutral group rather than the domain one.

## A hand-built probe of the fence hook is not the fence hook (measured 2026-08-19)

Three times in one night a session asked a control a question by re-deriving
its answer instead of letting the control answer, and twice it was wrong.

The sharpest instance, because it failed in the dangerous direction. A session
built a JSON payload by hand, piped it to the fence hook, read exit 0, and
concluded six files were free to edit. The real PreToolUse hook then refused
one of them by name, quoting a live fence line the probe had reported as
allowed. A synthetic probe is a SECOND IMPLEMENTATION of the question, and it
disagreed with the first silently.

Two narrower mistakes the same night, both caught: reading the word "fence"
out of the hook's own warning text and treating it as a refusal, when the
warning says the opposite (it could NOT enforce), and grepping STATE.md for
fence lines rather than asking the hook, which would have reported roughly
twenty live claims where the hook enforces almost none.

THE RULE THIS EARNS: the only authoritative answer to "may I write this file"
is attempting the write and letting the hook refuse. There is no supported
query verb today (`fences` is not a command this hook has), and until there is
one, treat any probe as a guess. Filed as M11.

WORTH KNOWING BESIDE IT: the hook does not gate Bash. A session working
through shell redirection crosses every fence unrefused and finds out only
afterwards, which is why editing shared code through the dedicated edit tool
is the safer habit even when a shell would be quicker.
