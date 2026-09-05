#!/usr/bin/env python3
"""gauntlet_hostile_ja: EXECUTE the frozen Hostile Japanese Identity gauntlet.

The specification is benchmarks/gauntlets/hostile-japanese-identity.json
(switching strategy section 20.5), frozen 2026-09-05. This runner does not
author cases and does not touch the corpus: the spec's own cases are the 78
cases of the FROZEN blind corpus at
benchmarks/ja-adversarial/adversarial-ja-corpus.json, and the spec's own
scoring rubric and per class floors are what score them. Section 9 of the
2026-09-05 morning steering directive forbids adding retrieval cases, so
none are added here.

WHAT THE SPEC ACTUALLY CONTAINS, stated because it matters: the spec carries
no "cases" array. It describes its cases in prose (workload_families: 12
hostile Japanese identity/disambiguation tasks; seeded_conditions: the ten
Zone 6 traps; seeded_conditions_note: those traps already live in the frozen
blind corpus). So the executable reading is the frozen corpus, run through
the shipped harness, scored per class against the harness's own floors,
plus the five bars of the spec's win_condition. Exactly that and no more.

WHAT IT RUNS
  Bar 1  standard Japanese benchmark, the shipped 245 case fixture, through
         products/brothermode/tools/bm_vault_jbench.py in process.
  Bar 2  frozen blind adversarial corpus, 78 cases, same harness.
  Bar 3  negative/disambiguation class of that same run.
  Bar 4  fresh unseen qualification. The spec says NO INSTRUMENT YET, and
         nothing on this estate authors an unseen corpus, so this bar reads
         NO-DATA and is never inferred from the frozen corpus score, which
         is the spec's own rubric rule.
  Bar 5  mutation sensitivity. The spec was frozen saying NO INSTRUMENT YET;
         scripts/test_ja_mutations.py landed afterwards and is run here, so
         the report states the current truth rather than the frozen sentence.
         If that file is absent the bar reads NO-DATA naming it.

COUNTING. n is the number of corpus cases actually scored. A class with zero
cases is NO-DATA: excluded from n, reported, and never counted as a pass.

EXIT CONTRACT
  0  every class with cases met its own declared floor.
  1  at least one class fell below its floor (the negative class is the
     critical one this gauntlet exists to measure, and is named as such).
  3  n is 0: no case was scored, so there is nothing to pass. A missing
     spec or a missing corpus lands here, with the missing file named.

Python 3.9, standard library only. No em or en dashes anywhere in this file.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gauntlet_frozen  # noqa: E402

SPEC_PATH = os.path.join(
    ROOT, "benchmarks", "gauntlets", "hostile-japanese-identity.json")
CORPUS_PATH = os.path.join(
    ROOT, "benchmarks", "ja-adversarial", "adversarial-ja-corpus.json")
JBENCH_PATH = os.path.join(
    ROOT, "products", "brothermode", "tools", "bm_vault_jbench.py")
MUTATION_PATH = os.path.join(ROOT, "scripts", "test_ja_mutations.py")
RESULTS_DIR = os.path.join(ROOT, "benchmarks", "results")

#: The class the win condition holds to 100 percent and the whole gauntlet is
#: named after. A failure anywhere fails the run; a failure here is called out.
CRITICAL_CLASS = "negative"

NO_INSTRUMENT = "NO INSTRUMENT YET"


# ---------------------------------------------------------------- counting
# Pure, harness free, so test_gauntlet_hostile_ja.py can drive it with a fake
# harness: all hits, all misses, and a NO-DATA class excluded from n.

def score_classes(per_class, floors):
    """Score {class: (hits, total) or None} against {class: floor}.

    Returns a dict with: rows (one per class, floors first then any
    undeclared class), n (cases actually scored, NO-DATA excluded), hits,
    misses, nodata (class names with no cases), below_floor (class names
    under their own floor). A class with no floor is UNDECLARED and counts
    as below_floor, never as a silent pass, mirroring the shipped harness.
    """
    rows = []
    nodata = []
    below_floor = []
    n = 0
    hits = 0
    for cls in sorted(floors) + sorted(set(per_class) - set(floors)):
        if cls not in per_class:
            continue
        result = per_class[cls]
        floor = floors.get(cls)
        if result is None or result[1] == 0:
            nodata.append(cls)
            rows.append({"class": cls, "hits": None, "total": 0,
                         "floor": floor, "verdict": "NO-DATA"})
            continue
        cls_hits, total = result
        n += total
        hits += cls_hits
        rate = cls_hits / total
        if floor is None:
            verdict = "UNDECLARED CLASS (no floor set)"
            below_floor.append(cls)
        elif rate >= floor:
            verdict = "OK"
        else:
            verdict = "BELOW FLOOR"
            below_floor.append(cls)
        rows.append({"class": cls, "hits": cls_hits, "total": total,
                     "floor": floor, "rate": rate, "verdict": verdict})
    return {"rows": rows, "n": n, "hits": hits, "misses": n - hits,
            "nodata": nodata, "below_floor": below_floor}


def exit_code(scored):
    """3 when nothing was scored, 1 when a class fell short, else 0."""
    if scored["n"] == 0:
        return 3
    return 1 if scored["below_floor"] else 0


# ------------------------------------------------------------------ harness

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def real_harness(cases_path):
    """(per_class, overall, detail) from an in process run of the shipped
    harness, or (None, None, "NO-DATA: ...") when it cannot run."""
    if not os.path.isfile(JBENCH_PATH):
        return None, None, "NO-DATA: no harness at %s" % JBENCH_PATH
    jb = _load("bm_vault_jbench", JBENCH_PATH)
    bm = jb._load_bm_vault()
    fixture, err = jb.load_fixture(cases_path)
    if err:
        return None, None, err
    return jb.run_benchmark(bm, fixture)


def harness_floors():
    if not os.path.isfile(JBENCH_PATH):
        return {}
    return dict(_load("bm_vault_jbench", JBENCH_PATH).CLASS_FLOORS)


# ------------------------------------------------------------- provenance

def _vcs(*args):
    try:
        out = subprocess.run(["git", "-C", ROOT] + list(args),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def provenance():
    """The vault revision and the corpus commit the rubric demands beside
    every published score. Either one absent makes the score NO-DATA by that
    rubric, so both are reported rather than quietly omitted."""
    sha1 = None
    if os.path.isfile(CORPUS_PATH):
        with open(CORPUS_PATH, "rb") as fh:
            sha1 = hashlib.sha1(fh.read()).hexdigest()[:12]
    return {
        "tree_revision": _vcs("rev-parse", "HEAD"),
        "tree_dirty": bool(_vcs("status", "--porcelain")),
        "corpus_path": os.path.relpath(CORPUS_PATH, ROOT),
        "corpus_commit": _vcs("log", "-1", "--format=%h", "--", CORPUS_PATH),
        "corpus_sha1": sha1,
    }


# ------------------------------------------------------------------- bars

def _pct(hits, total):
    return (100.0 * hits / total) if total else 0.0


def _spec_measure(spec, name):
    for m in spec.get("measures_outside_the_fifteen", []):
        if m.get("measure") == name:
            return m
    return {}


def bar_standard_benchmark():
    per_class, overall, detail = real_harness(
        os.path.join(os.path.dirname(JBENCH_PATH), "fixtures",
                     "japanese-benchmark.json"))
    if per_class is None:
        return {"bar": "standard Japanese benchmark: 100%",
                "verdict": "NO-DATA", "detail": detail}
    hits, total = overall
    return {"bar": "standard Japanese benchmark: 100%",
            "verdict": "MET" if total and hits == total else "NOT MET",
            "measured": "%d/%d (%.0f%%)" % (hits, total, _pct(hits, total)),
            "instrument": "products/brothermode/tools/bm_vault_jbench.py"}


def bar_blind_corpus(scored):
    hits, n = scored["hits"], scored["n"]
    if n == 0:
        return {"bar": "frozen blind adversarial benchmark: 100%",
                "verdict": "NO-DATA",
                "detail": "no case was scored on %s" % CORPUS_PATH}
    return {"bar": "frozen blind adversarial benchmark: 100%",
            "verdict": "MET" if hits == n else "NOT MET",
            "measured": "%d/%d (%.0f%%)" % (hits, n, _pct(hits, n)),
            "instrument": "products/brothermode/tools/bm_vault_jbench.py"}


def bar_negative_class(scored):
    for row in scored["rows"]:
        if row["class"] != CRITICAL_CLASS:
            continue
        if row["hits"] is None:
            return {"bar": "negative/disambiguation class: 100%",
                    "verdict": "NO-DATA",
                    "detail": "the %s class carries no cases" % CRITICAL_CLASS}
        hits, total = row["hits"], row["total"]
        return {"bar": "negative/disambiguation class: 100%",
                "verdict": "MET" if hits == total else "NOT MET",
                "measured": "%d/%d (%.0f%%)" % (hits, total, _pct(hits, total)),
                "instrument": "products/brothermode/tools/bm_vault_jbench.py"}
    return {"bar": "negative/disambiguation class: 100%", "verdict": "NO-DATA",
            "detail": "the corpus declares no %s class" % CRITICAL_CLASS}


def bar_fresh_qualification(spec):
    """The spec says NO INSTRUMENT YET and nothing on this estate authors an
    unseen corpus. Reported NO-DATA with the spec's own reason, never
    inferred from the frozen corpus score."""
    m = _spec_measure(spec, "fresh unseen qualification")
    instrument = m.get("instrument", NO_INSTRUMENT)
    if instrument != NO_INSTRUMENT:
        return {"bar": "fresh unseen qualification: 100%",
                "verdict": "NO-DATA",
                "detail": "the spec now names %r; this runner does not drive "
                          "it, so the bar is unmeasured here" % instrument}
    return {"bar": "fresh unseen qualification: 100%", "verdict": "NO-DATA",
            "detail": m.get("would_require", NO_INSTRUMENT),
            "instrument": NO_INSTRUMENT}


def bar_mutation_sensitivity():
    if not os.path.isfile(MUTATION_PATH):
        return {"bar": "mutation tests prove the mechanism matters",
                "verdict": "NO-DATA",
                "detail": "no mutation harness at %s"
                          % os.path.relpath(MUTATION_PATH, ROOT),
                "instrument": NO_INSTRUMENT}
    try:
        out = subprocess.run([sys.executable, MUTATION_PATH], cwd=ROOT,
                             capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        return {"bar": "mutation tests prove the mechanism matters",
                "verdict": "NO-DATA",
                "detail": "the mutation harness could not be run (%s)" % e}
    proven = nodata = None
    for line in out.stdout.splitlines():
        if line.startswith("mechanisms PROVEN by the benchmark:"):
            proven = line.split(":")[1].strip()
        elif line.startswith("mechanisms reported NO-DATA:"):
            nodata = line.split(":")[1].strip()
    if out.returncode != 0 or proven is None:
        return {"bar": "mutation tests prove the mechanism matters",
                "verdict": "NOT MET",
                "detail": "scripts/test_ja_mutations.py exited %d"
                          % out.returncode}
    return {"bar": "mutation tests prove the mechanism matters",
            "verdict": "MET" if int(proven) > 0 else "NOT MET",
            "measured": "%s mechanism(s) PROVEN, %s NO-DATA" % (proven, nodata),
            "instrument": "scripts/test_ja_mutations.py",
            "note": "the frozen spec predates this instrument and says "
                    "NO INSTRUMENT YET for this measure"}


# ------------------------------------------------------------------- run

def load_spec():
    if not os.path.isfile(SPEC_PATH):
        return None, "NO-DATA: no frozen spec at %s" % SPEC_PATH
    try:
        with open(SPEC_PATH, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as e:
        return None, "NO-DATA: %s is not readable JSON (%s)" % (SPEC_PATH, e)


def summary_line(scored, bars, prov, code):
    met = sum(1 for b in bars if b["verdict"] == "MET")
    nod = sum(1 for b in bars if b["verdict"] == "NO-DATA")
    return ("gauntlet hostile-japanese-identity: n=%d cases, %d hit, %d miss, "
            "%d NO-DATA class(es); classes %d OK %d below floor; bars %d of %d "
            "MET %d NO-DATA; tree %s%s corpus %s@%s -> %s"
            % (scored["n"], scored["hits"], scored["misses"],
               len(scored["nodata"]),
               len([r for r in scored["rows"] if r["verdict"] == "OK"]),
               len(scored["below_floor"]), met, len(bars), nod,
               (prov["tree_revision"] or "UNKNOWN")[:8],
               " (dirty)" if prov["tree_dirty"] else "",
               prov["corpus_sha1"] or "UNKNOWN",
               prov["corpus_commit"] or "UNKNOWN",
               {0: "PASS", 1: "FAIL", 3: "NO-DATA"}[code]))


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quiet", action="store_true",
                   help="suppress the per case lines")
    p.add_argument("--no-record", action="store_true",
                   help="score and print without writing the JSON record")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)

    spec, err = load_spec()
    if err:
        print(err)
        print("gauntlet hostile-japanese-identity: n=0 cases -> NO-DATA")
        return 3
    if not os.path.isfile(CORPUS_PATH):
        print("NO-DATA: the frozen corpus is not on disk at %s" % CORPUS_PATH)
        print("gauntlet hostile-japanese-identity: n=0 cases -> NO-DATA")
        return 3

    try:
        frozen_result = gauntlet_frozen.check(SPEC_PATH)
    except ValueError as exc:
        print(str(exc))
        return 1
    if frozen_result.startswith("NO-DATA"):
        print(frozen_result)
        print("gauntlet hostile-japanese-identity: n=0 cases -> NO-DATA")
        return 3
    print("frozen: OK %s" % frozen_result)

    print("gauntlet: %s (%s), frozen %s"
          % (spec.get("name"), spec.get("id"), spec.get("frozen_at")))
    prov = provenance()
    print("tree revision: %s%s" % (prov["tree_revision"],
                                   " (DIRTY WORKING TREE)"
                                   if prov["tree_dirty"] else ""))
    print("corpus: %s, sha1 %s, commit %s"
          % (prov["corpus_path"], prov["corpus_sha1"], prov["corpus_commit"]))

    per_class, _overall, detail = real_harness(CORPUS_PATH)
    if per_class is None:
        print(detail)
        print("gauntlet hostile-japanese-identity: n=0 cases -> NO-DATA")
        return 3

    floors = harness_floors()
    scored = score_classes(per_class, floors)

    if not args.quiet:
        print("cases (one line each, %d):" % len(detail))
        for line in detail:
            print(line)

    print("per class, against the harness's own frozen floors:")
    for row in scored["rows"]:
        if row["verdict"] == "NO-DATA":
            print("  %-22s NO-DATA (0 cases; excluded from n, never a pass)"
                  % row["class"])
            continue
        floor_txt = ("floor %.0f%%" % (row["floor"] * 100)
                     if row["floor"] is not None else "no floor declared")
        crit = "  [CRITICAL CLASS]" if row["class"] == CRITICAL_CLASS else ""
        print("  %-22s %d/%d (%.0f%%), %s  %s%s"
              % (row["class"], row["hits"], row["total"], row["rate"] * 100,
                 floor_txt, row["verdict"], crit))

    bars = [bar_standard_benchmark(), bar_blind_corpus(scored),
            bar_negative_class(scored), bar_fresh_qualification(spec),
            bar_mutation_sensitivity()]
    print("win condition, section 7 Zone 6, five bars:")
    for bar in bars:
        extra = bar.get("measured") or bar.get("detail") or ""
        print("  %-8s %s" % (bar["verdict"], bar["bar"]))
        if extra:
            print("           %s" % extra)
    print("claim scope: the measured qualification above and nothing wider. "
          "No claim of universal Japanese ability is made.")

    code = exit_code(scored)
    line = summary_line(scored, bars, prov, code)
    if not args.no_record:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        stamp = datetime.date.today().isoformat()
        record = os.path.join(RESULTS_DIR, "hostile-ja-%s.json" % stamp)
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({
                "gauntlet": spec.get("id"),
                "spec": os.path.relpath(SPEC_PATH, ROOT),
                "run_at": datetime.datetime.now().astimezone().isoformat(),
                "provenance": prov,
                "n": scored["n"], "hits": scored["hits"],
                "misses": scored["misses"], "nodata_classes": scored["nodata"],
                "below_floor": scored["below_floor"],
                "per_class": scored["rows"],
                "cases": [l.strip() for l in detail],
                "bars": bars,
                "exit_code": code,
                "summary": line,
            }, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print("record: %s" % os.path.relpath(record, ROOT))
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
