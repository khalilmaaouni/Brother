"""donecheck_acc2: the closing check for ACC2, the parallel fast gate.

Runs scripts/required_fast.sh twice on this one tree, first one check at a
time (REQUIRED_FAST_JOBS=1), then side by side (REQUIRED_FAST_JOBS, default
4), and compares what each check said.

PASS (exit 0): every check gave the same verdict and exit code in both runs,
and the side-by-side run took under half the one-at-a-time wall time.
FAIL (exit 1): any verdict differs, a check appears in one run only, or the
side-by-side run is not under half.
NO-DATA (exit 2): either run printed no summary line, so there is nothing
to compare.

--correctness (owner scope decision for 1.1.0): judge only the first half. PASS
when both runs printed their summary and every check gave the same verdict and
exit code; the wall ratio is printed as a measurement and NOT judged, because
the benchmark closure (the under half bar) moved to 1.1.1. A --correctness PASS
never claims the benchmark passed.

This is a unit done check, not a battery test: it may be red until the unit
is finished, and it runs the whole gate twice (minutes), so it is never
registered in check_all.sh.
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LINE = re.compile(r"^(PASS|FAIL|NO-DATA)\s+exit\s+(\d+)\s+(\S+)", re.M)
SUMMARY = re.compile(r"^pass \d+\s+fail \d+\s+no-data \d+", re.M)
#: the width the gate actually ran at, which it prints on its summary line (2026-10-04)
WIDTH = re.compile(r"^width (\d+)(?: \(forced by ([^)]*)\))?$", re.M)


def run(jobs):
    env = dict(os.environ)
    env["REQUIRED_FAST_JOBS"] = str(jobs)
    # THE SIDE BY SIDE RUN MUST BE SIDE BY SIDE (diagnosis 2026-10-04): the closer runs this check under
    # loop_switches.suite_env, whose BROTHER_JEV_STATE_DIR makes required_fast.sh force width 1, so both runs were the
    # serial gate and the check timed out three times; without it the gate makes its own private Jev directory under
    # the box's TMPDIR. Any other forcing variable still forces width 1, which main() now reads and refuses.
    env.pop("BROTHER_JEV_STATE_DIR", None)
    started = time.time()
    gate = os.environ.get("DONECHECK_GATE", "scripts/required_fast.sh")  # a stand-in gate proves the three verdicts
    proc = subprocess.run(["sh", gate], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    wall = time.time() - started
    out = proc.stdout + proc.stderr
    verdicts = {name: (verdict, int(code)) for verdict, code, name in LINE.findall(out)}
    return wall, verdicts, bool(SUMMARY.search(out)), out


def width_of(out):
    """(width, forced by) from the gate's own width line, or (None, '') when it printed none (an older gate, a crash)."""
    m = WIDTH.findall(out or "")
    return (int(m[-1][0]), m[-1][1].strip()) if m else (None, "")


def main(argv=None):
    correctness = "--correctness" in (sys.argv[1:] if argv is None else argv)
    jobs = int(os.environ.get("REQUIRED_FAST_JOBS", "4") or 4)
    serial_wall, serial, serial_done, serial_out = run(1)
    serial_width, _ = width_of(serial_out)
    print("one at a time: %.0f s, %d checks, width %s" % (serial_wall, len(serial), serial_width), flush=True)
    parallel_wall, parallel, parallel_done, parallel_out = run(jobs)
    parallel_width, forced = width_of(parallel_out)
    print("side by side (%d asked): %.0f s, %d checks, width %s" % (jobs, parallel_wall, len(parallel), parallel_width), flush=True)
    # A COMPARISON OF SERIAL WITH SERIAL PROVES NOTHING: an unreported or forced width is NO-DATA, never a pass
    if serial_width != 1 or parallel_width is None or parallel_width < 2:
        print("NO-DATA: the runs did not run at width 1 and above 1 (serial %s, side by side %s%s); a forcing variable or "
              "a gate without its width line leaves nothing to compare" % (serial_width, parallel_width,
                                                                          ", forced by " + forced if forced else ""))
        return 2
    if not (serial_done and parallel_done):
        print("NO-DATA: a run printed no summary line (serial %s, parallel %s)"
              % (serial_done, parallel_done))
        return 2
    differ = sorted(n for n in set(serial) | set(parallel) if serial.get(n) != parallel.get(n))
    for name in differ:
        print("DIFFERS %s: one at a time %s, side by side %s"
              % (name, serial.get(name), parallel.get(name)))
    ratio = parallel_wall / serial_wall if serial_wall else 1.0
    if correctness:
        print("wall ratio %.2f (recorded, not judged: the benchmark closure moved to 1.1.1)" % ratio)
        if differ:
            print("FAIL")
            return 1
        print("PASS (correctness: every check gave the same verdict one at a time and side by side; no benchmark claim)")
        return 0
    print("wall ratio %.2f (bar: under 0.50)" % ratio)
    if differ or ratio >= 0.5:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
