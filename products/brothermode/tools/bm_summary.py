#!/usr/bin/env python3
"""tools/bm_summary.py: F10, the run summary as a product object.

The founder is a non-engineer who will not read a transcript. Today his
only options are scrolling a two hour log or trusting a model's own
recollection of it, and both fail the same way: the important line gets
missed, or a wrong belief forms and nothing catches it. This file exists
to give him a third option: a short account of a run, built ONLY from the
durable record tools/bm_store.py already keeps of every controller run,
unit, dispatch and checkpoint (controller_runs / controller_units /
controller_dispatches / autonomy_checkpoints, read through
bs.ReadOnlyStore, never through a second, parallel notion of "what
happened").

BORROWED FROM Trae Agent (ByteDance): it records a detailed trajectory of
every step and renders "Lakeview", a concise summary of that trajectory,
so a human reviews the summary instead of the raw steps. THE ADAPTATION:
Brother already records MORE provenance than Trae's trajectory (a signed
chain of runs, units, dispatches, verifier verdicts and checkpoints,
each carrying its own actor and timestamp) and had no readable view of
it, so this is the rare case where the harder half already existed and
only the easy half, the summary, was missing. And unlike Lakeview, this
summary is generated FROM the stored record rather than beside it: there
is no separate trajectory object it could fall out of sync with.

THE PROPERTY THAT MAKES THIS WORTH ANYTHING: summarize() below is a PURE
function of the dict load_record() returns. It opens no file, makes no
clock call, and reads nothing outside its one argument. So the summary
CANNOT say anything the record does not contain: mutate the record and
the summary changes (tools/test_bm_summary.py proves this both ways,
by mutating a built record and checking the printed lines move, and by
handing it a thin or empty record and checking every unestablished field
reads NO-DATA rather than something invented to fill the line). If a
summary could drift from its record, it would be a model's recollection
wearing a report's clothes, and this estate already has one of those:
the whole point of this file is to not be a second one.

THE CEILING, stated once here rather than argued for at every call site:
a faithful summary of a record proves the summary MATCHES the record. It
does not, and cannot, prove the record is COMPLETE. A run that crashed
before recording anything, or a run nobody ever planned units for,
summarises to a page that says almost nothing, and says so honestly (see
the NO-DATA lines below). Read this output as "what the record
contains", never as "what happened": those two are the same only when
the record is complete, and this file has no way to check that from the
inside. NO-DATA is never folded into a pass and never folded into a
failure; it is its own line, because a check that never ran is a
different fact from a check that ran and lost.

Python 3.9, standard library only. No network. No em or en dash anywhere
in this file, its comments, or its output.
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the same technique tools/
    bm_controller.py and tools/test_bm_controller.py already use for
    tools/bm_store.py, so this file needs no package machinery to reuse
    the store's own read path."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

NO_DATA = "NO-DATA"

#: Run states tools/bm_store.py's own CONTROLLER_STATE_TRANSITIONS gives
#: no outgoing moves at all: a run in one of these is finished, and "step
#: the controller" is never the right next action for it.
_TERMINAL_RUN_STATES = ("COMPLETE", "STOPPED", "FAILED_TERMINAL")

#: Unit statuses that mean a result is currently in flight and needs the
#: controller (never the founder) to move it forward.
_IN_FLIGHT_UNIT_STATES = ("DISPATCHED", "RESULT_IN", "VERIFYING")


# ---------------------------------------------------------------------------
# The one function that touches the store. Everything below it is pure.
# ---------------------------------------------------------------------------

def load_record(root, project_id, raw=True):
    """Read every field bm_summary is allowed to show, straight off tools/
    bm_store.py's own ReadOnlyStore, and nothing else. This is the ONLY
    function in this file that opens sqlite or reads the filesystem: the
    dict it returns is the WHOLE input summarize() ever sees, so a test
    can build one of these by hand and never touch a real store, and a
    real run can never produce a summary this dict did not carry.

    `raw` matches tools/bm_controller.py's own cmd_status convention:
    True (the default, used for local text display) reads the store's
    own values unredacted; False asks the store to redact, the shape a
    caller handing this record onward (a JSON payload leaving this
    machine) should choose instead."""
    store = bs.ReadOnlyStore(root)
    try:
        run = store.get_run(project_id, raw=raw)
        units = []
        dispatches_by_unit = {}
        if run is not None:
            units = store.list_units(run["run_id"], raw=raw)
            for u in units:
                dispatches_by_unit[u["unit_id"]] = store.list_dispatches(
                    u["unit_id"], raw=raw)
        checkpoints = store.recent_checkpoints(project_id, limit=1, raw=raw)
        spend = store.spend_totals(project_id)
        open_steps = store.list_human_steps(project_id, resolved=False,
                                            raw=raw)
    finally:
        store.close()
    return {
        "project_id": project_id,
        "run": run,
        "units": units,
        "dispatches_by_unit": dispatches_by_unit,
        "last_checkpoint": checkpoints[0] if checkpoints else None,
        "spend": spend,
        "open_human_steps": open_steps,
    }


# ---------------------------------------------------------------------------
# Pure summarization: record in, lines out, nothing else read.
# ---------------------------------------------------------------------------

def _next_action(run, units, dispatches_by_unit, open_steps):
    """The smallest useful next action, decided entirely from the record's
    own fields, in priority order: a finished run has none; an open human
    step blocks everything else and always outranks the rest; a unit that
    exhausted its retries needs a person, not another dispatch; work still
    in flight needs the controller to continue it; a READY unit needs the
    controller to dispatch it; no units at all means the run needs a
    plan; anything else falls back to naming the state itself rather than
    guessing."""
    state = run.get("state")
    if state in _TERMINAL_RUN_STATES:
        return "none, this run is %s." % state
    if open_steps:
        return ("resolve %d open human step(s); the run cannot proceed "
                "without them." % len(open_steps))
    failed = sorted(u["unit_id"] for u in units
                    if u.get("status") == "FAILED")
    if failed:
        return ("%d unit(s) exhausted their retries (%s): they need a "
                "different approach, not another dispatch."
                % (len(failed), ", ".join(failed)))
    in_flight = sorted(u["unit_id"] for u in units
                       if u.get("status") in _IN_FLIGHT_UNIT_STATES)
    if in_flight:
        return ("%d unit(s) are in flight (%s): step the controller to "
                "carry them to a verdict." % (len(in_flight),
                                              ", ".join(in_flight)))
    ready = sorted(u["unit_id"] for u in units if u.get("status") == "READY")
    if ready:
        return ("%d unit(s) are ready to dispatch (%s): step the "
                "controller." % (len(ready), ", ".join(ready)))
    if not units:
        return "plan this run's units before anything can be dispatched."
    return "step the controller (run state %s)." % state


def summarize(record):
    """record (the dict load_record returns, or an equivalent plain dict
    built by hand) -> a list of summary lines. PURE: no I/O, no clock, no
    randomness, nothing read except `record` itself. The same record
    always yields the same lines, and two records that yield the same
    lines agreed on every field this function reads.

    Every field below is read with .get(...) and a NO_DATA fallback
    rather than assumed present, on purpose: a thin or empty record (a
    run that never got a plan, a project that never had a run at all)
    must summarise to explicit NO-DATA lines, never to an invented
    "no activity" story this function was not actually told."""
    lines = []
    project_id = record.get("project_id") or NO_DATA
    lines.append("project: %s" % project_id)

    run = record.get("run")
    if run is None:
        lines.append("run: %s (no controller run recorded for this "
                     "project)" % NO_DATA)
        lines.append("next action: start a run before there is anything "
                     "to summarise.")
        return lines

    lines.append("run: %s (state %s, workflow version %s)"
                 % (run.get("run_id") or NO_DATA, run.get("state") or NO_DATA,
                    run.get("workflow_version") if run.get("workflow_version")
                    is not None else NO_DATA))
    lines.append("outcome: %s" % (run.get("outcome") or NO_DATA))
    lines.append("done definition: %s"
                 % (run.get("done_definition") or NO_DATA))

    units = record.get("units") or []
    dispatches_by_unit = record.get("dispatches_by_unit") or {}

    if not units:
        lines.append("what changed: %s (no units planned yet)" % NO_DATA)
        lines.append("what actually ran: %s (no units planned yet)"
                     % NO_DATA)
        lines.append("what passed: %s (no units planned yet)" % NO_DATA)
        lines.append("what failed: none (no units planned yet)")
    else:
        changed = sorted({p for u in units
                          for p in (u.get("write_scope") or [])
                          if u.get("status") == "DONE"})
        lines.append("what changed: %s"
                     % (", ".join(changed) if changed
                        else "nothing yet, no unit has completed"))

        ran_ids = sorted(uid for uid, ds in dispatches_by_unit.items() if ds)
        never_run = sorted(u["unit_id"] for u in units
                           if not dispatches_by_unit.get(u["unit_id"]))
        lines.append("what actually ran: %d of %d unit(s) dispatched%s"
                     % (len(ran_ids), len(units),
                        (": " + ", ".join(ran_ids)) if ran_ids else ""))

        passed = sorted(u["unit_id"] for u in units
                        if u.get("status") == "DONE")
        lines.append("what passed: %s"
                     % (", ".join(passed) if passed else "none yet"))

        failed = sorted(u["unit_id"] for u in units
                        if u.get("status") == "FAILED")
        lines.append("what failed: %s"
                     % (", ".join(failed) if failed else "none"))

        # NO-DATA for units, named separately from failure: a unit that
        # never got a dispatch was never tried, which is not the same
        # fact as a unit that was tried and lost (that is "what failed"
        # above).
        lines.append("could not be established (never dispatched): %s"
                     % (", ".join(never_run) if never_run
                        else "none, every unit has at least one dispatch"))

    cp = record.get("last_checkpoint")
    if cp:
        lines.append("last checkpoint: %s (kind=%s, controller=%s)"
                     % (cp.get("created_at") or NO_DATA,
                        cp.get("kind") or NO_DATA,
                        cp.get("controller_id") or NO_DATA))
    else:
        lines.append("last checkpoint: %s (none recorded)" % NO_DATA)

    spend = record.get("spend") or {}
    verdict = spend.get("verdict")
    if verdict is None or verdict == "no-data":
        lines.append("spend: %s (no token or minute ceiling was ever set)"
                     % NO_DATA)
    else:
        lines.append("spend: %s (%s tokens, %s minutes)"
                     % (verdict, spend.get("tokens"), spend.get("minutes")))

    open_steps = record.get("open_human_steps") or []
    if open_steps:
        lines.append("waiting on the founder: %d open step(s)"
                     % len(open_steps))
    else:
        lines.append("waiting on the founder: none open")

    lines.append("next action: %s"
                 % _next_action(run, units, dispatches_by_unit, open_steps))
    return lines


def render(record):
    """summarize(record), joined into the one printed page."""
    return "\n".join(summarize(record))


# ---------------------------------------------------------------------------
# CLI, mirroring tools/bm_controller.py's own cmd_status: a read-only
# command needs no actor, no session, no write path.
# ---------------------------------------------------------------------------

def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg=""):
    sys.stderr.write("%s\n" % msg)


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True))


_USAGE = "usage: bm_summary.py --project ID [--json] [--raw]"


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        _out("bm_summary.py: F10, the run summary as a product object "
             "(reads tools/bm_store.py, writes nothing).")
        _out("")
        _out(_USAGE)
        return EXIT_OK
    kv = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--project":
            if i + 1 >= len(argv):
                _err(_USAGE)
                _err("bm_summary: --project needs a value")
                return EXIT_USAGE
            kv["project"] = argv[i + 1]
            i += 2
        elif tok == "--json":
            kv["json"] = True
            i += 1
        elif tok == "--raw":
            kv["raw"] = True
            i += 1
        else:
            _err(_USAGE)
            _err("bm_summary: unrecognized argument %r" % tok)
            return EXIT_USAGE
    project_id = kv.get("project")
    if not project_id:
        _err(_USAGE)
        _err("bm_summary: --project is required")
        return EXIT_USAGE
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    try:
        root, _source = bs.require_root()
        record = load_record(root, project_id, raw=want_raw)
    except bs.BMStoreError as e:
        _err("bm_summary: refused: %s" % e)
        return EXIT_REFUSED
    if kv.get("json"):
        _print_json({"record": record, "summary": summarize(record)})
    else:
        _out(render(record))
    return EXIT_OK


def cli():
    """Console-script entry point, matching tools/bm_controller.py's own
    cli(): a packaging entry point takes no arguments of its own."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
