#!/usr/bin/env python3
"""The effect-class registry: what every command in this project is
allowed to do to disk, declared by name, so an eighth "documented
read-only, actually writes" command can never merge silently.

WHY THIS EXISTS
  An independent audit and an inventory sweep both found commands that
  DOCUMENT themselves as read-only while writing to disk. Seven point
  fixes closed seven instances one at a time; the eighth was still free
  to happen, because nothing FORCED a new command to declare what it does
  before it could ship. This file is that force: a registry every command
  in tools/ must appear in, plus (in tools/test_bm_effects.py) a test that
  discovers every command mechanically and refuses to pass if one is
  missing.

THE ROOT CAUSE (bm_store.py, see tools/bm_store.py's Store.__init__
around lines 6392-6502)
  CONSTRUCTING a writable bm_store.Store is itself a write, before any SQL
  runs: it calls _ensure_git_excludes (a write), _chmod_best_effort, sets
  PRAGMA journal_mode=WAL (creating -wal and -shm sidecar files on disk),
  and on a pre-existing database calls _verify_schema_or_raise(migrate=
  True), which can migrate the schema. None of that depends on which
  command runs afterwards, or on whether that command's own job is to
  only ever read. bm_store.ReadOnlyStore does none of it. A command whose
  docstring promises "read-only" or "writes nothing" but that opens a
  writable Store anyway is broken at the moment of construction, before
  its own first line of logic runs.

THE FIVE EFFECT CLASSES
  pure_read                     touches no writable Store, opens no file
                                 for writing, spawns nothing. What it
                                 promises is what it does.
  ledger_write                  mutates lifecycle or knowledge-base
                                 records through the store (claim, task,
                                 forecast, alert, knowledge, learning,
                                 fence, checkpoint rows and the like).
  project_write                 (re)generates a file on disk that a
                                 project or the founder reads later
                                 (STATE.md, CANVAS.md, a rendered page, an
                                 exported document, a registry-driven
                                 adapter file).
  external_write                reaches outside this process: spawns a
                                 subprocess, launches another program, or
                                 makes a network call.
  destructive_external_action   permanently removes data this store or
                                 project owns; irreversible by design, not
                                 by accident.

THE CONTRACT
  REGISTRY["module.py"]["command verb"] = one of the five class
  constants below. "command verb" is spelled exactly as a founder types
  it: a single word for a flat subcommand ("status"), space-joined words
  for a two-level subcommand exactly as typed on the command line
  ("alert list", "forecast show", "task add"), never a hyphen standing in
  for a space that was never there.

  declared() never guesses. An (module, command) pair this registry does
  not carry raises UndeclaredCommand, loudly, rather than silently
  defaulting to any one class: a default is exactly how the eighth
  instance of this bug would have slipped past everyone again.

Python 3.9, standard library only. No network, no subprocess, no file
write anywhere in this module: bm_effects.py list is itself pure_read,
and it is not declared in its own registry because it is the register,
not one of the commands the register is about.

Usage:
  python3 tools/bm_effects.py list [--class NAME]
"""
import sys

# ---------------------------------------------------------------------------
# The five classes.
# ---------------------------------------------------------------------------

PURE_READ = "pure_read"
LEDGER_WRITE = "ledger_write"
PROJECT_WRITE = "project_write"
EXTERNAL_WRITE = "external_write"
DESTRUCTIVE_EXTERNAL_ACTION = "destructive_external_action"

CLASSES = (PURE_READ, LEDGER_WRITE, PROJECT_WRITE, EXTERNAL_WRITE,
           DESTRUCTIVE_EXTERNAL_ACTION)


class UndeclaredCommand(Exception):
    """Raised by declared() for any (module, command) pair this registry
    does not carry. Never caught and defaulted anywhere in this file: a
    caller that wants a default has to write one, on purpose, itself."""


# ---------------------------------------------------------------------------
# The registry.
#
# Seeded from the measured inventory (audit + sweep, evidence cited beside
# each entry that is not a plain, uncontroversial write), then extended to
# the FULL set of commands this project ships, because
# tools/test_bm_effects.py's completeness test discovers every `def cmd_*`
# in every tools/bm_*.py and tools/brothermode_cli.py and refuses to pass
# with one left out. Two modules (tools/bm_lint_walltime.py,
# tools/bm_progress_check.py, tools/bm_statusline.py) carry no `cmd_`
# functions at all (a bare main() is their whole CLI); they are registered
# here by module + verb exactly as tools/test_bm_effects.py invokes them,
# even though the completeness test's mechanical discovery never reaches
# them on its own.
#
# Three modules dispatch via a plain if/elif chain on sys.argv rather than
# a module-level dict (tools/bm_ledger.py, tools/bm_telemetry.py,
# tools/bm_autosave.py): the completeness test's mechanical discovery
# cannot parse that shape into a verb table and reports every `cmd_`
# function in those files as NO-DATA rather than silently passing over
# them. tools/bm_ledger.py's four commands are still registered below
# because the audit named them explicitly; tools/bm_telemetry.py and
# tools/bm_autosave.py are not, because nothing named them and inventing
# a classification for an unparsed command is exactly the guessing this
# registry exists to refuse.
# ---------------------------------------------------------------------------

REGISTRY = {

    # -- bm_project.py --------------------------------------------------
    # FIXED 2026-08-11 (codex-audit-split-2026-08-10-night.md HIGH #1 for
    # tools/bm_effects.py, R4-TRIAGE-2026-08-11.md row 1). Module docstring
    # (tools/bm_project.py:34) documents status, next, task list (via
    # "review"/read accessors), alert list and forecast show as "read
    # accessors only". status, next and forecast show used to call _store()
    # (bs.Store(root, create=False), a writable Store, never ReadOnlyStore)
    # and were declared pure_read anyway: documented pure, actually wrote,
    # demonstrated by tools/test_bm_effects.py's
    # TestPurityUnderAStoreThatIsBehind migrating a behind-schema store
    # through all three. All three now call _read_store()
    # (bs.ReadOnlyStore) instead; alert list already did (see its own
    # comment below), and this declaration is therefore now true rather
    # than aspirational.
    # -- bm_handover.py -------------------------------------------------
    # The baton ceremony (2026-08-11). Declared by the session that wrote the
    # verbs, rather than guessed by the session that found them undeclared.
    # skeleton writes pack pages inside the project through
    # bm_store.write_generated_document, so it is a project write and nothing
    # wider. zip is the one verb that leaves the project: it lands an archive
    # in the handovers directory outside the tree, which is exactly what
    # external_write means here, and it is why zip is the verb worth a second
    # look in any review. detect and verify-close open the store read only and
    # write nothing at all; verify-close returns a verdict through its exit
    # code, which is not a write.
    # owed (2026-08-12): pure_read like detect. It reads .git/HEAD and the
    # branch ref file, lists docs/handover, and stats a zip. It opens no
    # store and writes nothing, which is why it can run inside a session
    # start line that has already passed the consent door.
    "bm_handover.py": {
        "owed": PURE_READ,
        "skeleton": PROJECT_WRITE,
        "verify-close": PURE_READ,
        "zip": EXTERNAL_WRITE,
        "detect": PURE_READ,
    },

    # -- bm_plan.py ------------------------------------------------------
    # 2026-08-29. Landed with the W9 controller train and was undeclared here
    # while also missing from py-modules, the no-network claim and the
    # reviewed write-site inventory: four registries at once, which is one
    # pattern rather than four oversights.
    #
    # Declared by READING each verb rather than by pattern-matching the name,
    # which is the whole reason this table is checked instead of trusted.
    # record and approve both reach PlanStore._write, an fdopen("w") into the
    # project, so both are project writes and approve is the one worth a second
    # look in any review because it records a human approval. show calls
    # store.read and prints; check is documented in its own source as "a thin
    # read then check wrapper" over check_plan_gate and returns its verdict
    # through an exit code, which is not a write.
    "bm_plan.py": {
        "record": PROJECT_WRITE,
        "approve": PROJECT_WRITE,
        "show": PURE_READ,
        "check": PURE_READ,
        # redirect appends a redirect to the plan and bumps plan_version,
        # a project store write like record and approve.
        "redirect": PROJECT_WRITE,
    },

    "bm_project.py": {
        "status": PURE_READ,
        "next": PURE_READ,
        "alert list": PURE_READ,
        "forecast show": PURE_READ,
        "receipt list": PURE_READ,
        "start": LEDGER_WRITE,
        "task add": LEDGER_WRITE,
        "task start": LEDGER_WRITE,
        "task transition": LEDGER_WRITE,
        "forecast add": LEDGER_WRITE,
        "receipt add": LEDGER_WRITE,
        "review": LEDGER_WRITE,
        "alert raise": LEDGER_WRITE,
        "alert resolve": LEDGER_WRITE,
        "deliver": LEDGER_WRITE,
        "export": PROJECT_WRITE,
        "purge": DESTRUCTIVE_EXTERNAL_ACTION,
    },

    # -- bm_threads.py ----------------------------------------------------
    # dashboard (tools/bm_threads.py:871) ignores --help entirely and
    # calls _refresh_root_view at :899, rewriting STATE.md, on every
    # invocation including one carrying --help as an argument to the
    # subcommand. recommend's own docstring (:531) says "Advice ONLY.
    # Never touches the mode file", then opens a writable
    # `_store().Store(root, create=False)` at :548 whenever it needs to
    # count active threads. Both documented pure, both currently write.
    # off/start/checkpoint/decide/send/park/resume/complete/adopt
    # RECLASSIFIED 2026-08-11 (codex-audit-split-2026-08-10-night.md MEDIUM
    # #6, R4-TRIAGE-2026-08-11.md row 1): each of these regenerates a real
    # project file on disk (STATE.md via _refresh_root_view, inbox.md,
    # outbox.md or digest.md), not only a store row -- see
    # tools/bm_threads.py:748 (start's STATE.md refresh), :821 (checkpoint's
    # digest.md/outbox.md), :888 (send's inbox.md), and the shared
    # _transition_cmd/_refresh_root_view path park/resume/complete/adopt all
    # go through. project_write is the class the registry's own THE FIVE
    # EFFECT CLASSES above defines for exactly this: "(re)generates a file
    # on disk that a project or the founder reads later". "on" is untouched
    # by this finding (the audit does not name it, and it only ever writes
    # thread-mode.json, never STATE.md/inbox/outbox/digest) and stays
    # ledger_write.
    "bm_threads.py": {
        "dashboard": PURE_READ,
        "recommend": PURE_READ,
        "on": LEDGER_WRITE,
        "off": PROJECT_WRITE,
        "start": PROJECT_WRITE,
        "checkpoint": PROJECT_WRITE,
        "decide": PROJECT_WRITE,
        "send": PROJECT_WRITE,
        "park": PROJECT_WRITE,
        "resume": PROJECT_WRITE,
        "complete": PROJECT_WRITE,
        "adopt": PROJECT_WRITE,
    },

    # -- bm_learn.py ------------------------------------------------------
    # lookup RECLASSIFIED 2026-08-11 (codex-audit-split-2026-08-10-
    # night.md HIGH #1 for tools/bm_effects.py, R4-TRIAGE-2026-08-11.md row
    # 1), mirroring tools/bm_docs.py's cmd_tier precedent (commit d9d8003):
    # its own docstring used to say "Writes NOTHING, ever"; its body calls
    # _retrieve_command, which reaches _store() at :159 -- a writable
    # Store, always, even for this mode. The docstring is now corrected
    # (see cmd_lookup) rather than left false, and this row now says the
    # true class. It SHOULD be pure_read and is not yet, for the exact same
    # reason bm_docs.py's tier is not: the store method its own path calls
    # (retrieve_learning_rules) exists on bm_store.Store only, not on
    # bm_store.ReadOnlyStore, and adding it is a bm_store.py change out of
    # this fix's scope. Every other command in this file is a
    # knowledge-base mutation (capture a candidate, approve or reject it,
    # promote or cancel it, link, merge, supersede, note) or a query that
    # was never itself documented as read-only, so it stays registered as
    # the record-writing class rather than invented into pure_read on no
    # evidence.
    "bm_learn.py": {
        "lookup": LEDGER_WRITE,
        "capture": LEDGER_WRITE,
        "note": LEDGER_WRITE,
        "notes": LEDGER_WRITE,
        "resolve-note": LEDGER_WRITE,
        "index-status": LEDGER_WRITE,
        "rebuild-index": LEDGER_WRITE,
        "outcome": LEDGER_WRITE,
        "inbox": LEDGER_WRITE,
        "metrics": LEDGER_WRITE,
        "candidates": LEDGER_WRITE,
        "show-candidate": LEDGER_WRITE,
        "grant-approval": LEDGER_WRITE,
        "grant-state-receipt": LEDGER_WRITE,
        "approve": LEDGER_WRITE,
        "reject": LEDGER_WRITE,
        "rules": LEDGER_WRITE,
        "apply": LEDGER_WRITE,
        "relevant": LEDGER_WRITE,
        "applications": LEDGER_WRITE,
        "disposition": LEDGER_WRITE,
        "promote": LEDGER_WRITE,
        "cancel": LEDGER_WRITE,
        "classify": LEDGER_WRITE,
        "should-retrieve": LEDGER_WRITE,
        "why": LEDGER_WRITE,
        "deprecate": LEDGER_WRITE,
        "forget": LEDGER_WRITE,
        "conflicts": LEDGER_WRITE,
        "link": LEDGER_WRITE,
        "merge": LEDGER_WRITE,
        "supersede": LEDGER_WRITE,
        "resolve-conflict": LEDGER_WRITE,
        "confirm": LEDGER_WRITE,
        "rework": LEDGER_WRITE,
        "escaped-defect": LEDGER_WRITE,
        "repeat-check": LEDGER_WRITE,
        "loop-failures": LEDGER_WRITE,
        "rule-outcomes": LEDGER_WRITE,
        "verify": LEDGER_WRITE,
    },

    # -- bm_docs.py -------------------------------------------------------
    # tier's own docstring (:3150) says "Writes nothing"; its body calls
    # _store() at :3154, a writable Store. Documented pure, currently
    # writes. facts (:3256) is not documented read-only in its own words
    # (no "writes nothing" claim), so it stays out of pure_read even
    # though it shares tier's shape; verify-docs (:3667) and generate are
    # what the two remaining commands actually are.
    "bm_docs.py": {
        # RECLASSIFIED 2026-08-10, from PURE_READ, and the direction matters.
        # This is not a test being relaxed to go green; it is a declaration
        # being corrected to match measured behaviour. `tier` opens a WRITABLE
        # Store, so against a database one schema version behind it MIGRATES
        # IT. Its docstring used to say "Writes nothing", which was the actual
        # defect, and that sentence is now fixed at tools/bm_docs.py.
        # It SHOULD be pure_read and cannot be yet: Generator.__init__ calls
        # store.note_anchor_reports(), and its row builders call
        # store.list_learning_rules() and store.list_notes(), none of which
        # exists on ReadOnlyStore. Adding those three changes the store's own
        # public surface and deserves its own review. Until then this row says
        # the true thing rather than the flattering one.
        "tier": LEDGER_WRITE,
        "verify-docs": PURE_READ,
        "generate": PROJECT_WRITE,
        "capability-status": PROJECT_WRITE,
        "roadmap-status": PROJECT_WRITE,
        "facts": LEDGER_WRITE,
    },

    # -- bm_packs.py --------------------------------------------------------
    # stakes RECLASSIFIED 2026-08-11 (codex-audit-split-2026-08-10-
    # night.md HIGH #1 for tools/bm_effects.py, R4-TRIAGE-2026-08-11.md row
    # 1), mirroring tools/bm_docs.py's cmd_tier precedent (commit d9d8003):
    # its own docstring says "Generates nothing (spec A.2: nothing until
    # asked)" -- true about the pack FILE, but its body still calls
    # _store() at :1086/:1120/:1189, a writable Store, always. The
    # docstring is now corrected (see cmd_stakes) to say so plainly, and
    # this row now says the true class. It SHOULD be pure_read and is not
    # yet, for the same reason bm_learn.py's lookup is not: the store
    # methods its own path calls (get_learning_candidate, blocking_alerts,
    # gate_change_set) exist on bm_store.Store only, and adding read-only
    # counterparts to bm_store.ReadOnlyStore is a bm_store.py change out of
    # this fix's scope.
    "bm_packs.py": {
        "stakes": LEDGER_WRITE,
        "pack": PROJECT_WRITE,
        "review": PROJECT_WRITE,
    },

    # -- bm_runtimes.py -----------------------------------------------------
    "bm_runtimes.py": {
        "list": PURE_READ,          # no Store, pure in-memory validation
        "check": PURE_READ,         # compares disk to a fresh render only
        "emit": PROJECT_WRITE,
    },

    # -- bm_cursor.py ---------------------------------------------------
    # The Cursor compatibility mode (2026-08-18), declared when the
    # consolidation brought it onto main and TestCompleteness named all
    # fourteen verbs at once. Two shapes decide most of this block. First,
    # every verb except "status" calls _ensure_mailbox(), which CREATES
    # the mailbox directories, so none of them can be pure_read: the
    # purity test runs a pure_read command against a throwaway project and
    # requires the directory to come out byte-identical, which a mkdir
    # breaks. Second, the git helpers shell out, and external_write is the
    # class for spawning a subprocess, per the bm_autosave.py "recover"
    # precedent below.
    "bm_cursor.py": {
        "status": PURE_READ,        # reads the mailbox, creates nothing
        "doctor": EXTERNAL_WRITE,   # runs the hook under test as a subprocess
        "emit-rules": PROJECT_WRITE,
        "emit-hooks": PROJECT_WRITE,
        "dispatch": EXTERNAL_WRITE,     # _git_worktree_add, git subprocess
        "claim": PROJECT_WRITE,
        "claim-next": PROJECT_WRITE,    # same cmd_claim, second spelling
        "record-result": PROJECT_WRITE,
        "adopt": EXTERNAL_WRITE,        # subprocess.run on the adopted work
        "poll": PROJECT_WRITE,          # _ensure_mailbox creates directories
        "list": PROJECT_WRITE,          # same, so not pure_read despite reading
        # cancel and worktree-remove both reach _git_worktree_remove, which
        # runs `git worktree remove --force` (tools/bm_cursor.py:622). The
        # force flag discards uncommitted work in that worktree, which is
        # the destructive class exactly: irreversible by design, not by
        # accident. worktree-create is merely external because it only adds.
        "cancel": DESTRUCTIVE_EXTERNAL_ACTION,
        "worktree-create": EXTERNAL_WRITE,
        "worktree-remove": DESTRUCTIVE_EXTERNAL_ACTION,
    },

    # -- bm_sentinel.py -------------------------------------------------
    "bm_sentinel.py": {
        "remember-knowledge": LEDGER_WRITE,
        "remember-procedural": LEDGER_WRITE,
        "status-set": LEDGER_WRITE,
        "check": LEDGER_WRITE,
        "judge": LEDGER_WRITE,
        "list": LEDGER_WRITE,
        "stats": LEDGER_WRITE,
        "retire": LEDGER_WRITE,
    },

    # -- bm_bash_audit.py -------------------------------------------------
    "bm_bash_audit.py": {
        "pre": LEDGER_WRITE,
        "post": LEDGER_WRITE,
    },

    # -- bm_autonomy.py -----------------------------------------------------
    # show, gate-check and status use _read_store() -> bs.ReadOnlyStore
    # (:238). Every other command mutates autonomy/lifecycle state.
    "bm_autonomy.py": {
        "show": PURE_READ,
        "gate-check": PURE_READ,
        "status": PURE_READ,
        "sign": LEDGER_WRITE,
        "assume": LEDGER_WRITE,
        "interrupt": LEDGER_WRITE,
        "spend": LEDGER_WRITE,
        "pause": LEDGER_WRITE,
        "resume": LEDGER_WRITE,
        "stop": LEDGER_WRITE,
        "revoke": LEDGER_WRITE,
        "queue-human-step": LEDGER_WRITE,
        "human-steps": LEDGER_WRITE,  # dual-mode: --resolve writes
        "checkpoint": LEDGER_WRITE,
    },

    # -- bm_controller.py -----------------------------------------------
    # status uses _read_store() -> bs.ReadOnlyStore (:4468); its own
    # docstring calls it "the one command that never writes".
    #
    # start, step and record-result RECLASSIFIED 2026-08-11 (codex-audit-
    # split-2026-08-10-night.md HIGH #4, R4-TRIAGE-2026-08-11.md row 1):
    # they can run the subprocess-backed checker (its one execution
    # primitive is Runner.run at tools/bm_controller.py:306, a real
    # subprocess.run) through the check-execution path at :307-:1248, and
    # unattended start/step additionally runs a real fence-canary
    # subprocess (:5068). The five effect classes above define any
    # subprocess spawn as external_write, which is a bigger effect than the
    # ledger_write these three used to claim, not a smaller one; plan,
    # stop, adopt, resume and complete are untouched by this finding and
    # stay ledger_write.
    #
    # restore and redirect landed 2026-08-30 (F3 durable checkpoints,
    # F1 mid-stream steering) and were declared here by READING each
    # verb, the same discipline as every entry above. restore is
    # pure_read as a measured claim: cmd_restore opens the store through
    # _read_store() (bs.ReadOnlyStore) and ControllerEngine.
    # restore_checkpoint calls only get_checkpoint, get_run and
    # list_units; its own docstring says "a restore is a read, never a
    # rollback". redirect is project_write, not ledger_write: engine.
    # inject_redirect routes the note into tools/bm_plan.py's
    # PlanStore.record_redirect, which reaches PlanStore._write, an
    # fdopen("w") into the project, the same reason bm_plan.py's own
    # record and approve are project writes; the liveness checkpoint it
    # drops beside the bump is a store row and smaller than the file
    # write.
    "bm_controller.py": {
        "status": PURE_READ,
        "start": EXTERNAL_WRITE,
        "step": EXTERNAL_WRITE,
        "plan": LEDGER_WRITE,
        "record-result": EXTERNAL_WRITE,
        "stop": LEDGER_WRITE,
        "adopt": LEDGER_WRITE,
        "resume": LEDGER_WRITE,
        "complete": LEDGER_WRITE,
        "restore": PURE_READ,
        "redirect": PROJECT_WRITE,
    },

    # -- bm_lead.py -------------------------------------------------------
    # status and decisions pass write=False (:1560, :1585) into the
    # shared store opener, which resolves to bs.ReadOnlyStore.
    "bm_lead.py": {
        "status": PURE_READ,
        "decisions": PURE_READ,
        "outcome": LEDGER_WRITE,
        "brief": LEDGER_WRITE,
        "insight": LEDGER_WRITE,
        "handback": LEDGER_WRITE,
        "handover-pack": LEDGER_WRITE,
        "watchdog": LEDGER_WRITE,
    },

    # -- bm_view.py -------------------------------------------------------
    # alert passes write=False (:1360) -> ReadOnlyStore. doorway and
    # explain touch no store and open no file at all (verified: neither
    # function's body references _store_or_refuse, Store, or open()).
    #
    # url RECLASSIFIED 2026-08-11, DOWN from project_write (codex-audit-
    # split-2026-08-10-night.md LOW #7, R4-TRIAGE-2026-08-11.md row 1):
    # without --set it opens ReadOnlyStore (write=bool(kv.get("set")) at
    # :1236) and only ever reads; with --set, cmd_url's own body (:1259)
    # calls store.record_view(...), one store row, and generates no file
    # anywhere in the function. project_write was conservative rather than
    # measured; ledger_write is what this command's actual maximum effect
    # is.
    "bm_view.py": {
        "alert": PURE_READ,
        "doorway": PURE_READ,
        "explain": PURE_READ,
        "render": PROJECT_WRITE,
        "brief-page": PROJECT_WRITE,
        "url": LEDGER_WRITE,  # dual-mode: --set records a view row, no file
    },

    # -- bm_ledger.py -----------------------------------------------------
    # No Store anywhere in this file (a JSONL ledger under
    # BROTHERMODE_VAULT, not tools/bm_store.py). report only calls
    # _read_rows(). declare/amend append a row; close appends a graded
    # row. Dispatch is a plain if/elif chain on sys.argv (main(), not a
    # module-level dict), so tools/test_bm_effects.py's mechanical
    # discovery cannot parse a verb table out of this file and reports
    # every cmd_* here as NO-DATA; these four entries are registered
    # anyway because the audit named them by hand.
    "bm_ledger.py": {
        "report": PURE_READ,
        "declare": LEDGER_WRITE,
        "amend": LEDGER_WRITE,
        "close": LEDGER_WRITE,
    },

    # -- bm_statusline.py -------------------------------------------------
    # A bare main() (tools/bm_statusline.py:182), no `cmd_` functions:
    # ReadOnlyStore per its own module docstring and :168. Registered
    # under the verb "main" the way this file's own CLI is invoked (no
    # subcommand at all); test_bm_effects.py's mechanical `def cmd_`
    # discovery never reaches this module, so this entry exists purely
    # for the purity and --help suites.
    "bm_statusline.py": {
        "main": PURE_READ,
    },

    # -- bm_lint_walltime.py ----------------------------------------------
    # This project's own lint template (tools/bm_lint_walltime.py): reads
    # *.py files and writes stdout only, by design (see its own
    # docstring, "Python 3.9, standard library only. No network. No
    # subprocess"). A bare main(argv), no `cmd_` functions.
    "bm_lint_walltime.py": {
        "main": PURE_READ,
    },

    # -- bm_progress_check.py -----------------------------------------------
    # Its own module docstring states "EFFECT CLASS: pure_read ... This
    # tool writes NOT ONE BYTE anywhere, ever". A bare main(argv), no
    # `cmd_` functions.
    "bm_progress_check.py": {
        "status": PURE_READ,
    },

    # -- bm_forecast.py -------------------------------------------------
    # Added 2026-08-12. Four of its six verbs read the append-only
    # forecast log and decide; two append exactly one line each through
    # the single `_append_record` helper, which is the only write site in
    # the file and opens with O_APPEND at mode 0600. record and actual are
    # therefore ledger_write, the same class bm_project.py `forecast add`
    # already carries: an append to a project-local record of what was
    # decided, touching one file inside .brothermode and nothing else, no
    # store, no network, no subprocess. calibrate, apply, check and list
    # open that same file read-only and change no bytes.
    "bm_forecast.py": {
        "record": LEDGER_WRITE,
        "actual": LEDGER_WRITE,
        "calibrate": PURE_READ,
        "apply": PURE_READ,
        "check": PURE_READ,
        "list": PURE_READ,
    },

    # -- bm_toolkit.py --------------------------------------------------
    # Added 2026-08-12, the Toolkit's first verb. Its own module docstring
    # states EFFECT CLASS: pure_read, and it earns that the same way
    # bm_idle.py does: it walks the plugin cache, the skills directory and
    # the settings layers, reads JSON, and constructs no Store, because
    # constructing one is itself a write. Six write sites by the scanner's
    # count and every one of them is the stdout or stderr funnel. The
    # inventory it produces is the input every later toolkit verb consumes,
    # so a verb here that could mutate the machine it is describing would be
    # the wrong shape from the start.
    # "capabilities" (TK3, 2026-08-15) is the same shape: it renders
    # capability records built from the already-collected inventory data
    # (derived) plus an optional hand-typed override file (hand-asserted),
    # and constructs no Store, same as every other verb here.
    # "conflicts" (TK2, 2026-08-15, declared late: TK3's builder caught the
    # omission the same day) is the same shape again: it reads the same
    # inventory plus a conflict-class table and writes only its own verdict
    # report to stdout.
    # "route" (TK4, 2026-08-15) is the same shape once more: it reads the
    # same inventory plus a routing table (tools/toolkit_routes.json or the
    # embedded default) and writes only its resolved-or-degraded report to
    # stdout; presence is checked against the already-collected inventory,
    # never by running the routed capability.
    "bm_toolkit.py": {
        "inventory": PURE_READ,
        "json": PURE_READ,
        "conflicts": PURE_READ,
        "capabilities": PURE_READ,
        "route": PURE_READ,
    },

    # -- bm_idle.py -----------------------------------------------------
    # Added 2026-08-12. Its own module docstring states "EFFECT CLASS:
    # pure_read ... writes NOT ONE BYTE anywhere, ever", and it was built
    # that way deliberately: a mutating verb would have put it in the
    # reviewed write-site inventory for no gain, since a human or a model
    # edits docs/plan/QUEUE.json directly. It borrows bm_store.resolve_root
    # for --root resolution only and never constructs a Store, because
    # constructing one is itself a write.
    "bm_idle.py": {
        "check": PURE_READ,
        "depth": PURE_READ,
        "next": PURE_READ,
        "list": PURE_READ,
    },

    # -- bm_reality.py --------------------------------------------------
    # A5, the smallest verified-reality record (docs/NORTH-STAR-CHAIN.md's
    # own terminal stage). A bare main(argv), no `cmd_` functions, same
    # dispatch shape as bm_idle.py just above (its own structural
    # template) and bm_stall.py just below, so mechanical discovery finds
    # nothing here and this entry is hand-declared rather than caught by
    # TestCompleteness. `accept`, `enter` and `defect` each open a
    # writable bm_store.Store and append exactly one reality_records row;
    # `defect` also regenerates docs/plan/QUEUE.json first (a
    # project_write in its own right, folded into its ledger_write
    # declaration the same way a composite bm_project.py command is,
    # rather than invented as a sixth class for one verb). `show` opens
    # ONLY bm_store.ReadOnlyStore and writes nothing, including under
    # --help: tools/test_bm_effects.py's own purity suite runs it in a
    # sandbox and proves that, rather than trusting this comment.
    "bm_reality.py": {
        "accept": LEDGER_WRITE,
        "enter": LEDGER_WRITE,
        "defect": LEDGER_WRITE,
        "show": PURE_READ,
    },

    # -- bm_stall.py ----------------------------------------------------
    # Loop SD (docs/plan/PROGRAM-PLAN-2026-08-10.md, section "Loop SD"):
    # the active stall/dead-owner/orphan sweep. Its own module docstring
    # states "EFFECT CLASS: pure_read ... This file writes NOT ONE BYTE
    # anywhere, ever", opens only bm_store.ReadOnlyStore, and spawns no
    # subprocess (every SD5 clearing verb it prints is a string it built,
    # never executed). A bare main(argv), no `cmd_` functions.
    "bm_stall.py": {
        "sweep": PURE_READ,
    },

    # -- bm_escalate.py -----------------------------------------------------
    # The last of the six registration deltas listed at the end of
    # docs/ESCALATION.md, applied 2026-08-17. The other five were already in
    # place; this one was not, and no suite went red over it, because this
    # registry validates the entries it HAS, so an undeclared module is silent
    # rather than failing. That silence is the whole reason the delta list had
    # to exist on paper, and it is a finding in its own right, not a footnote.
    # Classes read off the code rather than copied from the delta list, which
    # named five verbs. There are SIX: `forcing` was missing from it.
    # ledger_write, each confirmed by a call to this module's own append():
    # attempt (:364) appends at :383, forcing (:401) appends at :408, resolve
    # (:445) appends at :446. append() writes only the JSON record it was
    # handed, at mode 0600, which is the shape write_sites.json reviewed at 3.
    # pure_read, each confirmed to reach no append() and no subprocess: check
    # (:415) returns CONTINUE or ESCALATE, packet (:425) prints the packet it
    # built as a string, open (:453) lists what is open for a close check.
    "bm_escalate.py": {
        "attempt": LEDGER_WRITE,
        "forcing": LEDGER_WRITE,
        "resolve": LEDGER_WRITE,
        "check": PURE_READ,
        "packet": PURE_READ,
        "open": PURE_READ,
    },

    # -- bm_store.py --------------------------------------------------------
    # dump (:18087) and handovers (:18119) explicitly open
    # ReadOnlyStore ("dump is a diagnostic, never creates" / "reads
    # through ReadOnlyStore and never creates the store it is reporting
    # on"); verify()'s own docstring (:17248) says "Read-only: opens via
    # ReadOnlyStore, never creating the thing it is diagnosing".
    # dashboard (:18077) calls _refresh_state_view, an unconditional
    # write to STATE.md, so it is a real write despite reading first.
    #
    # claim/park/resume/complete/adopt/checkpoint/decide/handover-ack
    # RECLASSIFIED 2026-08-11 (codex-audit-split-2026-08-10-night.md
    # MEDIUM #6, R4-TRIAGE-2026-08-11.md row 1): every one of them calls
    # _refresh_state_view(root) (claim at :17922 and :17984, park/resume/
    # complete/adopt via the shared _cmd_transition at :17984, checkpoint
    # at :18040, decide at :18072, handover-ack at :18157), which
    # regenerates the real project file STATE.md, not only a store row.
    # project_write is what the registry's own THE FIVE EFFECT CLASSES
    # above defines for exactly this. init is untouched by this finding
    # (it creates the store itself, which is a bigger effect already
    # covered by ledger_write's "lifecycle... records through the store",
    # and the audit does not name it) and stays ledger_write.
    "bm_store.py": {
        "dump": PURE_READ,
        "handovers": PURE_READ,
        "verify": PURE_READ,
        "init": LEDGER_WRITE,
        "claim": PROJECT_WRITE,
        "park": PROJECT_WRITE,
        "resume": PROJECT_WRITE,
        "complete": PROJECT_WRITE,
        "adopt": PROJECT_WRITE,
        "checkpoint": PROJECT_WRITE,
        "decide": PROJECT_WRITE,
        "handover-ack": PROJECT_WRITE,
        "dashboard": PROJECT_WRITE,
    },

    # -- bm_fence_hook.py -----------------------------------------------
    # hook (:1018) reads a JSON payload and, on the fast-open/fast-close
    # paths, never calls ensure_token or opens a writable Store; decide()
    # itself only ever opens bs.ReadOnlyStore (:749). session-label
    # (:1060) calls session_label() -> ensure_token(), which writes a
    # token file (:318-319) when one does not exist yet; whoami (:1104)
    # delegates straight to cmd_session_label and inherits that write.
    #
    # hook RECLASSIFIED 2026-08-11 (codex-audit-split-2026-08-10-night.md
    # HIGH #2, R4-TRIAGE-2026-08-11.md row 1), demonstrated by
    # tools/test_bm_effects.py's TestPurity with a real write-shaped
    # payload against a store that holds an active claim: decide() (:817)
    # calls active_claims() (:734), and once that returns at least one row
    # it calls session_label() (:912-913) UNCONDITIONALLY, before ever
    # checking whether the target path overlaps the claim, which creates
    # the fence token file exactly like cmd_session_label below. This
    # matches session-label's own classification: ledger_write.
    "bm_fence_hook.py": {
        "hook": LEDGER_WRITE,
        "session-label": LEDGER_WRITE,
        "whoami": LEDGER_WRITE,
        # query (2026-08-20, the real M11 fix) answers "would the hook
        # allow this write" by calling decide() directly, the same code
        # path as hook, so it inherits hook's classification for the
        # same reason: decide() can call session_label() -> ensure_token()
        # and create the fence token file.
        "query": LEDGER_WRITE,
    },

    # -- bm_docs_export.py --------------------------------------------------
    "bm_docs_export.py": {
        "report": PURE_READ,   # probes for optional export backends only
        "export": PROJECT_WRITE,
    },

    # -- bm_continue.py -----------------------------------------------------
    # Generates the handoff packet from store rows and launches the next
    # session as a real subprocess: an external action, not a file write
    # alone.
    "bm_continue.py": {
        "continue": EXTERNAL_WRITE,
    },

    # -- brothermode_cli.py -------------------------------------------------
    # A thin dispatcher (see its own per-command docstrings): each verb
    # loads and calls the real adapter's main(), so its class mirrors
    # the adapter it delegates to. update spawns `git ls-remote` and
    # makes an outbound network request (:401).
    #
    # doctor and recover RECLASSIFIED 2026-08-11 (codex-audit-split-2026-08-
    # 10-night.md HIGH #3 and #5, R4-TRIAGE-2026-08-11.md row 1). doctor
    # delegates to scripts/doctor.py, whose checks spawn real subprocesses
    # (scripts/doctor.py:267) and can create a temporary project and file
    # (scripts/doctor.py:279); the registry's own THE FIVE EFFECT CLASSES
    # above defines any subprocess spawn as external_write, and "read-only"
    # in its old comment here was the same kind of aspirational label the
    # rest of this audit corrects rather than a measured fact. recover
    # delegates to bm_autosave's recovery path (tools/brothermode_cli.py:
    # 291), which invokes Git via subprocess and, when a snapshot exists,
    # creates a temporary worktree (tools/bm_autosave.py:1821) -- also
    # external_write, and a bigger effect than the project_write this row
    # used to claim, not a smaller one.
    "brothermode_cli.py": {
        "status": PURE_READ,     # -> bm_lead.py status
        "next": PURE_READ,       # -> bm_project.py next
        "doctor": EXTERNAL_WRITE,  # -> scripts/doctor.py, spawns subprocesses
        "version": PURE_READ,    # reads the installed VERSION file
        "start": LEDGER_WRITE,   # -> bm_project.py start
        "review": LEDGER_WRITE,  # -> bm_project.py review
        "deliver": LEDGER_WRITE,  # -> bm_project.py deliver
        "view": PROJECT_WRITE,   # -> bm_view.py render
        "recover": EXTERNAL_WRITE,  # -> bm_autosave.py: git subprocess, temp worktree
        "continue": EXTERNAL_WRITE,  # -> bm_continue.py continue
        "update": EXTERNAL_WRITE,
    },
}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def declared(module, command):
    """The effect class for (module, command), or UndeclaredCommand.

    Never falls back to a default class: an undeclared pair is refused
    loudly rather than guessed at, because a default is exactly the shape
    of hole the eighth instance of this bug slipped through."""
    commands = REGISTRY.get(module)
    if commands is None or command not in commands:
        raise UndeclaredCommand(
            "%s %r is not declared in tools/bm_effects.py's REGISTRY. "
            "Add REGISTRY[%r][%r] = <one of %s> before this command can "
            "ship; an undeclared command is refused, never defaulted."
            % (module, command, module, command, ", ".join(CLASSES)))
    return commands[command]


def commands_of_class(cls):
    """Every (module, command) pair registered under `cls`, in a stable
    (sorted by module, then command) order."""
    for module in sorted(REGISTRY):
        for command in sorted(REGISTRY[module]):
            if REGISTRY[module][command] == cls:
                yield module, command


# ---------------------------------------------------------------------------
# CLI. This command is itself pure_read: it only ever reads this module's
# own in-memory REGISTRY and writes to stdout.
# ---------------------------------------------------------------------------

def _out(msg):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def cmd_list(argv):
    want_class = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--class":
            if i + 1 >= len(argv):
                _err("bm_effects: --class needs a value (one of %s)"
                     % ", ".join(CLASSES))
                return 2
            want_class = argv[i + 1]
            i += 2
            continue
        _err("bm_effects: unknown argument %r" % arg)
        return 2
    if want_class is not None and want_class not in CLASSES:
        _err("bm_effects: --class must be one of %s, got %r"
             % (", ".join(CLASSES), want_class))
        return 2
    for module in sorted(REGISTRY):
        for command in sorted(REGISTRY[module]):
            cls = REGISTRY[module][command]
            if want_class is not None and cls != want_class:
                continue
            _out("%s:%s %s" % (module, command, cls))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out(__doc__.strip())
        return 0
    if argv[0] == "list":
        return cmd_list(argv[1:])
    _err("bm_effects: unknown command %r (known: list)" % argv[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
