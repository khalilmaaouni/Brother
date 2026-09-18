#!/usr/bin/env python3
"""useful_unwatched_work: whether a safe unwatched span was backed by anything.

Row S10's instrument, scripts/safe_unwatched_time.py, answers HOW LONG a run
went unsupervised without a claim breaking. It does not ask whether anything
was actually delivered in that time: a run that sat idle for two hours,
finishing and verifying nothing, reads exactly as well as one that finished
five units, because "unbroken" and "useful" are different questions. This
file asks the second one.

THE DECIDING PROPERTY (docs/plan/ORCH-1020-WBS.json, unit DOM-20.03): unwatched
time counts as USEFUL only alongside at least one unit that is both FINISHED
and INDEPENDENTLY VERIFIED ACCEPTED, read from claims.json rather than
trusted from the journal's own tally. Zero such units means zero useful
minutes, never a positive score, no matter how long or how clean the
underlying span was: doing nothing safely is not the same claim as doing
something proven.

WHY THIS IS A SEPARATE FILE FROM safe_unwatched_time.py RATHER THAN A CHANGE
TO IT. safe_unwatched_time.py's own "units" figure already counts unit.done
events landing inside the span, and this file could look like it duplicates
that count. It does not: the sibling's units are read off the JOURNAL alone
(a unit finished), never cross-checked against the claim's own settled state
and exit code. A journal can carry a unit.done event for a unit whose claim
was never written at all, and a unit whose own check left no exit code is
already one of the sibling's four break conditions (NO_CHECK), which ends
the span before this file's count would ever include it anyway. This file
exists to make "counted" and "independently verified accepted" the same
claim, which the sibling deliberately does not promise on its own.

THREE OUTCOMES, kept distinct rather than folded into two:

  (a) measure() itself could not compute a span (no directory, no journal,
      no receipts). Propagated verbatim as NO-DATA: this file adds nothing
      to that reason, it is not this file's failure to explain.
  (b) measure() computed a span fine, but claims.json is missing, unreadable
      or not a JSON object at its top level. This is ALSO NO-DATA, with its
      own reason, and is never folded into "zero accepted units": a run
      whose acceptances were never recorded is not a run known to have zero
      of them, the same reasoning benchmarks/SAFE-UNWATCHED-TIME.md already
      applies to a run with no journal.
  (c) measure() computed a span fine, claims.json was read fine (an empty
      object included), and zero entries in it meet the test below. This is
      a real computed answer, reported as a definite 0.0 minutes, not
      NO-DATA: the difference between "we do not know" and "we checked and
      there were none" is the whole reason this file exists.

Reused, read only: safe_unwatched_time.measure() for the span and its break
classification, and its _epoch()/_parse_at() timestamp readers, so this file
never carries a second copy of that parsing (the released_at-falls-back-to-
claimed_at rule in particular is the sibling's own idiom, matched here on
purpose rather than re-derived).

Python 3.9 floor, standard library only.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import safe_unwatched_time as sut  # noqa: E402

NODATA = sut.NODATA

#: The three claims.json states the sibling's own find_breaks() also treats
#: as settled. Not exported there as a named constant, so this is the one
#: place this vocabulary is spelled a second time; kept identical on purpose.
ACCEPTED_STATES = ("done", "released", "closed")


def _is_accepted(claim, span_start, span_end):
    """True when one claims.json entry is a finished, independently verified,
    in-span unit: the exact test the module docstring promises.

    Every read here is defensive on purpose: a malformed entry is excluded,
    never allowed to raise and never allowed to count by accident. A
    non-string state is checked with isinstance before the membership test,
    because a list or dict state would otherwise be unhashable and raise on
    the `in` check rather than simply failing to match."""
    if not isinstance(claim, dict):
        return False
    state = claim.get("state")
    if not isinstance(state, str) or state not in ACCEPTED_STATES:
        return False
    evidence = claim.get("evidence")
    if not isinstance(evidence, dict):
        return False
    exit_code = evidence.get("exit_code")
    # type() rather than isinstance(): bool is a subclass of int, and a
    # True/False exit code is not a verified 0, it is a claim that never
    # carried a real one.
    if type(exit_code) is not int or exit_code != 0:
        return False
    when = sut._epoch(claim.get("released_at"))
    if when is None:
        when = sut._epoch(claim.get("claimed_at"))
    if when is None:
        return False
    return span_start <= when <= span_end


def measure(run_dir):
    """(result dict, exit code), the same two-value shape safe_unwatched_time
    uses. See the module docstring for the three outcomes this can report.

    Exit 0  a figure was computed: useful_minutes is either the full span
            (>= 1 accepted unit) or 0.0 (none), both real answers.
    Exit 2  run_dir is not a usable path, or the run directory itself could
            not be read by measure().
    Exit 3  NO-DATA: measure() itself had none, or claims.json could not be
            read, or its content could not be trusted as an object.
    """
    if not isinstance(run_dir, (str, os.PathLike)):
        return ({"nodata": "run_dir must be a path, got %r"
                 % type(run_dir).__name__}, 2)

    report, code = sut.measure(run_dir)
    if code != 0:
        return ({"nodata": report.get("nodata") or
                 "measure() could not compute a span"}, code)

    claims_path = os.path.join(run_dir, "claims.json")
    try:
        with open(claims_path, encoding="utf-8") as fh:
            claims = json.load(fh)
    except (OSError, ValueError):
        return ({"nodata": "%s: claims.json is missing, unreadable or not "
                 "valid JSON, so acceptance cannot be verified for this run"
                 % run_dir}, 3)
    if not isinstance(claims, dict):
        return ({"nodata": "%s: claims.json is not a JSON object, so "
                 "acceptance cannot be verified for this run" % run_dir}, 3)

    # measure() only ever hands back offset-carrying isoformat() strings it
    # built itself a few lines earlier in its own call; _parse_at() is the
    # exact reader that produced them in the first place, reused here rather
    # than re-parsed, so this can only fail if the sibling's own contract
    # breaks, and if it ever does that is measure()'s defect to surface, not
    # something to paper over here with an invented boundary.
    span_start = sut._parse_at(report["started"])
    span_end = sut._parse_at(report["ended"])
    if span_start is None or span_end is None:
        return ({"nodata": "%s: measure() returned span boundaries this "
                 "module could not parse" % run_dir}, 3)

    accepted_units = sum(1 for claim in claims.values()
                         if _is_accepted(claim, span_start, span_end))
    useful_minutes = report["minutes"] if accepted_units > 0 else 0.0

    return ({
        "nodata": "",
        "minutes": report["minutes"],
        "useful_minutes": useful_minutes,
        "accepted_units": accepted_units,
        "broken_by": report["broken_by"],
    }, 0)


def report_line(result):
    """The one line this module reports. Exactly this shape, everywhere.

    A result missing a key measure() promises on a non-NO-DATA return is NOT
    read as a computed zero: silently defaulting minutes or accepted_units
    to 0 for a malformed dict would manufacture an answer nothing computed,
    which is exactly the fabrication this estate's own rules forbid."""
    if not isinstance(result, dict):
        return "useful unwatched work: %s, not a result" % NODATA
    if result.get("nodata"):
        return "useful unwatched work: %s, %s" % (NODATA, result["nodata"])
    required = ("minutes", "useful_minutes", "accepted_units", "broken_by")
    missing = [key for key in required if key not in result]
    if missing:
        return ("useful unwatched work: %s, result missing %s"
                % (NODATA, ", ".join(missing)))
    return ("useful unwatched work: %.1f min over %d accepted unit(s) "
            "(raw span %.1f min, broken by %s)"
            % (result["useful_minutes"], result["accepted_units"],
               result["minutes"], result["broken_by"]))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", help="a run directory holding journal.jsonl")
    args = ap.parse_args(argv)

    result, code = measure(args.run_dir)
    print(report_line(result))
    return code


if __name__ == "__main__":
    sys.exit(main())
