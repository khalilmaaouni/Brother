#!/usr/bin/env python3
"""The DuckDB half of the unit ledger: one view over the JSONL rows unit_ledger.py writes, and the four rollups the loop's
decisions read (pass rate by model, by unit class, by round, the refusal reason histogram).
Kept OUT of unit_ledger.py on purpose: unit_ledger is imported by land_batch and so sits inside the reviewed runtime
surface, whose dependency census allows no third party import. This module imports nothing from the loop and nothing
imports it; it is a standalone tool run by hand.
usage (repo root):  unit_ledger_rollups.py [--out FILE.jsonl] [--db FILE.duckdb]
                    unit_ledger_rollups.py --selftest
Python without duckdb prints NO-DATA and exits 3 (never a pass, never an empty table read as zero)."""
import argparse, json, os, sys, tempfile
ROLLUPS = [("by model", "actual_model"), ("by unit class", "unit_class"), ("by round", "round"), ("by refusal reason", "refusal")]


def rollups(out_path, db_path):
    """Create or refresh the view in db_path over out_path and return {title: [(key, builds, passes, pass_rate, cost)]}.
    Raises ImportError when duckdb is absent: the caller says NO-DATA."""
    import duckdb  # noqa: the whole point of the tool; absent means NO-DATA, decided by the caller
    con = duckdb.connect(db_path)
    # a CREATE VIEW cannot take a prepared parameter (duckdb binder), so the path is quoted by hand
    con.execute("CREATE OR REPLACE VIEW unit_runs AS SELECT * FROM read_json_auto('%s', format='newline_delimited')" % os.path.abspath(out_path).replace("'", "''"))
    out = {}
    for title, col in ROLLUPS:
        where = " WHERE grade = 'FAIL' AND refusal <> ''" if col == "refusal" else ""
        out[title] = con.execute("SELECT CAST(%s AS VARCHAR) k, count(*) n, sum(CASE WHEN grade='PASS' THEN 1 ELSE 0 END) p, "
                                 "round(100.0 * sum(CASE WHEN grade='PASS' THEN 1 ELSE 0 END) / count(*), 1) rate, round(coalesce(sum(TRY_CAST(cost_usd AS DOUBLE)), 0), 2) usd "
                                 "FROM (SELECT * FROM unit_runs QUALIFY row_number() OVER (PARTITION BY run, round, build ORDER BY coalesce(\"at\", 0) DESC) = 1)%s GROUP BY 1 ORDER BY n DESC, k" % (col, where)).fetchall()
    con.close(); return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/.claude/evidence/unit-ledger.jsonl")); ap.add_argument("--db", default=os.path.expanduser("~/.claude/evidence/unit-ledger.duckdb"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest: return selftest()
    if not os.path.isfile(a.out): print("NO-DATA: no rows file at %s (run unit_ledger.py first)" % a.out); return 3
    try: tables = rollups(a.out, a.db)
    except ImportError: print("NO-DATA: duckdb is not importable under %s (/usr/bin/python3 carries duckdb)" % sys.executable); return 3
    print("VIEW unit_runs in %s" % a.db)
    for title, table in tables.items():
        print("\n%-24s %6s %6s %6s %8s" % (title.upper(), "builds", "pass", "rate%", "usd"))
        for k, n, p, rate, usd in table[:12]: print("%-24s %6d %6d %6s %8.2f" % ((k or "")[:24], n, p, rate, usd))
    return 0


def selftest():
    """A case that RAISES has not reported a verdict: anything that escapes prints FAILED and exits 1."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    d = tempfile.mkdtemp(prefix="unit-ledger-rollups-"); out = os.path.join(d, "rows.jsonl")
    def row(build, model, cls, grade, refusal="", cost=None):
        return {"run": "R-1", "round": 0, "build": build, "actual_model": model, "unit_class": cls, "grade": grade, "refusal": refusal, "cost_usd": cost, "at": 1.0}
    rs = [row("a", "deepseek/v", "runner", "FAIL", "suite not green with the code", 0.03), row("b", "deepseek/v", "runner", "PASS"),
          row("c", "muse/v", "docs", "FAIL", "safety screen"), row("d", "muse/v", "docs", "FAIL", "N patch problems"),
          row("e", "muse/v", "docs", "NO-DATA"), row("f", "muse/v", "docs", "FAIL")]
    with open(out, "w") as fh:
        for r in rs: fh.write(json.dumps(r) + "\n")
    try:
        tb = rollups(out, os.path.join(d, "t.duckdb"))
    except ImportError:
        print("selftest: duckdb not importable under %s: the view cases are NO-DATA here (run under /usr/bin/python3 for them)" % sys.executable); return 3
    m = {k: (n, p) for k, n, p, rate, usd in tb["by model"]}
    cases = [("view: pass rate by model counts builds and passes", m.get("deepseek/v") == (2, 1) and m.get("muse/v") == (4, 0)),
             ("view: refusal histogram lists only FAIL rows with a reason", sorted(k for k, *_ in tb["by refusal reason"]) == ["N patch problems", "safety screen", "suite not green with the code"]),
             ("view: cost sums what was reconciled", [usd for k, n, p, rate, usd in tb["by unit class"] if k == "runner"] == [0.03])]
    # cost_usd arrives as JSON shaped or text in some rows (arm A 2026-09-23 rows made duckdb infer JSON and sum() raised a
    # BinderException): the rollup casts, never crashes, and sums the numbers
    with open(out, "a") as fh:
        fh.write(json.dumps({"run": "X-1", "round": 0, "build": "X-r9", "unit": "X", "model": "muse/v", "grade": "FAIL", "refusal": "", "cost_usd": {"note": "none"}, "at": 1.0}) + "\n")
        fh.write(json.dumps({"run": "X-1", "round": 0, "build": "X-r8", "unit": "X", "model": "muse/v", "grade": "FAIL", "refusal": "", "cost_usd": "0.02", "at": 1.0}) + "\n")
    try:
        tb2 = rollups(out, os.path.join(d, "t2.duckdb")); ok2 = abs(sum(usd for k, n, p, rate, usd in tb2["by model"]) - 0.05) < 1e-6
    except Exception as exc:   # sbe: allow-silent the exception IS the case under test, named in the case title
        ok2 = False; print("rollup raised on mixed cost_usd: %s" % exc)
    cases.append(("view: a JSON shaped or text cost_usd never crashes the rollup and numbers still sum", ok2))
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(main())
