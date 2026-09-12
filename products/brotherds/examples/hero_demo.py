#!/usr/bin/env python3
"""BrotherDS hero demo: the release proof, run as a stranger would run it.

Builds every fixture it needs in a fresh temp directory, shells out to bds.py
exactly the way a person on the command line would (never imports its private
functions), and prints one short deterministic line per beat: a SYSTEM number
that reproduces from two independent derivations, a causal overclaim that
fails on its wording alone, the safe-wording rewrite the receipt carries, a
forecast held, a forecast missed, a forecast that declined to state an
interval, the ledger over those three, and the ledger over nothing at all.

Compares its own output against the expected file that matches this
interpreter's own capability (examples/hero_expected.txt when duckdb is
importable here, examples/hero_expected-noduckdb.txt when it is not) and, on
any mismatch, prints a unified diff and exits 1, so a change in bds.py that
quietly breaks a beat shows up here before it reaches a person.

Usage: python3 examples/hero_demo.py   (no arguments; works from any cwd)
"""
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

HERE = Path(__file__).resolve().parent
BDS = HERE.parent / "bds.py"


class DemoFailure(Exception):
    """Raised when bds.py itself did not do what this demo expected of it.
    Never caught silently: the point of this script is to refuse to fake a
    beat it could not actually reproduce."""


def run_bds(args, cwd):
    proc = subprocess.run([sys.executable, str(BDS)] + list(args),
                          cwd=str(cwd), capture_output=True, text=True)
    return proc.stdout


def gate_verdict(output, gate):
    """The verdict token bds.py printed for one named gate, or None if that
    gate never appears in this output at all."""
    pattern = re.compile(r"^\s*(PASS|FAIL|NO-DATA)\s+%s\b" % re.escape(gate),
                         re.M)
    m = pattern.search(output)
    return m.group(1) if m else None


def outcome_state(output):
    m = re.search(r"^OUTCOME (\S+)", output, re.M)
    return m.group(1) if m else None


def rate_state(output):
    m = re.search(r"VERIFIED CLAIM RATE\s+(\S+)", output)
    return m.group(1) if m else None


def duckdb_available():
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return False
    return True


def make_synthetic_db(db_path):
    """A tiny throwaway warehouse: two independently structured tables that
    both roll up to 1,200 (5 rows of 240 vs 4 rows of 300, for the number
    beat), plus a one-row table for the causal beat's own derivation."""
    import duckdb
    con = duckdb.connect(str(db_path))
    try:
        con.execute("create table orders(qty integer)")
        con.executemany("insert into orders values (?)", [(240,)] * 5)
        con.execute("create table orders_weekly(qty integer)")
        con.executemany("insert into orders_weekly values (?)", [(300,)] * 4)
        con.execute("create table campaign_delta(pct double)")
        con.execute("insert into campaign_delta values (8.4)")
    finally:
        con.close()


def load_fixture(name):
    return json.loads((HERE / name).read_text())


def write_claim(tmp, name, claim):
    p = tmp / name
    p.write_text(json.dumps(claim, indent=2, ensure_ascii=False) + "\n")
    return p


def line_number(tmp, db_path):
    """SYSTEM claim, value 1,200, re-derived two independent ways against the
    synthetic warehouse. NO-DATA on this machine's python if duckdb is not
    importable there: the check never runs a derivation it cannot really run,
    and never claims a PASS it did not earn."""
    if not duckdb_available():
        return "Number: NO-DATA (duckdb not installed)"
    claim = load_fixture("hero-number.json")
    claim["evidence"]["source"] = str(db_path)
    claim_path = write_claim(tmp, "hero-number.json", claim)
    out = run_bds(["check", str(claim_path)], cwd=tmp)
    g5 = gate_verdict(out, "G5.rederivation")
    g7 = gate_verdict(out, "G7.value")
    if g5 == PASS and g7 == PASS:
        return "Number: PASS"
    raise DemoFailure(
        "synthetic number claim did not reproduce as built: "
        "G5.rederivation=%r G7.value=%r\n%s" % (g5, g7, out))


def line_causality(tmp, db_path):
    """SYSTEM claim carrying 'drove', no design block. G4.causal must refuse
    it on the wording alone, whatever the derivations say about the number."""
    claim = load_fixture("hero-causal.json")
    claim["evidence"]["source"] = str(db_path)
    claim_path = write_claim(tmp, "hero-causal.json", claim)
    out = run_bds(["check", str(claim_path)], cwd=tmp)
    g4 = gate_verdict(out, "G4.causal")
    if g4 != FAIL:
        raise DemoFailure(
            "expected G4.causal FAIL on an undesigned causal statement, "
            "got %r\n%s" % (g4, out))
    return "Causality: FAIL", claim_path


def line_safe_wording(tmp, causal_claim_path):
    """The compact card's own safe-wording line, read back from the receipt
    file bds.py wrote, never recomputed independently."""
    dest = tmp / "hero-causal-receipt.md"
    run_bds(["receipt", str(causal_claim_path), str(dest)], cwd=tmp)
    if not dest.exists():
        raise DemoFailure("bds.py receipt did not write %s" % dest)
    text = dest.read_text()
    m = re.search(r"^- \*\*Safe wording \(recommended\):\*\* (.+)$", text, re.M)
    if not m:
        raise DemoFailure("receipt carries no Safe wording line:\n%s" % text)
    wording = m.group(1)
    if "8.4" not in wording:
        raise DemoFailure("safe wording dropped the digits 8.4: %r" % wording)
    return "Safe wording: %s" % wording


def line_score(tmp, fixture_name, actual, observed_on):
    """Runs bds.py score on a temp copy of the named fixture and reports the
    state it printed, never the state this demo assumed it would get."""
    claim = load_fixture(fixture_name)
    dest = write_claim(tmp, fixture_name, claim)
    out = run_bds(["score", str(dest), str(actual), "hero-demo",
                   observed_on], cwd=tmp)
    state = outcome_state(out)
    if state is None:
        raise DemoFailure("score printed no OUTCOME line for %s:\n%s"
                          % (fixture_name, out))
    return "Score %s: %s" % (state, claim["id"]), dest


def line_ledger(tmp, scored_paths):
    """The ledger's own printed lines over the three just-scored claims,
    copied through verbatim rather than reconstructed by hand."""
    ledger_dir = tmp / "ledger-claims"
    ledger_dir.mkdir()
    for p in scored_paths:
        shutil.copy(str(p), str(ledger_dir / p.name))
    out = run_bds(["ledger", str(ledger_dir)], cwd=tmp)
    return out.rstrip("\n").splitlines()


def line_ledger_empty(tmp):
    """The north star over a directory holding nothing at all: no numerator,
    no denominator, and the ledger must say so rather than print a zero."""
    empty_dir = tmp / "empty-claims"
    empty_dir.mkdir()
    out = run_bds(["ledger", str(empty_dir)], cwd=tmp)
    state = rate_state(out)
    return "Ledger on empty dir: %s" % state


def build_actual_lines():
    lines = []
    with tempfile.TemporaryDirectory(prefix="hero-demo-") as tmp_s:
        tmp = Path(tmp_s)
        db_path = tmp / "hero-demo.duckdb"

        # The number and causality beats both point evidence.source at this
        # file, so it must actually exist before either check runs. Built
        # only when duckdb is importable here: on an interpreter without it,
        # line_number short-circuits to NO-DATA before ever touching db_path,
        # and line_causality's own gate (G4.causal) never opens the source.
        if duckdb_available():
            make_synthetic_db(db_path)

        lines.append(line_number(tmp, db_path))

        causal_line, causal_claim_path = line_causality(tmp, db_path)
        lines.append(causal_line)
        lines.append(line_safe_wording(tmp, causal_claim_path))

        held_line, held_path = line_score(
            tmp, "hero-forecast-held.json", 500, "2026-09-10")
        missed_line, missed_path = line_score(
            tmp, "hero-forecast-missed.json", 900, "2026-09-10")
        unscoreable_line, unscoreable_path = line_score(
            tmp, "hero-forecast-unscoreable.json", 500, "2026-09-10")
        lines.append(held_line)
        lines.append(missed_line)
        lines.append(unscoreable_line)

        lines.extend(line_ledger(tmp, [held_path, missed_path,
                                       unscoreable_path]))
        lines.append(line_ledger_empty(tmp))
    return lines


def main():
    try:
        actual = build_actual_lines()
    except DemoFailure as exc:
        sys.stdout.write("DEMO FAILED: %s\n" % exc)
        return 1

    actual_text = "\n".join(actual) + "\n"

    # Which expected file is the right comparison depends on what this
    # interpreter can actually do, never on which file happens to exist:
    # the PASS branch and the NO-DATA branch are two different, both
    # honest, outcomes and each gets its own fixture.
    expected_name = ("hero_expected.txt" if duckdb_available()
                     else "hero_expected-noduckdb.txt")
    expected_path = HERE / expected_name
    expected_text = expected_path.read_text() if expected_path.exists() else ""

    if actual_text == expected_text:
        sys.stdout.write(actual_text)
        sys.stdout.write("Compared against: examples/%s\n" % expected_name)
        return 0

    diff = difflib.unified_diff(
        expected_text.splitlines(keepends=True),
        actual_text.splitlines(keepends=True),
        fromfile="examples/%s" % expected_name, tofile="actual stdout")
    sys.stdout.write("".join(diff))
    return 1


if __name__ == "__main__":
    sys.exit(main())
