#!/usr/bin/env python3
"""L5d steps 9 to 13 as one runnable driver: the loaded capture, the human gated quiet capture, the
classifier, and the two evidence files the unit's done check reads.

usage (repo root):
  python3 -B scripts/perf_audit_run.py loaded --out-dir DIR        step 9, taken while the machine is loaded
  python3 -B scripts/perf_audit_run.py quiet  --out-dir DIR        steps 10 to 13, taken in a quiet window
  python3 -B scripts/perf_audit_run.py poll                        one quiet reading, no side effect
  python3 -B scripts/perf_audit_run.py quiet-check [--gap 60]      two readings, exit 0 quiet, 2 not quiet
  python3 -B scripts/perf_audit_run.py --selftest

WHY THIS FILE EXISTS. The five L5d sub units landed a snapshotter, a capture harness, a classifier and an
audit page, but nothing joined them: the harness records the OS load only (scripts/perf_audit_load.snapshot,
whose test forbids ps), while the classifier refuses any record whose load block is not source uptime+ps
with a claude_processes count. This driver supplies that block (ps runs here, not in the load module) and
performs spec step 10: quiet is the 1 minute load at or under the classifier's QUIET_LOAD bar with
claude_processes <= 1, on two readings 60 s apart, a 30 minute budget, and INCONCLUSIVE with no time quoted
when quiet is never reached (H2, 2026-09-30: a claude only count read quiet at load 27 to 31). It never waits for quiet on its
own initiative: the owner runs `quiet` inside a window he chose.

WHAT IT REFUSES. `quiet` without a loaded capture on disk still takes the quiet capture (the quiet window is
the scarce input) but its verdict is NO_DATA naming the loaded capture step 9 still owes: the classifier
needs both. A verdict of INCONCLUSIVE or NO_DATA writes the classification and the audit page but never
docs/architecture/PERF-AUDIT-L5D.json, because that file's causes (scripts/donecheck_l5d.py CAUSES) are
claims and an inconclusive run has none to make. A gate that outruns --gate-timeout is killed and recorded
with exit 124, which the classifier reads as NO_DATA."""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.gate_order import CorruptLogError, NoDataError, parse_log  # noqa: E402
from scripts.perf_audit_capture import capture  # noqa: E402
from scripts.perf_audit_classify import QUIET_LOAD, Verdict, classify  # noqa: E402
from scripts.perf_audit_load import snapshot  # noqa: E402

GATE = "scripts/required_fast.sh"
TARGET_SECONDS = 90
QUIET_CLAUDE = 1
QUIET_GAP_S = 60
QUIET_BUDGET_S = 30 * 60
SOURCE = "uptime+ps"
JSON_OUT = os.path.join("docs", "architecture", "PERF-AUDIT-L5D.json")
DOC_OUT = os.path.join("docs", "architecture", "L5D-PERFORMANCE-AUDIT.md")
CAUSE = {Verdict.MACHINE_LOAD: "machine-contention", Verdict.CODE_REGRESSION: "code-regression",
         Verdict.STALE_TARGET: "stale-target"}
DOC_PATHS = ("scripts/required_fast.sh", "scripts/gate_order.py", "scripts/perf_audit_capture.py",
             "scripts/perf_audit_load.py", "scripts/perf_audit_classify.py", "scripts/perf_audit_run.py",
             "scripts/test_perf_audit.py", "scripts/donecheck_l5d.py", "docs/architecture/L5D-PERFORMANCE-AUDIT.md")


def count_processes(ps_text):
    """Counts from `ps -axo comm=` output. Pure, so the fixture test is exact.

    claude_processes counts Claude Code processes, whose executable is named exactly `claude` (the CLI
    and the agent SDK's bundled copy), never a comm that merely contains the word: measured 2026-09-30,
    the desktop app alone keeps 16 helpers named `Claude Helper`, `Claude`, `chrome-native-host` and so
    on alive while idle, so a substring count read 31 to 33 on a machine with one working session and
    the spec's quiet gate (at most 1) could never be met with the app open."""
    if not isinstance(ps_text, str):
        raise ValueError("ps output must be str")
    names = [line.strip() for line in ps_text.splitlines() if line.strip()]
    bases = [os.path.basename(n) for n in names]
    # Case sensitive on purpose: the desktop app's own binary is `Claude`, the CLI is `claude`.
    claude = sum(b == "claude" for b in bases)
    # A python worker is `python`, `Python`, `python3`, `python3.13`: the basename starts with the word.
    python = sum(b.lower().startswith("python") for b in bases)
    # H2 (attack 2026-09-30): the load on this machine came from npm (which runs as `node`), codex and
    # python workers that a claude only count never saw. Workers of every kind, by executable basename.
    workers = claude + python + sum(b in ("codex", "node", "npm") for b in bases)
    return {"claude_processes": claude, "python_processes": python, "worker_processes": workers,
            "total_processes": len(names)}


def load_block(run=subprocess.run):
    """The load block the classifier accepts. Never raises: an unreadable input is source unread."""
    try:
        block = dict(snapshot())
        out = run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=30, check=True).stdout
        block.update(count_processes(out))
        block["source"] = SOURCE
    except (OSError, ValueError, subprocess.SubprocessError):
        block = {"captured_at": datetime.now(timezone.utc).isoformat(), "load_average_1m": float("nan"),
                 "cpu_count": os.cpu_count() or 0, "claude_processes": -1, "source": "unread"}
    return block


def is_quiet(block):
    """Quiet is LOAD based at the source (H2): the 1 minute load at or under QUIET_LOAD, the bar the classifier
    and the done check apply, read from a real uptime+ps snapshot; the spec's claude_processes <= 1 stays as
    the secondary signal. A NaN or missing load compares False, so an unread load is never quiet."""
    load = block.get("load_average_1m", float("nan"))
    try:
        under_bar = float(load) <= QUIET_LOAD
    except (TypeError, ValueError):
        under_bar = False
    return (block.get("source") == SOURCE and under_bar
            and 0 <= block.get("claude_processes", -1) <= QUIET_CLAUDE)


def reading_line(block):
    return "%s claude_processes=%s workers=%s load_1m=%.2f cpu=%s source=%s" % (
        block.get("captured_at"), block.get("claude_processes"), block.get("worker_processes"),
        block.get("load_average_1m", float("nan")), block.get("cpu_count"), block.get("source"))


def wait_quiet(budget_s=QUIET_BUDGET_S, gap_s=QUIET_GAP_S, read=load_block, sleep=time.sleep,
               clock=time.monotonic, say=print):
    """Spec step 10: quiet is two consecutive quiet readings gap_s apart within budget_s.
    Returns (quiet, readings). A loaded reading between two quiet ones restarts the pair."""
    start = clock()
    readings = []
    streak = 0
    while True:
        block = read()
        readings.append(block)
        say("reading %d: %s" % (len(readings), reading_line(block)))
        streak = streak + 1 if is_quiet(block) else 0
        if streak >= 2:
            return True, readings
        if clock() - start + gap_s > budget_s:
            return False, readings
        sleep(gap_s)


def run_gate_factory(timeout_s):
    def run_gate(argv, log_path):
        env = dict(os.environ, BROTHER_DISK_GATE="off", PYTHONDONTWRITEBYTECODE="1")
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
                                    start_new_session=True)
            try:
                return proc.wait(timeout=timeout_s or None)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait()
                return 124
    return run_gate


def rows_in(log_path):
    try:
        return len(parse_log(log_path))
    except (NoDataError, CorruptLogError, OSError, ValueError):
        return None


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "not measured"


def cmd_quiet_check(gap_s, read=load_block, sleep=time.sleep, clock=time.monotonic, say=print):
    """Exactly two readings gap_s apart, no capture, no file written: 0 when both are quiet, 2 when not.

    scripts/capture_l5d_quiet.sh runs this before spending a quiet window on the gate. A budget of 2 * gap_s - 1
    gives wait_quiet room for the second reading (the first reading's own cost may reach gap_s - 1 seconds) and
    never a third, so this never waits for quiet on its own."""
    quiet, readings = wait_quiet(2 * gap_s - 1, gap_s, read, sleep, clock, say)
    if quiet:
        say("QUIET: %d readings %d s apart at or under load %.1f with claude_processes <= %d"
            % (len(readings), gap_s, QUIET_LOAD, QUIET_CLAUDE))
        return 0
    say("NOT QUIET: refusing to capture; close every Claude session (and any node, codex or python workers) and "
        "rerun when the 1 minute load is at or under %.1f with claude_processes <= %d" % (QUIET_LOAD, QUIET_CLAUDE))
    return 2


def evidence_json(quiet_rec, cls, quiet_readings, sha, loaded_rec=None):
    """The record scripts/donecheck_l5d.py judges. Only for a verdict that names a cause.

    A loaded gate that its timeout killed (exit 124) is named in the finding: its wall is a lower bound, and a
    reader of the cause must see that the loaded figure is not a completed run."""
    cause = CAUSE[cls["verdict"]]
    before = quiet_rec["load_before"]
    loaded_exit = (loaded_rec or {}).get("gate_exit_code")
    killed = (" The loaded gate was killed by its timeout (exit 124), so its wall of %d s is a lower bound, not a "
              "completed run." % cls["loaded_wall_seconds"]) if loaded_exit == 124 else ""
    return {
        "loaded_gate_exit_code": loaded_exit if isinstance(loaded_exit, int) else "not measured",
        "root_cause": cause,
        "measured_seconds": float(quiet_rec["wall_seconds"]),
        "load_at_measurement": float(before["load_average_1m"]),
        "cores": int(before["cpu_count"]),
        "documented_target_seconds": float(TARGET_SECONDS),
        "loaded_seconds": float(cls["loaded_wall_seconds"]),
        "loaded_load_avg_1m": float(cls["loaded_load_avg_1m"]),
        "commit_sha": sha,
        "commands": [
            {"command": "ps -axo comm= | grep -ci claude; sysctl -n vm.loadavg (two readings 60 s apart)",
             "output": "\n".join(reading_line(b) for b in quiet_readings)},
            {"command": "sh scripts/required_fast.sh  (label %s, BROTHER_DISK_GATE=off)" % quiet_rec["label"],
             "output": "exit %d after %d s; log %s" % (quiet_rec["gate_exit_code"], quiet_rec["wall_seconds"],
                                                       quiet_rec["gate_log_path"])},
            {"command": "python3 -B scripts/perf_audit_run.py quiet (classify quiet-q1 against loaded-l1)",
             "output": "%s: %s" % (cls["verdict"].value, cls["reasoning"])},
        ],
        "finding": "%s: quiet wall %d s at load %.2f on %d cores against a documented %d s; loaded wall %d s "
                   "at load %.2f. %s" % (cause, quiet_rec["wall_seconds"], before["load_average_1m"],
                                          before["cpu_count"], TARGET_SECONDS, cls["loaded_wall_seconds"],
                                          cls["loaded_load_avg_1m"], cls["reasoning"]) + killed,
    }


def render_doc(cls, loaded_rec, quiet_rec, quiet_readings, sha):
    """The audit page: exactly one verdict= line and one wall_seconds= line, every quoted path verbatim."""
    verdict = cls["verdict"].value
    measured = quiet_rec is not None
    lb = loaded_rec["load_before"] if loaded_rec else {}
    qb = quiet_rec["load_before"] if quiet_rec else {}
    nm = "not measured"
    lines = [
        "# L5D Performance Audit", "",
        "This page records the L5d audit of the fast gate against the documented %d second target." % TARGET_SECONDS,
        "It is prose only and is regenerated by scripts/perf_audit_run.py; every number below was read",
        "from a command that ran, or says not measured.", "",
        "## Verdict line", "", "verdict=%s" % verdict, "", "reasoning: %s" % cls["reasoning"], "",
        "## Quiet capture (spec step 10: claude_processes at most 1 on two readings 60 s apart)", "",
        "quiet gate_log_path: %s" % (quiet_rec["gate_log_path"] if measured else nm), "",
        "wall_seconds=%d" % (quiet_rec["wall_seconds"] if measured else 0), "",
    ]
    if not measured:
        lines += ["The digit above is a not measured placeholder, never a measured wall clock: quiet was not",
                  "reached inside the 30 minute budget, so no time is quoted, as the spec prescribes.", ""]
    lines += ["quiet load_avg_1m: %s" % (("%.2f" % qb["load_average_1m"]) if measured else nm),
              "quiet cpu_count: %s" % (qb.get("cpu_count") if measured else nm),
              "quiet claude_processes: %s" % (qb.get("claude_processes") if measured else nm),
              "quiet gate exit code: %s" % (quiet_rec["gate_exit_code"] if measured else nm),
              "quiet codes row count: %s" % ((rows_in(quiet_rec["gate_log_path"]) if measured else None) or nm),
              "", "Quiet readings taken (each one a real ps and load read at that time):", ""]
    lines += ["    " + reading_line(b) for b in quiet_readings] or ["    none"]
    lines += ["", "## Loaded capture (spec step 9)", "",
              "loaded gate_log_path: %s" % (loaded_rec["gate_log_path"] if loaded_rec else nm),
              "loaded wall seconds: %s" % (loaded_rec["wall_seconds"] if loaded_rec else nm),
              "load_avg_1m: %s" % (("%.2f" % lb["load_average_1m"]) if loaded_rec else nm),
              "cpu_count: %s" % (lb.get("cpu_count") if loaded_rec else nm),
              "claude_processes: %s" % (lb.get("claude_processes") if loaded_rec else nm),
              "loaded gate exit code: %s" % (loaded_rec["gate_exit_code"] if loaded_rec else nm),
              "codes row count: %s" % ((rows_in(loaded_rec["gate_log_path"]) if loaded_rec else None) or nm),
              "", "## Root cause", "",
              "top hog quiet: %s" % (cls["top_hog_quiet"] or nm),
              "top hog loaded: %s" % (cls["top_hog_loaded"] or nm),
              "target_seconds: %d (documented in scripts/required_fast.sh, never edited by L5d)" % TARGET_SECONDS,
              "", "## Environment", "",
              "commit_sha: %s" % sha,
              "worktree key: %s (the gate appends its own pid; not captured)" % os.path.basename(ROOT),
              "cache state: not measured", "",
              "## Paths named by this page", "",
              "Each line between the markers is a repository relative path and must exist.", "",
              "PATHLIST-BEGIN", *DOC_PATHS, "PATHLIST-END", ""]
    return "\n".join(lines)


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_loaded(a):
    out = Path(a.out_dir)
    rec = capture(a.label, out, os.path.join(ROOT, GATE), run_gate_factory(a.gate_timeout), load_block)
    print("captured %s: %d s, exit %d, load before %.2f, claude_processes %s, log %s" % (
        rec["label"], rec["wall_seconds"], rec["gate_exit_code"], rec["load_before"]["load_average_1m"],
        rec["load_before"]["claude_processes"], rec["gate_log_path"]))
    return 0 if rec["gate_exit_code"] != 124 else 1


def cmd_quiet(a):
    out = Path(a.out_dir)
    loaded_path = out / (a.loaded_label + "-capture.json")
    loaded_rec = None
    if loaded_path.is_file():
        loaded_rec = read_json(loaded_path)
    else:
        # A quiet window is the scarce input, so it is never wasted on this refusal: the quiet capture is
        # still taken, and the verdict reads NO_DATA naming the loaded capture step 9 still owes.
        print("WARNING: no loaded capture at %s; a quiet capture will still be taken, and the verdict will be "
              "NO_DATA until `loaded` runs while the machine is loaded" % loaded_path)
    sha = git_sha()
    quiet, readings = wait_quiet(a.budget, a.gap)
    if not quiet:
        lb = (loaded_rec or {}).get("load_before", {})
        cls = {"verdict": Verdict.INCONCLUSIVE, "quiet_wall_seconds": 0,
               "loaded_wall_seconds": (loaded_rec or {}).get("wall_seconds", 0),
               "loaded_load_avg_1m": lb.get("load_average_1m", float("nan")),
               "loaded_cpu_count": lb.get("cpu_count", 0),
               "top_hog_quiet": "", "top_hog_loaded": "", "target_seconds": TARGET_SECONDS,
               "reasoning": "quiet (claude_processes at most %d on two readings %d s apart) was not reached "
                            "within %d s over %d readings; no quiet time is quoted"
                            % (QUIET_CLAUDE, a.gap, a.budget, len(readings))}
        quiet_rec = None
    else:
        quiet_rec = capture(a.label, out, os.path.join(ROOT, GATE), run_gate_factory(a.gate_timeout), load_block)
        cls = classify(quiet_rec, loaded_rec or {}, TARGET_SECONDS)
    payload = dict(cls, verdict=cls["verdict"].value, quiet_readings=readings, commit_sha=sha)
    write_text(str(out / "classification.json"), json.dumps(payload, indent=2, sort_keys=True) + "\n")
    write_text(os.path.join(ROOT, DOC_OUT), render_doc(cls, loaded_rec, quiet_rec, readings, sha))
    print("verdict %s: %s" % (cls["verdict"].value, cls["reasoning"]))
    if cls["verdict"] not in CAUSE:
        print("no %s written: an %s verdict names no cause" % (JSON_OUT, cls["verdict"].value))
        return 1
    write_text(os.path.join(ROOT, JSON_OUT), json.dumps(evidence_json(quiet_rec, cls, readings, sha, loaded_rec), indent=2) + "\n")
    print("wrote %s and %s" % (JSON_OUT, DOC_OUT))
    return subprocess.call([sys.executable, "-B", os.path.join(HERE, "donecheck_l5d.py")], cwd=ROOT)


def selftest():
    import tempfile
    sys.path.insert(0, HERE)
    import donecheck_l5d
    ps = ("claude\n/usr/bin/python3\nClaude Helper\nbash\n/Applications/Claude.app/Contents/MacOS/Claude\n"
          "/x/claude_agent_sdk/_bundled/claude\n/x/claude-code/2.1.284/claude.app/Contents/MacOS/claude\n")
    fake = lambda claude: {"captured_at": "t", "load_average_1m": 1.0, "cpu_count": 8,  # noqa: E731
                           "claude_processes": claude, "source": SOURCE}
    seq = lambda blocks: (lambda it=iter(blocks): next(it))  # noqa: E731
    quiet_twice, r1 = wait_quiet(600, 60, seq([fake(1), fake(0)]), lambda s: None, iter(range(0, 1000, 10)).__next__,
                                 lambda s: None)
    broken_pair, r2 = wait_quiet(200, 60, seq([fake(1), fake(5), fake(1), fake(0)]), lambda s: None,
                                 iter(range(0, 1000, 10)).__next__, lambda s: None)
    out_of_time, r3 = wait_quiet(120, 60, seq([fake(9), fake(9), fake(9)]), lambda s: None,
                                 iter(range(0, 1000, 50)).__next__, lambda s: None)
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "q.log")
        with open(log, "w") as fh:
            fh.write("PASS    exit 0   version-truth         12s  ok\n")
        rec = {"label": "quiet-q1", "wall_seconds": 95, "gate_exit_code": 0, "gate_log_path": log,
               "load_before": fake(1), "load_after": fake(1)}
        loaded = dict(rec, label="loaded-l1", wall_seconds=714,
                      load_before=dict(fake(30), load_average_1m=40.0))
        cls = classify(rec, loaded, TARGET_SECONDS)
        ev = evidence_json(rec, cls, r1, "abc")
        ok_json, why = donecheck_l5d.judge(ev)
        ev_killed = evidence_json(rec, cls, r1, "abc", dict(loaded, gate_exit_code=124))
        ok_killed, _ = donecheck_l5d.judge(ev_killed)
        doc = render_doc(cls, loaded, rec, r1, "abc")
        doc_inc = render_doc(dict(cls, verdict=Verdict.INCONCLUSIVE), loaded, None, r3, "abc")
    one = lambda text, key: sum(line.startswith(key) for line in text.splitlines()) == 1  # noqa: E731
    said = []

    def slept_clock():
        """A clock that moves only when the wait sleeps, as the real pair does between its two readings."""
        state = {"t": 0.0}

        def sleep(s):
            state["t"] += s
        return sleep, lambda: state["t"]
    sl, ck = slept_clock()
    check_quiet = cmd_quiet_check(60, seq([fake(1), fake(0)]), sl, ck, said.append)
    sl, ck = slept_clock()
    check_loaded = cmd_quiet_check(60, seq([fake(1), fake(5)]), sl, ck, said.append)
    sl, ck = slept_clock()
    check_one_only = cmd_quiet_check(60, seq([fake(5), fake(0), fake(0)]), sl, ck, said.append)
    cases = [
        ("quiet-check is 0 on two quiet readings and 2 otherwise, never a third reading",
         check_quiet == 0 and check_loaded == 2 and check_one_only == 2),
        ("quiet-check says which way it went", any(l.startswith("QUIET:") for l in said)
         and any(l.startswith("NOT QUIET:") for l in said)),
        ("a loaded gate killed by its timeout is named in the finding and still judged",
         "lower bound" in ev_killed["finding"] and ev_killed["loaded_gate_exit_code"] == 124 and ok_killed
         and "lower bound" not in ev["finding"]),
        ("ps counts claude code by basename, never the app's helpers, python by basename, workers of every kind",
         count_processes(ps) == {"claude_processes": 3, "python_processes": 1, "worker_processes": 4,
                                 "total_processes": 7}),
        ("quiet is the load at or under the bar, an unread or loaded reading never is",
         is_quiet(fake(1)) and not is_quiet(dict(fake(1), load_average_1m=QUIET_LOAD + 1))
         and not is_quiet(dict(fake(1), load_average_1m=float("nan")))),
        ("a non string ps output is refused", _raises(lambda: count_processes(b"x"))),
        ("quiet is at most one claude process from a real source", is_quiet(fake(1)) and not is_quiet(fake(2))),
        ("an unread source is never quiet", not is_quiet(dict(fake(0), source="unread"))),
        ("two quiet readings in a row end the wait", quiet_twice and len(r1) == 2),
        ("a loaded reading between two quiet ones restarts the pair", broken_pair and len(r2) == 4),
        ("the budget ends the wait without quiet", not out_of_time and len(r3) >= 2),
        ("a machine load verdict yields evidence the done check accepts", cls["verdict"] is Verdict.MACHINE_LOAD
         and ok_json),
        ("the audit page carries one verdict and one wall line", one(doc, "verdict=") and one(doc, "wall_seconds=")),
        ("an inconclusive page quotes no time", one(doc_inc, "wall_seconds=0") and "placeholder" in doc_inc),
        ("every path the page names exists", all(os.path.isfile(os.path.join(ROOT, p)) for p in DOC_PATHS)),
        ("only cause naming verdicts map to the done check", set(CAUSE) == {Verdict.MACHINE_LOAD,
                                                                            Verdict.CODE_REGRESSION,
                                                                            Verdict.STALE_TARGET}),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", nargs="?", choices=("loaded", "quiet", "poll", "quiet-check"))
    ap.add_argument("--out-dir", help="where captures and classification.json land (outside the repository)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--loaded-label", default="loaded-l1")
    ap.add_argument("--gate-timeout", type=int, default=0, help="seconds before the gate is killed (0: none)")
    ap.add_argument("--budget", type=int, default=QUIET_BUDGET_S)
    ap.add_argument("--gap", type=int, default=QUIET_GAP_S)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.command == "poll":
        print(reading_line(load_block()))
        return 0
    if a.command == "quiet-check":
        return cmd_quiet_check(a.gap)
    if not a.command or not a.out_dir:
        ap.error("a command and --out-dir are required")
    a.label = a.label or ("loaded-l1" if a.command == "loaded" else "quiet-q1")
    return cmd_loaded(a) if a.command == "loaded" else cmd_quiet(a)


if __name__ == "__main__":
    sys.exit(main())
