#!/usr/bin/env python3
"""autonomy_corpus: loader and validator for the DOM-20.04 workload corpus.

WHY THIS EXISTS. docs/plan/ORCH-1020-WBS.json unit DOM-20.04 asks for
"frozen long-horizon workloads with injected faults", so a benchmark can
later measure how an unattended agent handles a task that runs for a long
stretch, decomposes into more than one independent unit, and has specific
faults deliberately planted in it. The unit's own note is explicit: "the
harness is dispatchable now; the row only MEANS something once the fixtures
are real, and a harness over empty fixtures reports NO-DATA rather than
passing". This module is that harness. It ships with zero real workload
fixtures (benchmarks/autonomy_corpus/workloads/ is empty on purpose, see the
README there); fabricating a workload here would be exactly the kind of
invented evidence this estate's own rules forbid.

THE SCHEMA one workload JSON file must satisfy, and what each rule is for,
are documented in full in benchmarks/autonomy_corpus/README.md. This module
is the code that enforces that document; read it first if a validation
reason here is not self-explanatory.

THE THREE OUTCOMES, kept distinct on purpose, the same shape this estate's
other measurement modules already use (see scripts/useful_unwatched_work.py
for the precedent this follows):

  (a) load_corpus() had nothing to read at all: the directory does not
      exist, is not a directory, or holds zero ".json" files directly
      inside it. This is NO-DATA (exit 3), never zero workloads loaded.
  (b) the directory could not even be listed (a permissions error on the
      directory itself). Also NO-DATA, exit 2, its own reason.
  (c) the directory held content but at least one file violates the
      schema, or two files declare the same workload_id. This is REFUSED
      (exit 4), not NO-DATA and not a pass: the corpus exists and has
      content, that content is invalid, and the whole load stops rather
      than silently dropping the bad file or silently proceeding with a
      partial, unverified corpus.
  (d) every file is valid and every workload_id is unique. A real pass
      (exit 0), carrying the validated workload list.

UNKNOWN INPUT RAISES, PER THE WORKER CONTRACT: a corpus_dir or path that is
not a string or os.PathLike is not silently coerced or ignored, it is
reported at the same NO-DATA/2 outcome a permissions failure would use, and
report_line() raises ValueError on an exit_code it does not recognise
rather than printing a placeholder for an outcome nothing here produces.

Reviewed against a Muse adversarial pass (see
/private/tmp/lanes/ORCH-1020-drafts/DOM-20.04-muse.txt for the raw findings);
the fixes folded in here are: json.loads is called with parse_constant set
to refuse the non-standard NaN/Infinity/-Infinity tokens Python's parser
otherwise accepts past strict JSON, open() catches ValueError alongside
OSError (an embedded-null-byte path raises ValueError before the OS layer
ever gets involved), both load_workload() and load_corpus() explicitly
type-check their path argument instead of letting a wrong type surface as
an uncaught TypeError deep in os.path, and report_line() raises on an
exit_code outside the four this module produces instead of carrying a dead,
untested fallback string. Two findings were reviewed and NOT changed: JSON
duplicate-key handling follows Python's own json module default (last key
wins) rather than a custom object_pairs_hook, matching every other JSON
reader already in this codebase; and os.path.exists()/os.path.isdir()
follow their own documented stdlib behavior of returning False rather than
raising on a stat error, which the ponytail comment below names as an
accepted, narrow limitation rather than something worth a heavier rewrite.

Python 3.9 floor, standard library only (json, os, sys, argparse). No
network, no subprocess.
"""
import argparse
import json
import os
import sys

NODATA = "NO-DATA"


def _bad_text(value):
    """True when value is not a string, or is blank once stripped."""
    return not isinstance(value, str) or not value.strip()


def _reject_constant(token):
    """Passed to json.loads as parse_constant: refuses the three
    non-standard tokens (NaN, Infinity, -Infinity) Python's json module
    otherwise accepts past strict JSON. Raising ValueError here lands in
    the same except clause json.loads' own syntax errors do, so a workload
    file using one of these tokens is refused at "does not parse as JSON"
    rather than silently accepted and then failing some other check (or
    worse, passing one it should not, since none of these tokens are valid
    strings and would only ever reach _bad_text as a non-string)."""
    raise ValueError("non-standard JSON constant %r is not valid JSON" % token)


def load_workload(path):
    """Load and validate one workload JSON file.

    Returns (workload_dict, None) on success, or (None, reason) on the
    first schema violation found, checked in the order
    benchmarks/autonomy_corpus/README.md documents. reason always names
    the file path and the exact rule broken; nothing here silently drops
    or coerces a bad field into something valid.
    """
    if not isinstance(path, (str, os.PathLike)):
        return None, "workload path must be a string or path-like object, got %r" % (
            type(path).__name__,)

    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError) as exc:
        return None, "workload file %r could not be read: %s" % (path, exc)

    try:
        data = json.loads(raw, parse_constant=_reject_constant)
    except ValueError as exc:
        return None, "workload file %r does not parse as JSON: %s" % (path, exc)

    if not isinstance(data, dict):
        return None, "workload file %r top level is not a JSON object" % (path,)

    if _bad_text(data.get("workload_id")):
        return None, ("workload file %r violates schema: workload_id is "
                       "missing, not a string, or blank" % (path,))
    if _bad_text(data.get("title")):
        return None, ("workload file %r violates schema: title is missing, "
                       "not a string, or blank" % (path,))

    units = data.get("units")
    if not isinstance(units, list):
        return None, ("workload file %r violates schema: units is missing "
                       "or not a list" % (path,))
    if len(units) < 2:
        return None, ("workload file %r violates schema: units has fewer "
                       "than 2 entries" % (path,))

    unit_ids = []
    for idx, unit in enumerate(units):
        if not isinstance(unit, dict):
            return None, ("workload file %r violates schema: units[%d] is "
                           "not an object" % (path, idx))
        if _bad_text(unit.get("unit_id")):
            return None, ("workload file %r violates schema: "
                           "units[%d].unit_id is missing, not a string, or "
                           "blank" % (path, idx))
        if _bad_text(unit.get("objective")):
            return None, ("workload file %r violates schema: "
                           "units[%d].objective is missing, not a string, "
                           "or blank" % (path, idx))
        if "depends_on" in unit:
            dep = unit["depends_on"]
            if not isinstance(dep, list) or not all(
                    isinstance(entry, str) for entry in dep):
                return None, ("workload file %r violates schema: "
                               "units[%d].depends_on is present but not a "
                               "list of strings" % (path, idx))
        unit_ids.append(unit["unit_id"])

    seen_unit_ids = set()
    for uid in unit_ids:
        if uid in seen_unit_ids:
            return None, ("workload file %r violates schema: duplicate "
                           "unit_id %r" % (path, uid))
        seen_unit_ids.add(uid)

    all_unit_ids = set(unit_ids)
    for idx, unit in enumerate(units):
        uid = unit["unit_id"]
        for entry in unit.get("depends_on", []):
            if entry == uid:
                return None, ("workload file %r violates schema: "
                               "units[%d].depends_on references its own "
                               "unit_id %r" % (path, idx, entry))
            if entry not in all_unit_ids:
                return None, ("workload file %r violates schema: "
                               "units[%d].depends_on references unknown "
                               "unit_id %r" % (path, idx, entry))

    independent_count = sum(
        1 for unit in units if len(unit.get("depends_on", [])) == 0)
    if independent_count < 2:
        return None, ("workload file %r violates schema: independence rule "
                       "requires at least 2 units with an empty depends_on "
                       "list, found %d" % (path, independent_count))

    failures = data.get("injected_failures")
    if not isinstance(failures, list):
        return None, ("workload file %r violates schema: injected_failures "
                       "is missing or not a list" % (path,))
    if len(failures) == 0:
        return None, ("workload file %r violates schema: injected_failures "
                       "is empty; a workload with units but no injected "
                       "failures is refused, never silently accepted with "
                       "zero faults" % (path,))

    failure_ids = []
    for idx, failure in enumerate(failures):
        if not isinstance(failure, dict):
            return None, ("workload file %r violates schema: "
                           "injected_failures[%d] is not an object"
                           % (path, idx))
        for field in ("failure_id", "kind", "target_unit", "description"):
            if _bad_text(failure.get(field)):
                return None, ("workload file %r violates schema: "
                               "injected_failures[%d].%s is missing, not a "
                               "string, or blank" % (path, idx, field))
        failure_ids.append(failure["failure_id"])

    seen_failure_ids = set()
    for fid in failure_ids:
        if fid in seen_failure_ids:
            return None, ("workload file %r violates schema: duplicate "
                           "failure_id %r" % (path, fid))
        seen_failure_ids.add(fid)

    for idx, failure in enumerate(failures):
        target = failure["target_unit"]
        if target not in all_unit_ids:
            return None, ("workload file %r violates schema: "
                           "injected_failures[%d].target_unit %r does not "
                           "match any unit_id" % (path, idx, target))

    return data, None


def load_corpus(corpus_dir):
    """(result, exit_code) for a whole corpus directory. See the module
    docstring for the four outcomes this can report."""
    if not isinstance(corpus_dir, (str, os.PathLike)):
        return ({"nodata": "corpus_dir must be a string or path-like "
                 "object, got %r" % type(corpus_dir).__name__}, 2)

    if not os.path.exists(corpus_dir):
        # ponytail: os.path.exists() and os.path.isdir() follow their own
        # documented stdlib contract of returning False on a stat error
        # (for example, a permission-denied parent directory) rather than
        # raising, so a permission failure at THIS check reads as "does
        # not exist" (NO-DATA, exit 3) rather than the exit-2 listing
        # failure below. A real, narrow limitation; a stricter os.stat
        # wrapper is the upgrade if this ever needs to distinguish the two.
        return ({"nodata": "corpus directory %r does not exist"
                 % (corpus_dir,)}, 3)
    if not os.path.isdir(corpus_dir):
        return ({"nodata": "corpus path %r is not a directory"
                 % (corpus_dir,)}, 3)

    try:
        entries = os.listdir(corpus_dir)
    except OSError as exc:
        return ({"nodata": "corpus directory %r could not be listed: %s"
                 % (corpus_dir, exc)}, 2)

    json_names = sorted(
        name for name in entries
        if name.endswith(".json")
        and os.path.isfile(os.path.join(corpus_dir, name)))
    if not json_names:
        return ({"nodata": "corpus directory %r contains zero .json files"
                 % (corpus_dir,)}, 3)

    workloads = []
    seen_workload_ids = {}
    for name in json_names:
        full = os.path.join(corpus_dir, name)
        workload, reason = load_workload(full)
        if reason is not None:
            return ({"refused": reason}, 4)
        wid = workload["workload_id"]
        if wid in seen_workload_ids:
            return ({"refused": ("workload file %r declares workload_id "
                     "%r which duplicates the workload_id declared in %r"
                     % (full, wid, seen_workload_ids[wid]))}, 4)
        seen_workload_ids[wid] = full
        workloads.append(workload)

    return ({"nodata": "", "workloads": workloads}, 0)


def report_line(result, exit_code):
    """The one line this module reports. Exactly this shape, everywhere.

    Raises ValueError on an exit_code outside the four load_corpus()
    actually produces (0, 2, 3, 4): a caller passing a fifth value is a
    defect in the caller, never a case this prints a placeholder for."""
    if exit_code in (2, 3):
        return "autonomy corpus: %s, %s" % (NODATA, result.get("nodata", ""))
    if exit_code == 4:
        return "autonomy corpus: REFUSED, %s" % (result.get("refused", ""),)
    if exit_code == 0:
        workloads = result.get("workloads", [])
        n_workloads = len(workloads)
        n_units = sum(len(w.get("units", [])) for w in workloads)
        n_failures = sum(len(w.get("injected_failures", []))
                         for w in workloads)
        return ("autonomy corpus: %d workload(s) loaded, %d total unit(s), "
                "%d total injected failure(s)"
                % (n_workloads, n_units, n_failures))
    raise ValueError("unknown exit code for a load_corpus() result: %r"
                     % (exit_code,))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus_dir", help="a directory of workload .json files")
    args = ap.parse_args(argv)

    result, exit_code = load_corpus(args.corpus_dir)
    print(report_line(result, exit_code))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
