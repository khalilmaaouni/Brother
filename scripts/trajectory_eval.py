#!/usr/bin/env python3
"""WBS-80.04 Trajectory evaluation: capture the 7 agent process metrics the
roadmap names, structurally kept apart from product-correctness verdicts.

Exact roadmap quote (WBS-80.04):
  "Capture agent process metrics:
   - repeated failed technique;
   - out-of-scope attempts;
   - unnecessary writes;
   - number of repair loops;
   - human interruptions;
   - verification after final edit;
   - stale evidence reuse attempts.
   Do not treat trajectory cleanliness as product correctness, but use it
   to compare workflow quality."

The roadmap names WHAT to capture, not an event schema, a collector
boundary, or an arm-integration API -- none of that is invented here
beyond a minimal, uniform log: a caller (a benchmark arm wrapper, a
human reviewer reading a transcript, whoever actually observes the
trajectory) calls record() once per observed event, tagged with one of
the 7 CATEGORIES below, and summarize() turns the log into per-category
counts for that one run.

STRUCTURAL SEPARATION (the roadmap's own "do not treat ... as product
correctness"): summarize()'s output never carries a "verdict" key -- the
one vocabulary word scripts/evidence_obligation.py and
scripts/benchmark_harness.py use for product correctness (PASS/FAIL/
NO-DATA). A caller who wants both reports them side by side (see
compare_workflows), never merged into one score; this module contains
no formula that would combine the two, on purpose (the roadmap does not
name one, and inventing one here would be a real per-metric weighting
decision this module has no basis for making).

Runs on the 3.9 floor: standard library only, no match statements.
"""
import argparse
import json
import sys

# The 7 metrics, verbatim from the roadmap list, named as event categories
# a caller tags one observed occurrence with. Order matches the roadmap.
CATEGORIES = (
    "repeated_failed_technique",
    "out_of_scope_attempt",
    "unnecessary_write",
    "repair_loop",
    "human_interruption",
    "verification_after_final_edit",
    "stale_evidence_reuse",
)

# The one word this module refuses to use as a summary key, on purpose --
# see the module docstring's STRUCTURAL SEPARATION note.
_FORBIDDEN_SUMMARY_KEY = "verdict"


class TrajectoryLog(object):
    """An append-only list of (category, detail) events for one workflow
    run. Deliberately not a dict-of-counts up front: the raw event order
    and detail text are kept, since a caller comparing workflow quality
    may want to read what actually happened, not just a count."""

    def __init__(self, workflow_name="unnamed-workflow"):
        self.workflow_name = workflow_name
        self.events = []

    def record(self, category, detail=""):
        if category not in CATEGORIES:
            raise ValueError("unknown trajectory category %r, must be one of %s" % (category, CATEGORIES))
        self.events.append({"seq": len(self.events), "category": category, "detail": detail})
        return self.events[-1]

    def summarize(self):
        counts = {cat: 0 for cat in CATEGORIES}
        for event in self.events:
            counts[event["category"]] += 1
        summary = {
            "workflow_name": self.workflow_name,
            "total_events": len(self.events),
            "counts": counts,
            "events": list(self.events),
            "note": (
                "trajectory cleanliness is not product correctness -- this "
                "summary carries no verdict and must not be merged into one"
            ),
        }
        assert _FORBIDDEN_SUMMARY_KEY not in summary, (
            "a trajectory summary must never carry a %r key -- that is the "
            "product-correctness vocabulary, kept structurally separate "
            "per WBS-80.04" % _FORBIDDEN_SUMMARY_KEY
        )
        return summary


def new_log(workflow_name="unnamed-workflow"):
    return TrajectoryLog(workflow_name)


def load_events_jsonl(path):
    """Read a JSON-Lines file of {"category": ..., "detail": ...} objects
    (one event per line) into a fresh TrajectoryLog. This is the one
    concrete file format this module picks, since the roadmap does not
    name one -- a caller free to build TrajectoryLog directly instead."""
    log = TrajectoryLog(workflow_name=path)
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("%s line %d: not valid JSON: %s" % (path, lineno, exc))
            if "category" not in event:
                raise ValueError("%s line %d: event missing required 'category' field" % (path, lineno))
            log.record(event["category"], event.get("detail", ""))
    return log


def compare_workflows(logs_by_name):
    """Lay per-workflow summaries side by side for comparison. Does not
    compute a combined score or a winner -- the roadmap says "use it to
    compare workflow quality," not "rank workflows by a formula," and no
    per-metric weighting is stated anywhere to invent one from."""
    return {name: log.summarize() for name, log in logs_by_name.items()}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-categories", action="store_true", help="print the 7 trajectory categories and exit")
    parser.add_argument("--events-file", help="JSONL file of {category, detail} events to summarize")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_categories:
        for cat in CATEGORIES:
            print(cat)
        return 0

    if not args.events_file:
        parser.error("--events-file is required (or pass --list-categories)")

    try:
        log = load_events_jsonl(args.events_file)
    except (OSError, ValueError) as exc:
        print("NO-DATA: could not load %s: %s" % (args.events_file, exc))
        return 2

    print(json.dumps(log.summarize(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
