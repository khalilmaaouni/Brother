#!/usr/bin/env python3
"""The done check for unit BL, brother.loop, covering ALL EIGHT sub units.

Written because BL's first done check ran four checks and the unit claims eight things. Closing a
unit on a check that does not test what the unit claims is the hollow green this estate keeps
finding, and it would have been found here by anyone who read the two lists side by side.

One row per sub unit. Every row runs a real command and reports its REAL exit code, captured
without a pipe, because an exit code after a pipe belongs to the pipe and this session misread
three that way in one evening.

Run: python3 scripts/donecheck_bl.py
"""
import concurrent.futures
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
PAGE = "docs/plan/BROTHER-LOOP.html"


def page_row():
    """BL.8 is a document, so its check is that the document exists, is not a stub, and that every
    repository path it NAMES actually resolves. A page that cites files which do not exist is worse
    than no page: it reads as authoritative and sends the reader nowhere."""
    import re, html as H
    p = os.path.join(ROOT, PAGE)
    if not os.path.isfile(p):
        return 1, "%s does not exist" % PAGE
    body = open(p, encoding="utf-8").read()
    if len(body) < 4000:
        return 1, "%s is only %d bytes, which is a stub" % (PAGE, len(body))
    paths = sorted(set(re.findall(r"(?:docs|scripts)/[A-Za-z0-9_./-]+\.(?:py|sh|md|json|html)",
                                  H.unescape(body))))
    missing = [q for q in paths if not os.path.isfile(os.path.join(ROOT, q))]
    if missing:
        return 1, "names %d path(s) that do not exist: %s" % (len(missing), ", ".join(missing[:3]))
    if "<svg" not in body:
        return 1, "carries no diagram, and the unit promises a schematic"
    return 0, "%d bytes, %d path(s) all resolve, %d diagram(s)" % (len(body), len(paths), body.count("<svg"))


def rho_row():
    """BL.7 promises, verbatim, that "the rho figures printed by the two tools must agree on the
    same window". Nothing automated it, so this row runs BOTH TOOLS and parses the rho each one
    PRINTS, over one synthetic ledger in a throwaway HOME.

    THE AGREEMENT IS PARTLY VACUOUS AND THIS ROW SAYS SO RATHER THAN BANKING IT. loop_report.py's
    utilisation() is a four line wrapper whose body is `return stage_log.utilisation(stage_rows)`,
    so the ARITHMETIC is one derivation wearing two names and can never disagree with itself.
    loop_report's own selftest case compares those two functions directly, which is the shape of a
    check that cannot fail. Read as a claim of two independent derivations agreeing, that is
    theatre, and this row is not written as one.

    WHAT IS STILL WORTH ASSERTING, because it is genuinely two code paths: each tool selects its
    own window and its own rows before the shared arithmetic ever runs. stage_log.main() filters
    with load(), which drops any row whose `at` is not numeric, and hands utilisation an EXPLICIT
    wall of last minus first; loop_report.report() filters with its own rows(), which keeps every
    dict, and passes NO wall, so utilisation derives one itself. Two different row sets or two
    different walls print two different rho values out of one formula. So: both tools must produce
    a rho, for the SAME set of stages, and the printed figures must match. Neither may print none.
    """
    import json as J, re, subprocess as SP, tempfile, time

    def parse(text, pat, after=None):
        """rho out of a printed table. Parsing the OUTPUT is the whole point of this row: comparing
        the functions is what was already being done and what proves nothing."""
        lines = text.splitlines()
        if after is not None:
            hit = [i for i, l in enumerate(lines) if after in l]
            if not hit:
                return {}
            lines = lines[hit[0] + 1:]
        out = {}
        for l in lines:
            m = re.match(pat, l)
            if m:
                out[m.group(1)] = m.group(2)
            elif out:
                break                      # the table ended; a later table must not be read as rho
        return out

    home = tempfile.mkdtemp(prefix="bl7-rho-")
    ev = os.path.join(home, ".claude", "evidence")
    os.makedirs(ev)
    t = time.time() - 600                  # well inside a one hour window for both tools, so the
    fixture = [                            # few milliseconds between the two runs cannot move it
        {"at": t, "stage": "model", "event": "enter", "sub": "A", "pid": 1},
        {"at": t + 120, "stage": "model", "event": "leave", "sub": "A", "pid": 1, "ok": True},
        {"at": t + 130, "stage": "grade", "event": "enter", "sub": "A", "pid": 1},
        {"at": t + 160, "stage": "grade", "event": "leave", "sub": "A", "pid": 1, "ok": True},
        {"at": t + 200, "stage": "model", "event": "enter", "sub": "B", "pid": 2},
        {"at": t + 400, "stage": "model", "event": "leave", "sub": "B", "pid": 2, "ok": False},
    ]
    with open(os.path.join(ev, "brother-stages.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("".join(J.dumps(r) + "\n" for r in fixture))
    env = dict(os.environ, HOME=home)
    try:
        a = SP.run([PY, "-B", "scripts/loop/stage_log.py", "--since-hours", "1"],
                   capture_output=True, text=True, timeout=120, env=env)
        b = SP.run([PY, "-B", "scripts/loop/loop_report.py", "--since-hours", "1"],
                   capture_output=True, text=True, timeout=120, env=env)
    except (OSError, SP.SubprocessError) as exc:
        return 1, "could not run both tools: %s" % type(exc).__name__
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)
    if a.returncode != 0 or b.returncode != 0:
        return 1, "stage_log exit %d, loop_report exit %d on one ledger" % (a.returncode, b.returncode)
    # stage_log:   stage  n  mean s  busy h  rho        loop_report:  stage  items  mean s  rho  constraint
    sl = parse(a.stdout, r"^(\w+)\s+\d+\s+[\d.]+\s+[\d.]+\s+([\d.]+)", after="busy h")
    lr = parse(b.stdout, r"^\s+(\w+)\s+\d+\s+[\d.]+\s+([\d.]+)\s+(?:YES|no),")
    # NEITHER MAY RETURN NOTHING. An empty table on both sides compares equal, and "they agree that
    # there is no rho" is the vacuous pass this row exists to refuse.
    if not sl or not lr:
        return 1, "no rho printed by %s" % (", ".join(n for n, d in (("stage_log", sl), ("loop_report", lr)) if not d))
    if set(sl) != set(lr):
        return 1, "different stages: stage_log %s vs loop_report %s" % (sorted(sl), sorted(lr))
    off = sorted(k for k in sl if sl[k] != lr[k])
    if off:
        return 1, "rho disagrees on %s: %s vs %s" % (off[0], sl[off[0]], lr[off[0]])
    return 0, "%d stage(s) agree on rho: %s" % (len(sl), ", ".join("%s %s" % kv for kv in sorted(sl.items())))


ROWS = [
    ("BL.1", "the lifecycle speaks: start note, end note, alarms, heartbeat",
     [PY, "scripts/loop/loop_heartbeat.py", "--selftest"]),
    # THE ROW BL.1 DID NOT HAVE. An adversarial audit, 2026-09-21: not one of the twelve rows
    # executed loop_until.sh, where BL.1's whole promise lives. The row above runs a different
    # file. This one drives the shell driver itself under a throwaway HOME.
    ("BL.1", "the driver itself: refusals speak, starts note, ends report",
     [PY, "scripts/test_loop_until_lifecycle.py"]),
    ("BL.2", "ownership is atomic and still recovers from a crash",
     [PY, "scripts/test_loop_guard_race.py"]),
    ("BL.3", "a red batch finds its own culprit",
     [PY, "scripts/loop/land_batch.py", "--selftest"]),
    ("BL.4", "the probe verdict can reject a build",
     [PY, "scripts/test_unit_runner_probe_gate.py"]),
    ("BL.5", "what runs is what was reviewed",
     [PY, "scripts/test_loop_tool_parity.py"]),
    ("BL.6", "one standard report, generated from the ledgers",
     [PY, "scripts/loop/loop_report.py", "--selftest"]),
    ("BL.7", "per stage timestamps, and the utilisation they answer",
     [PY, "scripts/loop/stage_log.py", "--selftest"]),
    # BL.7's spec says the rho figures printed by the two tools must agree on the same window.
    # The selftest above proves stage_log's own arithmetic; nothing ran the two tools side by side.
    # rho_row() does, and its docstring states plainly how much of that agreement is vacuous.
    ("BL.7", "both tools print the same rho for the same window",
     rho_row),                                # checked in code: two commands, one comparison
    ("BL.8", "documentation an engineer and a non engineer can follow",
     page_row),                               # checked in code: a page, not a command
    ("BL.*", "every component is named by the unit and committed",
     [PY, "scripts/test_bl_owns_complete.py"]),
    ("BL.*", "the lifecycle survives hostile input",
     [PY, "scripts/test_loop_lifecycle_probes.py"]),
    ("BL.*", "the model router and the three transports",
     [PY, "scripts/loop/model_router.py", "--selftest"]),
    ("BL.*", "the call path and its failover",
     [PY, "scripts/loop/model_call.py", "--selftest"]),
]


def run_row(row):
    """One row's (code, detail). Pure with respect to the others, which is WHY main() can run
    them concurrently: each is its own subprocess or its own pure function, and no row reads
    another row's result."""
    sub, claim, cmd = row
    if callable(cmd):
        return cmd()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        out = (r.stdout + r.stderr).strip().splitlines()
        return r.returncode, (out[-1][:46] if out else "no output")
    except (OSError, subprocess.SubprocessError) as exc:
        return 125, "%s: %s" % (type(exc).__name__, exc)


def main():
    os.chdir(ROOT)
    bad, skipped = [], []
    print("%-6s %-56s %-5s %s" % ("sub", "what it claims", "exit", "verdict"))
    # THE ROWS RUN CONCURRENTLY, AND THE OUTPUT STAYS IN PLAN ORDER. Measured 2026-09-22 by timing
    # every row: serial total 117.5 s against a parallel wall of 60.6 s, and the battery was run
    # serially five times in one evening while closing this unit, which is most of an hour spent
    # waiting on independent subprocesses.
    #
    # WHY THIS IS SAFE HERE, checked rather than assumed: every row is its own subprocess or its
    # own pure function, no row reads another row's output, and the two rows that touch shared
    # machine state already own it (test_loop_guard_race.py drives a lease path of its own under
    # a temp directory, and test_loop_tool_parity.py only reads). ThreadPoolExecutor is the right
    # pool because the work is subprocess and file IO, not Python CPU.
    #
    # ORDER IS NOT A COSMETIC DETAIL. executor.map yields in submission order, so the printed
    # table, the failure list and the summary all read exactly as they did serially. A reader
    # comparing two runs must not have to re-sort them, and a check's row must sit beside its own
    # sub unit rather than wherever it happened to finish.
    #
    # FAIL DIRECTION IS UNCHANGED: a row that raises inside the pool still returns 125 from
    # run_row, and NOT APPLICABLE and NO-DATA still count as not-run rather than as passes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ROWS)) as pool:
        results = list(pool.map(run_row, ROWS))
    for (sub, claim, cmd), (code, detail) in zip(ROWS, results):
        # A NOT APPLICABLE IS NOT A PASS, and counting it as one is how this check produced its
        # closing line on a machine with NO loop installed. Measured 2026-09-21: under an empty
        # HOME, BL.2 and BL.5 both printed "NOT APPLICABLE ... exit 0" and the summary still read
        # "all 12 checks pass, covering every one of the eight sub units". Two of eight sub units
        # tested nothing and the line did not say so.
        if "NOT APPLICABLE" in detail or "NO-DATA" in detail:
            skipped.append((sub, claim, detail))
        elif code != 0:
            bad.append((sub, claim, detail))
        print("%-6s %-56s %-5s %s" % (sub, claim[:56], code, detail[:46]))
    print()
    if skipped:
        print("NOT DONE: %d of %d check(s) could not run here, and a check that cannot run is not a "
              "pass:" % (len(skipped), len(ROWS)))
        for sub, claim, detail in skipped:
            print("  %s %s: %s" % (sub, claim, detail[:60]))
        return 1
    if bad:
        print("NOT DONE: %d of %d check(s) failed" % (len(bad), len(ROWS)))
        for sub, claim, detail in bad:
            print("  %s %s: %s" % (sub, claim, detail))
        return 1
    print("BL DONE: all %d checks pass, covering every one of the eight sub units" % len(ROWS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
