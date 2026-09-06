#!/usr/bin/env python3
"""bm_autonomy.py: the command line for U1, the autonomy contract layer
(docs/AUTONOMY.md; design docs/superpowers/specs/2026-08-05-u1-autonomy-
contract-design.md; store schema 14, tools/bm_store.py section "U1: the
autonomy contract layer").

WHAT THIS IS
  A THIN CLI over tools/bm_store.py's autonomy Store service methods
  (sign_contract, set_contract_state, record_spend, record_assumption,
  record_interruption, answer_interruption, queue_human_step,
  resolve_human_step, record_checkpoint) and its read accessors
  (latest_contract, contract_revisions, spend_totals, list_assumptions,
  list_interruptions, list_human_steps, recent_checkpoints, gate_check),
  in exactly the sense tools/bm_project.py states as law: this file never
  issues SQL of its own. The moment this file needs a query the store does
  not already offer, it has become a second writer and must be folded back
  into bm_store.py instead of growing one here. A structural guard test
  (tools/test_bm_autonomy.py, TestNoSQLGuard, copied from
  tools/test_bm_project.py) parses this file with ast and fails the build
  if a SQL-shaped string literal or a sqlite3 import ever appears here.

WHAT THIS IS NOT
  Not a second store, not a second writer, not an enforcement point. Every
  command below either APPENDS a new contract revision through
  Store.sign_contract / Store.set_contract_state, or ANSWERS a question
  (gate-check, show, status, human-steps) by reading rows the store already
  redacts through its own export policy. gate-check in particular only
  ANSWERS "would this be allowed"; it never blocks anything on its own, and
  a caller that never asks it is never refused (docs/AUTONOMY.md, the U1/U2
  boundary section).

FOURTEEN COMMANDS
  sign               append revision 1 (or amend a live one with
                     --supersede): the founder's outcome, done definition,
                     allowed paths and surfaces, granted risk classes, and
                     optional token/minute ceilings, signed by a named human
  show               the live contract, or the whole revision chain with
                     --all (read only)
  gate-check         would ACTION_CLASS be authorised right now, optionally
                     scoped to a path or a surface (read only, never writes)
  assume             record one reversible assumption against the live
                     contract
  interrupt          record one forcing-condition question against the live
                     contract; --condition must be one of the four named in
                     AUTONOMY_CONDITIONS or this refuses by name
  spend              record tokens and/or minutes spent against the live
                     contract and print the breaker verdict
  pause              suspend authorisation without losing it (revocable
                     back to live)
  resume             restore authorisation from paused back to live
  stop               the 3am kill switch; --reason is optional on purpose
  revoke             end the contract for good; terminal, only a fresh sign
                     restarts authorisation
  status             one screen: state, spend against each ceiling or
                     NO-DATA, open interruptions against the zero-to-three
                     target, assumption count, open human steps by lane,
                     and the most recent controller checkpoint (read only)
  queue-human-step   queue one step only a human can complete (optionally
                     tied to one of the six safety floors), blocking the
                     lane it names and no other
  human-steps        list open (default) or all human steps, or resolve one
                     with --resolve
  checkpoint         record one controller liveness beacon; not in the
                     founder's original list by name (it lives in the FIELD
                     list, not the command list) but needed by U2, per the
                     design's own section 3: "U1 is meant to leave U2
                     nothing to design" does not hold for checkpoints if the
                     store's own record_checkpoint method has no CLI door.

THE SIX FLOORS, NEVER GRANTABLE BY ANY CONTRACT
  credential-entry, payment, account-signin, permanent-delete,
  publish-release, governance-write (bs.AUTONOMY_FLOORS). gate-check
  refuses one of these WITHOUT EVEN CONSULTING THE CONTRACT: a floor id
  smuggled into a contract's risk_classes by hand-editing the store is
  still refused, because the floor check runs before the contract is read
  at all (Store.gate_check, check 2). No flag on this command line can
  grant one. governance-write (L09, 2026-08-06) is the PATH-shaped floor:
  a write to the project's own .brothermode store, its .git directory or
  .claude/settings.json is refused at gate time whatever the contract's
  allowed_paths say, and an --allowed-path that NAMES one of those
  surfaces is refused at sign time. A writing contract must also declare
  at least one --allowed-path at sign time; only a contract granting
  nothing but bs.AUTONOMY_READ_ONLY_RISK_CLASSES may sign with none.

THE SIGNER CHECK IS A DENYLIST, NOT AN AUTHENTICATION CHECK
  `sign --signed-by` refuses roughly thirty model-name tokens
  (bs.AUTONOMY_MODEL_TOKENS: claude, opus, sonnet, gpt, gemini, llama,
  assistant, agent, bot, ai, and the rest), case-folded and Unicode
  normalized, split on non-alphanumerics. It is a speed bump against the
  accidental case, not a cryptographic identity check, and it does NOT
  catch a model deliberately told to sign as a real person's name, a new
  vendor not yet on the list, a name in a non-Latin script, a deliberate
  misspelling, or a bare initial. docs/AUTONOMY.md and docs/KNOWN-LIMITS.md
  state this in full; this file never overstates it in its own output.

EXIT CODES
  0 on success (including a genuine ALLOWED gate-check, and every
  idempotent no-op: stop-on-stopped, revoke-on-revoked). 1 on a refusal:
  the store said no to something that was a legal question to ask, or
  gate-check itself answered anything other than ALLOWED. 2 on a usage
  error: an unrecognized or missing flag, a malformed ceiling or spend
  amount, produced by this file BEFORE any store is opened, so a bad flag
  never touches the database. gate-check is the one place this is worth
  stating twice: it exits 1 for EVERY refusal verdict, not 0, because the
  caller in U2 is a shell loop and a checker that exits 0 when it means no
  will eventually be read as yes.

  A negative or non-numeric --token-ceiling / --minutes-ceiling, and a
  negative, non-numeric, or entirely-absent --tokens / --minutes pair on
  spend, are usage errors (exit 2) checked by THIS FILE before
  sign_contract or record_spend is ever called: they are a typo in the
  invocation, not a situation the store judged. Every other refusal this
  file's commands can produce (an unknown project, a model name in
  signed_by, a floor smuggled into --risk-class, a path outside the
  project root, an illegal state move, a condition outside the four
  forcing conditions, a breaker tripped, an already-answered interruption
  or already-resolved human step) comes from actually calling a Store
  method, and every one of those is exit 1: the store looked at a real
  question and said no.

USER-FACING STRINGS NAME NO REPO-RELATIVE PATH
  Every instruction this file prints (the gate-check example in `sign`'s
  own output, the `start` pointer in the no-project refusal) is built
  through bs.invocation(), the same resolver tools/bm_project.py and
  tools/bm_sentinel.py already use: "bm-autonomy" (or "bm-project") when
  this file is running from the environment that owns that console script,
  and an absolute "python3 <path>" otherwise. Never a bare "tools/
  bm_autonomy.py", which is only ever true for a clone.

PATHS ARE PROJECT-ROOT RELATIVE, NEVER CWD RELATIVE
  Store.sign_contract and Store.gate_check both canonicalize every path
  through canonicalize_path(self.root, p, cwd=None): neither one accepts a
  cwd argument at all, so --allowed-path and --path are always resolved
  against the PROJECT ROOT, exactly as if this command had been run from
  there, regardless of the directory the caller actually typed it from.
  This file therefore never computes or forwards os.getcwd(): there is
  nowhere in the store's own signature for it to go. Documented in
  docs/AUTONOMY.md; see this file's own report for the design-doc line
  this deviates from.

U1/U2 BOUNDARY
  This file writes and reads the contract, the breaker, the human-step
  queue, and the checkpoint beacon. It drives nothing: no loop, no
  dispatcher, no worktree, no tool invocation. The controller that calls
  gate-check before every unit of work, reads spend_totals()['verdict']
  to decide whether to keep starting new units, and re-reads
  latest_contract()['revision'] to detect a contract that moved out from
  under it, is U2, a later loop. docs/AUTONOMY.md states the staleness
  protocol U2 needs and this file already exposes it: gate-check's own
  JSON output carries the revision it judged against.

Python 3.9, standard library only. No network. No subprocess.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import datetime
import importlib.util
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the exact technique tools/bm_project.py
    uses for tools/bm_store.py: this file is invoked from arbitrary working
    directories and must not depend on whatever sys.path the caller happened
    to have."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
# Loaded at module level, not lazily inside each command: by the time this
# file was written, Writer A's schema-14 landing had already happened in
# this same working tree (the design's own section 2.1 states the reason
# tools/bm_sentinel.py had to load lazily no longer applies here). The
# class-identity trap tools/bm_project.py documents still applies: every
# exception caught below is bs.BMStoreError, from THIS load, never a second
# independent one.

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

# SQLite stores an INTEGER as a signed 64 bit value; a spend or ceiling
# at or beyond this raises OverflowError in Python or an integer-overflow
# in a later SUM. Both are refused here as a usage typo so a fat-fingered
# ceiling never reaches the store as a scary corruption message.
_SQLITE_INT_MAX = (1 << 63) - 1


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _cmd():
    """This tool's own invocation, for user-facing instruction text. See
    the module docstring's "USER-FACING STRINGS NAME NO REPO-RELATIVE
    PATH" section."""
    return bs.invocation("bm-autonomy", __file__)


def _project_cmd():
    """tools/bm_project.py's own invocation, for the "create the project
    first" pointer in the no-project refusal."""
    return bs.invocation(
        "bm-project", os.path.join(HERE, "bm_project.py"))


def _root():
    root, _source = bs.require_root()
    return root


def _store():
    # Matching bm_store.py's own CLI convention (its cmd_claim and every
    # other command besides init): only `bm_store.py init` may pass
    # create=True. Every command here refuses "no-store" instead, naming
    # the fix, exactly as tools/bm_project.py's own _store() does.
    return bs.Store(_root(), create=False)


def _read_store():
    """A read-only diagnostic handle for the commands that never write
    (show, gate-check, status, and the listing half of human-steps),
    matching the discipline tools/bm_store.py:11318 to 11326 documents: a
    diagnostic that can write is a diagnostic that can silently create the
    thing it is judging."""
    return bs.ReadOnlyStore(_root())


def _parse(argv, known, wants_value=(), repeatable=()):
    """Flags into (positional, kv), refusing anything unrecognized. Merges
    tools/bm_project.py's --help handling (printing the recognized flags
    rather than falling through to a bare usage refusal) with tools/
    bm_packs.py's repeatable-flag collection (a repeatable flag accumulates
    a list rather than being overwritten by its last occurrence)."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--help", "-h") and "help" not in known:
            _out("recognized flags: %s"
                 % ", ".join("--" + k for k in sorted(known)))
            if wants_value or repeatable:
                _out("flags taking a value: %s"
                     % ", ".join("--" + k for k in
                                 sorted(set(wants_value) | set(repeatable))))
            sys.exit(EXIT_OK)
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_autonomy: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(EXIT_USAGE)
            if name in wants_value or name in repeatable:
                if i + 1 >= len(argv):
                    _err("bm_autonomy: --%s needs a value" % name)
                    sys.exit(EXIT_USAGE)
                if name in repeatable:
                    kv.setdefault(name, []).append(argv[i + 1])
                else:
                    kv[name] = argv[i + 1]
                i += 2
                continue
            kv[name] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    return positional, kv


def _csv(value):
    """"a, b ,c" -> ["a", "b", "c"]. None or "" -> []."""
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def _require(kv, name, usage):
    val = kv.get(name)
    if not val:
        _err(usage)
        _err("bm_autonomy: --%s is required" % name)
        sys.exit(EXIT_USAGE)
    return val


_ACTOR_FLAGS = ("actor-type", "actor-name", "session-id")


def _actor(kv, usage):
    """Build the actor dict every mutating command passes to the store, so
    attribution is a real record of who or what acted. Copied structurally
    from tools/bm_project.py's own _actor: actor_type restricted to
    human|model on this CLI surface, actor_name required with no sensible
    default, session_id a fresh unguessable id per process when omitted."""
    actor_type = kv.get("actor-type", "model")
    if actor_type not in ("human", "model"):
        _err(usage)
        _err("bm_autonomy: --actor-type must be 'human' or 'model', got %r"
             % actor_type)
        sys.exit(EXIT_USAGE)
    actor_name = _require(kv, "actor-name", usage)
    session_id = kv.get("session-id") or ("cli-" + uuid.uuid4().hex)
    return {"actor_type": actor_type, "actor_name": actor_name,
            "session_id": session_id}


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True))


def _fmt_n(n):
    return "{:,}".format(n)


def _ceiling_or_nodata(value):
    """Invariant I8: a None ceiling prints the literal NO-DATA, never a
    zero and never "unlimited". Both alternatives are comforting lies
    about a number nobody set."""
    return "NO-DATA" if value is None else _fmt_n(value)


def _pct_or_nodata(value):
    return "NO-DATA" if value is None else "%.1f%%" % value


def _elapsed_since(ts):
    """A short "Ns/Nm/Nh/Nd ago" rendering of an ISO-8601 UTC timestamp in
    bs.now_iso()'s own format, for status's "how long ago" line. Never
    raises on a malformed or missing timestamp: this is display, not a
    correctness check, so it degrades to "unknown" instead."""
    if not ts:
        return "unknown"
    try:
        then = datetime.datetime.strptime(
            ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return "unknown"
    seconds = (datetime.datetime.now(datetime.timezone.utc)
               - then).total_seconds()
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%ds ago" % seconds
    if seconds < 3600:
        return "%dm ago" % (seconds // 60)
    if seconds < 86400:
        return "%dh ago" % (seconds // 3600)
    return "%dd ago" % (seconds // 86400)


def _require_project(store, project_id):
    """The tools/bm_sentinel.py _require_project lesson, applied in
    advance of ever calling sign_contract: a typo'd project id does not
    fail loudly on its own, it leaves an attribution row (or, here, a
    contract row) pointing at nothing, which the store's own verify then
    reports as a dangling reference. Checked here with a plain read, before
    any write is attempted, so the refusal names the fix in the CLI's own
    words rather than sign_contract's terser "not-found"."""
    if store.get_project(project_id) is None:
        _err("bm_autonomy: refused. no project %r in this store. A "
             "contract authorises work on a project, and signing against "
             "one that does not exist leaves the store's own verify "
             "reporting a dangling row. Create it first: %s start "
             "--project-id %s --name ... --actor-name ..."
             % (project_id, _project_cmd(), project_id))
        sys.exit(EXIT_REFUSED)


# ---------------------------------------------------------------------------
# sign
# ---------------------------------------------------------------------------

_SIGN_FLAGS = ("project", "outcome", "done-definition", "allowed-path",
              "allowed-surface", "risk-class", "token-ceiling",
              "minutes-ceiling", "signed-by", "supersede") + _ACTOR_FLAGS


def _sign_usage():
    return ("usage: sign --project ID --outcome T --done-definition T "
            "[--allowed-path P ...] [--allowed-surface A ...] "
            "[--risk-class C ...] [--token-ceiling N] "
            "[--minutes-ceiling N] --signed-by NAME [--supersede] "
            "--actor-name NAME [--actor-type human|model] "
            "[--session-id SID]")


def _ceiling_flag(kv, name, usage):
    """Parse an optional --token-ceiling / --minutes-ceiling as a
    non-negative whole number, or None when the flag was not given. A
    malformed or negative value is exit 2 (usage): a typo in the
    invocation, never a situation for the store to judge."""
    raw = kv.get(name)
    if raw is None:
        return None
    try:
        n = int(raw)
    except ValueError:
        _err(usage)
        _err("bm_autonomy: --%s must be a whole number, got %r"
             % (name, raw))
        sys.exit(EXIT_USAGE)
    if n < 0:
        _err(usage)
        _err("bm_autonomy: --%s must be zero or a positive whole number, "
             "got %d. A negative ceiling is a typo in the invocation, not "
             "a situation." % (name, n))
        sys.exit(EXIT_USAGE)
    if n > _SQLITE_INT_MAX:
        _err(usage)
        _err("bm_autonomy: --%s is larger than the store can hold "
             "(%d, the signed 64 bit maximum). A ceiling this size is a "
             "typo in the invocation, not a situation."
             % (name, _SQLITE_INT_MAX))
        sys.exit(EXIT_USAGE)
    return n


def cmd_sign(argv):
    _pos, kv = _parse(
        argv, _SIGN_FLAGS,
        wants_value=("project", "outcome", "done-definition",
                     "token-ceiling", "minutes-ceiling",
                     "signed-by") + _ACTOR_FLAGS,
        repeatable=("allowed-path", "allowed-surface", "risk-class"))
    usage = _sign_usage()
    project_id = _require(kv, "project", usage)
    outcome = _require(kv, "outcome", usage)
    done_definition = _require(kv, "done-definition", usage)
    signed_by = _require(kv, "signed-by", usage)
    token_ceiling = _ceiling_flag(kv, "token-ceiling", usage)
    minutes_ceiling = _ceiling_flag(kv, "minutes-ceiling", usage)
    actor = _actor(kv, usage)
    allowed_paths = kv.get("allowed-path") or []
    allowed_surfaces = kv.get("allowed-surface") or []
    risk_classes = kv.get("risk-class") or []
    store = _store()
    try:
        _require_project(store, project_id)
        result = store.sign_contract(
            project_id, outcome, done_definition, allowed_paths,
            allowed_surfaces, risk_classes, token_ceiling, minutes_ceiling,
            signed_by, actor["session_id"], actor,
            supersede=bool(kv.get("supersede")))
    finally:
        store.close()
    _out("signed contract for project %s: revision %d (live, %s)"
         % (project_id, result["revision"], result["change_kind"]))
    _out("outcome: %s" % outcome)
    _out("done definition: %s" % done_definition)
    _out("token ceiling: %s" % _ceiling_or_nodata(token_ceiling))
    _out("minutes ceiling: %s" % _ceiling_or_nodata(minutes_ceiling))
    _out("risk classes granted: %s" % (", ".join(risk_classes) or "(none)"))
    _out("allowed paths: %d, allowed surfaces: %d"
         % (len(allowed_paths), len(allowed_surfaces)))
    _out("check authorisation before acting: %s gate-check --project %s "
         "--action-class <class> [--path P] [--surface A]"
         % (_cmd(), project_id))
    return EXIT_OK


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

_SHOW_FLAGS = ("project", "all", "json", "raw")


def cmd_show(argv):
    _pos, kv = _parse(argv, _SHOW_FLAGS, wants_value=("project",))
    usage = "usage: show --project ID [--all] [--json] [--raw]"
    project_id = _require(kv, "project", usage)
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    store = _read_store()
    try:
        latest = store.latest_contract(project_id, raw=want_raw)
        if latest is None:
            _err("bm_autonomy: refused. no live contract for project %r. "
                 "Nothing is authorised. Run sign first." % (project_id,))
            return EXIT_REFUSED
        revisions = (store.contract_revisions(project_id, raw=want_raw)
                    if kv.get("all") else None)
    finally:
        store.close()
    if kv.get("json"):
        out = {"latest": latest}
        if revisions is not None:
            out["revisions"] = revisions
        _print_json(out)
        return EXIT_OK
    _out("project %s: revision %d, state %s"
         % (project_id, latest["revision"], latest["state"]))
    _out("outcome: %s" % (latest.get("outcome") or "(none)"))
    _out("done definition: %s" % (latest.get("done_definition") or "(none)"))
    _out("signed by: %s at %s" % (latest.get("signed_by") or "(none)",
                                  latest.get("signed_at") or "(n/a)"))
    if revisions is not None:
        _out("revision history (oldest first):")
        for r in revisions:
            _out("  rev %d: %s -> %s by %s at %s"
                 % (r["revision"], r["change_kind"], r["state"],
                    r.get("changed_by") or r.get("signed_by") or "(unknown)",
                    r.get("created_at")))
    return EXIT_OK


# ---------------------------------------------------------------------------
# gate-check
# ---------------------------------------------------------------------------

_GATE_CHECK_FLAGS = ("project", "action-class", "path", "surface", "json")


def cmd_gate_check(argv):
    _pos, kv = _parse(
        argv, _GATE_CHECK_FLAGS,
        wants_value=("project", "action-class", "path", "surface"))
    usage = ("usage: gate-check --project ID --action-class C [--path P] "
             "[--surface A] [--json]")
    project_id = _require(kv, "project", usage)
    action_class = _require(kv, "action-class", usage)
    store = _read_store()
    try:
        verdict = store.gate_check(project_id, action_class,
                                   path=kv.get("path"),
                                   surface=kv.get("surface"))
    finally:
        store.close()
    if kv.get("json"):
        _print_json(verdict)
    else:
        _out("%s: %s (revision %s)"
             % (verdict["verdict"], verdict["reason"],
                verdict["revision"] if verdict["revision"] is not None
                else "none"))
    return EXIT_OK if verdict["verdict"] == "ALLOWED" else EXIT_REFUSED


# ---------------------------------------------------------------------------
# assume
# ---------------------------------------------------------------------------

_ASSUME_FLAGS = ("project", "text", "reversal") + _ACTOR_FLAGS


def cmd_assume(argv):
    _pos, kv = _parse(argv, _ASSUME_FLAGS,
                      wants_value=("project", "text",
                                  "reversal") + _ACTOR_FLAGS)
    usage = ("usage: assume --project ID --text T [--reversal T] "
             "--actor-name NAME [--actor-type human|model] "
             "[--session-id SID]")
    project_id = _require(kv, "project", usage)
    text = _require(kv, "text", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        assumption_id = store.record_assumption(
            project_id, text, kv.get("reversal") or "",
            actor["session_id"], actor)
    finally:
        store.close()
    _out("recorded assumption %s for project %s" % (assumption_id, project_id))
    return EXIT_OK


# ---------------------------------------------------------------------------
# interrupt
# ---------------------------------------------------------------------------

_INTERRUPT_FLAGS = ("project", "condition", "question") + _ACTOR_FLAGS


def cmd_interrupt(argv):
    _pos, kv = _parse(argv, _INTERRUPT_FLAGS,
                      wants_value=("project", "condition",
                                  "question") + _ACTOR_FLAGS)
    usage = ("usage: interrupt --project ID --condition C --question T "
             "--actor-name NAME [--actor-type human|model] "
             "[--session-id SID]")
    project_id = _require(kv, "project", usage)
    condition = _require(kv, "condition", usage)
    question = _require(kv, "question", usage)
    actor = _actor(kv, usage)
    # Validated HERE, before the store is even opened, so the refusal reads
    # in the CLI's own richer words rather than the store's generic
    # "unknown forcing condition" ValueError: this IS the mechanism that
    # keeps the question policy honest (design section 7). Still exit 1,
    # not 2: an out-of-policy condition is a real situation, not a flag
    # typo, matching the treatment every other numbered sign validation
    # gets except the ceiling check.
    if condition not in bs.AUTONOMY_CONDITIONS:
        _err("bm_autonomy: refused. %r is not a forcing condition. The "
             "four are %s. Anything else is an assumption to record, not "
             "a question to ask: run assume instead."
             % (condition, ", ".join(bs.AUTONOMY_CONDITIONS)))
        return EXIT_REFUSED
    store = _store()
    try:
        interruption_id = store.record_interruption(
            project_id, condition, question, actor["session_id"], actor)
        open_count = len(
            store.list_interruptions(project_id, answered=False))
    finally:
        store.close()
    _out("recorded interruption %s for project %s"
         % (interruption_id, project_id))
    _out("open interruptions for this project: %d (target: zero to three)"
         % open_count)
    return EXIT_OK


# ---------------------------------------------------------------------------
# spend
# ---------------------------------------------------------------------------

_SPEND_FLAGS = ("project", "tokens", "minutes", "note", "json") + _ACTOR_FLAGS


def _spend_amount(kv, name, usage):
    """Parse an optional --tokens / --minutes as zero or a positive whole
    number. Missing means 0. Negative is exit 2 (usage): a spend row only
    ever goes up, and a clamped negative would silently become zero and
    leave the meter wrong in a way nobody could see."""
    raw = kv.get(name)
    if raw is None:
        return 0
    try:
        n = int(raw)
    except ValueError:
        _err(usage)
        _err("bm_autonomy: --%s must be zero or a positive whole number, "
             "got %r" % (name, raw))
        sys.exit(EXIT_USAGE)
    if n < 0:
        _err(usage)
        _err("bm_autonomy: --%s must be zero or a positive whole number, "
             "got %d. Spend only ever goes up: if you need to correct an "
             "over-recording, record the correction as a note and say so."
             % (name, n))
        sys.exit(EXIT_USAGE)
    if n > _SQLITE_INT_MAX:
        _err(usage)
        _err("bm_autonomy: --%s is larger than the store can hold "
             "(%d, the signed 64 bit maximum). A value this size is a typo "
             "in the invocation, not a real spend." % (name, _SQLITE_INT_MAX))
        sys.exit(EXIT_USAGE)
    return n


def cmd_spend(argv):
    _pos, kv = _parse(argv, _SPEND_FLAGS,
                      wants_value=("project", "tokens", "minutes",
                                  "note") + _ACTOR_FLAGS)
    usage = ("usage: spend --project ID [--tokens N] [--minutes N] "
             "[--note T] --actor-name NAME [--actor-type human|model] "
             "[--session-id SID] [--json]")
    project_id = _require(kv, "project", usage)
    if "tokens" not in kv and "minutes" not in kv:
        _err(usage)
        _err("bm_autonomy: spend needs at least one of --tokens or "
             "--minutes")
        return EXIT_USAGE
    tokens = _spend_amount(kv, "tokens", usage)
    minutes = _spend_amount(kv, "minutes", usage)
    if tokens == 0 and minutes == 0:
        _err(usage)
        _err("bm_autonomy: --tokens and --minutes are both zero; a spend "
             "row recording nothing is noise. Record at least one of them "
             "as a positive whole number.")
        return EXIT_USAGE
    actor = _actor(kv, usage)
    store = _store()
    try:
        totals = store.record_spend(project_id, tokens, minutes,
                                    kv.get("note") or "",
                                    actor["session_id"], actor)
    finally:
        store.close()
    if kv.get("json"):
        _print_json(totals)
        return EXIT_OK
    _out("spend recorded for project %s" % project_id)
    _out("tokens: %s spent, ceiling %s, breaker %s"
         % (_fmt_n(totals["tokens"]),
            _ceiling_or_nodata(totals["token_ceiling"]),
            _pct_or_nodata(totals["token_pct"])))
    _out("minutes: %s spent, ceiling %s, breaker %s"
         % (_fmt_n(totals["minutes"]),
            _ceiling_or_nodata(totals["minutes_ceiling"]),
            _pct_or_nodata(totals["minutes_pct"])))
    verdict = totals["verdict"]
    if verdict == "hard-stop":
        _out("verdict: hard-stop. Stop. Checkpoint every worktree and "
             "write the close record.")
    elif verdict == "soft-stop":
        _out("verdict: soft-stop. Stop STARTING new work. Finish what is "
             "in flight, then checkpoint.")
    elif verdict == "no-data":
        _out("verdict: no-data. No ceiling is set, so nothing can trip.")
    else:
        _out("verdict: ok")
    return EXIT_OK


# ---------------------------------------------------------------------------
# pause / resume / stop / revoke
# ---------------------------------------------------------------------------

_STATE_VERB = {"paused": "pause", "live": "resume",
              "stopped": "stop", "revoked": "revoke"}


def _state_change(argv, new_state, reason_required):
    verb = _STATE_VERB[new_state]
    flags = ("project", "reason") + _ACTOR_FLAGS
    _pos, kv = _parse(argv, flags,
                      wants_value=("project", "reason") + _ACTOR_FLAGS)
    usage = ("usage: %s --project ID %s--actor-name NAME "
             "[--actor-type human|model] [--session-id SID]"
             % (verb, "--reason T " if reason_required else "[--reason T] "))
    project_id = _require(kv, "project", usage)
    reason = _require(kv, "reason", usage) if reason_required else (
        kv.get("reason") or "")
    actor = _actor(kv, usage)
    store = _store()
    try:
        result = store.set_contract_state(
            project_id, new_state, actor["actor_name"], reason,
            actor["session_id"], actor)
        if not result["changed"]:
            # Invariant I3: a repeated stop (or any repeated state move)
            # writes NO new revision and still exits 0. The timestamp on
            # this line is a supplementary read of the SAME unchanged row,
            # never a second mutation.
            latest = store.latest_contract(project_id, raw=True)
            when = latest.get("created_at") if latest else "?"
            _out("project %s is already %s (revision %d, at %s). Nothing "
                 "to do." % (project_id, new_state, result["revision"],
                            when))
            return EXIT_OK
    finally:
        store.close()
    _out("project %s contract -> %s (revision %d)"
         % (project_id, result["state"], result["revision"]))
    return EXIT_OK


def cmd_pause(argv):
    return _state_change(argv, "paused", reason_required=True)


def cmd_resume(argv):
    return _state_change(argv, "live", reason_required=True)


def cmd_stop(argv):
    # The 3am kill switch: requiring a sentence from somebody stopping a
    # runaway is a usability defect dressed as rigour.
    return _state_change(argv, "stopped", reason_required=False)


def cmd_revoke(argv):
    return _state_change(argv, "revoked", reason_required=True)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

_STATUS_FLAGS = ("project", "json", "raw")


def cmd_status(argv):
    _pos, kv = _parse(argv, _STATUS_FLAGS, wants_value=("project",))
    usage = "usage: status --project ID [--json] [--raw]"
    project_id = _require(kv, "project", usage)
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    store = _read_store()
    try:
        latest = store.latest_contract(project_id, raw=want_raw)
        if latest is None:
            _err("bm_autonomy: refused. no contract for project %r yet. "
                 "Run sign first." % (project_id,))
            return EXIT_REFUSED
        # Read-only, so this works identically whether the contract is
        # live, paused, stopped or revoked: pause blocks new authorisation
        # through gate-check, never through status.
        totals = store.spend_totals(project_id)
        open_interruptions = store.list_interruptions(project_id,
                                                       answered=False)
        assumptions = store.list_assumptions(project_id, raw=want_raw)
        open_steps = store.list_human_steps(project_id, resolved=False,
                                            raw=want_raw)
        checkpoints = store.recent_checkpoints(project_id, limit=1,
                                               raw=want_raw)
    finally:
        store.close()
    by_lane = {}
    for step in open_steps:
        by_lane.setdefault(step.get("lane") or "default", []).append(step)
    if kv.get("json"):
        _print_json({
            "contract": latest, "spend": totals,
            "open_interruptions": len(open_interruptions),
            "assumption_count": len(assumptions),
            "open_human_steps_by_lane":
                dict((k, len(v)) for k, v in by_lane.items()),
            "last_checkpoint": checkpoints[0] if checkpoints else None,
        })
        return EXIT_OK
    _out("project %s: %s (revision %d)"
         % (project_id, latest["state"], latest["revision"]))
    _out("outcome: %s" % (latest.get("outcome") or "(none)"))
    _out("done definition: %s" % (latest.get("done_definition") or "(none)"))
    _out("tokens: %s spent, ceiling %s, breaker %s"
         % (_fmt_n(totals["tokens"]),
            _ceiling_or_nodata(totals["token_ceiling"]),
            _pct_or_nodata(totals["token_pct"])))
    _out("minutes: %s spent, ceiling %s, breaker %s"
         % (_fmt_n(totals["minutes"]),
            _ceiling_or_nodata(totals["minutes_ceiling"]),
            _pct_or_nodata(totals["minutes_pct"])))
    _out("verdict: %s" % totals["verdict"])
    _out("open interruptions: %d (target: zero to three)"
         % len(open_interruptions))
    _out("assumptions recorded: %d" % len(assumptions))
    if by_lane:
        _out("open human steps by lane:")
        for lane in sorted(by_lane):
            _out("  %s: %d" % (lane, len(by_lane[lane])))
    else:
        _out("open human steps: (none)")
    if checkpoints:
        cp = checkpoints[0]
        _out("last checkpoint: %s by controller %s, %s"
             % (cp.get("created_at"), cp.get("controller_id"),
                _elapsed_since(cp.get("created_at"))))
    else:
        _out("last checkpoint: (none recorded)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# queue-human-step
# ---------------------------------------------------------------------------

_QUEUE_HUMAN_STEP_FLAGS = ("project", "what", "floor", "lane", "click-path",
                          "blocks") + _ACTOR_FLAGS


def cmd_queue_human_step(argv):
    _pos, kv = _parse(argv, _QUEUE_HUMAN_STEP_FLAGS,
                      wants_value=("project", "what", "floor", "lane",
                                  "click-path", "blocks") + _ACTOR_FLAGS)
    usage = ("usage: queue-human-step --project ID --what T [--floor F] "
             "[--lane L] [--click-path T] [--blocks TASK_ID,...] "
             "--actor-name NAME [--actor-type human|model] "
             "[--session-id SID]")
    project_id = _require(kv, "project", usage)
    what = _require(kv, "what", usage)
    actor = _actor(kv, usage)
    lane = kv.get("lane") or "default"
    blocks = _csv(kv.get("blocks"))
    store = _store()
    try:
        step_id = store.queue_human_step(
            project_id, kv.get("floor") or "", lane, what,
            kv.get("click-path") or "", blocks, actor["session_id"], actor)
    finally:
        store.close()
    _out("queued human step %s in lane %r" % (step_id, lane))
    _out("lane %r is blocked pending this step; every other lane is "
         "unaffected and continues." % (lane,))
    return EXIT_OK


# ---------------------------------------------------------------------------
# human-steps
# ---------------------------------------------------------------------------

_HUMAN_STEPS_FLAGS = ("project", "lane", "open", "all", "resolve",
                     "resolution", "json") + _ACTOR_FLAGS


def cmd_human_steps(argv):
    _pos, kv = _parse(argv, _HUMAN_STEPS_FLAGS,
                      wants_value=("project", "lane", "resolve",
                                  "resolution") + _ACTOR_FLAGS)
    usage = ("usage: human-steps --project ID [--lane L] [--open|--all] "
             "[--resolve ID --resolution T --actor-name NAME] [--json]")
    project_id = _require(kv, "project", usage)
    if kv.get("resolve"):
        resolution = _require(kv, "resolution", usage)
        actor = _actor(kv, usage)
        store = _store()
        try:
            resolved_at = store.resolve_human_step(
                kv["resolve"], project_id, resolution, actor)
        finally:
            store.close()
        _out("resolved human step %s at %s" % (kv["resolve"], resolved_at))
        return EXIT_OK
    if kv.get("open") and kv.get("all"):
        _err(usage)
        _err("bm_autonomy: --open and --all are mutually exclusive")
        return EXIT_USAGE
    resolved = None if kv.get("all") else False
    store = _read_store()
    try:
        steps = store.list_human_steps(project_id, lane=kv.get("lane"),
                                       resolved=resolved,
                                       raw=not kv.get("json"))
    finally:
        store.close()
    steps = list(reversed(steps))  # store returns oldest first; want newest
    if kv.get("json"):
        _print_json({"human_steps": steps})
        return EXIT_OK
    if not steps:
        _out("no %shuman steps for project %s"
             % ("open " if resolved is False else "", project_id))
        return EXIT_OK
    by_lane = {}
    for step in steps:
        by_lane.setdefault(step.get("lane") or "default", []).append(step)
    for lane in sorted(by_lane):
        _out("lane %s:" % lane)
        for step in by_lane[lane]:
            _out("  %s: %s%s" % (step.get("step_id"), step.get("what"),
                                 " (resolved)" if step.get("resolved_at")
                                 else ""))
    return EXIT_OK


# ---------------------------------------------------------------------------
# checkpoint
# ---------------------------------------------------------------------------

_CHECKPOINT_FLAGS = ("project", "controller-id", "kind", "note") + _ACTOR_FLAGS


def cmd_checkpoint(argv):
    _pos, kv = _parse(argv, _CHECKPOINT_FLAGS,
                      wants_value=("project", "controller-id", "kind",
                                  "note") + _ACTOR_FLAGS)
    usage = ("usage: checkpoint --project ID --controller-id ID [--kind K] "
             "[--note T] --actor-name NAME [--actor-type human|model] "
             "[--session-id SID]")
    project_id = _require(kv, "project", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        checkpoint_id = store.record_checkpoint(
            project_id, controller_id, kv.get("kind") or "",
            kv.get("note") or "", actor["session_id"], actor)
    finally:
        store.close()
    _out("recorded checkpoint %s for controller %s"
         % (checkpoint_id, controller_id))
    return EXIT_OK


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "sign": cmd_sign,
    "show": cmd_show,
    "gate-check": cmd_gate_check,
    "assume": cmd_assume,
    "interrupt": cmd_interrupt,
    "spend": cmd_spend,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "stop": cmd_stop,
    "revoke": cmd_revoke,
    "status": cmd_status,
    "queue-human-step": cmd_queue_human_step,
    "human-steps": cmd_human_steps,
    "checkpoint": cmd_checkpoint,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out(__doc__.strip())
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return EXIT_OK
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_autonomy: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return EXIT_USAGE
    try:
        return COMMANDS[cmd](argv[1:])
    except bs.BMStoreError as e:
        # Covers OwnershipRefused, StaleIdentity and StoreCorrupt: every
        # situation the store looked at and refused. Never restated in
        # this file's own words; the store's message already names the
        # fix.
        _err("bm_autonomy: refused: %s" % e)
        return EXIT_REFUSED
    except ValueError as e:
        # The store raises ValueError by name for a malformed argument it
        # judged rather than a flag this file could have caught itself
        # (an unrecognized risk class, an unrecognized floor, an empty
        # required string): still a real refusal, exit 1, never a raw
        # traceback reaching the founder's terminal.
        _err("bm_autonomy: refused: %s" % e)
        return EXIT_REFUSED


def cli():
    """Console-script entry point; see tools/bm_project.py's cli() for why
    this exists beside main() rather than duplicating the sys.exit(main(
    ...)) line: a packaging entry point must take no arguments."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
