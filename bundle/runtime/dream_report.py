#!/usr/bin/env python3
"""dream_report: summarize the dream decision journal of one run (unit D5).

Reads the decisions recorded by dream_record.read_decisions and prints a
summary: decisions per kind, graded/ungraded/conflict tallies, PASS rate per
(kind, chosen option) with n, decisions whose artifact could not be read, and
the total measured cost with the number of unmeasured cost fields.

A missing journal is NO-DATA (exit 2), never an empty summary. An unreadable
artifact is reported as unreadable, never silently treated as a low score.
A cost of None is unmeasured and is never folded into the total as 0.
A cost that is present but not a finite number is corrupt: it is listed under
corrupt_costs, never measured and never folded into the total as 0.
"""
import json
import math
import sys

try:
    from . import dream_record
except (ImportError, ValueError):
    import dream_record


def summarize(run_dir):
    """The summary dict for a run, or None when the run has no journal."""
    rows = dream_record.read_decisions(run_dir)
    if rows is None:
        return None

    per_kind = {}
    states = {"graded": 0, "ungraded": 0, "conflict": 0}
    buckets = {}
    unreadable = []
    corrupt_costs = []
    cost_total = 0.0
    cost_measured = 0
    cost_unmeasured = 0

    def add_cost(value, owner=None):
        nonlocal cost_total, cost_measured, cost_unmeasured
        if value is None:
            cost_unmeasured += 1
        elif (isinstance(value, bool)
              or not isinstance(value, (int, float))
              or (isinstance(value, float) and not math.isfinite(value))):
            # Present but not a finite number: corrupt. It is never measured
            # and never folded into the total as 0.
            corrupt_costs.append(owner)
        else:
            cost_total += value
            cost_measured += 1

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("dream_report: decision row is not a record: %r"
                             % (row,))
        kind = row.get("kind")
        per_kind[kind] = per_kind.get(kind, 0) + 1

        state = row.get("state")
        if not isinstance(state, str) or state not in states:
            raise ValueError("dream_report: unknown decision state %r for event %r"
                             % (state, row.get("event_id")))
        states[state] += 1

        record = row.get("record")
        if not isinstance(record, dict):
            # Missing, corrupt, or non-record artifact: its cost cannot be
            # measured. Never read as the safe case.
            unreadable.append(row.get("event_id"))
            add_cost(None, row.get("event_id"))
        else:
            add_cost(record.get("cost"), row.get("event_id"))

        outcomes = row.get("outcomes") or ()
        if not isinstance(outcomes, (list, tuple)):
            raise ValueError("dream_report: outcomes is not a list for event %r"
                             % (row.get("event_id"),))
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                raise ValueError("dream_report: outcome is not a record for event %r"
                                 % (row.get("event_id"),))
            add_cost(outcome.get("cost"), row.get("event_id"))

        if state == "graded" and isinstance(record, dict):
            chosen = record.get("chosen")
            outcome = outcomes[0] if outcomes else {}
            key = (kind, chosen)
            slot = buckets.setdefault(key, [0, 0])
            slot[0] += 1
            if outcome.get("status") == "PASS":
                slot[1] += 1

    pass_rate = []
    for (kind, chosen), (n, passed) in buckets.items():
        pass_rate.append({"kind": kind, "chosen": chosen, "n": n,
                          "pass": passed,
                          "pass_rate": (passed / n) if n else 0.0})
    pass_rate.sort(key=lambda item: (str(item["kind"]), str(item["chosen"])))

    return {
        "status": "OK",
        "run_dir": str(run_dir),
        "total_decisions": len(rows),
        "per_kind": [{"kind": k, "count": v}
                     for k, v in sorted(per_kind.items(), key=lambda p: str(p[0]))],
        "states": states,
        "pass_rate": pass_rate,
        "unreadable_artifacts": unreadable,
        "corrupt_costs": corrupt_costs,
        # nothing measured is NO-DATA, never a total of 0.0
        "cost": {"total": cost_total if cost_measured else None,
                 "measured_count": cost_measured,
                 "unmeasured_count": cost_unmeasured},
    }


def format_text(summary):
    """Human-readable rendering of a summarize() result."""
    if not isinstance(summary, dict):
        raise ValueError("dream_report: summary must be a dict")
    for key in ("run_dir", "total_decisions"):
        if key not in summary:
            raise ValueError("dream_report: summary is missing key %r" % (key,))
    for key in ("per_kind", "pass_rate", "unreadable_artifacts", "corrupt_costs"):
        if not isinstance(summary.get(key), (list, tuple)):
            raise ValueError("dream_report: summary key %r must be a list" % (key,))
    for key in ("states", "cost"):
        if not isinstance(summary.get(key), dict):
            raise ValueError("dream_report: summary key %r must be a dict" % (key,))
    lines = []
    lines.append("dream_report: %s" % summary["run_dir"])
    lines.append("decisions: %d" % summary["total_decisions"])
    lines.append("per kind:")
    for entry in summary["per_kind"]:
        lines.append("  %s: %d" % (entry["kind"], entry["count"]))
    st = summary["states"]
    lines.append("states: graded=%d ungraded=%d conflict=%d"
                 % (st["graded"], st["ungraded"], st["conflict"]))
    lines.append("pass rate (graded decisions):")
    if summary["pass_rate"]:
        for entry in summary["pass_rate"]:
            lines.append("  %s / %s: %d/%d = %.4f"
                         % (entry["kind"], entry["chosen"], entry["pass"],
                            entry["n"], entry["pass_rate"]))
    else:
        lines.append("  (none)")
    lines.append("unreadable artifacts: %d" % len(summary["unreadable_artifacts"]))
    for event_id in summary["unreadable_artifacts"]:
        lines.append("  %s" % event_id)
    lines.append("corrupt costs: %d" % len(summary["corrupt_costs"]))
    for event_id in summary["corrupt_costs"]:
        lines.append("  %s" % event_id)
    cost = summary["cost"]
    lines.append("cost: total=%s measured=%d unmeasured=%d"
                 % ("NO-DATA" if cost["total"] is None else cost["total"],
                    cost["measured_count"], cost["unmeasured_count"]))
    return "\n".join(lines) + "\n"


def report(run_dir, as_json=False, out=None, err=None):
    """Print and return the summary for run_dir, or None on NO-DATA."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err

    summary = summarize(run_dir)
    if summary is None:
        if as_json:
            out.write(json.dumps({"status": "NO-DATA", "run_dir": str(run_dir)},
                                 ensure_ascii=False, sort_keys=True) + "\n")
        err.write("dream_report: NO-DATA: run has no journal: %s\n" % run_dir)
        return None

    if as_json:
        out.write(json.dumps(summary, default=str, ensure_ascii=False,
                             sort_keys=True) + "\n")
    else:
        out.write(format_text(summary))
    return summary


USAGE = "usage: python3 -m plugin.runtime.brother.core.dream_report RUN_DIR [--json]\n"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = False
    paths = []
    for arg in argv:
        if arg == "--json":
            as_json = True
        elif arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        elif arg.startswith("-"):
            sys.stderr.write("dream_report: unknown option %s\n" % arg)
            sys.stderr.write(USAGE)
            return 2
        else:
            paths.append(arg)
    if len(paths) != 1:
        sys.stderr.write(USAGE)
        return 2
    result = report(paths[0], as_json=as_json)
    return 0 if result is not None else 2


if __name__ == "__main__":
    sys.exit(main())
