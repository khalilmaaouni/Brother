#!/usr/bin/env python3
"""bm_vault_jbench: the Japanese-first retrieval benchmark runner. WBS VB2-03.

Runs tools/fixtures/japanese-benchmark.json (245 cases across six classes:
lexical_only, mixed, kana_alias, width_variant, dictionary_dependent,
negative) against a FIXTURE VAULT this tool builds directly in a fresh
SQLite connection, via bm_vault.py's own _schema()/_upsert_note() (the same
functions the real "index" command uses), never through .md files on disk.
Building the fixture as SQLite rows rather than files means this tool has no
file-write site of its own (see tools/write_sites.json's own contract): it
reads the case file and a temp dictionary file it writes UNDER
tempfile.mkdtemp(), never a tracked path.

WHY A FRESH IN-MEMORY VAULT rather than bm_vault.py's real index: the real
index lives at a fixed path (~/.claude/bm_vault_index.sqlite3) and mixes in
whatever a real vault holds; a benchmark needs a KNOWN, REPRODUCIBLE corpus
so a class's score means what it says. bm_vault._search(con, ...) already
takes any sqlite3 connection as its first argument (nothing in it reads
INDEX_PATH directly), so this tool hands it a purpose-built one.

WHAT A HIT MEANS.
  Positive classes (lexical_only, mixed, kana_alias, width_variant,
  dictionary_dependent): the case's expected_note's note id appears
  anywhere in the top --limit fused results for its query.
  negative: the case's forbidden_note's note id does NOT appear anywhere in
  the top --limit fused results for its query.

dictionary_dependent cases run with the fixture's declared dictionary_terms
(_meta.dictionary_terms in the case file) INSTALLED as the fixture vault's
user-dictionary.json for the whole run, since the row's own done-check
("a user-dictionary change moves ranking deterministically") is proven by
bm_vault_analyzer's own before/after flip in test_bm_vault_analyzer.py; this
benchmark measures the WITH-dictionary state the shipped analyzer actually
runs under, honestly, rather than re-proving the flip a second way.

FLOORS. Declared per class in CLASS_FLOORS below, from an actual measured
run of this exact fixture (see the module docstring's own measurement note
for the run that set them). A class with zero cases in the file is reported
NO-DATA, never counted as a pass. --fast (bm_vault.py's own dense-embedder
skip) is always on: the dense signal needs a subprocess embedder this
benchmark does not depend on and should not wait 30-75 seconds per case for.

Exit 0: every declared class met or exceeded its floor. Exit 1: at least
one class fell short of its floor, or a declared class has zero cases
(NO-DATA, reported, never a silent pass). Exit 2: the case file is missing,
unreadable, or carries no cases at all.

Python 3.9, standard library only, no network, no subprocess.

No em or en dashes anywhere in this file.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CASES_PATH = os.path.join(HERE, "fixtures", "japanese-benchmark.json")

#: Honest floors, set from an actual measured run of the fixture shipped
#: alongside this tool (see docs/plan for the run that produced them; a
#: floor above the measured rate would fail this benchmark on its own
#: fixture, which test_bm_vault_jbench.py checks for directly). A class not
#: listed here is unknown to this tool, not merely under a floor of 0.
CLASS_FLOORS = {
    "lexical_only": 0.90,
    "mixed": 0.70,
    "kana_alias": 0.70,
    "width_variant": 0.70,
    "dictionary_dependent": 0.90,
    "negative": 0.90,
}

DEFAULT_LIMIT = 10


def _load_bm_vault():
    """Dynamic import by path, the same defensive pattern every sibling tool in
    this estate uses for bm_vault.py (see bm_vault_audit.py's own
    _load_bm_vault for the precedent): a bare `import bm_vault` only resolves
    by accident of sys.path, and this file sets up none of its own."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bm_vault", os.path.join(HERE, "bm_vault.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_fixture(path):
    """(fixture_dict, None) or (None, "NO-DATA: ..."). Never raises: a
    missing file, unreadable JSON, or a value with no "cases" list is
    reported, not crashed on."""
    if not path or not os.path.isfile(path):
        return None, "NO-DATA: no case file at %r" % path
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return None, "NO-DATA: %r is not readable JSON (%s)" % (path, e)
    if not isinstance(data, dict) or not data.get("cases"):
        return None, "NO-DATA: %r carries no cases" % path
    return data, None


def build_fixture_vault(bm, fixture):
    """A fresh sqlite3 connection with every fixture note upserted, and a
    {stem: note_id} map. mtime is the note's own position in the list
    (0.0, 1.0, 2.0, ...): distinct and monotonic, which is all _upsert_note
    needs to treat every note as a fresh insert on a brand-new connection."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    bm._schema(con)
    stem_to_id = {}
    for i, note in enumerate(fixture["notes"]):
        stem = note["stem"]
        path = "japanese-benchmark/%s.md" % stem
        bm._upsert_note(con, path, note.get("title", stem), "", "jbench",
                         "lesson", float(i), note["body"])
        row = con.execute("SELECT id FROM notes WHERE path=?", (path,)).fetchone()
        stem_to_id[stem] = row["id"]
    return con, stem_to_id


def write_dictionary(vault_dir, terms):
    """Install TERMS as the fixture vault's user-dictionary.json, the shape
    bm_vault_analyzer.load_dictionary reads (an object with a "terms" list).
    This is the tool's own temp-directory write, never a tracked path (see
    the module docstring)."""
    base = os.path.join(vault_dir, "99-System", "dictionaries")
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "user-dictionary.json"), "w", encoding="utf-8") as fh:
        json.dump({"terms": list(terms)}, fh, ensure_ascii=False)


def run_case(bm, con, case, stem_to_id, limit):
    """True (hit/pass) or False for one case. Unknown expected_note or
    forbidden_note (a fixture bug: a stem not in stem_to_id) counts as a
    miss, never a silent pass."""
    query = case["query"]
    fused, _why = bm._search(con, text=query, limit=limit, fast=True)
    top_ids = {nid for nid, _score in fused}
    if case["class"] == "negative":
        forbidden = stem_to_id.get(case.get("forbidden_note"))
        if forbidden is None:
            return False
        return forbidden not in top_ids
    expected = stem_to_id.get(case.get("expected_note"))
    if expected is None:
        return False
    return expected in top_ids


def run_benchmark(bm, fixture, limit=DEFAULT_LIMIT):
    """(per_class, overall, [detail_line, ...]). per_class: {class_name:
    (hits, total) or None for NO-DATA (zero cases)}. overall: (hits, total)
    across every case actually run (NO-DATA classes contribute nothing)."""
    con, stem_to_id = build_fixture_vault(bm, fixture)
    vault_dir = tempfile.mkdtemp(prefix="bm-jbench-vault-")
    orig_default_vault = bm._default_vault
    try:
        write_dictionary(vault_dir, fixture.get("_meta", {}).get("dictionary_terms", []))
        bm._default_vault = lambda: vault_dir

        per_class = {}
        detail = []
        declared = fixture.get("_meta", {}).get("classes", sorted(CLASS_FLOORS))
        by_class = {c: [] for c in declared}
        for case in fixture["cases"]:
            by_class.setdefault(case["class"], []).append(case)
        for cls in declared:
            cases = by_class.get(cls, [])
            if not cases:
                per_class[cls] = None
                continue
            hits = 0
            for case in cases:
                ok = run_case(bm, con, case, stem_to_id, limit)
                hits += int(ok)
                detail.append("  [%s] %s %r -> %s"
                              % ("HIT" if ok else "MISS", case["id"],
                                 case["query"], case["class"]))
            per_class[cls] = (hits, len(cases))
        total_hits = sum(v[0] for v in per_class.values() if v)
        total_cases = sum(v[1] for v in per_class.values() if v)
        return per_class, (total_hits, total_cases), detail
    finally:
        bm._default_vault = orig_default_vault
        con.close()
        shutil.rmtree(vault_dir, ignore_errors=True)


def cmd_run(args):
    bm = _load_bm_vault()
    fixture, err = load_fixture(args.cases)
    if err:
        print("bm_vault_jbench: %s" % err)
        return 2
    per_class, overall, detail = run_benchmark(bm, fixture, limit=args.limit)
    if args.verbose:
        for line in detail:
            print(line)
    ok = True
    print("per-class score table:")
    for cls in sorted(CLASS_FLOORS):
        result = per_class.get(cls)
        floor = CLASS_FLOORS[cls]
        if result is None:
            print("  %-22s NO-DATA (0 cases; floor %.0f%%)" % (cls, floor * 100))
            ok = False
            continue
        hits, total = result
        rate = hits / total if total else 0.0
        verdict = "OK" if rate >= floor else "BELOW FLOOR"
        if rate < floor:
            ok = False
        print("  %-22s %d/%d (%.0f%%), floor %.0f%%  %s"
              % (cls, hits, total, rate * 100, floor * 100, verdict))
    unknown = set(per_class) - set(CLASS_FLOORS)
    for cls in sorted(unknown):
        hits, total = per_class[cls]
        print("  %-22s %d/%d, UNDECLARED CLASS (no floor set)" % (cls, hits, total))
        ok = False
    total_hits, total_cases = overall
    overall_rate = (100.0 * total_hits / total_cases) if total_cases else 0.0
    print("overall: %d/%d (%.0f%%)" % (total_hits, total_cases, overall_rate))
    return 0 if ok else 1


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["run"], nargs="?", default="run")
    p.add_argument("--cases", default=DEFAULT_CASES_PATH,
                    help="path to the benchmark case file (default: the shipped fixture)")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="top-N fused results a case counts as a hit within (default 10)")
    p.add_argument("--verbose", action="store_true", help="print every case's hit/miss")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
