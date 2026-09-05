#!/usr/bin/env python3
"""validate: the frozen gauntlet specs are loadable, complete, and cite nothing
that is not on disk.

WHY THIS EXISTS. A benchmark specification is only frozen if something refuses
to let it drift. Two drifts are cheap to make and expensive to find later: a
spec that quietly loses a required field (so a later run scores against half a
contract), and a spec that cites an instrument by a path that has since moved or
never existed (so a metric reads as measured when nothing measures it). Both are
mechanical, so both are checked here rather than trusted.

The one sentence that carries meaning in these files is the exact string
NO INSTRUMENT YET. A metric carrying it must say what would have to exist; a
metric NOT carrying it must name a real file and the roadmap row that proved it.
This file is what makes that distinction load bearing, and the count it prints
is the honest headline of the whole benchmark: how much of section 19's metric
list this estate can measure today.

Usage:
  python3 benchmarks/gauntlets/validate.py            # check every spec
  python3 benchmarks/gauntlets/validate.py --selftest # drive the checks backwards
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

NO_INSTRUMENT = "NO INSTRUMENT YET"

#: section 19's own fifteen raw metrics, copied without addition.
FIFTEEN = [
    "TASK CORRECTNESS",
    "TIME TO OUTCOME",
    "HUMAN INTERVENTIONS",
    "FALSE PASS COUNT",
    "NO-DATA HONESTY",
    "SCOPE DRIFT",
    "DEFECTS INTRODUCED",
    "DEFECTS DETECTED",
    "RECOVERY TIME",
    "LOST WORK",
    "REPEATED FAILURE",
    "ACCEPTANCE TIME",
    "ACCEPT/REJECT ACCURACY",
    "TOKEN COST",
    "SAFE UNWATCHED DURATION",
]

#: section 19's own seven fairness controls, copied without addition.
CONTROLS = [
    "starting repository",
    "model where possible",
    "token budget",
    "task instruction",
    "hardware/environment",
    "maximum human interventions",
    "scoring rubric before execution",
]

REQUIRED = [
    "id", "name", "frozen_at", "status", "source", "zone",
    "workload_families", "seeded_conditions", "seeded_conditions_note",
    "fairness_controls", "fairness_notes", "metrics", "scoring_rubric",
    "win_condition", "raw_artifacts", "frozen",
]

STATUSES = ("measured", "partial", "none")

#: a repository path with a file extension, anywhere inside any string value.
PATH_RE = re.compile(
    r"(?:scripts|benchmarks|products|docs|tools)/[A-Za-z0-9._/-]+"
    r"\.(?:py|sh|json|md|log)")


def _strings(value):
    """Every string anywhere in a loaded spec, dict keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, sub in value.items():
            yield key
            for found in _strings(sub):
                yield found
    elif isinstance(value, list):
        for sub in value:
            for found in _strings(sub):
                yield found


def check_spec(spec, name, root=ROOT):
    """(problems, with_instrument, without_instrument). A problem is a plain
    sentence naming the spec and what is wrong with it. Never raises on a
    malformed spec: a malformed spec is a finding, not a crash."""
    problems = []
    with_instrument = 0
    without_instrument = 0

    if not isinstance(spec, dict):
        return ["%s: the spec is not a JSON object" % name], 0, 0

    for field in REQUIRED:
        if field not in spec or not spec[field]:
            problems.append("%s: required field missing or empty: %s"
                            % (name, field))

    for fam in spec.get("workload_families") or []:
        if not isinstance(fam, dict) or "n" not in fam or "name" not in fam:
            problems.append("%s: a workload family entry lacks n or name" % name)
            continue
        if not isinstance(fam["n"], int) or not 1 <= fam["n"] <= 12:
            problems.append("%s: workload family number %r is not in 1 to 12"
                            % (name, fam["n"]))

    declared = list(spec.get("fairness_controls") or [])
    excused = spec.get("fairness_controls_not_applicable") or {}
    for control in CONTROLS:
        if control in declared:
            continue
        reason = excused.get(control) if isinstance(excused, dict) else None
        if not reason:
            problems.append("%s: fairness control neither declared nor excused "
                            "with a reason: %s" % (name, control))
    for control in declared:
        if control not in CONTROLS:
            problems.append("%s: fairness control is not one of section 19's "
                            "seven: %s" % (name, control))

    metrics = spec.get("metrics") or []
    if not metrics:
        problems.append("%s: no metrics" % name)
    seen = set()
    for entry in metrics:
        if not isinstance(entry, dict):
            problems.append("%s: a metric entry is not an object" % name)
            continue
        metric = entry.get("metric")
        if metric not in FIFTEEN:
            problems.append("%s: metric is not one of section 19's fifteen: %r"
                            % (name, metric))
        if metric in seen:
            problems.append("%s: metric listed twice: %s" % (name, metric))
        seen.add(metric)
        status = entry.get("status")
        if status not in STATUSES:
            problems.append("%s: metric %s has status %r, not one of %s"
                            % (name, metric, status, ", ".join(STATUSES)))
            continue
        instrument = entry.get("instrument")
        if status == "none":
            without_instrument += 1
            if instrument != NO_INSTRUMENT:
                problems.append("%s: metric %s has no instrument but does not "
                                "say the exact sentence %s"
                                % (name, metric, NO_INSTRUMENT))
            if not entry.get("would_require"):
                problems.append("%s: metric %s says %s without saying what "
                                "would have to exist"
                                % (name, metric, NO_INSTRUMENT))
        else:
            with_instrument += 1
            if not instrument or instrument == NO_INSTRUMENT:
                problems.append("%s: metric %s is %s but names no instrument"
                                % (name, metric, status))
            if not entry.get("row"):
                problems.append("%s: metric %s names an instrument but no row "
                                "that proved it" % (name, metric))
            if not entry.get("proved"):
                problems.append("%s: metric %s names an instrument but does "
                                "not say what it proved" % (name, metric))
            if status == "partial" and not entry.get("gap"):
                problems.append("%s: metric %s is partial and does not say "
                                "what is missing" % (name, metric))

    rubric = spec.get("scoring_rubric") or {}
    if not isinstance(rubric, dict) or rubric.get("fixed_before_execution") is not True:
        problems.append("%s: the scoring rubric is not marked fixed before "
                        "execution" % name)
    elif not rubric.get("rules"):
        problems.append("%s: the scoring rubric names no rules" % name)

    for text in _strings(spec):
        for cited in PATH_RE.findall(text):
            if not os.path.exists(os.path.join(root, cited)):
                problems.append("%s: cites a path that does not exist: %s"
                                % (name, cited))

    return problems, with_instrument, without_instrument


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()

    paths = sorted(p for p in os.listdir(HERE) if p.endswith(".json"))
    if not paths:
        print("NO-DATA: no spec found in %s" % HERE)
        return 1

    problems = []
    total_with = 0
    total_without = 0
    for path in paths:
        full = os.path.join(HERE, path)
        try:
            with open(full, encoding="utf-8") as handle:
                spec = json.load(handle)
        except (OSError, ValueError) as exc:
            problems.append("%s: cannot be loaded: %s" % (path, exc))
            continue
        found, with_i, without_i = check_spec(spec, path)
        problems.extend(found)
        total_with += with_i
        total_without += without_i
        print("  %-34s %2d metric(s), %2d with an instrument, %2d %s"
              % (path, with_i + without_i, with_i, without_i, NO_INSTRUMENT))

    print("%d spec(s), %d metric entr(ies): %d with an instrument, %d %s"
          % (len(paths), total_with + total_without, total_with,
             total_without, NO_INSTRUMENT))

    if problems:
        for problem in problems:
            print("FAIL: %s" % problem)
        return 1
    print("PASS: every spec is complete and every cited path exists")
    return 0


def selftest():
    """Drive the checks backwards: a spec broken on purpose must be refused,
    and refused for the right reason. A validator nobody drove backwards is a
    claim, not a control."""
    good = {
        "id": "x", "name": "X", "frozen_at": "2026-09-05", "status": "S",
        "source": {"document": "d"}, "zone": {"id": 1},
        "workload_families": [{"n": 1, "name": "tiny bug fix"}],
        "seeded_conditions": ["a"], "seeded_conditions_note": "n",
        "fairness_controls": list(CONTROLS), "fairness_notes": {"a": "b"},
        "metrics": [
            {"metric": "TASK CORRECTNESS", "status": "measured",
             "instrument": "benchmarks/gauntlets/validate.py", "row": "E1",
             "proved": "p"},
            {"metric": "ACCEPTANCE TIME", "status": "none",
             "instrument": NO_INSTRUMENT, "row": None, "would_require": "w"},
        ],
        "scoring_rubric": {"fixed_before_execution": True, "rules": ["r"]},
        "win_condition": {"text": "t"}, "raw_artifacts": {"root": "r"},
        "frozen": {
            "corpus": "none: cases are generated by the runner",
            "corpus_sha1": "0" * 40, "frozen_at": "2026-09-05",
            "frozen_by": "the S27 lane, 2026-09-05",
        },
    }
    problems, with_i, without_i = check_spec(good, "good")
    assert not problems, problems
    assert (with_i, without_i) == (1, 1), (with_i, without_i)

    cases = [
        ("a missing required field", lambda s: s.pop("win_condition"),
         "required field missing"),
        ("no frozen block", lambda s: s.pop("frozen"),
         "required field missing or empty: frozen"),
        ("a cited path that does not exist",
         lambda s: s["metrics"][0].__setitem__(
             "instrument", "scripts/no_such_instrument.py"),
         "cites a path that does not exist"),
        ("a metric outside the fifteen",
         lambda s: s["metrics"][0].__setitem__("metric", "VIBES"),
         "not one of section 19's fifteen"),
        ("an absent instrument without the exact sentence",
         lambda s: s["metrics"][1].__setitem__("instrument", "none really"),
         "does not say the exact sentence"),
        ("an absent instrument that says nothing would have to exist",
         lambda s: s["metrics"][1].__setitem__("would_require", ""),
         "without saying what would have to exist"),
        ("an instrument with no row",
         lambda s: s["metrics"][0].__setitem__("row", ""),
         "no row that proved it"),
        ("a dropped fairness control with no reason",
         lambda s: s["fairness_controls"].remove("token budget"),
         "neither declared nor excused"),
        ("a rubric not fixed before execution",
         lambda s: s["scoring_rubric"].__setitem__(
             "fixed_before_execution", False),
         "not marked fixed before execution"),
        ("a workload family outside 1 to 12",
         lambda s: s["workload_families"][0].__setitem__("n", 13),
         "is not in 1 to 12"),
    ]
    failures = 0
    for label, break_it, expected in cases:
        broken = json.loads(json.dumps(good))
        break_it(broken)
        found, _, _ = check_spec(broken, "broken")
        if not any(expected in p for p in found):
            print("FAIL: %s was not refused for %r; got %r"
                  % (label, expected, found))
            failures += 1
        else:
            print("  refused: %s" % label)
    if failures:
        print("FAIL: %d case(s) not refused" % failures)
        return 1
    print("PASS: %d broken spec(s) refused, one clean spec accepted"
          % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
