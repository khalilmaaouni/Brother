#!/usr/bin/env python3
"""endurance_classes: score useful units, not elapsed time.

WHY THIS EXISTS. docs/plan/ORCH-1020-WBS.json unit DOM-20.05's objective is
"score useful units, not elapsed time." scripts/safe_unwatched_time.py and
scripts/useful_unwatched_work.py already draw that distinction for ONE
span of ONE run (how long a run went unsupervised, versus how much of that
span was backed by verified work). This module extends the same discipline
into eight fixed dimensions, scored across a CORPUS of real run
directories, the same "CORPUS" row shape unit DOM-20.04 uses:

    useful_units, idle_minutes, retries, abandoned_approaches,
    context_resets, crashes, interventions, scope_incidents

THE DECIDING PROPERTY: an endurance run reports these eight fields; any
field this codebase has no instrument for today is reported as the exact
string "NO-DATA", never as 0. A field that was actually counted and
genuinely came out to zero is reported as a real 0. The two must never
look the same, because a run that quietly retried five times and a run
that never retried at all must not both read as 0 just because nothing
today records a retry.

WHAT HAS A REAL INSTRUMENT TODAY, AND WHAT DOES NOT.

  useful_units, idle_minutes   scripts/useful_unwatched_work.py's own
                                measure(): accepted_units directly, and
                                idle_minutes as minutes minus
                                useful_minutes (0.0 when the whole span was
                                backed by accepted work, the full span
                                length when none of it was).
  interventions                scripts/intervention_events.py's
                                read_interventions(): a real count, or
                                NO-DATA when the run has no journal at all.
  scope_incidents               this module's own
                                scope_incidents_in_journal(), reading
                                journal.jsonl directly for integrate.refused
                                events whose reason names a scope
                                violation, the same wording
                                safe_unwatched_time.py already recognizes
                                elsewhere in this codebase (see that
                                module's _refusal_kind()).
  retries, abandoned_approaches, context_resets, crashes
                                NO INSTRUMENT ANYWHERE IN THIS CODEBASE
                                TODAY. No event kind records a retry, an
                                abandoned approach, a context reset, or a
                                crash. These four are UNCONDITIONALLY
                                "NO-DATA" on every call. Inferring any of
                                them from a broken span, a nonzero exit
                                code, or a missing journal would be exactly
                                the fabricated measurement this estate's
                                own rules forbid; they stay NO-DATA until a
                                real instrument for each is built, and this
                                module keeps reporting them rather than
                                dropping them from the dict, because the
                                fixed 8-key shape is itself part of the
                                contract: a caller checking for a missing
                                key must be able to tell "not measured yet"
                                from "this module changed shape".

Reused, imported and never reimplemented: useful_unwatched_work.measure()
for useful_units and idle_minutes, intervention_events.read_interventions()
for interventions. Neither is called for its side effects, only its return
value.

Reviewed against a Muse adversarial pass (see
/private/tmp/lanes/ORCH-1020-drafts/DOM-20.05-muse.txt for the raw
findings). Fixes folded in from that pass: corpus_dir and run_dir are
explicitly type-checked before any os.path call touches them, matching the
isinstance guard useful_unwatched_work.measure() already uses, so a
wrong-typed argument reports a controlled NO-DATA/2 instead of an uncaught
TypeError; journal.jsonl is opened with errors="replace" (matching
journal.py's own read() of the same file) so a torn multi-byte sequence at
a crash-truncated file's end raises UnicodeDecodeError, never left for an
except OSError to miss; interventions is only read off a value that is
actually a list, never len()'d blind; score_corpus never smuggles a
vanished-mid-scan run's single-key NO-DATA shape into the promised 8-key
runs dict, and treats "every qualifying name vanished before it could be
read" as NO-DATA rather than a hollow pass; and report_line raises on a
result that breaks score_corpus()'s own exit_code-0 promise instead of
reading it as zero runs scored. Two findings were reviewed and NOT
separately re-fixed: the private helpers this module already carries
(_is_nodata_report's non-dict branch, _is_real_number's bool exclusion)
are exercised directly by this module's own test file rather than only
through the sibling call sites that cannot presently produce those shapes,
which is what closes the "dead path, no coverage" finding without adding
further unreachable guards on top of guards.

Python 3.9 floor, standard library only (json, os, sys, argparse). No
network, no subprocess.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import useful_unwatched_work as uuw  # noqa: E402
import intervention_events  # noqa: E402

NODATA = "NO-DATA"

#: The event type and reason-substrings safe_unwatched_time.py's own
#: _refusal_kind() already uses to recognize a scope violation. Matched
#: here on purpose rather than re-derived, since it is the one place this
#: codebase already spells "this integrate.refused reason means scope".
SCOPE_EVENT_TYPE = "integrate.refused"
SCOPE_REASON_MARKERS = ("quarantine", "never declared")

#: The fixed 8-key shape. Order matters only for readability; the test
#: enumerates this exact set, not this exact order.
REPORT_FIELDS = (
    "useful_units", "idle_minutes", "retries", "abandoned_approaches",
    "context_resets", "crashes", "interventions", "scope_incidents",
)

#: The four fields with no instrument anywhere in this codebase today.
#: Kept as a named set so classify_run's initial dict and this module's
#: own tests read the same list rather than restating it.
UNINSTRUMENTED_FIELDS = frozenset((
    "retries", "abandoned_approaches", "context_resets", "crashes",
))


def _is_nodata_report(report):
    """True when a sibling module's (report, exit_code) report half is its
    own NO-DATA shape: a dict whose only meaningful key is a non-empty
    "nodata" string. A non-dict report is treated as NO-DATA rather than
    risking a fabricated read off something that is not the shape either
    sibling promises."""
    if not isinstance(report, dict):
        return True
    nodata = report.get("nodata")
    return isinstance(nodata, str) and nodata != ""


def _is_real_number(value):
    """True for int or float, never bool: bool is an int subclass in
    Python, and a stray True/False must never be read as a measured 1 or
    0 for a minutes or count field."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def scope_incidents_in_journal(run_dir):
    """(count_or_none, reason) for one run directory's journal.jsonl.

    (None, reason)  no journal.jsonl exists to read: NO-DATA, nothing was
                    recorded.
    (0, "")         journal.jsonl exists and was read; a real, computed
                    zero matching events.
    (N, "")         journal.jsonl exists and N events matched.

    A malformed line, a non-object event, a non-object payload or a
    non-string reason is skipped, never raised: this reads a possibly
    crash-truncated journal, and a torn final line is expected, not fatal
    (the same stance scripts/safe_unwatched_time.py's own read_journal()
    already takes on the same file).
    """
    if not isinstance(run_dir, (str, os.PathLike)):
        return (None, "run_dir must be a string or path-like object, got "
                "%r" % type(run_dir).__name__)

    journal_path = os.path.join(run_dir, "journal.jsonl")
    if not os.path.isfile(journal_path):
        return (None, "%s: no journal.jsonl to read" % run_dir)

    count = 0
    try:
        # errors="replace" rather than a second except clause for
        # UnicodeDecodeError: this mirrors journal.py's own read(), which
        # reads this exact file under the same tolerance, because a
        # crash-truncated journal can leave a torn multi-byte sequence at
        # its very end and that must not be fatal to counting the whole
        # events that came before it.
        with open(journal_path, encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") != SCOPE_EVENT_TYPE:
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                reason_text = payload.get("reason")
                if not isinstance(reason_text, str):
                    continue
                lowered = reason_text.lower()
                if any(marker in lowered for marker in SCOPE_REASON_MARKERS):
                    count += 1
    except OSError as exc:
        return (None, "%s: journal.jsonl exists but could not be read: %s"
                % (run_dir, exc))

    return (count, "")


def classify_run(run_dir):
    """(report, exit_code) for one run directory's eight endurance fields.

    Exit 0  the 8-key report dict was built. Individual fields may still
            be the string "NO-DATA"; this exit code only says the dict
            itself was produced, never that every field was measured.
    Exit 2  run_dir is not a usable path, or is not a directory at all.
            The returned dict is the single-key {"nodata": reason} shape,
            not the 8-key shape, because nothing was even attempted.
    """
    if not isinstance(run_dir, (str, os.PathLike)):
        return ({"nodata": "run_dir must be a string or path-like object, "
                 "got %r" % type(run_dir).__name__}, 2)
    if not os.path.isdir(run_dir):
        return ({"nodata": "%s is not a directory" % run_dir}, 2)

    report = dict((field, NODATA) for field in REPORT_FIELDS)

    uuw_report, _uuw_code = uuw.measure(run_dir)
    if not _is_nodata_report(uuw_report):
        accepted = uuw_report.get("accepted_units")
        if isinstance(accepted, int) and not isinstance(accepted, bool):
            report["useful_units"] = accepted

        minutes = uuw_report.get("minutes")
        useful_minutes = uuw_report.get("useful_minutes")
        if _is_real_number(minutes) and _is_real_number(useful_minutes):
            report["idle_minutes"] = float(minutes) - float(useful_minutes)

    # interventions and scope_incidents both read the journal directly,
    # independently of whether uuw.measure() could compute a span: a run
    # with no receipt (uuw.measure()'s own NO-DATA reason) can still have
    # a journal worth reading for these two fields.
    interventions = intervention_events.read_interventions(run_dir)
    # isinstance, not "is not None": the sibling contract promises None or
    # a list, never anything else, but reading len() of whatever comes
    # back without checking would treat a wrong-shaped value (a string, a
    # dict) as a real count instead of the unmeasured field it actually is.
    if isinstance(interventions, list):
        report["interventions"] = len(interventions)

    scope_count, _scope_reason = scope_incidents_in_journal(run_dir)
    if scope_count is not None:
        report["scope_incidents"] = scope_count

    # retries, abandoned_approaches, context_resets, crashes: left at
    # NO-DATA, unconditionally. See the module docstring for why.

    return (report, 0)


def score_corpus(corpus_dir):
    """(result, exit_code) for a whole corpus of run directories.

    A qualifying run is an immediate subdirectory of corpus_dir that
    contains a file literally named "journal.jsonl". Anything else
    directly inside corpus_dir is silently skipped, never an error.

    Exit 0  {"nodata": "", "runs": {dirname: 8-key report, ...}}
    Exit 3  corpus_dir missing, not a directory, or holds zero qualifying
            runs: NO-DATA, never zero runs scored as a pass.
    Exit 2  corpus_dir itself could not be listed (OSError), or is not a
            usable path type.
    """
    if not isinstance(corpus_dir, (str, os.PathLike)):
        return ({"nodata": "corpus_dir must be a string or path-like "
                 "object, got %r" % type(corpus_dir).__name__}, 2)
    if not os.path.isdir(corpus_dir):
        return ({"nodata": "corpus directory %r does not exist"
                 % (corpus_dir,)}, 3)

    try:
        entries = os.listdir(corpus_dir)
    except OSError as exc:
        return ({"nodata": "corpus directory %r could not be listed: %s"
                 % (corpus_dir, exc)}, 2)

    run_names = sorted(
        name for name in entries
        if os.path.isdir(os.path.join(corpus_dir, name))
        and os.path.isfile(os.path.join(corpus_dir, name, "journal.jsonl")))

    if not run_names:
        return ({"nodata": "corpus directory %r holds zero run directories "
                 "(a subdirectory containing journal.jsonl)"
                 % (corpus_dir,)}, 3)

    runs = {}
    for name in run_names:
        report, code = classify_run(os.path.join(corpus_dir, name))
        # A subdirectory that qualified a moment ago (it had journal.jsonl
        # when listed above) can still vanish before classify_run reaches
        # it: a concurrent second actor cleaning up a run directory while
        # this scan runs. classify_run then reports its own single-key
        # NO-DATA shape (exit code 2), not the promised 8-key report; that
        # shape is never smuggled into runs, which is documented to hold
        # only 8-key reports. The vanished run is simply not counted,
        # exactly as a subdirectory that never had a journal.jsonl at all
        # would not be.
        if code == 0:
            runs[name] = report

    if not runs:
        return ({"nodata": "corpus directory %r held %d run directory "
                 "name(s) at listing time, but none could still be "
                 "classified (each vanished or stopped qualifying before "
                 "it was read)" % (corpus_dir, len(run_names))}, 3)

    return ({"nodata": "", "runs": runs}, 0)


def report_line(result, exit_code):
    """The one line this module reports. Exactly this shape, everywhere.

    Raises ValueError on an exit_code outside the three score_corpus()
    actually produces (0, 2, 3): a caller passing a fourth value is a
    defect in the caller, never a case this prints a placeholder for."""
    if exit_code in (2, 3):
        return "endurance classes: %s, %s" % (
            NODATA, result.get("nodata", "") if isinstance(result, dict)
            else "")
    if exit_code == 0:
        if not isinstance(result, dict) or not isinstance(
                result.get("runs"), dict):
            # exit_code 0 is score_corpus()'s own promise that "runs" is
            # present and is a dict; a caller handing this function a
            # result that breaks that promise is a defect in the caller,
            # never read here as a silent zero.
            raise ValueError("a score_corpus() result with exit_code 0 "
                             "must carry a dict at result['runs'], got %r"
                             % (result,))
        return "endurance classes: %d run(s) scored" % len(result["runs"])
    raise ValueError("unknown exit code for a score_corpus() result: %r"
                     % (exit_code,))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus_dir",
                    help="a directory of run subdirectories, each holding "
                         "a journal.jsonl")
    args = ap.parse_args(argv)

    result, exit_code = score_corpus(args.corpus_dir)
    print(report_line(result, exit_code))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
