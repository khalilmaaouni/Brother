#!/usr/bin/env python3
"""ORCH-08 of the 1.0.20 orchestration control plane: the resume capsule.

WHY THIS EXISTS. An orchestrator process dies mid-run. A new one starts with
no transcript and never will have one, so it must reconstruct the run
entirely from durable state on disk, never from what it assumes was
happening. The failure this module exists to prevent is a restarted
orchestrator that is CONFIDENTLY WRONG: it read a gap as "nothing to do"
instead of "could not see", and every action after that is consistent with
its wrong picture. See the `unreadable` rule below, which is this module's
whole reason to exist.

THIS IS A THIN EXTENSION, NOT A SECOND RECONSTRUCTION. scripts/continuity.py
(row E73.1) already rebuilds a run's projection from the journal, the Work
document and claims.json: canonical revision, the objective, and a per-unit
bucket (integrated / active / pending / abandoned / unclear) with an attempt
count and a detail string, the abandoned bucket already naming a dead
lease's own reason. Building a second, competing reconstruction beside it
would drift from it the first time either one changed, and a restarted
orchestrator would then get a different answer depending on which module it
happened to call. So this module calls continuity.capsule() for everything
it already covers, and adds only the handful of facts that module has no
concept of: which authority lease THIS instance holds, the run's hard stop,
the units awaiting review, the required gates, and the red queue and amber
rulings a review/retry unit not yet built in this run will eventually own.

FIELD SOURCES, so a later reader knows where every capsule key actually
comes from without re-deriving it:

  run_id              the journal's own first recorded event's "run_id"
                      field when the journal has any events (journal.py
                      stamps every event with it); falling back to
                      os.path.basename(run_root) -- journal.append()'s own
                      definition of run_id (see its docstring) -- only when
                      the journal is readable but empty. Never guessed: an
                      unreadable journal still leaves this derivable from
                      the path alone, which is a fact about the filesystem,
                      not an assumption about the run's content.
  goal                continuity's own "objective" field (the Work
                      document's "outcome"). A NO-DATA-prefixed value from
                      continuity (no Work document could be read) is
                      treated as this module's own "work_document" source
                      being unreadable, not as a real goal string.
  hard_stop           orchestrator_control.json's own "hard_stop_jst".
                      continuity has no concept of a run deadline; no other
                      unit in this build owns writing this file yet.
  canonical_revision  continuity's own "canonical_revision" field, with its
                      NO-DATA-prefixed "could not read git HEAD" case
                      folded into this module's own unreadable list.
  work_graph_summary,
  attempt_counts,
  failed_units        derived from continuity's own "units"/"buckets":
                      work_graph_summary counts units by continuity's
                      bucket; attempt_counts is {unit id: attempt} off the
                      same units; failed_units is every unit continuity put
                      in its "abandoned" bucket (a claim whose lease died
                      before it finished), each carrying continuity's own
                      already-computed detail string as latest_failure.
                      All three come from the SAME "work document" source
                      as goal and are set to None together when it is
                      unreadable, because a row's title, owns and done_check
                      all live only in that file: a work graph missing its
                      own row context is not a partial graph, it is not a
                      graph this module can honestly describe.
  active_claims,
  active_lanes        THIS module's own read of claims.json (existence and
                      JSON validity only), gating continuity's "active"
                      bucket units (active_claims) and, via
                      claim_store.reconcile() called directly, the set of
                      owners currently holding a live claim (active_lanes).
                      continuity degrades a missing or corrupt claims.json
                      to its own "unclear" bucket per unit rather than
                      failing outright; this module additionally names the
                      claim store itself as unreadable at the top level, so
                      a caller checking active_claims alone (rather than
                      reading every unit's bucket) still sees the gap.
  awaiting_review,
  ready_now,
  red_queue,
  amber_rulings,
  required_gates,
  resource_capacity   orchestrator_control.json, this run's own small
                      control-plane snapshot. None of continuity, claim_store
                      or journal has a concept of a REVIEW state, a red
                      queue or an amber ruling (those verbs belong to
                      orchestrator_invariants.TASK_STATES and to
                      fable_authority.py's classify()/queue_red()/
                      record_amber(), which are estate-wide and carry no
                      run_id at all, so reading them wholesale into one
                      run's capsule would attribute another night's
                      decisions to this one). A future unit in this build
                      (the router, the review gate, the retry policy, the
                      supervisor) is expected to WRITE this file as it
                      ticks; this module only reads it.
  my_authority        orchestrator_authority.current(), never parsed by
                      hand. Reports {"held": False, ...} naming who
                      actually holds the scope when it is not this
                      instance, rather than ever reporting someone else's
                      lease as this instance's own (see build_capsule()).
  last_journal_seq    THIS module's own strict read of journal.jsonl: a
                      single torn line makes the WHOLE journal unreadable
                      here, unlike journal.read()'s per-line tolerance
                      (right for an ordinary projection, wrong for a resume
                      capsule whose job is to say plainly when it cannot
                      trust a sequence rather than silently report a
                      shorter one).
  unreadable,
  generated_at,
  digest              this module's own: see below.

FIXTURE LAYOUT ON DISK, `run_root` (the same directory journal.py,
claim_store.py, brother_run.py and continuity.py already share):

  <run_root>/journal.jsonl              journal.py's own append-only log.
  <run_root>/claims.json                claim_store.py's own store.
  <run_root>/target.json                brother_run's own {"cwd": ...}.
  <run_root>/<name>.json                the Work document (any name not
                                         one of brother_run.ENGINE_JSON_FILES):
                                         {"outcome": ..., "rows": [{"id",
                                         "title", "status", "done_check",
                                         "owns", "integration_refused"}]}.
  <run_root>/authority.json             orchestrator_authority.py's own
                                         store, scope AUTHORITY_SCOPE below.
  <run_root>/orchestrator_control.json  this module's own small file:
                                         {"hard_stop_jst", "required_gates",
                                         "resource_capacity", "ready_now",
                                         "awaiting_review", "red_queue",
                                         "amber_rulings"}.

THE UNREADABLE RULE, this module's central law. For every source it cannot
read (missing file, malformed JSON, a permission error), the gap is named
explicitly in the `unreadable` list and the field(s) that source feeds are
set to None, NEVER to an empty list, a zero or a plausible default.
`active_claims: []` means the claim store was read and holds nothing.
`active_claims: None` with a matching `unreadable` entry means it could not
be read at all. A capsule with a non-empty `unreadable` list is still a
valid capsule: it is the honest answer, never an error.

Python 3.9 floor, standard library only, no network.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import claim_store
import continuity
import journal
import orchestrator_authority

NODATA = "NO-DATA"

#: The one authority scope a night run's control plane holds: whichever
#: orchestrator instance holds this scope is the one driving the whole run.
AUTHORITY_SCOPE = "night-run"

#: continuity._work_doc_path() (and brother_run._find_work_doc, the
#: function it mirrors) treats "the one *.json file in run_dir that is not
#: one of brother_run.ENGINE_JSON_FILES" as the Work document. Neither set
#: is this module's to extend, so this module's own two files live in a
#: subdirectory instead of run_dir's own top level, where they can never
#: be mistaken for the Work document or force that lookup to see two
#: candidates and refuse both.
CONTROL_DIR = "orchestrator-resume"
AUTHORITY_FILE = CONTROL_DIR + "/authority.json"
CLAIMS_FILE = "claims.json"
CONTROL_FILE = CONTROL_DIR + "/control.json"

#: How many entries a bounded render() list shows before it names how many
#: more were dropped, so a run with hundreds of units never produces an
#: unusable capsule.
RENDER_LIMIT = 20

#: orchestrator_control.json's own keys, defaulted to an empty collection
#: when the file is present but a writer has not populated them yet -- a
#: file that exists and parses is a readABLE source even when a future
#: unit has not started writing to every key of it. Only a missing or
#: unparsable file is `unreadable`; a present-but-partial one is not.
_CONTROL_DEFAULTS = {
    "hard_stop_jst": None,
    "required_gates": [],
    "resource_capacity": {},
    "ready_now": [],
    "awaiting_review": [],
    "red_queue": [],
    "amber_rulings": [],
}


def _path(run_root, filename):
    return os.path.join(str(run_root), filename)


def _read_json(run_root, filename, label):
    """(value, problem). `value` is None and `problem` names `label` and
    the reason whenever the file is missing, unreadable, or not valid
    JSON. A caller never treats a None value as empty: it records
    `problem` in the capsule's unreadable list."""
    path = _path(run_root, filename)
    if not os.path.isfile(path):
        return None, "%s: no file at %s" % (label, path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        return None, "%s: could not read %s: %s" % (label, path, exc)
    except ValueError as exc:
        return None, "%s: %s is not valid JSON: %s" % (label, path, exc)
    return data, None


def _read_journal_strict(run_root):
    """(events, problem). Unlike journal.read(), a single torn line makes
    the WHOLE journal unreadable here (events is None), rather than being
    skipped: see the module docstring's `last_journal_seq` entry. A
    missing file is also unreadable here, unlike journal.read()'s "no run
    directory is not a failure" stance, which is right for a library used
    both inside and outside a run; a resume capsule is always built for a
    run that is supposed to already exist."""
    path = _path(run_root, journal.JOURNAL_FILENAME)
    label = "journal"
    if not os.path.isfile(path):
        return None, "%s: no file at %s" % (label, path)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return None, "%s: could not read %s: %s" % (label, path, exc)
    events = []
    for number, text in enumerate(lines, 1):
        text = text.strip()
        if not text:
            continue
        try:
            events.append(json.loads(text))
        except ValueError as exc:
            return None, ("%s: line %d of %s is not valid JSON: %s"
                           % (label, number, path, exc))
    return events, None


def _claims_readable(run_root):
    """(ok, problem). Existence and JSON validity of claims.json ONLY --
    this module never re-derives claim liveness or bucket membership by
    hand, that stays claim_store's and continuity's job. This exists
    because continuity degrades a missing or corrupt claims.json to its
    own per-unit "unclear" bucket rather than failing outright, which is
    the right thing for a projection but would leave this module's own
    top-level active_claims/active_lanes silently absent rather than
    explicitly unreadable."""
    path = _path(run_root, CLAIMS_FILE)
    if not os.path.isfile(path):
        return False, "claims: no file at %s" % path
    try:
        with open(path, encoding="utf-8") as fh:
            json.load(fh)
    except OSError as exc:
        return False, "claims: could not read %s: %s" % (path, exc)
    except ValueError as exc:
        return False, "claims: %s is not valid JSON: %s" % (path, exc)
    return True, None


def build_capsule(run_root, run_id, orchestrator, instance, now=None):
    """The resume capsule for `run_root`, built fresh from disk on every
    call (never cached, never assumed unchanged from a prior call). See
    the module docstring for where every field comes from and the
    unreadable rule that decides whether a gap is named or silently
    filled.

    `run_id` is the identity the CALLING orchestrator believes it is
    operating under, used only to look up ITS OWN authority lease (a
    lease store is keyed by run_id and scope, see orchestrator_authority.py).
    The capsule's own reported `run_id` field is read from durable state
    (the journal, or the run directory's own name) instead, since a resume
    capsule's job is to state what the disk says, not to echo a caller's
    belief back to it unchecked.

    `orchestrator` and `instance` name which orchestrator identity and
    running instance this capsule is being built for; they decide only
    `my_authority` (see below), nothing else on this capsule depends on
    who is asking.
    """
    now = time.time() if now is None else float(now)
    clock = (lambda: now)
    unreadable = []
    capsule = {}

    # --- the journal, this module's own strict read -------------------
    events, journal_problem = _read_journal_strict(run_root)
    if journal_problem is not None:
        unreadable.append(journal_problem)
    capsule["last_journal_seq"] = len(events) if events is not None else None
    if events:
        capsule["run_id"] = events[0].get("run_id") or os.path.basename(
            os.path.normpath(str(run_root)))
    else:
        capsule["run_id"] = os.path.basename(os.path.normpath(str(run_root)))

    # --- continuity's own projection: revision, goal, work graph ------
    base, base_problem = continuity.capsule(run_root, clock=clock)
    if base is None:
        unreadable.append("continuity: %s" % base_problem)
        capsule["canonical_revision"] = None
        capsule["goal"] = None
        capsule["work_graph_summary"] = None
        capsule["attempt_counts"] = None
        capsule["failed_units"] = None
        base_units = []
    else:
        revision = base.get("canonical_revision")
        if isinstance(revision, str) and revision.startswith(NODATA):
            unreadable.append("canonical_revision: %s" % revision)
            capsule["canonical_revision"] = None
        else:
            capsule["canonical_revision"] = revision

        objective = base.get("objective")
        base_units = base.get("units") or []
        if isinstance(objective, str) and objective.startswith(NODATA):
            unreadable.append("work_document: %s" % objective)
            capsule["goal"] = None
            capsule["work_graph_summary"] = None
            capsule["attempt_counts"] = None
            capsule["failed_units"] = None
        else:
            capsule["goal"] = objective
            buckets = base.get("buckets") or {}
            capsule["work_graph_summary"] = {
                "total": len(base_units),
                "by_bucket": {k: len(v) for k, v in buckets.items()},
            }
            capsule["attempt_counts"] = {
                u["id"]: u.get("attempt") for u in base_units}
            capsule["failed_units"] = [
                {"id": u["id"], "title": u.get("title"),
                 "state": u.get("bucket"), "attempt": u.get("attempt"),
                 "latest_failure": u.get("detail")}
                for u in base_units if u.get("bucket") == "abandoned"]

    # --- claims.json, this module's own readability gate ---------------
    claims_ok, claims_problem = _claims_readable(run_root)
    if not claims_ok:
        unreadable.append(claims_problem)
        capsule["active_claims"] = None
        capsule["active_lanes"] = None
    elif base is None:
        # continuity itself could not be built (no journal): bucket
        # membership is unknown even though claims.json parses fine.
        capsule["active_claims"] = None
        capsule["active_lanes"] = None
    else:
        capsule["active_claims"] = [
            u for u in base_units if u.get("bucket") == "active"]
        findings, reconcile_problem = claim_store.reconcile(
            _path(run_root, CLAIMS_FILE), clock=clock)
        if findings is None:
            unreadable.append("claims: reconcile could not read %s: %s"
                               % (_path(run_root, CLAIMS_FILE), reconcile_problem))
            capsule["active_lanes"] = None
        else:
            capsule["active_lanes"] = sorted({
                f["owner"] for f in findings
                if f.get("status") == "in-flight" and f.get("owner")})

    # --- this run's own control-plane snapshot -------------------------
    control, control_problem = _read_json(run_root, CONTROL_FILE,
                                           "orchestrator_control")
    if control_problem is not None:
        unreadable.append(control_problem)
        for key in _CONTROL_DEFAULTS:
            capsule[_CONTROL_KEY_MAP[key]] = None
    else:
        for key, default in _CONTROL_DEFAULTS.items():
            capsule[_CONTROL_KEY_MAP[key]] = control.get(key, default)

    # --- this instance's own authority, never someone else's -----------
    authority_path = _path(run_root, AUTHORITY_FILE)
    try:
        lease = orchestrator_authority.current(
            authority_path, run_id, AUTHORITY_SCOPE, now=now)
    except orchestrator_authority.AuthorityUnreadable as exc:
        unreadable.append("authority: %s" % exc)
        capsule["my_authority"] = None
    else:
        if lease is None:
            capsule["my_authority"] = {
                "held": False, "scope": AUTHORITY_SCOPE,
                "reason": "no live lease exists for this scope",
            }
        elif lease.instance != instance:
            capsule["my_authority"] = {
                "held": False, "scope": AUTHORITY_SCOPE,
                "reason": ("scope is held live by instance %s (orchestrator "
                           "%s) at epoch %s, not by %s"
                           % (lease.instance, lease.orchestrator, lease.epoch,
                              instance)),
                "held_by": lease.instance, "epoch": lease.epoch,
            }
        else:
            capsule["my_authority"] = {
                "held": True, "scope": AUTHORITY_SCOPE,
                "orchestrator": lease.orchestrator, "instance": lease.instance,
                "epoch": lease.epoch, "acquired_at": lease.acquired_at,
                "expires_at": lease.expires_at,
            }

    capsule["unreadable"] = unreadable
    capsule["generated_at"] = now
    capsule["digest"] = capsule_digest(capsule)
    return capsule


#: orchestrator_control.json's own key names to this module's capsule key
#: names, named once here so build_capsule() and the docstring table above
#: cannot drift apart on which is which.
_CONTROL_KEY_MAP = {
    "hard_stop_jst": "hard_stop",
    "required_gates": "required_gates",
    "resource_capacity": "resource_capacity",
    "ready_now": "ready_now",
    "awaiting_review": "awaiting_review",
    "red_queue": "red_queue",
    "amber_rulings": "amber_rulings",
}


def capsule_digest(capsule):
    """A stable sha256 over `capsule`'s own content, excluding the volatile
    `generated_at` and the digest field itself (a digest that hashed its
    own value, or the clock reading, could never match twice). Canonical:
    sorted keys, fixed separators, so identical content always serialises
    identically regardless of dict insertion order."""
    payload = {k: v for k, v in capsule.items()
               if k not in ("generated_at", "digest")}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _bounded(items, limit=RENDER_LIMIT):
    """(shown, omitted_count). Never truncates silently: the caller always
    has the omitted count to print."""
    items = list(items)
    return items[:limit], max(0, len(items) - limit)


def _render_section(lines, title, value, formatter=str):
    if value is None:
        lines.append("%s: UNKNOWN (unreadable, see UNREADABLE above)" % title)
        return
    if isinstance(value, dict):
        keys = sorted(value.keys(), key=str)
        shown, omitted = _bounded(keys)
        lines.append("%s (%d):" % (title, len(value)))
        for k in shown:
            lines.append("  %s: %s" % (k, formatter(value[k])))
        if omitted:
            lines.append("  ... and %d more omitted" % omitted)
        return
    shown, omitted = _bounded(value)
    lines.append("%s (%d):" % (title, len(value)))
    for item in shown:
        lines.append("  - %s" % formatter(item))
    if omitted:
        lines.append("  ... and %d more omitted" % omitted)


def render(capsule):
    """The bounded text an orchestrator is actually given: never more than
    RENDER_LIMIT entries per list, always naming how many were dropped,
    and the `unreadable` gaps shown FIRST and prominently rather than
    buried, since the entire point of this capsule is that a restarted
    orchestrator knows what it cannot see before it reads anything else."""
    lines = []
    lines.append("RESUME CAPSULE for run %s"
                  % (capsule.get("run_id") or "UNKNOWN"))
    lines.append("generated_at=%s digest=%s"
                  % (capsule.get("generated_at"), capsule.get("digest")))
    lines.append("goal: %s" % (capsule.get("goal") or "UNKNOWN"))
    lines.append("hard_stop: %s" % (capsule.get("hard_stop") or "UNKNOWN"))
    lines.append("canonical_revision: %s"
                  % (capsule.get("canonical_revision") or "UNKNOWN"))
    lines.append("")

    unreadable = capsule.get("unreadable") or []
    if unreadable:
        lines.append("UNREADABLE (%d source(s) this capsule could NOT read; "
                      "the matching field is unknown, never empty):"
                      % len(unreadable))
        shown, omitted = _bounded(unreadable)
        for item in shown:
            lines.append("  - %s" % item)
        if omitted:
            lines.append("  ... and %d more omitted" % omitted)
    else:
        lines.append("UNREADABLE: none, every source was read")
    lines.append("")

    authority = capsule.get("my_authority")
    if authority is None:
        lines.append("MY AUTHORITY: UNKNOWN (authority store unreadable, "
                      "see UNREADABLE above)")
    elif authority.get("held"):
        lines.append("MY AUTHORITY: held, scope=%s epoch=%s expires_at=%s"
                      % (authority.get("scope"), authority.get("epoch"),
                         authority.get("expires_at")))
    else:
        lines.append("MY AUTHORITY: NOT held (%s)" % authority.get("reason"))
    lines.append("")

    _render_section(lines, "READY NOW", capsule.get("ready_now"))
    _render_section(lines, "AWAITING REVIEW", capsule.get("awaiting_review"))
    _render_section(lines, "ACTIVE LANES", capsule.get("active_lanes"))
    _render_section(
        lines, "ACTIVE CLAIMS", capsule.get("active_claims"),
        formatter=lambda u: "%s (%s)" % (u.get("id"), u.get("detail") or ""))
    _render_section(
        lines, "FAILED UNITS", capsule.get("failed_units"),
        formatter=lambda f: "%s: %s" % (f.get("id"), f.get("latest_failure")))
    _render_section(lines, "RED QUEUE", capsule.get("red_queue"))
    _render_section(lines, "AMBER RULINGS", capsule.get("amber_rulings"))
    _render_section(lines, "ATTEMPT COUNTS", capsule.get("attempt_counts"))
    _render_section(lines, "REQUIRED GATES", capsule.get("required_gates"))
    lines.append("")

    wgs = capsule.get("work_graph_summary")
    if wgs is None:
        lines.append("WORK GRAPH SUMMARY: UNKNOWN (unreadable, see above)")
    else:
        lines.append("WORK GRAPH SUMMARY: %d total, by bucket %s"
                      % (wgs.get("total"), wgs.get("by_bucket")))

    rc = capsule.get("resource_capacity")
    lines.append("RESOURCE CAPACITY: %s"
                  % (rc if rc is not None else "UNKNOWN (unreadable, see above)"))

    ljs = capsule.get("last_journal_seq")
    lines.append("LAST JOURNAL SEQ: %s"
                  % (ljs if ljs is not None else "UNKNOWN (unreadable, see above)"))

    return "\n".join(lines) + "\n"


def main(argv):
    parser = argparse.ArgumentParser(
        description="Build a resume capsule for a night run directory.")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--orchestrator", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--out", default=None,
                         help="write the JSON capsule here instead of stdout")
    parser.add_argument("--render", action="store_true",
                         help="print the bounded human text instead of JSON")
    args = parser.parse_args(argv)
    capsule = build_capsule(args.run_root, args.run_id, args.orchestrator,
                             args.instance, now=args.now)
    if args.render:
        sys.stdout.write(render(capsule))
        return 0
    text = json.dumps(capsule, indent=1, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
