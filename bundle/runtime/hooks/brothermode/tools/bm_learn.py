#!/usr/bin/env python3
"""BrotherMode correction learning: the founder's command line.

WHAT THIS IS FOR
  You correct me. That correction should survive the session, apply when it is
  relevant, and never change my behaviour until YOU said so. This command line
  is the only path from "you said something" to "it is a rule", and every step
  of it is yours.

THE FIVE SEPARATE QUESTIONS, KEPT SEPARATE ON PURPOSE
  Most systems collapse these into one claim that the assistant "learned":
    1. capture       did you correct me, and what exactly did you say
    2. interpretation what narrow trigger and action does that evidence support
    3. approval      did you approve that interpretation as a rule
    4. application   was the rule retrieved and followed when it mattered
    5. outcome       did following it help
  This tool keeps them auditable, so "it learned" is never a claim you have to
  take on trust.

WHAT THIS FILE MAY NOT DO
  It never writes the database directly. Every mutation goes through
  bm_store.py, which stays the single writer. It performs no network access and
  spawns no subprocess.

APPROVAL NEEDS A RECEIPT FROM A REAL ANSWER OF YOURS
  There is no --auto, no daemon, and no hook that can approve anything, and
  since 2026-07-29 that is mechanical rather than a promise. Approval takes two
  steps:

    1. You are asked a question about one candidate. Whoever asked runs
       `grant-approval <candidate> --answer "<what you said>"`, which prints a
       one-time token, good for fifteen minutes, for that candidate only, and
       tied to the exact rule text you were shown.
    2. `approve <candidate> --receipt <token>` spends it. Once.

  Change the candidate or the rule text after the question and the token dies.
  Use it twice and the second try refuses. There is no override and no
  break-glass: without a token, no rule is created.

  WHAT THIS DOES NOT CLAIM. Nothing here authenticates WHICH human answered.
  The token proves an answer was given about this exact thing and has not been
  spent; it does not prove your identity, and no wording in this product says
  it does. What it removes is the real hole: before this, any process that
  could run this file could manufacture an approved rule, and the reference it
  recorded said "run by the founder" whether or not anyone was there.

RETRIEVAL MODE: deterministic lexical matching by default, and that path is
complete on its own. An OPTIONAL SQLite FTS5 index can be turned on with
BROTHERMODE_FTS5=1, which adds stemmed matching and a BM25 component to the
ranking; BROTHERMODE_NO_FTS5=1 forces it back off and wins over the first. Every
retrieval prints the mode it actually used, and no output claims BM25 unless a
real index answered. See `index-status` and `rebuild-index`.

TWO RETRIEVAL VERBS, NOT ONE VERB AND A FLAG
  `lookup` reads and writes nothing. `apply` retrieves AND records that the
  rules were surfaced, requires --session, and exits 3 with a PARTIAL status
  if the recording fails. Substantial work uses `apply`. The old `relevant`
  survives as a deprecated alias that says so on every run, because recording
  behind an opt-in flag meant one forgotten flag left no trace at all.

Python 3.9, standard library only.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the same way bm_telemetry loads bm_store:
    this file is invoked from arbitrary working directories and must not depend
    on whatever sys.path the caller happened to have."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
L = _load("bm_learning")


def _telemetry():
    """bm_telemetry owns the vault's corrections.jsonl inbox: its path, its
    permissions and its parser. Loaded lazily and by path so this file never
    reimplements the vault layout and then drifts from it. READ ONLY from here:
    no command in this CLI writes, moves or truncates that file."""
    return _load("bm_telemetry")


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _parse(argv, known, wants_value=()):
    """Flags into a dict, REFUSING anything unrecognized.

    An unknown flag exits non-zero and names itself. The sibling `bm_store.py
    claim --help` treats an unknown flag as a record name and silently claims
    something called "--help" (NOT-FINALIZED item 16); this file deliberately
    does not inherit that behaviour."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--help", "-h") and "help" not in known:
            # See the same block in tools/bm_project.py: the generated
            # adapters promise that --help lists the real flags, and until
            # 2026-08-02 that promise failed at the subcommand level on every
            # tool. A docstring above notes this file refuses unknown flags
            # rather than claiming a record called "--help"; refusing is
            # right, but --help is not an unknown flag, it is the documented
            # way to ask what the flags are.
            print("recognized flags: %s"
                  % ", ".join("--" + k for k in sorted(known)))
            if wants_value:
                print("flags taking a value: %s"
                      % ", ".join("--" + k for k in sorted(wants_value)))
            sys.exit(0)
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_learn: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("bm_learn: --%s needs a value" % name)
                    sys.exit(2)
                kv[name] = argv[i + 1]
                i += 2
                continue
            kv[name] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    return positional, kv


def _root():
    """require_root returns (root, source), not a bare path. Unpacked in ONE
    place so a caller cannot pass the tuple into a path function; doing exactly
    that raised "expected str, bytes or os.PathLike object, not tuple" on the
    first live run of this file."""
    root, _source = bs.require_root()
    return root


def _store(create=False):
    return bs.Store(_root(), create=create)


def _ctx(kv):
    """Task context for scope matching. The project defaults to the resolved
    project root's own name, so a project rule is scoped to where you actually
    are rather than to a string you had to remember to type."""
    ctx = {"project": os.path.basename(_root())}
    for key in ("project", "domain", "artifact", "relationship", "tool"):
        if kv.get(key):
            ctx[key] = kv[key]
    return ctx


def _rule_line(r):
    return "  %s  [%s, %s%s] v%d" % (
        r["rule_uuid"][:8],
        L.safe_scope(r["scope_type"], r["scope_key"]),
        r["state"],
        ", gate" if r.get("severity") == "gate" else "",
        r["current_version"])


def cmd_capture(argv):
    pos, kv = _parse(argv, {"trigger", "action", "because", "domain", "scope",
                            "scope-key", "source", "session", "raw", "json",
                            "show-source", "record"},
                     wants_value=("trigger", "action", "because", "domain",
                                  "scope", "scope-key", "source", "session",
                                  "raw", "record"))
    # LOOP 0 SWEEP, D6, ORCHESTRATOR RULING (2026-07-30): `capture` with no
    # arguments used to store an EMPTY candidate at exit 0 instead of
    # printing usage, while this same CLI already refuses an unknown flag.
    # Fixed HERE, at the command line, not in bm_store.capture_learning_candidate:
    # that store method has roughly thirty legitimate call shapes across the
    # test suites, and an all-empty refusal bolted into the shared primitive
    # is a policy change with a blast radius nobody sized. The user error
    # happens at the command line, so the refusal belongs at the command
    # line.
    if not (kv.get("trigger") or "").strip() and not (kv.get("action") or "").strip() \
            and not (kv.get("because") or "").strip():
        _err("usage: capture --trigger \"...\" --action \"...\" "
             "[--because \"...\"] [--domain ...] "
             "[--scope global|project|domain|artifact|relationship|tool] "
             "[--scope-key ...] [--source ...] [--session ...] "
             "[--raw \"...\"] [--record <id>]")
        _err("capture needs at least a trigger, an action, or a because; a "
             "candidate with none of the three is not a correction anyone gave")
        return 2
    scope = kv.get("scope", "project")
    scope_key = kv.get("scope-key")
    if scope_key is None and scope == "project":
        scope_key = os.path.basename(_root())
    store = _store()
    try:
        cand = store.capture_learning_candidate(
            kv.get("source", "explicit_correction"),
            raw_text=kv.get("raw", ""), trigger=kv.get("trigger", ""),
            action=kv.get("action", ""), because=kv.get("because", ""),
            domain=kv.get("domain", ""), scope_type=scope,
            scope_key=scope_key or "", session_id=kv.get("session", ""),
            # The link to the work this came out of. It is what lets a gate pack
            # and the alert guard see which files the approval would change, so
            # it is worth passing whenever the work record exists.
            record_uuid=kv.get("record"))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(_withhold_source(cand, kv.get("show-source")),
                        indent=2, sort_keys=True))
        return 0
    _out("captured %s (pending, nothing changes until you approve it)"
         % cand["candidate_uuid"][:8])
    problems = L.atomicity_problems(cand["proposed_action"])
    if problems:
        _out("  heads up, this looks like more than one rule: %s" % "; ".join(problems))
        _out("  approval will refuse it unless you split it or pass --override-reason")
    return 0


WITHHELD = "[withheld: pass --show-source]"


def _withhold_source(row, show):
    """Strip the verbatim founder columns out of a JSON row unless asked.

    ONE definition of the withholding rule, used by every command that can emit
    a candidate or a piece of evidence. It used to be re-implemented inline in
    show-candidate only, which is exactly how `candidates --json` and
    `why --json` came to print the same columns in full (LOOP 12)."""
    if show:
        return dict(row)
    out = dict(row)
    # LOOP 5: disposition_reason, verification_ref and outcome_ref are
    # founder-typed prose on learning_applications rows, exactly like
    # task_excerpt beside them, and cmd_applications --json used to print
    # all three straight from the row with no withholding at all (this
    # function was never called for that command). Same rule, same columns
    # list, so a column added to one prose-carrying table is not forgotten
    # on this one.
    for col in ("raw_text", "excerpt", "task_excerpt", "disposition_reason",
                "verification_ref", "outcome_ref"):
        if col in out and out[col]:
            out[col] = WITHHELD
    return out


def cmd_candidates(argv):
    pos, kv = _parse(argv, {"status", "json", "show-source"},
                     wants_value=("status",))
    store = _store()
    try:
        rows = store.list_learning_candidates(kv.get("status", "pending"))
    finally:
        store.close()
    if kv.get("json"):
        # LOOP 12: this printed raw_text in full, with no flag and no warning,
        # while show-candidate --json withheld the same column and dump()
        # withheld it entirely. The gate was enforced in three places and
        # bypassed here, so "verbatim founder words never reach a pipeable
        # artifact" was not true. Same flag, same wording, same default.
        _out(json.dumps([_withhold_source(r, kv.get("show-source")) for r in rows],
                        indent=2, sort_keys=True))
        return 0
    if not rows:
        _out("no candidates with that status")
        return 0
    for c in rows:
        _out("  %s  [%s%s]  %s" % (
            c["candidate_uuid"][:8], c["proposed_scope_type"],
            "" if c["proposed_scope_type"] == "global" else ":" + c["proposed_scope_key"],
            c["status"]))
        if c["proposed_trigger"]:
            _out("     When: %s" % L.safe_display(c["proposed_trigger"], 100))
            _out("     Do  : %s" % L.safe_display(c["proposed_action"], 100))
        if c["raw_text"]:
            # The raw founder quote is NOT printed by default. It is the most
            # sensitive thing in the store and the least necessary for triage.
            _out("     source: %d chars captured, %d redactions (--show-source to read)"
                 % (len(c["raw_text"]), c["redaction_count"]))
    _out("")
    # The hint shows the SAME form docs/CORRECTION-LEARNING.md walks through,
    # optional flags included. It used to show only --because, so a founder
    # following the hint never met --gate or --ref and the page and the tool
    # disagreed about what approving a candidate looks like. It now also carries
    # the receipt step, because approving refuses without a receipt AND without
    # a reference of your own.
    _out("%d candidate(s). Ask the founder, then: bm_learn.py grant-approval "
         "<id> --answer \"...\"" % len(rows))
    _out("  then: bm_learn.py approve <id> --receipt <token> "
         "--ref \"why you approved\" [--because \"...\"] [--gate]")
    return 0


def cmd_show_candidate(argv):
    pos, kv = _parse(argv, {"show-source", "json"})
    if not pos:
        _err("usage: show-candidate <id> [--show-source] [--json]")
        return 2
    store = _store()
    try:
        c = store.get_learning_candidate(pos[0])
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(_withhold_source(c, kv.get("show-source")),
                        indent=2, sort_keys=True))
        return 0
    _out("candidate %s" % c["candidate_uuid"])
    for label, key in (("status", "status"), ("source", "source_type"),
                       ("scope", "proposed_scope_type"), ("trigger", "proposed_trigger"),
                       ("action", "proposed_action"), ("because", "proposed_because")):
        _out("  %-8s %s" % (label, L.safe_display(str(c[key]), 300)))
    if kv.get("show-source"):
        _out("  WARNING: the following is your own verbatim text, secrets scrubbed but")
        _out("           prose intact. Do not paste it somewhere public.")
        _out("  source   %s" % L.safe_display(c["raw_text"], 2000))
    else:
        _out("  source   %d chars withheld (--show-source to read)" % len(c["raw_text"] or ""))
    return 0


_SHAPE_FLAGS = ("trigger", "action", "because", "domain", "scope", "scope-key", "type")


def _shape_kwargs(kv):
    """The flags that decide what the rule WOULD SAY, in the one shape both
    grant-approval and approve pass down.

    Shared on purpose: the receipt is fingerprinted over these values, so if the
    two commands built them differently the token would never match and the
    mismatch would look like tampering rather than a bug."""
    return {"trigger": kv.get("trigger"), "action": kv.get("action"),
            "because": kv.get("because"), "domain": kv.get("domain"),
            "scope_type": kv.get("scope"), "scope_key": kv.get("scope-key"),
            "rule_type": kv.get("type", "preference"),
            "severity": "gate" if kv.get("gate") else "soft"}


def cmd_grant_approval(argv):
    """Turn one real answer from the founder into a one-time approval receipt.

    Human-confirmed, one-time receipt-gated approval. Automatic capture
    cannot approve or promote its own candidates.

    WHAT THIS DOES NOT PROVE. The receipt proves that an answer was supplied
    for this exact proposed rule and has not already been used. It does not
    cryptographically prove which human supplied the answer.

    Run this the moment he answers a question window, with the rule-shaping
    flags set to EXACTLY what he was shown. The token it prints is the only copy
    that will ever exist: the store keeps a hash, this command prints the
    secret, and nothing else in the system ever sees it again.

    The token goes to the person who ran the command, on stdout, once. Do not
    paste it into a transcript, a commit message, a log or an issue: anyone
    holding it can spend that one answer."""
    pos, kv = _parse(argv, {"trigger", "action", "because", "domain", "scope",
                            "scope-key", "type", "gate", "answer", "json",
                            "override-reason", "override-conflict",
                            "override-alerts"},
                     wants_value=("trigger", "action", "because", "domain", "scope",
                                  "scope-key", "type", "answer",
                                  "override-reason", "override-conflict",
                                  "override-alerts"))
    if not pos or not (kv.get("answer") or "").strip():
        _err("usage: grant-approval <candidate-id> --answer \"<what the founder "
             "actually said>\" [--trigger ...] [--action ...] [--because ...] "
             "[--scope ...] [--scope-key ...] [--gate] [--override-reason ...] "
             "[--override-conflict ...] [--override-alerts ...]")
        _err("the shaping flags must match what he was SHOWN: the receipt is "
             "bound to that exact rule text and dies if it changes. So do the "
             "override flags: a receipt minted for a clean question cannot be "
             "spent with an override attached.")
        return 2
    store = _store()
    try:
        rec = store.mint_approval_receipt(
            pos[0], founder_response=kv["answer"],
            atomicity_override=kv.get("override-reason", ""),
            conflict_override=kv.get("override-conflict", ""),
            alerts_override=kv.get("override-alerts", ""),
            **_shape_kwargs(kv))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(rec, indent=2, sort_keys=True))
        return 0
    _out("approval receipt %s for candidate %s"
         % (rec["receipt_uuid"][:8], rec["candidate_uuid"][:8]))
    _out("  expires %s (%d seconds), one candidate, one use"
         % (rec["expires_at"], rec["ttl_seconds"]))
    _out("  it dies if the candidate or the rule text changes before you approve")
    # Whatever a guard flagged is printed BESIDE the token, because a receipt
    # minted with an override is consent to the override and the founder should
    # be able to see, in writing, what he just waved through (FIX ROUND P3).
    if rec["atomicity_problems"]:
        _out("  OVERRIDDEN, not atomic: %s" % "; ".join(rec["atomicity_problems"]))
    if rec["contradicts"]:
        _out("  OVERRIDDEN, contradicts existing rule(s): %s"
             % ", ".join(rec["contradicts"]))
    if rec["duplicates"]:
        _out("  OVERRIDDEN, duplicates existing rule(s): %s"
             % ", ".join(rec["duplicates"]))
    for a in rec["blocking_alerts"]:
        _out("  OVERRIDDEN, critical alert %s by %s about %s: %s"
             % (a["note_uuid"][:8], L.safe_display(a["author"], 60),
                a["matched"], L.safe_display(a["body"], 120)))
    if (rec["override_atomicity"] or rec["override_conflict"]
            or rec["override_alerts"]):
        _out("  approve must repeat the SAME override flags or the receipt dies")
    _out("")
    _out("  RECEIPT TOKEN, shown once and stored nowhere:")
    _out("  %s" % rec["token"])
    _out("")
    _out("  spend it: bm_learn.py approve %s --receipt <token>"
         % rec["candidate_uuid"][:8])
    _out("  or put it in BM_APPROVAL_RECEIPT so it stays out of your shell history")
    _out("  do not paste it into a log, a transcript or a commit message")
    return 0


def cmd_approve(argv):
    pos, kv = _parse(argv, {"trigger", "action", "because", "domain", "scope",
                            "scope-key", "type", "gate", "override-reason",
                            "override-conflict", "override-alerts", "ref",
                            "receipt", "json"},
                     wants_value=("trigger", "action", "because", "domain", "scope",
                                  "scope-key", "type", "override-reason",
                                  "override-conflict", "override-alerts", "ref",
                                  "receipt"))
    if not pos:
        _err("usage: approve <candidate-id> --receipt <token> [--trigger ...] "
             "[--action ...] "
             "[--because ...] [--scope global|project|domain|artifact|relationship|tool] "
             "[--scope-key ...] [--gate] --ref \"why you approved\" (required)")
        _err("the token comes from `grant-approval`, run when the founder "
             "answered. BM_APPROVAL_RECEIPT is read when --receipt is absent.")
        return 2
    # TWO INDEPENDENT GUARDS, from two lanes, both kept at the release cut.
    #
    # The RECEIPT is the gate (post-audit LOOP 3): a one-time token minted by
    # `grant-approval` against a real answer, bound to the exact rule shape the
    # founder was shown.
    #
    # The REFERENCE IS THE FOUNDER'S, NOT THE TOOL'S (loop P18-fix). It once
    # defaulted to a machine-written timestamp, which satisfied the store's
    # "refuses without a founder reference" guard with a string the machine
    # wrote about itself. A generated timestamp is not a reason, and a guard a
    # tool can satisfy on its own behalf is not a guard. Merging the two lanes
    # by letting the receipt excuse the missing reference would have restored
    # exactly that hole, so approval still refuses rather than inventing one.
    ref = (kv.get("ref") or "").strip()
    # argv is visible to every process on the machine through ps, so the token
    # may also arrive by environment. Neither path logs it.
    receipt = kv.get("receipt") or os.environ.get("BM_APPROVAL_RECEIPT", "")
    if not ref:
        # When BOTH are missing the refusal names both, rather than sending the
        # caller back for one thing and then the other. The receipt clause
        # keeps the store's own wording, "no-approval-receipt", because that is
        # the string a reader greps for and the one the audit repro produced.
        missing = "bm_learn: approve needs --ref \"why you approved this\". The "\
                  "reference is your evidence, in your words, and this tool "\
                  "will not write one for you."
        if not receipt:
            missing += (" It also needs a receipt (no-approval-receipt): run "
                        "`grant-approval <candidate> --answer \"...\"` first.")
        _err(missing)
        return 2
    store = _store()
    try:
        rule = store.approve_learning_candidate(
            pos[0], founder_ref=ref, receipt=receipt, trigger=kv.get("trigger"),
            action=kv.get("action"), because=kv.get("because"),
            domain=kv.get("domain"), scope_type=kv.get("scope"),
            scope_key=kv.get("scope-key"),
            rule_type=kv.get("type", "preference"),
            severity="gate" if kv.get("gate") else "soft",
            atomicity_override=kv.get("override-reason", ""),
            conflict_override=kv.get("override-conflict", ""),
            alerts_override=kv.get("override-alerts", ""))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(rule, indent=2, sort_keys=True))
        return 0
    _out("approved as rule %s" % rule["rule_uuid"][:8])
    _out(_rule_line(rule))
    _out("     When: %s" % L.safe_display(rule["trigger_text"], 160))
    _out("     Do  : %s" % L.safe_display(rule["action_text"], 160))
    return 0


def cmd_reject(argv):
    pos, kv = _parse(argv, {"because"}, wants_value=("because",))
    if not pos or not kv.get("because"):
        _err("usage: reject <candidate-id> --because \"why not\"")
        return 2
    store = _store()
    try:
        c = store.reject_learning_candidate(pos[0], kv["because"])
    finally:
        store.close()
    _out("rejected %s. The reason is kept, so the same suggestion does not keep "
         "coming back." % c["candidate_uuid"][:8])
    return 0


def cmd_rules(argv):
    pos, kv = _parse(argv, {"json", "all"})
    store = _store()
    try:
        rows = store.list_learning_rules(include_forgotten=bool(kv.get("all")))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    if not rows:
        _out("no approved rules yet")
        return 0
    for r in rows:
        _out(_rule_line(r))
        _out("     When: %s" % L.safe_display(r["trigger_text"], 120))
        _out("     Do  : %s" % L.safe_display(r["action_text"], 120))
    _out("")
    _out("%d rule(s)." % len(rows))
    return 0


def _soft_omitted(res):
    """How many soft rules the caller's limit held back.

    ONE definition, because two places read this number and they must agree.
    soft_omitted is what retrieval reports since Loop P4; the older `omitted`
    is the fallback for a result dict minted before that field existed."""
    return res.get("soft_omitted", res.get("omitted", 0))


def _delivery_footer(res, limit):
    """TWO SENTENCES, BECAUSE THEY ARE TWO DIFFERENT FACTS (Loop P4).

    The old single "%d omitted" covered gates and preferences alike, so a
    founder reading it could not tell a hidden preference from a hidden safety
    gate. Gate delivery is stated as the guarantee it is; the soft omission
    count is stated separately as the tuning knob it is.

    THIS IS A FUNCTION AND NOT INLINE TEXT ON PURPOSE. Loop P4 first shipped it
    inline at the bottom of cmd_relevant, which meant the zero-result path
    returned before ever reaching it and disclosed nothing. Every exit path
    that prints for a human now calls this same block, so a path cannot go
    quiet again without deleting the call."""
    _out("Gates: %d of %d applicable returned. A result limit cannot hide one."
         % (res.get("gates_returned", 0), res.get("gates_total", 0)))
    if "gate_manifest" in res:
        # LOOP 3. "returned" above is the never-hidden guarantee; this line is
        # the OTHER half, how much of that was full text versus the compact
        # manifest, which is what actually keeps a large corpus readable.
        _out("  of those: %d shown in full (bounded per call), %d in the "
             "compact manifest only. Pull one by id with --expand."
             % (res.get("gates_expanded", 0), res.get("gates_manifest_only", 0)))
    omitted = _soft_omitted(res)
    if omitted:
        _out("Soft rules: %d shown, %d omitted by --limit %d. Raise the limit "
             "to see them." % (res.get("soft_returned", 0), omitted, limit))
    else:
        _out("Soft rules: %d shown, none omitted." % res.get("soft_returned", 0))


def _recording_footer(res, record_arg):
    """What the bookkeeping did, on EVERY printing exit path.

    ONE DEFINITION, for the same reason _delivery_footer has one: Loop P4's
    defect was a disclosure that lived on one exit path only, and the zero
    result path returned before reaching it. This block is called from both.

    A FAILED WRITE IS SHOUTED, NOT MENTIONED. The old text was an indented
    line under a run that otherwise read as success, and the exit code was 0,
    so a caller checking the status of `relevant --record-applications` could
    not tell a recorded retrieval from an unrecorded one. The rules still
    print, because the retrieval genuinely succeeded and withholding it helps
    nobody; the status says partial and the process exits 3."""
    if "recorded" not in res:
        return
    _out("")
    _out("recorded %d application(s), %d already recorded for this task "
         "(task %s)" % (res["recorded"], res["already_recorded"],
                        res["task_fingerprint"]))
    if res.get("new_record_uuid"):
        # LOOP 4: --new-record's whole point is a durable identity the
        # caller did not have before this call, so it is printed, not left
        # to a follow-up `dashboard` round trip.
        _out("  created provisional work record %s"
             % res["new_record_uuid"][:8])
    if res.get("linked"):
        # Said out loud because the whole point of re-running with --record is
        # that the link lands, and a silent "already recorded" once left the
        # caller believing it had.
        _out("  linked %d already recorded application(s) to work record %s"
             % (res["linked"], L.safe_display(record_arg, 40)))
    if res.get("record_error"):
        _out("")
        _out("STATUS: PARTIAL. RULES RETRIEVED, APPLICATION NOT RECORDED.")
        _out("  reason: %s" % L.safe_display(res["record_error"], 200))
        _out("  the rules above are correct and can be acted on.")
        _out("  \"was this rule followed\" is UNANSWERABLE for this task until "
             "the row lands.")
        # TWO REMEDIES, BECAUSE THERE ARE TWO FAILURES. The single old line
        # told every caller to re-run the identical command, which is right for
        # a busy database and provably never converges for a --record that does
        # not resolve: that one fails the same way forever, and the founder is
        # left re-running a command that cannot succeed.
        if res.get("record_error_kind") in ("not-found", "ambiguous"):
            _out("  this is the --record ARGUMENT, not a database failure. "
                 "Re-running the identical command will fail identically.")
            _out("  pass a --record that resolves, or drop --record entirely "
                 "and re-run; recording is idempotent, and a later run with "
                 "the right id links the row it wrote.")
        else:
            _out("  fix the reason, then re-run the identical apply; it is "
                 "idempotent and will not double count.")
        _out("  exit status 3.")
    else:
        _out("  status: recorded.")
        if res.get("retrieval_uuid"):
            # Named so the founder can point at the run behind a miss finding.
            # The run is what makes a miss count defensible: it holds the scope
            # context, the limit and the eligible count as they were, none of
            # which can be recomputed once the corpus moves.
            _out("  retrieval run %s recorded (%d eligible, %d returned, "
                 "limit %d)" % (res["retrieval_uuid"][:8], res["eligible"],
                                len(res["results"]),
                                res.get("requested_limit", len(res["results"]))))
        if res.get("already_linked_records"):
            # THE ONE CASE "already recorded" MUST NOT BE READ AS SUCCESS FOR
            # THIS WORK. No --record was passed, so the row that already exists
            # may belong to a DIFFERENT unit of work worded the same way. Said
            # out loud rather than guessed at: the silent version reported a
            # clean recorded run while the work in hand had no row of its own
            # and could never be graded.
            _out("  NOTE: an already recorded row for this task belongs to "
                 "work record %s."
                 % ", ".join(u[:8] for u in res["already_linked_records"]))
            _out("  this run passed no --record, so it cannot tell a re-read "
                 "of that work from DIFFERENT work worded the same way. If "
                 "this is different work, re-run with --record <its id> and it "
                 "gets a row of its own.")
        _out("  close them with: bm_learn.py disposition <application-id> "
             "followed|ignored|not_relevant")


_USAGE = {
    "lookup": "usage: lookup --query \"what you want to read\" [--artifact ...] "
              "[--limit N] [--expand G1234abcd,...]   "
              "(READ ONLY; use `apply` before doing the work)",
    "apply": "usage: apply --query \"what you are about to do\" --session ID "
             "(--record UUID | --new-record NAME | %s in the environment) "
             "[--artifact ...] [--limit N] [--expand G1234abcd,...]"
             % bs.ACTIVE_RECORD_ENV,
    "relevant": "usage: relevant --query \"...\"   (DEPRECATED alias; use "
                "`lookup` to read or `apply` to do the work)",
}


def _retrieve_command(mode, argv):
    """The one body behind `lookup`, `apply` and the deprecated `relevant`.

    LOOP P5 SPLIT, AND WHY IT IS A SPLIT AND NOT A DEFAULT.
      Before this, one command did both jobs and recording was an opt-in flag,
      so the only thing standing between substantial work and an unrecorded
      retrieval was the model remembering to type --record-applications. A
      forgotten flag is not a failure anyone can see afterwards: the retrieval
      looks identical and the application rows simply never exist. So the
      choice moved out of a flag and into the verb.

        lookup  never writes. Curiosity cannot pollute the outcome data.
        apply   always attempts to record, and refuses without --session,
                because an application row with no session identity cannot be
                tied back to the work it belongs to.

      RECORDING FAILURE IS NOT A RETRIEVAL FAILURE. The rules print either
      way, because losing the answer to a bookkeeping problem is the worse
      outcome. But the exit code is 3 and the status is loud, so a caller
      cannot read a failed write as a clean run."""
    recording_flag = mode != "lookup"
    known = {"query", "project", "domain", "artifact", "relationship", "tool",
             "limit", "json", "session", "record", "new-record", "not-shown",
             "expand", "store-excerpt"}
    if mode == "relevant":
        known.add("record-applications")
    pos, kv = _parse(argv, known,
                     wants_value=("query", "project", "domain", "artifact",
                                  "relationship", "tool", "limit", "session",
                                  "record", "new-record", "expand"))
    if mode == "relevant":
        _err("bm_learn: `relevant` is DEPRECATED and will be removed in the "
             "next major version. It is now an alias: use `lookup` to read "
             "without recording, or `apply --session ID` to do substantial "
             "work, which always records.")
    query = kv.get("query") or " ".join(pos)
    if not query.strip():
        _err(_USAGE[mode])
        return 2
    if mode == "lookup":
        # Named refusal rather than _parse's generic unknown-flag line, because
        # someone reaching for these flags wants the recorded path and should
        # be sent there, not told the flag does not exist.
        for flag in ("session", "record", "new-record", "not-shown"):
            if kv.get(flag):
                _err("bm_learn: lookup never writes, so --%s means nothing "
                     "here. Use: apply --query \"...\" --session ID "
                     "(--record UUID | --new-record NAME)" % flag)
                return 2
    if mode == "apply" and not (kv.get("session") or "").strip():
        _err("bm_learn: apply requires --session ID. An application row with "
             "no session identity cannot be tied back to the work it belongs "
             "to, and an untied row is the bookkeeping this command exists to "
             "prevent. For a read with no work attached, use `lookup`.")
        return 2
    # LOOP 4: session identity alone is not enough for `apply`. A durable
    # work identity is required beyond it: an existing --record, a fresh
    # --new-record, or an active record BrotherMode already established in
    # the environment. Checked here, before the store is even opened, so a
    # missing identity is refused exactly like a missing --session above,
    # not attempted and reported as a partial write.
    if mode == "apply":
        record_arg = (kv.get("record") or "").strip()
        new_record_arg = (kv.get("new-record") or "").strip()
        env_active = os.environ.get(bs.ACTIVE_RECORD_ENV, "").strip()
        if record_arg and new_record_arg:
            _err("bm_learn: apply takes --record OR --new-record, not both "
                 "(%r and %r); each names a different way to identify this "
                 "work and only one can govern a single apply."
                 % (record_arg, new_record_arg))
            return 2
        if not (record_arg or new_record_arg or env_active):
            _err("bm_learn: apply requires a work identity beyond --session. "
                 "Pass exactly one of:")
            _err("  --record <existing-work-uuid>   attach to work already claimed")
            _err("  --new-record <name>              create a provisional work "
                 "record atomically with this application")
            _err("  %s=<uuid> in the environment     use the record BrotherMode "
                 "already made active (`bm_store.py claim` or "
                 "`bm_threads.py start`)" % bs.ACTIVE_RECORD_ENV)
            _err("A substantial-work application with session identity alone "
                 "cannot be tied back to one unambiguous unit of work.")
            return 2
    try:
        limit = int(kv.get("limit", 5))
    except ValueError:
        _err("bm_learn: --limit needs a whole number")
        return 2
    if mode == "relevant":
        recording_flag = bool(kv.get("record-applications"))
    # --expand takes a comma-separated list of short gate ids (G1234abcd) or
    # rule_uuid prefixes, so a caller who already knows which gate matters can
    # pull its full text without depending on relevance or scope to trigger
    # Layer B. Blank entries from a stray comma are dropped rather than
    # passed through as a request for the empty id.
    expand_ids = set(x.strip() for x in (kv.get("expand") or "").split(",")
                     if x.strip())
    store = _store()
    try:
        if recording_flag:
            res = store.record_learning_applications(
                query, context=_ctx(kv), limit=limit,
                session_id=kv.get("session", ""),
                record_prefix=kv.get("record"),
                new_record_name=kv.get("new-record"),
                # Only `apply` requires a work identity beyond --session;
                # `relevant`'s deprecated opt-in path stays exactly as
                # lenient as it always was, since this loop's mandate is
                # about apply's OWN refusal at the door above, not about
                # retroactively tightening the alias it survives as.
                require_record_identity=(mode == "apply"),
                shown_to_model=not kv.get("not-shown"),
                # LOOP 5: readable task_excerpt is opt-in, never the default.
                # Omitting --store-excerpt (the ordinary path) leaves
                # task_excerpt=None, which record_learning_applications now
                # stores as a bounded term set, not the verbatim query.
                task_excerpt=(query if kv.get("store-excerpt") else None),
                expand_ids=expand_ids)
        else:
            res = store.retrieve_learning_rules(query, context=_ctx(kv), limit=limit,
                                                expand_ids=expand_ids)
    finally:
        store.close()
    if recording_flag:
        # Machine-readable twin of the loud block below. It is set here, on the
        # one path both the JSON and the human output leave by, so the two can
        # never disagree about whether the write landed.
        res["recording_status"] = ("partial-recording-failed"
                                   if res.get("record_error") else "recorded")
    exit_code = 3 if res.get("recording_status") == \
        "partial-recording-failed" else 0
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return exit_code
    if not res["results"]:
        # WHY THIS BRANCH IS NOT ALLOWED TO SAY "none matched" UNCONDITIONALLY.
        #
        # Zero results has two completely different causes and they were
        # printed with the same sentence. Either nothing matched the query, or
        # rules DID match and the limit cut every last one of them (--limit 0
        # and negative limits both do exactly that). Telling the founder
        # "none matched" in the second case is the same false clean answer
        # Loop P4 exists to stop, moved onto the empty path: the JSON from the
        # identical call reports the omission and the screen did not.
        if _soft_omitted(res):
            _out("no founder rules SHOWN here (%d in scope; mode=%s). Rules "
                 "matched. The result limit cut every one of them."
                 % (res["eligible"], res["mode"]))
        else:
            _out("no founder rules apply here (%d in scope, none matched; mode=%s)"
                 % (res["eligible"], res["mode"]))
        _delivery_footer(res, limit)
        _recording_footer(res, kv.get("record", ""))
        return exit_code
    # LOOP 3, Layer A. Every applicable gate, compact, always, printed once
    # here regardless of how many of them go on to earn full text below. This
    # is what keeps a 20-gate corpus a bounded read instead of twenty repeats
    # of the same trigger/action/why block: manifest membership is the "not
    # hidden" guarantee now, not the full block.
    manifest = res.get("gate_manifest")
    if manifest and manifest["count"]:
        _out(manifest["text"])
        _out("")
    _out("RELEVANT FOUNDER RULES (mode=%s)" % res["mode"])
    for r in res["results"]:
        why = r["why"]
        if L.is_gate(r) and r.get("presentation") == "manifest":
            # NOT printed as its own block. It is already a line in the
            # manifest above (same short id, so --expand pulls it by that
            # id), and repeating a trigger/action/why block per gate here is
            # exactly the flooding this loop exists to stop. A conflict
            # involving this gate is still disclosed: it is caught below by
            # the UNRESOLVED CONFLICT section, which walks res["conflicts"]
            # (built from every row in `results`, not just the ones printed
            # in full here), so nothing about this gate's conflicts is lost.
            continue
        _out("")
        _out("  %s  rank=%d" % (r["rule_uuid"][:8], r["rank"]))
        _out("  Scope: %s     State: %s%s" % (
            why["scope"], why["state"], "     GATE" if L.is_gate(r) else ""))
        _out("  When : %s" % L.safe_display(r["trigger_text"], 160))
        _out("  Do   : %s" % L.safe_display(r["action_text"], 160))
        if r.get("because_text"):
            _out("  Why  : %s" % L.safe_display(r["because_text"], 160))
        # Named components, never one opaque score: the founder has to be able
        # to see WHY this rule sits where it does. bm25 is printed only when a
        # real index answered, because printing "bm25 0.0" in lexical mode
        # would imply a number was computed and lost.
        #
        # The no-exact-terms line names the REAL reason, and there are now two
        # of them. Saying "shown because it is a gate" about a rule the search
        # index matched on a stem would be this tool explaining itself wrongly,
        # which is the one thing the explanation exists to prevent.
        if why["matched_terms"]:
            terms = why["matched_terms"]
        elif L.is_gate(r):
            terms = "(none, shown because it is a gate)"
        elif why["mode"] == L.FTS5_MODE and why.get("bm25"):
            terms = "(no exact term; the search index matched a word stem)"
        else:
            terms = "(none)"
        _out("  Match: terms %s, relevance %s%s"
             % (terms, why["relevance"],
                (", bm25 %s (mode=%s)" % (why.get("bm25", 0.0), why["mode"]))
                if why["mode"] == L.FTS5_MODE else ""))
        if L.is_gate(r) and r.get("expansion_reason"):
            _out("  Expanded: %s" % r["expansion_reason"])
        if r.get("conflicts_with"):
            _out("  CONFLICT: contradicts %s. Both are live; see below."
                 % ", ".join(u[:8] for u in r["conflicts_with"]))
    if res.get("conflicts"):
        # Surfaced, not resolved. Silently dropping one side would be this tool
        # deciding which of your instructions is the real one, and it does not
        # get to make that call.
        _out("")
        _out("UNRESOLVED CONFLICT (%d). Two of your rules disagree, so neither is "
             "authoritative until you say which stands." % len(res["conflicts"]))
        for p in res["conflicts"]:
            _out("")
            _pair_block(p)
        _out("")
        _out("Decide with: bm_learn.py resolve-conflict <rule> --with <other> "
             "--how superseded|contradicted|deprecated --because \"...\"")
    _out("")
    _out("Constitution overrides learned rules.")
    _delivery_footer(res, limit)
    _recording_footer(res, kv.get("record", ""))
    return exit_code


def cmd_lookup(argv):
    """Read the founder rules. For human exploration and for deciding
    whether a task needs the recorded path at all. Never RECORDS an
    application (see _retrieve_command's own "LOOP P5 SPLIT" docstring for
    why lookup and apply are split verbs rather than one command with an
    opt-in flag).

    CORRECTED 2026-08-11 (codex-audit-split-2026-08-10-night.md HIGH #1 for
    tools/bm_effects.py, R4-TRIAGE-2026-08-11.md row 1). This said "Writes
    NOTHING, ever", which was FALSE in the same way tools/bm_docs.py's
    cmd_tier docstring was: _retrieve_command's shared `store = _store()`
    opens a WRITABLE bm_store.Store even for this mode, so a lookup against
    a database one schema version behind MIGRATES IT before ever reading a
    rule. It is declared ledger_write in tools/bm_effects.py, the true
    classification, not the flattering one.

    OPEN, and named rather than left implied, exactly like tools/bm_docs.py's
    cmd_tier: this SHOULD be read-only and cannot be yet.
    retrieve_learning_rules (the only store method this mode's own path
    calls, tools/bm_store.py:9530) exists on bm_store.Store only;
    bm_store.ReadOnlyStore does not inherit from Store (they are unrelated
    classes, both direct subclasses of object) and carries no
    retrieve_learning_rules of its own, so swapping the constructor would
    raise AttributeError on every normal invocation rather than only on the
    behind-schema case. Adding a read-only retrieve_learning_rules to
    ReadOnlyStore is what would close this, and that is a change to the
    store's own public surface, which deserves its own review rather than
    being smuggled in here."""
    return _retrieve_command("lookup", argv)


def cmd_apply(argv):
    """The substantial-work path. Retrieves AND records, with no flag standing
    between the two, because a flag is exactly what gets forgotten."""
    return _retrieve_command("apply", argv)


def cmd_relevant(argv):
    """DEPRECATED alias kept so existing scripts do not break silently. It says
    so on every run and will be removed in the next major version."""
    return _retrieve_command("relevant", argv)


def cmd_applications(argv):
    """What was surfaced for a task, and what happened to it.

    This is the answer to "was the rule followed". Each row shows the rule text
    AS IT WAS APPLIED, not as it reads today, so an edit made afterwards cannot
    quietly rewrite the history."""
    pos, kv = _parse(argv, {"session", "record", "rule", "task", "disposition",
                            "json", "show-source"},
                     wants_value=("session", "record", "rule", "task",
                                  "disposition"))
    store = _store()
    try:
        rows = store.list_learning_applications(
            session_id=kv.get("session"), rule_prefix=kv.get("rule"),
            record_prefix=kv.get("record"), task_fingerprint=kv.get("task"),
            disposition=kv.get("disposition"))
    finally:
        store.close()
    # LOOP 5: this used to print `rows` straight from the store, so
    # task_excerpt, disposition_reason, verification_ref and outcome_ref (all
    # founder-typed prose) and session_id (founder free text, not always a
    # generated id, see is_id_shaped) left the machine verbatim on both the
    # --json and the plain-text path. Same policy as every candidate/evidence
    # command in this file: withheld by default, --show-source opts in.
    show_source = bool(kv.get("show-source"))
    rows = [_withhold_source(r, show_source) for r in rows]
    for r in rows:
        if not show_source:
            r["session_id"] = bs.export_column(
                "learning_applications", "session_id", r.get("session_id"))
    if kv.get("json"):
        _out(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    if not rows:
        _out("no applications recorded for that filter")
        return 0
    for a in rows:
        _out("  %s  rule %s v%d  rank=%s  %s" % (
            a["application_uuid"][:8], a["rule_uuid"][:8], a["rule_version"],
            a["retrieval_rank"], a["scope_match"]))
        _out("     Do   : %s" % L.safe_display(a["action_text"], 120))
        _out("     shown: %s   disposition: %s   outcome: %s" % (
            "yes" if a["shown_to_model"] else "no", a["disposition"],
            a["outcome"]))
        if a["disposition_reason"]:
            _out("     why  : %s" % L.safe_display(a["disposition_reason"], 120))
        if a["verification_ref"]:
            _out("     check: %s" % L.safe_display(a["verification_ref"], 120))
    _out("")
    _out("%d application(s). Grade them with: bm_learn.py classify" % len(rows))
    return 0


def cmd_disposition(argv):
    """Record whether a retrieved rule was followed, and why not when it was not.

    usage: disposition <application-id> followed|ignored|not_relevant|unknown
                       [--because "..."] [--verification-ref "test:..."]
                       [--outcome accepted|rework|escaped_defect|corrected_again]
                       [--outcome-ref "..."]"""
    pos, kv = _parse(argv, {"because", "verification-ref", "outcome",
                            "outcome-ref", "json", "show-source"},
                     wants_value=("because", "verification-ref", "outcome",
                                  "outcome-ref"))
    if len(pos) != 2:
        _err("usage: disposition <application-id> "
             "followed|ignored|not_relevant|unknown [--because \"...\"] "
             "[--verification-ref \"test:...\"] [--outcome ...]")
        return 2
    store = _store()
    try:
        app = store.set_application_disposition(
            pos[0], pos[1], reason=kv.get("because", ""),
            verification_ref=kv.get("verification-ref", ""),
            outcome=kv.get("outcome"), outcome_ref=kv.get("outcome-ref", ""))
    finally:
        store.close()
    # LOOP 5: this printed `app` straight from the store, so the --because,
    # --verification-ref and --outcome-ref text just typed on THIS command
    # line echoed back in full, and so did task_excerpt and session_id --
    # same gap as cmd_applications, same fix: cmd_capture's own
    # _withhold_source-by-default, --show-source-to-see-it convention.
    show_source = bool(kv.get("show-source"))
    app = _withhold_source(app, show_source)
    if not show_source:
        app["session_id"] = bs.export_column(
            "learning_applications", "session_id", app.get("session_id"))
    if kv.get("json"):
        _out(json.dumps(app, indent=2, sort_keys=True))
        return 0
    _out("application %s: %s (outcome %s)" % (
        app["application_uuid"][:8], app["disposition"], app["outcome"]))
    cls, why = L.classify_application(app["disposition"], app["shown_to_model"],
                                       app["outcome"])
    _out("  %s: %s" % (cls or "no finding", why))
    return 0


def cmd_promote(argv):
    """Turn a provisional work record (LOOP 4, from `apply --new-record`)
    into an ordinary full record. Nothing about the underlying record
    changes -- same lifecycle_uuid, same state -- so every application
    already linked to it stays linked.

    usage: promote <lifecycle_uuid-or-prefix> [--json]"""
    pos, kv = _parse(argv, {"json"})
    if len(pos) != 1:
        _err("usage: promote <lifecycle_uuid-or-prefix> [--json]")
        return 2
    store = _store()
    try:
        rec = store.promote_provisional_record(pos[0])
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps({"lifecycle_uuid": rec.lifecycle_uuid, "name": rec.name,
                         "state": rec.state, "version": rec.version},
                        indent=2, sort_keys=True))
        return 0
    _out("promoted '%s' (lifecycle %s) to a full work record"
         % (rec.name, rec.lifecycle_uuid))
    return 0


def cmd_cancel(argv):
    """Cancel a provisional work record (LOOP 4) before it is ever promoted.
    The underlying record is never deleted, only moved to 'complete'; every
    application already linked to it stays linked, forever.

    usage: cancel <lifecycle_uuid-or-prefix> [--session ID] [--because "..."] [--json]"""
    pos, kv = _parse(argv, {"session", "because", "json"},
                     wants_value=("session", "because"))
    if len(pos) != 1:
        _err("usage: cancel <lifecycle_uuid-or-prefix> [--session ID] "
             "[--because \"...\"] [--json]")
        return 2
    store = _store()
    try:
        rec = store.cancel_provisional_record(
            pos[0], session_id=kv.get("session", ""), note=kv.get("because", ""))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps({"lifecycle_uuid": rec.lifecycle_uuid, "name": rec.name,
                         "state": rec.state, "version": rec.version},
                        indent=2, sort_keys=True))
        return 0
    _out("cancelled '%s' (lifecycle %s); state is now %s"
         % (rec.name, rec.lifecycle_uuid, rec.state))
    return 0


def cmd_classify(argv):
    """Grade the recorded applications, and refuse to grade what has no evidence.

    Five classes, and none of them is forced: retrieval miss, compliance
    failure, bad rule, scope error, not decidable."""
    pos, kv = _parse(argv, {"session", "json"}, wants_value=("session",))
    store = _store()
    try:
        res = store.classify_learning_applications(session_id=kv.get("session"))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    if not res["applications"] and not res["retrieval_misses"]:
        _out("nothing to classify yet: no applications have been recorded")
        return 0
    _out("APPLICATION CLASSIFICATION (%d application(s))" % len(res["applications"]))
    for a in res["applications"]:
        _out("  %s  rule %s v%d  %-18s %s" % (
            a["application_uuid"][:8], a["rule_uuid"][:8], a["rule_version"],
            a["classification"] or "no_finding",
            L.safe_display(a["classification_reason"], 110)))
    for m in res["retrieval_misses"]:
        # The class, not a fixed word: a limit miss and a relevance miss have
        # different fixes, and printing both as "retrieval_miss" is how a limit
        # set to 1 stays invisible while retrieval quality looks bad.
        _out("  task %s  rule %s  %-20s %s" % (
            m["task_fingerprint"][:8], m["rule_uuid"][:8],
            m.get("classification", "retrieval_miss"),
            L.safe_display(m["classification_reason"], 110)))
    for u in res["not_decidable_tasks"]:
        _out("  task %s  not_decidable (%s)  %s" % (
            u["task_fingerprint"][:8], u.get("evidence", "unknown"),
            L.safe_display(u["reason"], 110)))
    _out("")
    _out("counts: %s" % res["counts"])
    return 0


def cmd_should_retrieve(argv):
    """Is retrieval proportionate for this task? Records NOTHING either way.

    The trivial-task bypass has to be explicit and it has to be free of side
    effects: deciding not to retrieve must never manufacture an application
    row, or the outcome data fills up with tasks that never happened."""
    pos, kv = _parse(argv, {"communication", "architecture", "multi-file",
                            "risky", "prior-correction", "json"})
    signals = {
        "communication_artifact": bool(kv.get("communication")),
        "architecture_decision": bool(kv.get("architecture")),
        "multi_file_change": bool(kv.get("multi-file")),
        "risky_operation": bool(kv.get("risky")),
        "prior_correction": bool(kv.get("prior-correction")),
    }
    store = _store()
    try:
        gates = [r for r in store.list_learning_rules(states=L.INJECTABLE_STATES)
                 if r.get("severity") == "gate"]
    finally:
        store.close()
    res = L.retrieval_advised(signals, gate_rules_exist=bool(gates))
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    _out("retrieval advised: %s" % ("yes" if res["advised"] else "no"))
    for r in res["reasons"]:
        _out("  %s" % r)
    if not res["advised"]:
        _out("  nothing was recorded, because a task that did not happen must "
             "not appear in the outcome data")
    return 0


_STATE_CHANGE_RECEIPT_ENV = "BM_STATE_CHANGE_RECEIPT"


def _state_change_receipt(kv):
    """--receipt, or the shared env var, exactly as cmd_approve reads
    BM_APPROVAL_RECEIPT: argv is visible to every process on the machine
    through ps, so the token may also arrive by environment. Neither path
    logs it."""
    return kv.get("receipt") or os.environ.get(_STATE_CHANGE_RECEIPT_ENV, "")


def cmd_grant_state_receipt(argv):
    """Turn one real answer from the founder into a one-time receipt for
    supersede, resolve-conflict, deprecate, forget, or resolving a critical
    alert (LOOP 2, 2026-07-30): the same mechanism grant-approval uses for
    creating a rule, generalised to the four other commands that can also
    alter the live rule set.

    Run this the moment he answers, with the shaping flags set to EXACTLY
    what he was shown: the token is bound to the target and to the exact
    change proposed for it, and dies if either changes before the matching
    command spends it. --because is the content every kind fingerprints on
    (the reason for supersede/resolve-conflict/deprecate/forget, the exact
    resolution text for resolve-note): it must read, word for word, whatever
    the matching command's own --because will carry when it spends this
    receipt."""
    pos, kv = _parse(argv, {"answer", "with", "how", "because", "json"},
                     wants_value=("answer", "with", "how", "because"))
    if len(pos) < 2 or not (kv.get("answer") or "").strip():
        _err("usage: grant-state-receipt <kind> <target-id> --answer "
             "\"<what the founder actually said>\" [--with <rule-id>] "
             "[--how superseded|contradicted|deprecated] --because \"...\"")
        _err("kind is one of: %s" % ", ".join(bs.STATE_CHANGE_RECEIPT_KINDS))
        return 2
    kind, target = pos[0], pos[1]
    if kind not in bs.STATE_CHANGE_RECEIPT_KINDS:
        _err("bm_learn: unknown state-change receipt kind %r (known: %s)"
             % (kind, ", ".join(bs.STATE_CHANGE_RECEIPT_KINDS)))
        return 2
    if kind in ("supersede", "resolve-conflict") and not kv.get("with"):
        _err("%s receipts need --with <rule-id>, the exact successor or "
             "other rule the founder was shown" % kind)
        return 2
    if kind == "resolve-conflict" and kv.get("how") not in (
            "superseded", "contradicted", "deprecated"):
        _err("resolve-conflict receipts need --how superseded|contradicted|deprecated")
        return 2
    if kind == "resolve-note" and not (kv.get("because") or "").strip():
        _err("resolve-note receipts need --because \"exact text that will "
             "resolve the alert\"")
        return 2
    reason = kv.get("because", "")
    store = _store()
    try:
        with_uuid = (store.get_learning_rule(kv["with"])["rule_uuid"]
                    if kv.get("with") else "")
        if kind == "supersede":
            content_parts = ("superseded", with_uuid, reason)
        elif kind == "resolve-conflict":
            content_parts = (kv["how"], with_uuid, reason)
        elif kind == "deprecate":
            content_parts = ("deprecated", "", reason)
        elif kind == "forget":
            content_parts = ("forgotten", "", reason)
        else:  # resolve-note
            content_parts = (reason,)
        rec = store.mint_state_change_receipt(
            kind, target, founder_response=kv["answer"],
            content_parts=content_parts)
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(rec, indent=2, sort_keys=True))
        return 0
    _out("state-change receipt %s for %s (%s)"
         % (rec["receipt_uuid"][:8], rec["target_uuid"][:8], kind))
    _out("  expires %s (%d seconds), one target, one use"
         % (rec["expires_at"], rec["ttl_seconds"]))
    _out("  it dies if the target or the change proposed for it changes "
         "before you spend it")
    _out("")
    _out("  RECEIPT TOKEN, shown once and stored nowhere:")
    _out("  %s" % rec["token"])
    _out("")
    _out("  spend it with the matching command's --receipt (or put it in "
         "%s so it stays out of your shell history)" % _STATE_CHANGE_RECEIPT_ENV)
    _out("  do not paste it into a log, a transcript or a commit message")
    return 0


def cmd_forget(argv):
    pos, kv = _parse(argv, {"yes", "because", "receipt"},
                     wants_value=("because", "receipt"))
    if not pos:
        _err("usage: forget <rule-id> --yes --receipt <token>")
        return 2
    if not kv.get("yes"):
        _err("forget removes a rule from every future retrieval. Re-run with --yes "
             "if that is what you want.")
        return 2
    receipt = _state_change_receipt(kv)
    if not receipt:
        _err("forget requires a one-time receipt (no-state-change-receipt): "
             "run `grant-state-receipt forget <rule-id> --answer \"...\"` first.")
        return 2
    store = _store()
    try:
        r = store.change_learning_rule_state(pos[0], "forgotten",
                                              reason=kv.get("because", ""),
                                              receipt=receipt,
                                              receipt_kind="forget")
    finally:
        store.close()
    _out("forgotten %s. It will not be retrieved again. A tombstone remains so "
         "past applications stay honest." % r["rule_uuid"][:8])
    return 0


def cmd_deprecate(argv):
    pos, kv = _parse(argv, {"because", "receipt"},
                     wants_value=("because", "receipt"))
    if not pos:
        _err("usage: deprecate <rule-id> --because \"...\" --receipt <token>")
        return 2
    receipt = _state_change_receipt(kv)
    if not receipt:
        _err("deprecate requires a one-time receipt (no-state-change-receipt): "
             "run `grant-state-receipt deprecate <rule-id> --answer \"...\"` first.")
        return 2
    store = _store()
    try:
        r = store.change_learning_rule_state(pos[0], "deprecated",
                                              reason=kv.get("because", ""),
                                              receipt=receipt,
                                              receipt_kind="deprecate")
    finally:
        store.close()
    _out("deprecated %s (kept for history, not retrieved)" % r["rule_uuid"][:8])
    return 0


def cmd_why(argv):
    pos, kv = _parse(argv, {"json", "show-source"})
    if not pos:
        _err("usage: why <rule-id> [--json] [--show-source]")
        return 2
    store = _store()
    try:
        rule = store.get_learning_rule(pos[0])
        ev = store.list_learning_evidence(rule["rule_uuid"])
        versions = [dict(r) for r in store.conn.execute(
            "SELECT version, change_type, change_reason, created_at "
            "FROM learning_rule_versions WHERE rule_uuid=? ORDER BY version",
            (rule["rule_uuid"],)).fetchall()]
    finally:
        store.close()
    if kv.get("json"):
        # LOOP 12: evidence rows carry `excerpt`, which is the candidate's
        # verbatim raw_text copied forward at approval. dump() withholds that
        # column entirely and show-candidate withholds it behind a flag; this
        # printed it in full with no flag at all.
        _out(json.dumps({"rule": rule,
                         "evidence": [_withhold_source(e, kv.get("show-source"))
                                      for e in ev],
                         "versions": versions},
                        indent=2, sort_keys=True))
        return 0
    _out("rule %s" % rule["rule_uuid"])
    _out(_rule_line(rule))
    _out("  When: %s" % L.safe_display(rule["trigger_text"], 200))
    _out("  Do  : %s" % L.safe_display(rule["action_text"], 200))
    _out("  Why : %s" % L.safe_display(rule["because_text"], 200))
    _out("")
    _out("  versions:")
    for v in versions:
        _out("    v%d  %s  %s" % (v["version"], v["change_type"],
                                   L.safe_display(v["change_reason"], 80)))
    _out("  evidence (%d):" % len(ev))
    for e in ev:
        _out("    %s %s  %s" % (e["polarity"], e["evidence_type"],
                                 L.safe_display(e["source_ref"], 70)))
    return 0


def _ts_cell(ts):
    """The timestamp column for one inbox row, for a file edited by hand.

    A non-string ts is a malformed row, not a crash: it is labelled so the
    founder can see WHICH row is wrong, and every later row still prints."""
    if not isinstance(ts, str):
        return "(bad ts)" if ts is not None else "?"
    return L.safe_display(ts, 40)[:19] or "?"


def cmd_inbox(argv):
    """Show, and on request import, the GLOBAL correction capture inbox.

    The inbox is one file shared by every project on this machine. This store is
    the system of record for THIS project. Importing is triage: an inbox row
    becomes a pending candidate here, scoped to this project, and it stays
    pending until you approve it. Nothing is approved, and the inbox file itself
    is only ever read."""
    pos, kv = _parse(argv, {"file", "backfill", "json"}, wants_value=("file",))
    bt = _telemetry()
    path = kv.get("file") or bt.CORRECTIONS
    if not os.path.isfile(path):
        _out("no correction inbox at %s (nothing captured yet)" % path)
        return 0
    rows, bad = bt.read_jsonl(path, report_bad=True)
    # read_jsonl returns any valid JSON VALUE, so a hand-edited line holding a
    # bare string or a number parses fine and is not a row. Counted and named,
    # never a traceback in the founder's face.
    malformed = sum(1 for r in rows if not isinstance(r, dict))
    rows = [r for r in rows if isinstance(r, dict)]
    if not kv.get("backfill"):
        store = _store()
        try:
            known = {c["content_hash"] for c in store.list_learning_candidates(status=None)
                     if c["source_type"] == "detected_correction"}
        finally:
            store.close()
        new = [r for r in rows
               if L.inbox_identity(r.get("session_id", ""), r.get("text", ""))
               not in known]
        if kv.get("json"):
            _out(json.dumps({"path": path, "rows": len(rows), "not_yet_imported": len(new),
                             "unparsable_lines": bad, "malformed_rows": malformed},
                            indent=2, sort_keys=True))
            return 0
        _out("inbox %s" % path)
        _out("  %d row(s), %d not yet imported into this project" % (len(rows), len(new)))
        if malformed:
            _out("  %d line(s) parsed but were not a row object; skipped" % malformed)
        if bad:
            _out("  %d line(s) could not be parsed (line numbers %s); the text is not "
                 "printed because it is yours" % (len(bad), bad))
        for r in new:
            # LOOP 12: this sliced r["ts"] straight, so ONE hand-edited row with
            # a numeric or null ts raised TypeError mid-loop and every genuine
            # correction after it was never shown, while --backfill still
            # imported them. The docstring above promises malformed rows are
            # counted and named, never a traceback in the founder's face.
            _out("  %s  %s  %s" % (_ts_cell(r.get("ts")), r.get("project", "?"),
                                    L.safe_display(r.get("text", ""), 90)))
        _out("")
        _out("import them with: bm_learn.py inbox --backfill (nothing is approved)")
        return 0
    store = _store()
    try:
        res = store.import_correction_inbox(rows, source_label=os.path.basename(path))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    _out("imported %d candidate(s), skipped %d already present"
         % (res["imported"], res["skipped"]))
    bad_shape = malformed + res.get("malformed", 0)
    if bad_shape:
        _out("  %d line(s) parsed but were not a row object; skipped" % bad_shape)
    if res["possible_duplicates"]:
        _out("  %d flagged as a possible duplicate of an earlier candidate; the note "
             "is on the candidate, nothing was discarded" % res["possible_duplicates"])
    _out("All pending. Review with: bm_learn.py candidates")
    return 0


def cmd_outcome(argv):
    """Capture channel 3: a candidate derived from an OUTCOME, not from words.

    You redid the same artifact, or a defect escaped a record you had already
    completed. That is evidence some preference was not followed, so it becomes
    a pending candidate carrying the work record and the artifact it is about.
    It is still only a candidate: the action text is empty, so it cannot become
    a rule until you write one at approval.

    usage: outcome <record-id> --kind rework|escaped_defect
                   [--artifact PATH] [--note "what happened"]
    The artifact defaults to the paths that record claims."""
    pos, kv = _parse(argv, {"kind", "artifact", "note", "session", "scope-key", "json"},
                     wants_value=("kind", "artifact", "note", "session", "scope-key"))
    if not pos:
        _err("usage: outcome <record-id> --kind rework|escaped_defect "
             "[--artifact PATH] [--note \"what happened\"]")
        return 2
    store = _store()
    try:
        cand = store.capture_outcome_candidate(
            kv.get("kind", "rework"), pos[0], artifact_ref=kv.get("artifact", ""),
            summary=kv.get("note", ""), session_id=kv.get("session", ""),
            scope_key=kv.get("scope-key"))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(cand, indent=2, sort_keys=True))
        return 0
    _out("captured %s from %s (pending, nothing changes until you approve it)"
         % (cand["candidate_uuid"][:8], cand["source_type"]))
    _out("  %s" % L.safe_display(cand["source_ref"], 160))
    if cand["review_note"]:
        _out("  %s" % L.safe_display(cand["review_note"], 160))
    return 0


def cmd_metrics(argv):
    """Descriptive capture volumes. Deliberately NOT called accuracy."""
    pos, kv = _parse(argv, {"json"})
    store = _store()
    try:
        m = store.learning_capture_metrics()
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(m, indent=2, sort_keys=True))
        return 0
    _out("capture metrics (descriptive counts, not accuracy)")
    _out("  candidates by status: %s" % (m["candidates_by_status"] or "none"))
    _out("  candidates by source: %s" % (m["candidates_by_source"] or "none"))
    _out("  possible duplicates flagged: %d" % m["possible_duplicates"])
    _out("  false positive reasons: %s" % (m["false_positive_reasons"] or "none rejected yet"))
    _out("  approved rules: %d" % m["rules_total"])
    _out("")
    _out("There is no labelled review set, so none of these is a precision or a")
    _out("recall number, and none of them should be read as one.")
    return 0


def _pair_block(p):
    """One conflicting pair, printed the same way everywhere it appears.

    Only the two rules' own trigger and action text reaches this function; the
    store's _conflict_side already dropped every capture excerpt and source
    reference, so a conflict report can be read out or pasted somewhere without
    carrying the founder's verbatim words with it."""
    _out("  %s  vs  %s      (%s)" % (
        p["a"]["rule_uuid"][:8], p["b"]["rule_uuid"][:8],
        "you declared this" if p["declared"] else "detected: " + ", ".join(p["reasons"])))
    for side in ("a", "b"):
        s = p[side]
        _out("    %s  [%s, %s]" % (s["rule_uuid"][:8], s["scope"], s["state"]))
        _out("       When: %s" % L.safe_display(s["trigger"], 140))
        _out("       Do  : %s" % L.safe_display(s["action"], 140))


def cmd_conflicts(argv):
    """What currently disagrees with what. Reports, never resolves."""
    pos, kv = _parse(argv, {"json"})
    store = _store()
    try:
        res = store.learning_conflicts()
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    if not res["contradictions"] and not res["duplicates"]:
        _out("no conflicts between the rules that can currently speak")
        return 0
    if res["contradictions"]:
        _out("CONTRADICTIONS (%d). Both rules are live, so both would be injected."
             % len(res["contradictions"]))
        for p in res["contradictions"]:
            _out("")
            _pair_block(p)
        _out("")
        _out("Resolve one with: bm_learn.py resolve-conflict <rule> --with <other> "
             "--how superseded|contradicted|deprecated --because \"...\"")
    if res["duplicates"]:
        _out("")
        _out("POSSIBLE DUPLICATES (%d)" % len(res["duplicates"]))
        for p in res["duplicates"]:
            _out("")
            _pair_block(p)
    return 0


def cmd_link(argv):
    """Record a relationship the detector cannot see.

    The lexical detector finds a reversal ("always X" against "never X"). It
    cannot find that "use tabs" and "use spaces" fight. You can, so you can say
    so, and a declared conflict counts exactly as much as a detected one."""
    pos, kv = _parse(argv, {"because", "json"}, wants_value=("because",))
    if len(pos) != 3:
        _err("usage: link <rule-a> <relation> <rule-b> --because \"...\"")
        _err("relations: %s" % ", ".join(r for r in L.RELATIONS if r != "supersedes"))
        _err("supersession is its own command, because it changes state: supersede")
        return 2
    store = _store()
    try:
        edge = store.link_learning_rules(pos[0], pos[2], pos[1],
                                          note=kv.get("because", ""))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(edge, indent=2, sort_keys=True))
        return 0
    _out("recorded: %s %s %s" % (edge["from_rule_uuid"][:8], edge["relation"],
                                  edge["to_rule_uuid"][:8]))
    if edge["relation"] == "contradicts":
        _out("Both rules are still live. Run conflicts to see it, and "
             "resolve-conflict when you decide which one stands.")
    return 0


def cmd_merge(argv):
    """Fold a duplicate candidate into the rule it repeats."""
    pos, kv = _parse(argv, {"into", "because", "json"},
                     wants_value=("into", "because"))
    if not pos or not kv.get("into"):
        _err("usage: merge <candidate-id> --into <rule-id> --because \"...\"")
        return 2
    store = _store()
    try:
        rule = store.merge_learning_candidate(pos[0], kv["into"],
                                               reason=kv.get("because", ""))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(rule, indent=2, sort_keys=True))
        return 0
    _out("merged into rule %s. No second rule was created, and the candidate is "
         "kept as supporting evidence on that rule." % rule["rule_uuid"][:8])
    return 0


def cmd_supersede(argv):
    """Replace an old rule with a newer one, in one step."""
    pos, kv = _parse(argv, {"with", "because", "json", "receipt"},
                     wants_value=("with", "because", "receipt"))
    if not pos or not kv.get("with"):
        _err("usage: supersede <old-rule> --with <new-rule> --because \"...\" "
             "--receipt <token>")
        return 2
    receipt = _state_change_receipt(kv)
    if not receipt:
        _err("supersede requires a one-time receipt (no-state-change-receipt): "
             "run `grant-state-receipt supersede <old-rule> --with <new-rule> "
             "--answer \"...\"` first.")
        return 2
    store = _store()
    try:
        old = store.change_learning_rule_state(
            pos[0], "superseded", reason=kv.get("because", ""),
            successor_prefix=kv["with"], receipt=receipt,
            receipt_kind="supersede")
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(old, indent=2, sort_keys=True))
        return 0
    _out("%s is superseded. It is no longer retrieved; the successor is returned "
         "in its place from now on." % old["rule_uuid"][:8])
    return 0


def cmd_resolve_conflict(argv):
    """Stand one rule down so a conflict stops being live.

    You choose which one. This command records the choice and the reason; it
    has no opinion about which rule was right."""
    pos, kv = _parse(argv, {"with", "how", "because", "json", "receipt"},
                     wants_value=("with", "how", "because", "receipt"))
    if not pos or not kv.get("with") or not kv.get("how"):
        _err("usage: resolve-conflict <rule> --with <other-rule> "
             "--how superseded|contradicted|deprecated --because \"...\" "
             "--receipt <token>")
        _err("the rule you name is the one that stands down")
        return 2
    receipt = _state_change_receipt(kv)
    if not receipt:
        _err("resolve-conflict requires a one-time receipt "
             "(no-state-change-receipt): run `grant-state-receipt "
             "resolve-conflict <rule> --with <other-rule> --how ... "
             "--answer \"...\"` first. There is no override: standing a "
             "conflict down without one is the exact hole this closes.")
        return 2
    store = _store()
    try:
        r = store.resolve_learning_conflict(pos[0], kv["with"], kv["how"],
                                             reason=kv.get("because", ""),
                                             receipt=receipt)
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(r, indent=2, sort_keys=True))
        return 0
    _out("%s is now %s. It stops being injected; the record of why stays."
         % (r["rule_uuid"][:8], r["state"]))
    return 0


def _outcome_event(kind, argv):
    """Shared body of `rework` and `escaped-defect`.

    Both record the same shape of thing (external evidence that work went
    wrong, tied to the work record it went wrong in) and both link that
    evidence to the rules that were applied at the time. They stay two
    commands because the founder types the name of what happened, not a
    --kind flag describing it."""
    known = {"original-session", "record", "artifact", "because", "evidence",
             "defect-class", "json"}
    pos, kv = _parse(argv, known,
                     wants_value=("original-session", "record", "artifact",
                                  "because", "evidence", "defect-class"))
    record = kv.get("record") or (pos[0] if pos else "")
    if not record:
        _err("usage: %s --record <record-id> [--original-session ID] "
             "[--artifact PATH] %s" % (
                 kind.replace("_", "-"),
                 "[--defect-class NAME] [--evidence \"...\"]"
                 if kind == "escaped_defect" else "[--because \"...\"]"))
        _err("the work record is required: an outcome that cannot say WHICH "
             "work it came from can grade nothing")
        return 2
    summary = kv.get("because", "") or kv.get("evidence", "")
    store = _store()
    try:
        res = store.record_outcome_event(
            kind, record, session_id=kv.get("original-session", ""),
            artifact_ref=kv.get("artifact", ""), summary=summary,
            defect_class=kv.get("defect-class", ""))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    _out("recorded %s as candidate %s (pending: nothing becomes a rule here)"
         % (kind, res["candidate_uuid"][:8]))
    _out("  %s" % L.safe_display(res["artifact_ref"], 160))
    if res["defect_class"]:
        _out("  defect class: %s" % L.safe_display(res["defect_class"], 80))
    # Notes print whether or not anything was graded. The one saying this
    # event was already recorded is the founder's only signal that his second
    # run changed nothing, and hiding it behind an empty link list is how a
    # re-run looked like a second event.
    for note in res["notes"]:
        _out("  %s" % note)
    if not res["linked_applications"]:
        return 0
    _out("  %d rule application(s) graded by this outcome:"
         % len(res["linked_applications"]))
    for link in res["linked_applications"]:
        _out("    %s v%d  %-18s %s"
             % (link["rule_uuid"][:8], link["rule_version"],
                link["classification"], L.safe_display(link["classification_reason"], 100)))
    return 0


def cmd_rework(argv):
    """The same work had to be done again. External evidence, so it grades."""
    return _outcome_event("rework", argv)


def cmd_escaped_defect(argv):
    """A defect reached you in work that was already called done."""
    return _outcome_event("escaped_defect", argv)


def cmd_confirm(argv):
    """Promote a rule to confirmed, or a confirmed one to settled.

    Loop 8 grades a repeated correction only against a CONFIRMED or SETTLED
    rule, and until this command existed there was no founder-facing way to
    reach either state: every rule stayed 'approved' forever and the repeat
    check could never fire on real usage. Found by driving the CLI, not by a
    test.

    The store refuses this unless the rule already carries at least one
    supporting event that is not the original approval. That refusal is the
    point: approval is your intent, not evidence the rule worked."""
    pos, kv = _parse(argv, {"to", "because", "json"},
                     wants_value=("to", "because"))
    target = kv.get("to", "confirmed")
    if not pos or target not in ("confirmed", "settled"):
        _err("usage: confirm <rule-id> [--to confirmed|settled] "
             "[--because \"...\"]")
        return 2
    store = _store()
    try:
        r = store.change_learning_rule_state(pos[0], target,
                                             reason=kv.get("because", ""))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(r, indent=2, sort_keys=True))
        return 0
    _out("%s is now %s. A correction that repeats it from here is graded as a "
         "loop failure rather than as new evidence."
         % (r["rule_uuid"][:8], r["state"]))
    return 0


def cmd_repeat_check(argv):
    """Did you already tell me this? READ ONLY unless you pass --record.

    Answers the question the counter cannot: not how often the same correction
    came back, but WHY the rule you already approved failed to prevent it.
    Never retrieved, retrieved and skipped, retrieved into the wrong work, or
    followed and wrong anyway are four different repairs.

    --record writes the finding down: evidence on the rule, and a
    corrected_again outcome on the applications from that work. It approves
    nothing and changes no rule's state."""
    pos, kv = _parse(argv, {"record", "json"})
    if not pos:
        _err("usage: repeat-check <candidate-id> [--record] [--json]")
        return 2
    store = _store()
    try:
        res = store.detect_repeated_correction(pos[0], record=bool(kv.get("record")))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    _out("candidate %s (%s)" % (res["candidate_uuid"][:8], res["candidate_status"]))
    for note in res["notes"]:
        _out("  %s" % note)
    for m in res["unsettled_matches"]:
        _out("  %s" % L.safe_display(m["reason"], 160))
    for f in res["repeats"]:
        _out("  repeats %s (%s, v%d): %s"
             % (f["rule_uuid"][:8], f["rule_state"], f["rule_version"],
                f["classification"]))
        _out("      %s" % L.safe_display(f["classification_reason"], 150))
        if f["recorded"]:
            _out("      evidence recorded as %s" % f["polarity"])
        else:
            _out("      nothing written; pass --record to keep this finding")
    if not res["repeats"]:
        return 0
    _out("")
    _out("A repeat is not an argument for deleting the rule. Which repair it "
         "asks for is exactly what the class above names.")
    return 0


def cmd_loop_failures(argv):
    """Where the loop broke, over a window, counted from rows that exist.

    Nothing here is estimated. A class with no data says so rather than
    printing a zero that reads like a clean bill of health."""
    pos, kv = _parse(argv, {"since", "json"}, wants_value=("since",))
    days = None
    if kv.get("since"):
        days, err = L.parse_window_days(kv["since"])
        if err:
            _err("bm_learn: %s" % err)
            return 2
    store = _store()
    try:
        res = store.learning_loop_failures(window_days=days)
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    _out("loop failures%s" % (" since %s" % res["since"] if res["since"] else
                              " (all recorded history)"))
    _out("  applications in window: %d" % res["applications_in_window"])
    for name in res["classes"]:
        _out("  %-20s %d" % (name, res["counts"].get(name, 0)))
    _out("  repeated settled corrections: %d"
         % len(res["repeated_settled_corrections"]))
    for r in res["repeated_settled_corrections"]:
        _out("    %s repeats %s: %s"
             % (r["candidate_uuid"][:8], r["rule_uuid"][:8], r["classification"]))
    _out("  unresolved contradictions: %d" % len(res["unresolved_contradictions"]))
    _out("  rules never retrieved: %d" % len(res["rules_never_retrieved"]))
    _out("  rules always marked not relevant: %d"
         % len(res["rules_always_not_relevant"]))
    _out("  rework and escaped defects linked to a rule: %d"
         % len(res["outcomes_linked_to_rules"]))
    # Printed here and NOT under repeated corrections. An escaped defect is
    # not an instruction the founder gave twice, and reading it as one told
    # him he was repeating himself when he was not.
    for r in res["outcome_gradings"]:
        _out("    %s (%s) grades %s: %s"
             % (r["candidate_uuid"][:8], r["source_type"],
                r["rule_uuid"][:8], r["classification"]))
    _out("  unattributed outcomes (listed separately, never averaged in): %d"
         % len(res["unattributed_outcomes"]))
    for u in res["unattributed_outcomes"]:
        _out("    %s %s: %s" % (u["candidate_uuid"][:8], u["source_type"],
                                u["reason"]))
    for note in res["notes"]:
        _out("  NOT MEASURED: %s" % note)
    return 0


def cmd_rule_outcomes(argv):
    """What happened after this rule was shown. Counts only, never a rate."""
    pos, kv = _parse(argv, {"json"})
    if not pos:
        _err("usage: rule-outcomes <rule-id> [--json]")
        return 2
    store = _store()
    try:
        res = store.learning_rule_outcomes(pos[0])
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    _out("rule %s  [%s, %s, v%d]" % (res["rule_uuid"][:8], res["state"],
                                      res["rule_type"], res["current_version"]))
    _out("  applications: %d" % res["applications"])
    _out("  by disposition: %s" % (res["by_disposition"] or "none recorded"))
    _out("  by outcome: %s" % (res["by_outcome"] or "none recorded"))
    _out("  by rule version: %s" % (res["by_rule_version"] or "none recorded"))
    _out("  evidence by polarity: %s" % (res["evidence_by_polarity"] or "none"))
    for r in res["repeated_corrections"]:
        # "repeated by" only where something really was said again. A rework
        # or an escaped defect graded this rule, it did not repeat it.
        _out("  %s by %s (%s): %s"
             % ("repeated" if r["is_correction"] else "graded",
                r["candidate_uuid"][:8], r["source_type"], r["classification"]))
    for g in res["graded_applications"]:
        if g["classification"]:
            _out("  %s v%d: %s" % (g["application_uuid"][:8], g["rule_version"],
                                    g["classification"]))
    for note in res["notes"]:
        _out("  NOT MEASURED: %s" % note)
    _out("")
    _out("These are counts of recorded events, not a success rate. No "
         "denominator here is large enough for one.")
    return 0


def cmd_verify(argv):
    """Integrity of the learning tables, with an exit code a script can read.

    0 clean, 1 findings, 2 the command itself could not run. That is the whole
    contract, and it is why this prints a count even when it is zero."""
    pos, kv = _parse(argv, {"json"})
    store = _store()
    try:
        res = store.learning_verify()
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res["ok"] else 1
    _out("learning-verify: %d rule(s), %d edge(s), %d check(s) run"
         % (res["rules"], res["edges"], len(res["checks"])))
    for note in res["notes"]:
        _out("  note: %s" % note)
    if res["ok"]:
        _out("  no findings")
        return 0
    _out("  %d finding(s):" % len(res["findings"]))
    for f in res["findings"]:
        _out("    %-28s %s" % (f["code"], L.safe_display(f["detail"], 160)))
    return 1


def cmd_index_status(argv):
    """What the optional search index is doing. Read only."""
    pos, kv = _parse(argv, {"json"})
    store = _store()
    try:
        res = store.learning_index_status()
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0
    _out("search index: mode=%s" % res["mode"])
    _out("  requested: %s     available: %s"
         % ("yes" if res["requested"] else "no",
            "yes" if res["available"] else "no"))
    if res["available"]:
        _out("  %s row(s) indexed for %s rule(s)"
             % (res["indexed_rows"], res["rules"]))
        _out("  drift is checked by: bm_learn.py verify")
    else:
        _out("  retrieval is lexical, which is complete on its own.")
        _out("  turn the fast path on with %s (off again with %s)"
             % (res["enable_with"], res["disable_with"]))
    return 0


def cmd_rebuild_index(argv):
    """Rebuild the search index from the rules, atomically. Exit 0 when it
    rebuilt, 2 when there was no index to rebuild and it said why."""
    pos, kv = _parse(argv, {"json"})
    store = _store()
    try:
        res = store.rebuild_learning_index()
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res["ok"] else 2
    if not res["ok"]:
        _err("bm_learn: %s" % res["reason"])
        return 2
    _out("search index rebuilt: %d row(s), mode=%s" % (res["indexed"], res["mode"]))
    _out("Confirm with: bm_learn.py verify")
    return 0


def _anchor(spec):
    """"<type>:<key>" into (type, key). Split once, so a Windows-style key or a
    path with a colon in it keeps its colon."""
    atype, sep, key = (spec or "").partition(":")
    if not sep or not atype.strip() or not key.strip():
        _err("bm_learn: --anchor takes <type>:<key>, for example "
             "file:tools/bm_store.py or candidate:1a2b3c4d (types: %s)"
             % ", ".join(bs.NOTE_ANCHOR_TYPES))
        sys.exit(2)
    return atype.strip(), key.strip()


def _note_payload(n):
    """One note row as --json prints it: the digest-shaped columns withheld by
    bm_store.withhold_digest_columns, which is the one withholding policy (I9).

    Presentation only, which is why it lives here. Reproduced against a real
    store before this existed: `notes --json` printed notes.anchor_line_hash in
    full, the unsalted sha256 of the anchored source line, while
    `bm_store dump` withheld the same column and facts.json redacted the line
    itself. A guessed line could be confirmed byte for byte from that digest."""
    return bs.withhold_digest_columns("notes", n)


def _note_line(n):
    state = "open"
    if n["resolved_at"]:
        state = "resolved"
    if n["overridden_at"]:
        state = "overridden" if not n["resolved_at"] else "resolved, overridden"
    where = "%s:%s" % (n["anchor_type"], L.safe_display(n["anchor_key"], 80))
    if n["anchor_line"]:
        where += ":%d" % n["anchor_line"]
    return "%s  %-8s %-9s %-8s %s  by %s" % (
        n["note_uuid"][:8], n["kind"], n["severity"] or "-", state, where,
        L.safe_display(n["author"], 40))


def cmd_note(argv):
    """Write one anchored note. A critical alert written here REFUSES an
    approval whose change set touches its anchor, so the body is what the
    founder will read at the refusal."""
    pos, kv = _parse(argv, {"kind", "severity", "author", "author-kind",
                            "anchor", "line", "body", "session", "json"},
                     wants_value=("kind", "severity", "author", "author-kind",
                                  "anchor", "line", "body", "session"))
    if not kv.get("anchor") or not (kv.get("body") or "").strip():
        _err("usage: note --kind %s --anchor <type>:<key> --body \"...\" "
             "--author \"name\" [--author-kind founder|assistant|human] "
             "[--severity info|warning|critical] [--line N]"
             % "|".join(bs.NOTE_KINDS))
        _err("only an unresolved alert at severity critical refuses an approval.")
        return 2
    atype, akey = _anchor(kv["anchor"])
    store = _store()
    try:
        n = store.add_note(
            kind=kv.get("kind", "insight"), body=kv["body"],
            author=kv.get("author", ""),
            author_kind=kv.get("author-kind", "human"),
            anchor_type=atype, anchor_key=akey,
            severity=kv.get("severity", ""), anchor_line=kv.get("line"),
            session_id=kv.get("session", ""))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(_note_payload(n), indent=2, sort_keys=True))
        return 0
    _out("note %s recorded" % n["note_uuid"][:8])
    _out("  " + _note_line(n))
    if (n["anchor_type"] == "file" and n["anchor_line"]
            and not n["anchor_line_hash"]):
        # SAID AT WRITE TIME, because this is the one moment the author can fix
        # it. No fingerprint means a later move of that line cannot be detected,
        # and the reports will say unverifiable rather than pretending otherwise.
        _out("  the anchored line could not be fingerprinted (the file could "
             "not be read, or the line is blank), so if it later moves nothing "
             "can detect that. Anchor to a line with code on it, or drop --line.")
    if n["kind"] == bs.BLOCKING_NOTE_KIND and n["severity"] == bs.BLOCKING_NOTE_SEVERITY:
        # WHAT THIS SAYS DEPENDS ON THE ANCHOR, because the store's teeth do.
        # bm_store.blocking_alerts matches a file, a candidate and a work
        # record; a rule or a decision anchor is recorded and rendered but
        # refuses nothing. Printing one promise for all five anchor types told an
        # author a gate was held when nothing was held, which is the one thing an
        # alert must never do.
        if n["anchor_type"] in ("file", "candidate", "record"):
            _out("  this REFUSES an approval anchored here until it is resolved, "
                 "or until the founder overrides it AT THAT GATE with a recorded "
                 "reason. An override at one gate does not clear it for the next.")
        else:
            _out("  recorded and rendered at its anchor, but an alert anchored to "
                 "a %s refuses no approval by itself: anchor it to the file, the "
                 "candidate or the work record if it must hold a gate."
                 % n["anchor_type"])
    return 0


def cmd_notes(argv):
    pos, kv = _parse(argv, {"kind", "anchor", "severity", "open", "json"},
                     wants_value=("kind", "anchor", "severity"))
    anchor_pairs = [_anchor(kv["anchor"])] if kv.get("anchor") else None
    store = _store()
    try:
        rows = store.list_notes(
            kinds=(kv["kind"],) if kv.get("kind") else None,
            severities=(kv["severity"],) if kv.get("severity") else None,
            anchors=anchor_pairs, include_resolved=not kv.get("open"))
        # One read per file however many notes point at it, and the SAME
        # implementation the documentation engine and the packs use, so no two
        # surfaces can disagree about whether a reviewer is looking at the line
        # the note was written about.
        anchors = {r["note_uuid"]: r for r in store.note_anchor_reports(rows)}
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps([_note_payload(r) for r in rows],
                        indent=2, sort_keys=True))
        return 0
    if not rows:
        _out("no notes match")
        return 0
    for n in rows:
        _out(_note_line(n))
        _out("     %s" % L.safe_display(n["body"], 200))
        # WHERE THE ANCHORED LINE IS NOW, printed for every anchor that is not
        # simply still there. Before this, a note anchored to a line that had
        # moved listed exactly like one that had not, and the author read the
        # note as being about whatever now sits at that line number.
        found = anchors.get(n["note_uuid"])
        if found is not None and found["state"] != "resolves":
            _out("     ANCHOR %s: %s" % (found["state"].upper(), found["why"]))
        if n["resolved_at"]:
            _out("     resolved %s: %s"
                 % (n["resolved_at"], L.safe_display(n["resolution"], 160)))
        if n["overridden_at"]:
            _out("     OVERRIDDEN %s by %s: %s"
                 % (n["overridden_at"], L.safe_display(n["override_by"], 60),
                    L.safe_display(n["override_reason"], 160)))
    _out("%d note(s)" % len(rows))
    return 0


def cmd_resolve_note(argv):
    """Record that a note was answered. NOT an override: see the docstring on
    bm_store.resolve_note for what this does and does not prove.

    A CRITICAL alert additionally needs a receipt (LOOP 2, 2026-07-30):
    resolving one used to unblock an approval with no human answer anywhere,
    since blocking_alerts stops counting a note the moment it is resolved. An
    ordinary note, and a non-critical alert, still resolve with no receipt,
    exactly as before."""
    pos, kv = _parse(argv, {"because", "json", "receipt"},
                     wants_value=("because", "receipt"))
    if not pos or not (kv.get("because") or "").strip():
        _err("usage: resolve-note <note-id> --because \"what resolved it\" "
             "[--receipt <token>]")
        _err("a receipt is required only when the note is a critical alert; "
             "run `grant-state-receipt resolve-note <note-id> --because "
             "\"<the same text, word for word>\" --answer \"...\"` first if "
             "it is")
        return 2
    store = _store()
    try:
        n = store.resolve_note(pos[0], kv["because"],
                               receipt=_state_change_receipt(kv))
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(_note_payload(n), indent=2, sort_keys=True))
        return 0
    _out("note %s resolved" % n["note_uuid"][:8])
    _out("  " + _note_line(n))
    return 0


COMMANDS = {
    "capture": cmd_capture,
    "note": cmd_note,
    "notes": cmd_notes,
    "resolve-note": cmd_resolve_note,
    "index-status": cmd_index_status,
    "rebuild-index": cmd_rebuild_index,
    "outcome": cmd_outcome,
    "inbox": cmd_inbox,
    "metrics": cmd_metrics,
    "candidates": cmd_candidates,
    "show-candidate": cmd_show_candidate,
    "grant-approval": cmd_grant_approval,
    "grant-state-receipt": cmd_grant_state_receipt,
    "approve": cmd_approve,
    "reject": cmd_reject,
    "rules": cmd_rules,
    "lookup": cmd_lookup,
    "apply": cmd_apply,
    "relevant": cmd_relevant,
    "applications": cmd_applications,
    "disposition": cmd_disposition,
    "promote": cmd_promote,
    "cancel": cmd_cancel,
    "classify": cmd_classify,
    "should-retrieve": cmd_should_retrieve,
    "why": cmd_why,
    "deprecate": cmd_deprecate,
    "forget": cmd_forget,
    "conflicts": cmd_conflicts,
    "link": cmd_link,
    "merge": cmd_merge,
    "supersede": cmd_supersede,
    "resolve-conflict": cmd_resolve_conflict,
    "confirm": cmd_confirm,
    "rework": cmd_rework,
    "escaped-defect": cmd_escaped_defect,
    "repeat-check": cmd_repeat_check,
    "loop-failures": cmd_loop_failures,
    "rule-outcomes": cmd_rule_outcomes,
    "verify": cmd_verify,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out(__doc__.strip())
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_learn: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    try:
        return COMMANDS[cmd](argv[1:])
    except bs.OwnershipRefused as e:
        # Fail CLOSED and say which rule refused, so the next step is obvious
        # rather than a guess. Matches bm_threads.py's failure policy.
        _err("refused (%s): %s" % (e.reason, e))
        return 2
    except bs.StaleIdentity as e:
        _err("refused (stale-identity): %s" % e)
        return 2
    except bs.BMStoreError as e:
        _err("bm_learn: %s" % e)
        return 2


def cli():
    """Console-script entry point for a packaged install (pipx, uv, pip).

    A packaging entry point must be callable with no arguments, and main()
    takes argv. The __main__ block below calls this same function rather
    than repeating the line, so `bm-learn` and
    `python3 tools/bm_learn.py` cannot drift apart: there is exactly one
    path into main() from a shell."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
