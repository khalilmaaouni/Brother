#!/usr/bin/env python3
"""DOM-30.02: does the acceptance fixture corpus cover its seven classes.

WHY THIS EXISTS. docs/plan/ORCH-1020-WBS.json names one deciding property
for this row: the acceptance fixtures cover frontend, mobile, API contract,
data pipeline, dependency update, refactor and matching logic, each with a
seeded defect. Nothing in this tree answered that question before tonight.
This module is the harness that answers it: it scans
benchmarks/fixture_classes/corpus/ for fixture directories, validates each
one's fixture.json against benchmarks/fixture_classes/SCHEMA.json, and
reports, per class, whether a real fixture covers it.

THE SCHEMA IS THE ONE SOURCE OF THE CLASS LIST. SCHEMA.json's
properties.class.enum is the seven names, typed once. This module loads it
at import time and never retypes the list, the same reuse discipline
scripts/orchestrator_invariants.py applies to its own vocabulary: two
copies of a class list drift the first time one of them is edited and the
other is not.

THE ROW'S OWN NOTE, why NO-DATA is the honest answer tonight: "the harness
is dispatchable now; the row only MEANS something once the fixtures are
real, and a harness over empty fixtures reports NO-DATA rather than
passing". No real fixture ships in this commit (see
benchmarks/fixture_classes/README.md): corpus/ does not exist, so every
class reports uncovered and coverage_report() returns NO-DATA rather than a
fabricated PASS. NO-DATA is never read as a pass here or anywhere it is
used, matching the worker contract's rule 1.

WHAT COUNTS AS A DEFECT VERSUS WHAT COUNTS AS "NOT YET COVERED". An empty
or missing corpus/ is not a defect, it is the honest starting state, and
reports NO-DATA. A corpus/ that exists and contains a directory with no
fixture.json, a fixture.json that is not valid JSON, a fixture.json whose
class is outside the schema's enum, or a fixture.json whose seeded_defect
is missing or blank, IS a defect: it means something under corpus/ claims
to be a fixture and is not a usable one. Rule 2 of the worker contract
("unknown input raises, no permissive default") says a caller must never
silently drop that entry and quietly under-count coverage; scan_corpus()
raises FixtureCorpusError naming the exact path and field instead.

Python 3.9 floor, standard library only, no network.
"""

import argparse
import collections
import json
import os
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_MODULE_DIR)
SCHEMA_PATH = os.path.join(
    _REPO_ROOT, "benchmarks", "fixture_classes", "SCHEMA.json")
DEFAULT_CORPUS_ROOT = os.path.join(
    _REPO_ROOT, "benchmarks", "fixture_classes", "corpus")

MANIFEST_NAME = "fixture.json"


class FixtureCorpusError(Exception):
    """The corpus under corpus/ contains something that claims to be a
    fixture and is not a usable one: bad JSON, a directory with no
    manifest, an unrecognised class, or a blank seeded_defect. Never
    caught and silently skipped by scan_corpus(); a defect here must stop
    the count, not quietly shrink it."""


def _load_schema(schema_path):
    try:
        with open(schema_path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except OSError as exc:
        raise RuntimeError(
            "fixture_classes: cannot read schema at %r: %s" %
            (schema_path, exc))
    except ValueError as exc:
        raise RuntimeError(
            "fixture_classes: schema at %r is not valid JSON: %s" %
            (schema_path, exc))
    try:
        classes = tuple(schema["properties"]["class"]["enum"])
        required = tuple(schema["required"])
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            "fixture_classes: schema at %r is missing the class enum or "
            "the required-field list: %s" % (schema_path, exc))
    if not classes:
        raise RuntimeError(
            "fixture_classes: schema at %r declares an empty class enum" %
            (schema_path,))
    return classes, required


CLASSES, _REQUIRED_FIELDS = _load_schema(SCHEMA_PATH)
CLASSES = frozenset(CLASSES)

FixtureRecord = collections.namedtuple(
    "FixtureRecord", "fixture_id klass seeded_defect path")


def _read_manifest(manifest_path):
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise FixtureCorpusError(
            "%s: could not read manifest: %s" % (manifest_path, exc))
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise FixtureCorpusError(
            "%s: not valid JSON: %s" % (manifest_path, exc))
    if not isinstance(manifest, dict):
        raise FixtureCorpusError(
            "%s: manifest must be a JSON object, got %s" %
            (manifest_path, type(manifest).__name__))
    return manifest


def _validate_manifest(manifest, manifest_path):
    for field in _REQUIRED_FIELDS:
        if field not in manifest:
            raise FixtureCorpusError(
                "%s: missing required field %r" % (manifest_path, field))
    klass = manifest["class"]
    if klass not in CLASSES:
        raise FixtureCorpusError(
            "%s: class %r is not one of the schema's known classes: %s" %
            (manifest_path, klass, sorted(CLASSES)))
    seeded_defect = manifest["seeded_defect"]
    if not isinstance(seeded_defect, str) or not seeded_defect.strip():
        raise FixtureCorpusError(
            "%s: seeded_defect must be a non-blank string" % (manifest_path,))
    return klass, seeded_defect


def scan_corpus(corpus_root=None):
    """Return a tuple of FixtureRecord, one per valid fixture directory
    under corpus_root, sorted by fixture_id for a deterministic order.

    A missing corpus_root, or one that exists but is empty, returns an
    empty tuple: that is the honest "no fixtures yet" state, not a defect.
    corpus_root existing with anything else that is not a well-formed
    fixture directory (no fixture.json, invalid JSON, unrecognised class,
    blank seeded_defect) raises FixtureCorpusError; see the module
    docstring for why this is never silently skipped.
    """
    root = corpus_root if corpus_root is not None else DEFAULT_CORPUS_ROOT
    if not os.path.isdir(root):
        return ()
    records = []
    for name in sorted(os.listdir(root)):
        entry_path = os.path.join(root, name)
        if not os.path.isdir(entry_path):
            raise FixtureCorpusError(
                "%s: corpus/ must contain only fixture directories, found "
                "a non-directory entry" % (entry_path,))
        manifest_path = os.path.join(entry_path, MANIFEST_NAME)
        if not os.path.isfile(manifest_path):
            raise FixtureCorpusError(
                "%s: no %s manifest" % (entry_path, MANIFEST_NAME))
        manifest = _read_manifest(manifest_path)
        klass, seeded_defect = _validate_manifest(manifest, manifest_path)
        records.append(FixtureRecord(
            fixture_id=name, klass=klass, seeded_defect=seeded_defect,
            path=entry_path))
    return tuple(records)


def coverage_report(corpus_root=None):
    """Return a dict describing, per class, whether the corpus covers it.

    {
      "classes": {class_name: (fixture_id, ...), ...},   # every known class
      "missing_classes": (class_name, ...),               # sorted, may be empty
      "verdict": "PASS" or "NO-DATA",                      # never FAIL, never PASS
      "reason": str,                                       #   with any class missing
    }

    verdict is PASS only when every one of CLASSES has at least one
    fixture. Any missing class makes the whole corpus NO-DATA: a partial
    corpus is not evidence that acceptance coverage is complete, so it is
    never read as a passing count for the classes it does have.
    """
    records = scan_corpus(corpus_root)
    by_class = {klass: [] for klass in CLASSES}
    for record in records:
        by_class[record.klass].append(record.fixture_id)
    classes = {klass: tuple(sorted(ids)) for klass, ids in by_class.items()}
    missing = tuple(sorted(k for k, ids in classes.items() if not ids))
    if missing:
        verdict = "NO-DATA"
        reason = "missing classes: %s" % (", ".join(missing),)
    else:
        verdict = "PASS"
        reason = "all %d classes covered" % (len(CLASSES),)
    return {
        "classes": classes,
        "missing_classes": missing,
        "verdict": verdict,
        "reason": reason,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--corpus-root", default=None,
        help="override the corpus directory (default: "
             "benchmarks/fixture_classes/corpus)")
    args = ap.parse_args(argv)

    try:
        report = coverage_report(args.corpus_root)
    except FixtureCorpusError as exc:
        print("fixture_classes: CANNOT-DECIDE, corpus defect: %s" % (exc,))
        return 2

    for klass in sorted(CLASSES):
        ids = report["classes"][klass]
        state = "covered (%s)" % (", ".join(ids),) if ids else "uncovered"
        print("fixture_classes: %-18s %s" % (klass, state))
    print("fixture_classes: %s, %s" % (report["verdict"], report["reason"]))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
