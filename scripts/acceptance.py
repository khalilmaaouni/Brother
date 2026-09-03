#!/usr/bin/env python3
"""Acceptance harness for the eleven capability areas (G1-M3.2 of
docs/plan/READINESS-ROADMAP-2026-08-29.json, node G1-M3).

Loads docs/plan/CAPABILITY-AREAS.json and, for each area, runs the
per-area test script named by convention: scripts/acceptance_<id>.py.
That script is one process, one area, and its own exit code is read as:

    0   PASS
    2   NO-DATA
    any other code   FAIL

An area with no per-area script yet is NO-DATA by name, never a pass and
never silently omitted from the printed table. As of G1-M3.3/M3.4, area 1
has a real test (scripts/acceptance_1.py); the other ten still report
NO-DATA because their own subtasks have not landed yet.

Exit contract: nonzero only on FAIL. NO-DATA never flips the exit code,
per this estate's NO-DATA law (see scripts/leaf_pin_check.py and
scripts/coverage_check.py for the sibling form of the same law): a run
of all NO-DATA has not said anything is broken, so it must not read as a
failure any more than it may read as a pass.

--area ID restricts the run to one area. --explain (G1-M3.3.2) and
--calibrate (G1-M3.4.2) both require --area and are forwarded to that
area's own script: --explain asks it to print the template it leaves for
later areas, --calibrate asks it to prove it can actually go red.
"""
import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
AREAS_FILE = ROOT / "docs" / "plan" / "CAPABILITY-AREAS.json"
SCRIPTS_DIR = ROOT / "scripts"


def load_areas(areas_file=None):
    # ponytail: "areas_file or AREAS_FILE" reads the module global at CALL
    # time, not at definition time. A plain `areas_file=AREAS_FILE` default
    # would bind the value once, when this function object was created, and
    # a test patching acceptance.AREAS_FILE afterward would have no effect.
    with open(areas_file or AREAS_FILE) as f:
        return json.load(f)


def run_area(area, scripts_dir=None, extra_args=None):
    """Run one area's per-area script. Returns (verdict, evidence).

    extra_args is forwarded to the per-area script's own argv (used for
    --explain and --calibrate), so it must be a flag that script itself
    understands."""
    scripts_dir = scripts_dir or SCRIPTS_DIR  # see load_areas' note above
    script = scripts_dir / "acceptance_{}.py".format(area["id"])
    if not script.exists():
        return "NO-DATA", "no test yet: {} not found".format(
            script.relative_to(ROOT) if scripts_dir == SCRIPTS_DIR else script.name)
    try:
        out = subprocess.run([sys.executable, str(script)] + list(extra_args or []),
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return "NO-DATA", "could not run {}: {}".format(script.name, exc)
    if extra_args:
        # --explain prints its template ahead of the verdict line; show all
        # of it rather than only the last line, which is the point of asking.
        print(out.stdout.rstrip())
    lines = out.stdout.strip().splitlines()
    evidence = lines[-1][:200] if lines else "(no output)"
    if out.returncode == 0:
        return "PASS", evidence
    if out.returncode == 2:
        return "NO-DATA", evidence
    return "FAIL", evidence


def run_all(areas, scripts_dir=None, extra_args=None):
    return [(area["id"], area["name"]) + run_area(area, scripts_dir, extra_args)
            for area in areas]


def selftest():
    """Fixture proof that PASS, FAIL and NO-DATA are all reachable, using
    throwaway scripts rather than the real (currently mostly-absent)
    per-area tests."""
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "acceptance_p.py").write_text("print('fixture pass')\nraise SystemExit(0)\n")
        (tmp / "acceptance_f.py").write_text("print('fixture fail')\nraise SystemExit(1)\n")
        (tmp / "acceptance_n.py").write_text("print('fixture no-data')\nraise SystemExit(2)\n")
        cases = [
            ({"id": "p", "name": "pass-fixture"}, "PASS"),
            ({"id": "f", "name": "fail-fixture"}, "FAIL"),
            ({"id": "n", "name": "no-data-fixture"}, "NO-DATA"),
            ({"id": "missing", "name": "missing-fixture"}, "NO-DATA"),
        ]
        for area, expect in cases:
            verdict, evidence = run_area(area, scripts_dir=tmp)
            if verdict != expect:
                print("SELFTEST FAIL: area {} expected {} got {} ({})".format(
                    area["id"], expect, verdict, evidence))
                ok = False
    if ok:
        print("selftest OK: PASS, FAIL and NO-DATA are all reachable")
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the eleven capability-area acceptance tests.")
    parser.add_argument("--selftest", action="store_true",
                         help="run the built-in fixtures instead of the real areas")
    parser.add_argument("--area", default=None,
                         help="run only the area with this id (see "
                             "docs/plan/CAPABILITY-AREAS.json)")
    parser.add_argument("--explain", action="store_true",
                         help="also print the area's own explanation of its "
                             "shape; only valid together with --area")
    parser.add_argument("--calibrate", action="store_true",
                         help="ask the area's own test to prove it can fail; "
                             "only valid together with --area")
    args = parser.parse_args(argv)

    if args.selftest:
        return 0 if selftest() else 1

    if args.explain and not args.area:
        parser.error("--explain only makes sense together with --area")
    if args.calibrate and not args.area:
        parser.error("--calibrate only makes sense together with --area")

    areas = load_areas()
    if args.area is not None:
        areas = [a for a in areas if a["id"] == args.area]
        if not areas:
            print("NO-DATA: no such area id {!r} in {}".format(args.area, AREAS_FILE))
            return 2

    extra_args = (["--explain"] if args.explain else []) + \
        (["--calibrate"] if args.calibrate else [])
    results = run_all(areas, extra_args=extra_args)
    fail_count = 0
    nodata_count = 0
    for area_id, name, verdict, evidence in results:
        print("{:<8} [{}] {:<50} {}".format(verdict, area_id, name, evidence))
        if verdict == "FAIL":
            fail_count += 1
        elif verdict == "NO-DATA":
            nodata_count += 1

    print()
    print("{} area(s): {} pass, {} fail, {} no-data".format(
        len(results), len(results) - fail_count - nodata_count, fail_count, nodata_count))
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
