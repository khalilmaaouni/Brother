#!/usr/bin/env python3
"""UNIT TRIGGER-07: registration is not activation.

THE GAP THIS CLOSES. A capability can ship a passing suite, be listed in the
battery, and still never be CALLED by anything in the real path: a side
effect ledger nobody invokes, a reclaim tool whose only registration was its
own test, a durable watcher pointed at a worktree from ten days ago, a limit
watcher nothing ever armed. A test proves the part works; it proves nothing
about whether an eligible event reaches it. This audit answers the second
question, never the first.

INPUT, a declarations file (JSON): a list of capabilities, each naming
  id                 unique name
  trigger             the eligible condition, in words (never checked, only
                      carried through to the report so a human can judge it)
  dispatcher          the path that should carry the event (must exist on
                      disk; a declaration naming one that does not exist can
                      never be called ACTIVE)
  deadline_seconds    how long the capability has to complete once triggered
                      (carried through, never itself enforced by wall clock
                      here: the audit reads history, it does not wait)
  observable          the path whose existence and non-empty content is the
                      proof the capability ran

and an evidence file (JSONL, optional): one recorded dispatch event per
line, {"capability": id, "dispatcher": path, "kind": "real"|"canary",
"success": bool}. A canary exercises the same dispatch path as a stand-in
for a real event and is ALWAYS labelled as one, never counted as a real
observation.

VERDICTS, per capability:
  ACTIVE           a real event reached the declared dispatcher and the
                   observable result is present and non-empty
  CANARY-ONLY      only a canary reached the declared dispatcher and
                   produced the observable result
  REGISTERED-ONLY  the declaration is complete and the dispatcher exists,
                   but no real or canary event ever reached it (the shape
                   this run keeps rediscovering: a passing suite, no caller)
  NO-DATA          the declaration is incomplete, the dispatcher does not
                   exist, an event reached the dispatcher but the observable
                   never appeared or is empty, or nothing could be read

FAIL DIRECTION. Every read (declarations, events, the observable file) fails
closed: unreadable, missing a required field, or ambiguous is NO-DATA, never
ACTIVE. NO-DATA is reported per capability, never silently promoted to a
pass.

Exit 0 every capability is ACTIVE. Exit 1 at least one is CANARY-ONLY,
REGISTERED-ONLY, or NO-DATA (this is the interesting case and must be seen,
never buried in a 0). Exit 2 the declarations or events could not be read at
all, or there was nothing to check.

Usage:
  python3 scripts/activation_audit.py --declarations FILE [--events FILE]
      [--tree .] [--id ID ...]
"""
import argparse
import json
import os
import sys

VERDICTS = ("ACTIVE", "CANARY-ONLY", "REGISTERED-ONLY", "NO-DATA")
REQUIRED_FIELDS = ("id", "trigger", "dispatcher", "deadline_seconds", "observable")


class Unreadable(Exception):
    """A required source could not be read or parsed at all."""


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise Unreadable("%s could not be read: %s" % (path, exc))
    except ValueError as exc:
        raise Unreadable("%s is not valid JSON: %s" % (path, exc))


def _read_events(path):
    """[] when no evidence file was given or none exists yet: a capability
    with nothing recorded is a legitimate state (REGISTERED-ONLY), not an
    error. A file that exists but cannot be parsed IS an error: fail closed
    rather than silently treating corrupt evidence as "no events"."""
    if not path:
        return []
    if not os.path.exists(path):
        return []
    events = []
    try:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError as exc:
                    raise Unreadable("%s line %d is not valid JSON: %s" % (path, lineno, exc))
    except OSError as exc:
        raise Unreadable("%s could not be read: %s" % (path, exc))
    return events


def _resolve(tree, rel):
    """A declared path, resolved. Found by running the audit for real on
    2026-09-18: a dispatcher written as ~/.claude/hooks/x.py was reported as
    "does not exist" because nothing expanded the home marker, so five live
    hooks read as NO-DATA. A control that cannot find a dispatcher it was
    correctly given reports a gap that is not there, which is as harmful as
    missing one that is."""
    rel = os.path.expanduser(rel)
    if os.path.isabs(rel):
        return rel
    return os.path.join(tree, rel)


def _duplicate_ids(caps):
    seen = {}
    for cap in caps:
        seen.setdefault(cap.get("id"), []).append(cap)
    return {cid: entries for cid, entries in seen.items() if cid and len(entries) > 1}


def audit_capability(cap, tree, events):
    """(verdict, [reasons]) for one declared capability.

    `events` is the full event list; this function filters to the ones
    naming this capability's id so a caller can pass the same list to every
    capability without re-reading the evidence file per row.
    """
    for field in REQUIRED_FIELDS:
        value = cap.get(field)
        if value in (None, ""):
            return "NO-DATA", ["declaration missing required field: %s" % field]

    dispatcher = cap["dispatcher"]
    observable = cap["observable"]
    dispatcher_path = _resolve(tree, dispatcher)
    if not os.path.exists(dispatcher_path):
        return "NO-DATA", ["declared dispatcher does not exist: %s" % dispatcher]

    mine = [e for e in events if isinstance(e, dict) and e.get("capability") == cap["id"]]
    matching = [e for e in mine if e.get("dispatcher") == dispatcher]
    stray = sorted({e.get("dispatcher") for e in mine
                    if e.get("dispatcher") and e.get("dispatcher") != dispatcher})
    real_ok = [e for e in matching if e.get("kind") == "real" and e.get("success", True)]
    canary_ok = [e for e in matching if e.get("kind") == "canary" and e.get("success", True)]
    canary_bad = [e for e in matching if e.get("kind") == "canary" and not e.get("success", True)]

    observable_path = _resolve(tree, observable)
    obs_exists = os.path.exists(observable_path)
    obs_nonempty = obs_exists and os.path.getsize(observable_path) > 0

    def _with_observable(label, kind):
        if obs_nonempty:
            return label, ["a %s event reached %s, observable result present at %s"
                           % (kind, dispatcher, observable)]
        if obs_exists:
            return "NO-DATA", ["observable exists but is empty: %s" % observable]
        return "NO-DATA", ["an event reached the dispatcher but the observable "
                           "result never appeared: %s" % observable]

    if real_ok:
        return _with_observable("ACTIVE", "real")
    if canary_ok:
        return _with_observable("CANARY-ONLY", "canary")

    reasons = ["no real or canary event reached the declared dispatcher: %s" % dispatcher]
    if stray:
        reasons.append("event(s) recorded for this capability reached a different "
                        "dispatcher instead: %s" % ", ".join(stray))
    if canary_bad:
        reasons.append("a canary was run against this dispatcher and failed")
    return "REGISTERED-ONLY", reasons


def run_audit(declarations, tree, events, wanted=None):
    """([(id, verdict, reasons)], counted) over every capability checked.

    Duplicate ids are refused for every entry sharing the id: a lease on
    which declaration governs does not exist, so neither may be trusted.
    """
    dupes = _duplicate_ids(declarations)
    results = []
    for cap in declarations:
        cid = cap.get("id")
        if wanted is not None and cid not in wanted:
            continue
        if cid in dupes:
            results.append((cid, "NO-DATA",
                            ["registered more than once (%d declarations); "
                             "no single declaration governs" % len(dupes[cid])]))
            continue
        verdict, reasons = audit_capability(cap, tree, events)
        results.append((cid, verdict, reasons))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--declarations", required=True)
    ap.add_argument("--events", default=None)
    ap.add_argument("--tree", default=".")
    ap.add_argument("--id", action="append", default=None, dest="ids",
                    help="only audit these capability ids (repeatable)")
    a = ap.parse_args(argv)

    try:
        data = _read_json(a.declarations)
        caps = data["capabilities"] if isinstance(data, dict) else data
        if not isinstance(caps, list):
            raise Unreadable("%s does not contain a list of capabilities" % a.declarations)
        events = _read_events(a.events)
    except (Unreadable, KeyError, TypeError) as exc:
        print("activation-audit: NO-DATA, %s. Refusing to call anything ACTIVE." % exc)
        return 2

    wanted = set(a.ids) if a.ids else None
    results = run_audit(caps, a.tree, events, wanted=wanted)
    if not results:
        print("activation-audit: NO-DATA, no capability was checked")
        return 2

    by_verdict = {v: [] for v in VERDICTS}
    for cid, verdict, reasons in results:
        by_verdict[verdict].append((cid, reasons))

    # REGISTERED-ONLY is the interesting one and is printed first, never
    # buried under the passing rows.
    for label in ("REGISTERED-ONLY", "CANARY-ONLY", "NO-DATA", "ACTIVE"):
        for cid, reasons in by_verdict[label]:
            print("%-16s %s" % (label, cid))
            for reason in reasons:
                print("                 %s" % reason)

    print("activation-audit: %d capability(ies) checked: %d active, %d canary-only, "
          "%d registered-only, %d no-data" % (
              len(results), len(by_verdict["ACTIVE"]), len(by_verdict["CANARY-ONLY"]),
              len(by_verdict["REGISTERED-ONLY"]), len(by_verdict["NO-DATA"])))
    if by_verdict["CANARY-ONLY"] or by_verdict["REGISTERED-ONLY"] or by_verdict["NO-DATA"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
