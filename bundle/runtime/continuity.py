#!/usr/bin/env python3
"""continuity: the resume screen. E73.1 of the productization directive.

WHAT THIS IS. journal.py (E59) records what happened; journal_projection.py
(row E60, this same night) folds those events per unit for a delivery
report. Neither answers the one question a session picking a killed run
back up actually asks first: "what is this run, where did it get to, and
what is safe to do next." The continuity capsule is that answer, built
entirely from the journal (scripts/journal.py) and the stores it already
sits beside (the Work document, claims.json through scripts/claim_store.py):
objective, canonical revision, active/pending/abandoned units with their
attempt counts, verification state per unit, the environment assumptions a
resume needs (runs root, target cwd, slot count, model adapter), and one
safe next action.

WHAT IT DELIBERATELY HOLDS NONE OF: anything a `git log` or `git diff`
against the target repository could answer on its own, any file's contents,
any diff, any captured test output. Those already have homes (the attempt
trace, the run log, git itself); copying them into a JSON blob would make
this the second copy journal.py's own docstring warns against.

NO JOURNAL, NO CAPSULE. A run directory with no journal.jsonl predates row
E59, or the journal could not be written; capsule() refuses rather than
guessing at a run it cannot see the events for, and names the run_dir in
its refusal. Every run since E59 has one.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_run  # noqa: E402
import claim_store  # noqa: E402
import journal  # noqa: E402
import journal_projection  # noqa: E402

NODATA = "NO-DATA"

#: Written beside journal.jsonl in the same run directory, at each lifecycle
#: checkpoint (E73.2). Named here, once, rather than typed again at brother_
#: run.py's own ENGINE_JSON_FILES, which excludes it by this same string so
#: _work_doc_path (above) never mistakes it for the run's Work document.
CAPSULE_FILENAME = "capsule.json"


def _target_cwd_from_file(run_dir):
    """target.json's own "cwd" field (brother_run._write_run_target's
    output), or None when the file is absent or unreadable. Mirrors
    brother_run._read_run_target rather than reaching into that private
    function, since the two fields it reads (TARGET_FILENAME, "cwd") are
    both public."""
    path = os.path.join(run_dir, brother_run.TARGET_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("continuity: could not read %s (%s)\n" % (path, exc))
        return None
    return data.get("cwd")


def _target_cwd_from_journal(events):
    """The fallback for a run whose target.json write failed or predates
    it: run.opened's own "cwd" payload field (brother_run.main, the event
    written once the run directory is settled)."""
    for event in events:
        if event.get("type") == "run.opened":
            return (event.get("payload") or {}).get("cwd")
    return None


def _work_doc_path(run_dir):
    """The one *.json file in run_dir that is not one of the engine's own
    bookkeeping files, mirroring brother_run._find_work_doc but built off
    the public ENGINE_JSON_FILES set rather than the private function
    itself, the same reuse boundary journal_projection.py already keeps."""
    if not os.path.isdir(run_dir):
        return None
    try:
        names = os.listdir(run_dir)
    except OSError as exc:
        sys.stderr.write("continuity: could not list %s (%s)\n" % (run_dir, exc))
        return None
    files = [f for f in names if f.endswith(".json")
             and f not in brother_run.ENGINE_JSON_FILES]
    return os.path.join(run_dir, files[0]) if len(files) == 1 else None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), ""
    except (OSError, ValueError) as exc:
        return None, str(exc)


def _runs_root_from(run_dir):
    """The runs_root run_dir_for(outcome, runs_root) was called with, read
    back off the path shape it always builds: runs_root/docs/plan/runs/name.
    None when run_dir does not sit under that shape (a foreign layout, or a
    test fixture that skipped it), never guessed."""
    run_dir = os.path.abspath(run_dir)
    # run_dir = runs_root/docs/plan/runs/NAME: four components (docs, plan,
    # runs, NAME) sit between runs_root and this directory, so it takes four
    # dirname() calls, not three, to walk back up to runs_root.
    up = run_dir
    for _ in range(4):
        up = os.path.dirname(up)
    expected = os.path.join(up, "docs", "plan", "runs",
                            os.path.basename(run_dir))
    return up if os.path.normpath(expected) == run_dir else None


def _canonical_revision(cwd):
    """git rev-parse HEAD of the run's own target repository, read live
    (this is run-specific state -- WHICH repository -- not something the
    journal or claims duplicate; the revision itself is never persisted
    anywhere a finished run can be re-read for, per journal_projection.py's
    own docstring on before/after/changed). None when there is no target
    cwd to ask, or git cannot answer."""
    if not cwd:
        return None
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write("continuity: could not read git HEAD of %s (%s)\n"
                         % (cwd, exc))
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _dispatch_env(events):
    """(slots, own_tools) off the LAST dispatch.round event (brother_run.
    run_loop's own payload), the most recent round's settings being the
    ones that describe the environment right now. (None, None) when no
    round has ever dispatched."""
    slots = own_tools = None
    for event in events:
        if event.get("type") == "dispatch.round":
            payload = event.get("payload") or {}
            slots = payload.get("slots")
            own_tools = payload.get("own_tools")
    return slots, own_tools


def _bucket_for(uid, row, held, reconcile_findings, reconcile_problem,
                claims_path_exists, reconcile_by_unit):
    """(bucket, detail): where one unit sits for a resume, and why.

    "integrated" trusts only the row's own stamped status (brother_run.
    _mark_integrated re-verifies before setting it, so it is the one signal
    this estate already treats as authoritative -- a claim released "done"
    is not, on its own, per that same re-verification's whole reason to
    exist). A unit still claimed needs claims.json's lease (expires_at, pid)
    to say whether it is live or abandoned, since the journal never carries
    a lease; when that store cannot answer, this refuses to guess and calls
    the unit "unclear" instead, per row E73's own "refusal when state
    cannot be trusted"."""
    claim_state = held.get("state") if held else None
    if row.get("status") == "DONE":
        return "integrated", ""
    if claim_state != "claimed":
        return "pending", ""
    if reconcile_findings is None:
        return "unclear", ("%s: claims.json could not be read (%s), so "
                           "whether %s's lease is still live cannot be told"
                           % (NODATA, reconcile_problem, uid))
    finding = reconcile_by_unit.get(uid)
    if finding is None:
        reason = ("claims.json is missing" if not claims_path_exists
                  else "claims.json holds no live record of it")
        return "unclear", ("%s: the journal says %s is still claimed, but "
                           "%s, so its lease state cannot be trusted"
                           % (NODATA, uid, reason))
    if finding["status"] == "in-flight":
        return "active", finding.get("detail", "")
    return "abandoned", finding.get("detail", "")


def _next_action(units, buckets):
    """One recommended line, priority order: an abandoned lease can be
    safely resumed; an unclear one must not be, and a person is named
    instead; an active one says to wait; a pending one names what to claim
    next; nothing left says so."""
    by_id = {u["id"]: u for u in units}
    if buckets["abandoned"]:
        uid = buckets["abandoned"][0]
        return ("resume: %s's claim expired while still marked claimed (%s); "
                "a resume can safely re-run it"
                % (uid, by_id[uid]["detail"] or "the lease expired"))
    if buckets["unclear"]:
        uid = buckets["unclear"][0]
        return ("do not resume automatically: %s's state cannot be trusted "
                "(%s); a person must look before anything claims it again"
                % (uid, by_id[uid]["detail"] or NODATA))
    if buckets["active"]:
        uid = buckets["active"][0]
        return ("wait: %s is still in flight (%s); let it finish before "
                "resuming" % (uid, by_id[uid]["detail"] or "still leased"))
    if buckets["pending"]:
        uid = buckets["pending"][0]
        return "resume: claim and run %s, the next pending unit" % uid
    return "done: every unit in this run is integrated; nothing is left to resume"


def capsule(run_dir, clock=None):
    """(capsule, problem). problem is non-empty, and capsule is None, only
    when run_dir holds no journal at all -- everything else this reads
    (Work document, claims.json, git) degrades to a NO-DATA field rather
    than failing the whole capsule, because the journal is the one input
    every other field is built alongside, not derived from."""
    run_dir = os.path.abspath(str(run_dir or ""))
    journal_path = os.path.join(run_dir, journal.JOURNAL_FILENAME)
    if not os.path.isfile(journal_path):
        return None, ("%s: no %s in %s (this run predates row E59, or its "
                      "journal could not be written, so there is nothing "
                      "this capsule can be built from)"
                      % (NODATA, journal.JOURNAL_FILENAME, run_dir))
    events = journal.read(run_dir) or []

    doc_path = _work_doc_path(run_dir)
    record = None
    if doc_path:
        record, doc_problem = _read_json(doc_path)
        if record is None:
            sys.stderr.write("continuity: could not read %s (%s)\n"
                             % (doc_path, doc_problem))
    record = record or {}
    rows = record.get("rows") or record.get("units") or []
    row_by_id = {str(r.get("id")): r for r in rows}

    target_cwd = (_target_cwd_from_file(run_dir)
                 or _target_cwd_from_journal(events))
    runs_root = _runs_root_from(run_dir)
    slots, own_tools = _dispatch_env(events)
    if own_tools is None:
        model_adapter = "%s: no dispatch.round event recorded yet" % NODATA
    elif own_tools:
        model_adapter = "the product's own worker adapter"
    else:
        model_adapter = "the engine's default worker adapter (scripts/model_worker.py)"
    canonical_revision = _canonical_revision(target_cwd)

    journal_claims = journal_projection.claims_from_journal(events)
    claims_path = os.path.join(run_dir, brother_run.CLAIMS_FILENAME)
    claims_path_exists = os.path.isfile(claims_path)
    reconcile_findings, reconcile_problem = claim_store.reconcile(
        claims_path, clock=clock)
    reconcile_by_unit = {f["unit_id"]: f for f in (reconcile_findings or [])}

    unit_ids = sorted(set(row_by_id) | set(journal_claims))
    units = []
    buckets = {"integrated": [], "active": [], "pending": [],
              "abandoned": [], "unclear": []}
    for uid in unit_ids:
        row = row_by_id.get(uid, {})
        held = journal_claims.get(uid)
        bucket, detail = _bucket_for(uid, row, held, reconcile_findings,
                                     reconcile_problem, claims_path_exists,
                                     reconcile_by_unit)
        attempt = held.get("attempt") if held else None
        if attempt is None and bucket == "abandoned":
            attempt = (reconcile_by_unit.get(uid) or {}).get("attempt")
        buckets[bucket].append(uid)
        units.append({
            "id": uid,
            "title": row.get("title") or row.get("objective") or uid,
            "bucket": bucket,
            "claim_state": (held.get("state") if held else None) or "not-started",
            "attempt": attempt,
            "detail": detail,
        })

    return {
        "run_dir": run_dir,
        "objective": record.get("outcome") or (
            "%s: no Work document could be read for this run" % NODATA),
        "canonical_revision": canonical_revision or (
            "%s: no readable git HEAD for the target repository" % NODATA),
        "environment": {
            "runs_root": runs_root or (
                "%s: run_dir does not sit under docs/plan/runs of a runs root"
                % NODATA),
            "target_cwd": target_cwd or (
                "%s: no target.json and no run.opened event recorded a cwd"
                % NODATA),
            "slots": slots if slots is not None else (
                "%s: no dispatch.round event recorded yet" % NODATA),
            "model_adapter": model_adapter,
        },
        "units": units,
        "buckets": buckets,
        "next_action": _next_action(units, buckets),
    }, ""


def write_capsule(run_dir, clock=None):
    """(ok, problem): builds this run's capsule and writes it to
    <run_dir>/capsule.json, atomically (tempfile.mkstemp beside the target,
    fsync, os.replace), mirroring claim_store._write's own pattern rather
    than inventing a second one -- a capsule half-written when the power
    goes is exactly the case that pattern exists to survive, and capsule.json
    lives beside claims.json in the same directory for the same reason.

    AVAILABILITY OVER BOOKKEEPING, journal.append's own stance (its module
    docstring): a capsule write must NEVER stop the run it is checkpointing.
    Three failure shapes -- capsule() itself refusing (no journal yet),
    capsule() raising out of one of the stores it reads (a malformed
    claims.json a test fixture wrote by hand, or any other bug this call
    cannot control in a dependency it does not own), and the write to disk
    failing (a full disk, an unwritable directory) -- all return (False, a
    one-line reason) rather than raising; ok is True only once the file is
    durably on disk. E73.2's own callers in brother_run.py journal a
    `capsule.write_failed` event on a False return, so the failure has a
    permanent record beside the checkpoint it belongs to, not just a stderr
    line that scrolls away."""
    try:
        cap, problem = capsule(run_dir, clock=clock)
    except Exception as exc:  # pylint: disable=broad-except
        # DELIBERATELY BROAD: this call sits beside every lifecycle
        # checkpoint in the run loop now (E73.2), so a bug anywhere under
        # capsule() -- including one in a store this module does not own,
        # like claim_store.reconcile() choking on a malformed record --
        # must degrade to a lost checkpoint, never a lost run.
        reason = "capsule() raised %s: %s" % (type(exc).__name__, exc)
        sys.stderr.write("continuity: %s; the run continues without it\n"
                         % reason)
        return False, reason
    if cap is None:
        return False, problem
    run_dir = os.path.abspath(str(run_dir))
    path = os.path.join(run_dir, CAPSULE_FILENAME)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=".capsule-",
                                   suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cap, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        reason = "could not write %s (%s)" % (path, exc)
        sys.stderr.write("continuity: %s; the run continues without it\n"
                         % reason)
        return False, reason
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
    return True, ""


def _print_screen(cap):
    print("objective: %s" % cap["objective"])
    print("canonical revision: %s" % cap["canonical_revision"])
    env = cap["environment"]
    print("environment:")
    print("  runs root:     %s" % env["runs_root"])
    print("  target cwd:    %s" % env["target_cwd"])
    print("  slots:         %s" % env["slots"])
    print("  model adapter: %s" % env["model_adapter"])
    print("units (%d):" % len(cap["units"]))
    for u in cap["units"]:
        attempt = u["attempt"] if u["attempt"] is not None else NODATA
        tail = (" -- %s" % u["detail"]) if u["detail"] else ""
        print("  %-24s %-11s attempt=%s%s" % (u["id"], u["bucket"], attempt, tail))
    print("")
    print("next action: %s" % cap["next_action"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir", help="a run directory under docs/plan/runs")
    ap.add_argument("--json", action="store_true",
                    help="print the capsule record itself, not the screen")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    cap, problem = capsule(args.run_dir)
    if cap is None:
        print(problem)
        return 2
    if args.json:
        print(json.dumps(cap, indent=1, sort_keys=True))
    else:
        _print_screen(cap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
