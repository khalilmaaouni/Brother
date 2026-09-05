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

WHAT IT DELIBERATELY HOLDS NONE OF: any file's contents, any unified diff,
any captured test output. Those already have homes (the attempt trace, the
run log, git itself); copying them into a JSON blob would make this the
second copy journal.py's own docstring warns against. `git status
--porcelain` and `git worktree list --porcelain` are read for two zone-3
items below (file paths and worktree paths only, never a diff or a file's
own text), which is a narrower thing than "a `git log` or `git diff`".

ZONE 3, THE FIFTEEN ITEMS (row S13, docs/plan/SWITCHING-STRATEGY-2026-09-04.md
"Zone 3 : Engineering Continuity"): the capsule above answers four of them
under its own names (objective, canonical_revision, environment, next_action).
cap["zone3"] answers all fifteen under the document's own names, each a real
value or an explicit NO-DATA naming why this run has none -- never an empty
string, never a missing key. ZONE3_ITEMS below names the fifteen, in the
document's own order, once, so a reader and a test both check against the
same list rather than two copies of it.

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

#: Zone 3's own fifteen names, verbatim from
#: docs/plan/SWITCHING-STRATEGY-2026-09-04.md, in the document's own order.
#: capsule()'s "zone3" field carries exactly these keys, always, each one a
#: real value or a NO-DATA sentence naming why this run has none -- named
#: once here so test_capsule_items.py checks against this same list rather
#: than a second, driftable copy of it.
ZONE3_ITEMS = (
    "WHERE WE WERE",
    "WHY WE WERE THERE",
    "CANONICAL COMMIT",
    "CURRENT OBJECTIVE",
    "ACTIVE / FINISHED WORKTREES",
    "CHANGED FILES",
    "IMPORTANT COMMANDS",
    "EXPECTED SERVICES",
    "VERIFICATION STATE",
    "KNOWN FAILURES",
    "REJECTED APPROACHES",
    "ENVIRONMENT ASSUMPTIONS",
    "UNFINISHED WORK",
    "RELEVANT LESSONS",
    "SAFE NEXT ACTION",
)


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


def _last_unit_event(events):
    """The most recent journal event that named a unit (events is
    append-order, so the last match is the most recent), or None when
    every event so far is run-level -- honest for a run that has not
    touched a unit yet."""
    last = None
    for event in events:
        if event.get("unit_id") is not None:
            last = event
    return last


def _worktrees(cwd):
    """`git worktree list --porcelain` of the run's own target repository,
    parsed down to the path each "worktree <path>" line names. A finished
    per-unit scratch worktree is removed by brother_run.py's own cleanup
    once a unit ends (`git worktree remove --force`, line 2463), so
    whatever this lists IS the active set; there is nothing left on disk to
    call "finished" separately. None when there is no target cwd to ask or
    git cannot answer."""
    if not cwd:
        return None
    try:
        proc = subprocess.run(["git", "worktree", "list", "--porcelain"],
                              cwd=cwd, capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write("continuity: could not list worktrees of %s (%s)\n"
                         % (cwd, exc))
        return None
    if proc.returncode != 0:
        return None
    return [line[len("worktree "):] for line in proc.stdout.splitlines()
            if line.startswith("worktree ")]


def _changed_files(cwd):
    """`git status --porcelain` of the run's own target repository: file
    paths and their status codes ONLY, never a diff or a file's contents --
    the same boundary this module's docstring holds everywhere else. None
    when there is no target cwd to ask or git cannot answer."""
    if not cwd:
        return None
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write("continuity: could not read git status of %s (%s)\n"
                         % (cwd, exc))
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _important_commands(rows):
    """Every distinct done_check command a unit in the Work document
    carries, first-seen order -- the same field brother_run.py's own
    verifier re-executes (_verify_evidence), so this names exactly the
    commands a resuming session would need to re-run, never a guess at
    what "important" means."""
    seen = []
    for row in rows:
        cmd = str(row.get("done_check") or "").strip()
        if cmd and cmd not in seen:
            seen.append(cmd)
    return seen


def _known_failures(rows, events):
    """One line per unit this run has already refused: the row's own
    integration_refused reason where the Work document still carries it
    (a row pulled out during a refusal keeps the reason on itself, per
    brother_run._refuse_broken_precheck_units and _refuse_exhausted_units),
    falling back to a unit.refused journal event's own "why" payload field
    for a unit whose row has since rolled back off the document."""
    out = {}
    for row in rows:
        reason = row.get("integration_refused")
        if reason:
            out[str(row.get("id"))] = str(reason)
    for event in events:
        if event.get("type") == "unit.refused" and event.get("unit_id") is not None:
            uid = str(event["unit_id"])
            if uid not in out:
                why = (event.get("payload") or {}).get("why")
                if why:
                    out[uid] = str(why)
    return ["%s: %s" % (uid, reason) for uid, reason in sorted(out.items())]


def _recalled_lessons(events):
    """Every {"slug","path","state","line"} record any vault.recall
    journal event has carried so far (brother_run.VAULT_RECALL_EVENT_TYPE,
    E74's own recall-before-write hook), flattened across units and events
    in the order recorded. [] before that hook has fired for the first
    time in a run, which is the honest starting state, never a guess."""
    out = []
    for event in events:
        if event.get("type") == brother_run.VAULT_RECALL_EVENT_TYPE:
            out.extend((event.get("payload") or {}).get("records") or [])
    return out


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

    objective = record.get("outcome") or (
        "%s: no Work document could be read for this run" % NODATA)
    canonical_revision_field = canonical_revision or (
        "%s: no readable git HEAD for the target repository" % NODATA)
    environment = {
        "runs_root": runs_root or (
            "%s: run_dir does not sit under docs/plan/runs of a runs root"
            % NODATA),
        "target_cwd": target_cwd or (
            "%s: no target.json and no run.opened event recorded a cwd"
            % NODATA),
        "slots": slots if slots is not None else (
            "%s: no dispatch.round event recorded yet" % NODATA),
        "model_adapter": model_adapter,
    }
    next_action = _next_action(units, buckets)

    # ZONE 3 (row S13): the eleven items the four fields above do not
    # already answer under their own names, plus those four again under
    # the document's own names, so cap["zone3"] alone carries all fifteen.
    last_event = _last_unit_event(events)
    if last_event is None:
        where_we_were = ("%s: no unit-scoped journal event recorded yet; "
                         "only run-level events exist" % NODATA)
        why_we_were_there = ("%s: no unit has been touched yet, so there "
                             "is no reason to record" % NODATA)
    else:
        last_uid = last_event.get("unit_id")
        where_we_were = ("last recorded activity: %s on %s at %s"
                         % (last_event.get("type"), last_uid,
                            last_event.get("at") or NODATA))
        why = (last_event.get("payload") or {}).get("why")
        if not why:
            last_row = row_by_id.get(last_uid) or {}
            why = last_row.get("objective") or last_row.get("title")
        why_we_were_there = why or (
            "%s: no reason recorded for %s" % (NODATA, last_uid))

    worktrees = _worktrees(target_cwd)
    if worktrees is None:
        active_worktrees = ("%s: no target checkout to ask, or git could "
                            "not list its worktrees" % NODATA)
    else:
        # realpath, not normpath: git worktree list resolves any symlink
        # in the path (/tmp -> /private/tmp on macOS) but target_cwd, read
        # straight off target.json/run.opened, does not -- a bare
        # normpath compare would then count the target checkout itself as
        # an "extra" worktree and mislabel the single-worktree case.
        extra = [w for w in worktrees if os.path.realpath(w) !=
                os.path.realpath(target_cwd or "")]
        active_worktrees = (extra if extra else
                            "no worktree beyond the target checkout itself; "
                            "any per-unit scratch worktree already "
                            "finished and was removed")

    changed = _changed_files(target_cwd)
    if changed is None:
        changed_files = ("%s: no target checkout to ask, or git could not "
                         "read its status" % NODATA)
    elif not changed:
        changed_files = "clean: no uncommitted changes in the target checkout"
    else:
        changed_files = changed

    important_commands = _important_commands(rows) or (
        "%s: no unit in this run's Work document carries a done_check"
        % NODATA)

    known_failures = _known_failures(rows, events) or (
        "no refused or failing unit recorded in this run")

    rejected = [u for u in units if u["bucket"] == "abandoned"]
    rejected_approaches = ([
        "%s: %s" % (u["id"], u["detail"] or "its claim expired before "
                    "finishing") for u in rejected] if rejected else
        "no unit's claim has been abandoned mid-attempt in this run")

    unfinished = [u["id"] for u in units if u["bucket"] != "integrated"]
    unfinished_work = (unfinished if unfinished else
                       "nothing left unfinished; every unit in this run "
                       "is integrated")

    recalled = _recalled_lessons(events)
    relevant_lessons = ([
        "%s (%s)" % (r.get("slug"), r.get("state")) for r in recalled]
        if recalled else
        "%s: no vault.recall event recorded for this run yet" % NODATA)

    verification_state = ("%d integrated, %d active, %d pending, "
                          "%d abandoned, %d unclear"
                          % (len(buckets["integrated"]), len(buckets["active"]),
                             len(buckets["pending"]), len(buckets["abandoned"]),
                             len(buckets["unclear"])))

    zone3 = {
        "WHERE WE WERE": where_we_were,
        "WHY WE WERE THERE": why_we_were_there,
        "CANONICAL COMMIT": canonical_revision_field,
        "CURRENT OBJECTIVE": objective,
        "ACTIVE / FINISHED WORKTREES": active_worktrees,
        "CHANGED FILES": changed_files,
        "IMPORTANT COMMANDS": important_commands,
        "EXPECTED SERVICES": ("%s: this engine runs no background service "
                              "(stdlib-only, no network); nothing is "
                              "expected to be up" % NODATA),
        "VERIFICATION STATE": verification_state,
        "KNOWN FAILURES": known_failures,
        "REJECTED APPROACHES": rejected_approaches,
        "ENVIRONMENT ASSUMPTIONS": environment,
        "UNFINISHED WORK": unfinished_work,
        "RELEVANT LESSONS": relevant_lessons,
        "SAFE NEXT ACTION": next_action,
    }
    assert set(zone3) == set(ZONE3_ITEMS), sorted(set(zone3) ^ set(ZONE3_ITEMS))

    return {
        "run_dir": run_dir,
        "objective": objective,
        "canonical_revision": canonical_revision_field,
        "environment": environment,
        "units": units,
        "buckets": buckets,
        "next_action": next_action,
        "zone3": zone3,
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
    print("")
    print("zone 3 (%d items):" % len(ZONE3_ITEMS))
    for name in ZONE3_ITEMS:
        value = cap["zone3"][name]
        if isinstance(value, list):
            print("  %s:" % name)
            for line in value:
                print("    - %s" % line)
        elif isinstance(value, dict):
            print("  %s:" % name)
            for k, v in value.items():
                print("    %s: %s" % (k, v))
        else:
            print("  %s: %s" % (name, value))


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
