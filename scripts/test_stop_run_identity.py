"""A stop stops only what belongs to a run of THIS loop, never a process that merely has a loop tool's shape.

WHY (2026-09-29 18:00 JST, ~/.claude/evidence/loop-until-20260929-1737.log). At the run's deadline the driver's stop
printed "STOP INCOMPLETE: 2 process(es) still alive", naming <hermetic home>/.claude/brother-scratch/straggler-*/bin/
unit_runner.py and the `python -m plugin.runtime.brother.core.or_fanout` it had started. Neither was the loop's: both
were fixtures of scripts/test_runner_straggler_settles.py, run at that moment by a verification job. loop_procs.owned()
selected a loop tool by its NAME wherever it lived, so the fixture's copy of unit_runner.py read as a runner of this
loop and its fan out followed it by parent chain. The driver set END_FAILED and skipped the finisher.

The rule now: a loop tool belongs to this loop when its program lives in this loop's tool directory ($HOME/.claude/bin,
where every run launches its tools from), or when its own environment names a run under this loop's runs root
(BROTHER_RUN_DIR, which the driver exports to everything it starts; the code root helpers run from outside the bin).
Anything else is a stranger: never signalled, never counted as alive.

Every stop here runs with HOME inside its own fixture and STOP_LOOP_ONLY pointed at that fixture, so no real loop on
this machine can be reached even when the rule under test is broken (the mutation run breaks it on purpose).
Run from the repository root: python3 -B scripts/test_stop_run_identity.py
"""
import os, re, shutil, signal, subprocess, sys, tempfile, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")
STOP = os.path.join(LOOP, "stop_loop.sh")
sys.path.insert(0, LOOP)
import loop_procs as LP  # noqa: E402

HOME = "/Users/you"   # the placeholder home shipped files may name (scripts/test_export_public.py)
BIN = HOME + "/.claude/bin"
RUNS = HOME + "/.claude/evidence/loop-runs"
STRAY = HOME + "/.claude/brother-scratch/straggler-a/bin/unit_runner.py"
FANOUT = "python3 -m plugin.runtime.brother.core.or_fanout %s/jobs-fanout.json --workers 99"
RUN_VARS = ("BROTHER_RUN_DIR", "BROTHER_RUNS_ROOT", "STOP_LOOP_ONLY")


def rows(*r):
    return [dict(pid=a, ppid=b, pgid=c, command=d, **(e[0] if e else {})) for a, b, c, d, *e in r]


class Rule(unittest.TestCase):
    """owned() over one snapshot, HOME naming the loop; each case isolates ONE piece of evidence."""

    def owned(self, snap, rx=LP.RUNNERS_RX):
        env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
        env["HOME"] = HOME
        with mock.patch.dict(os.environ, env, clear=True):
            return set(LP.owned(snap, rx, self_pid=1))

    def test_a_tool_in_the_loops_bin_is_owned_with_no_run_marker(self):
        self.assertEqual(self.owned(rows((500, 1, 500, "python3 %s/unit_runner.py H3 H3.d" % BIN))), {500})

    def test_the_same_tool_outside_the_loops_bin_is_a_stranger(self):
        self.assertEqual(self.owned(rows((500, 1, 500, "python3 -B %s Z9 Z9.1 1" % STRAY))), set())

    def test_a_strangers_fan_out_is_not_owned_through_it(self):
        snap = rows((500, 1, 500, "python3 -B %s Z9 Z9.1 1" % STRAY), (501, 500, 501, FANOUT % "/s/home"))
        self.assertEqual(self.owned(snap), set())

    def test_a_stranger_beside_a_real_runner_leaves_only_the_real_family(self):
        snap = rows((500, 1, 500, "python3 %s/unit_runner.py H3 H3.d" % BIN), (501, 500, 501, FANOUT % RUNS),
                    (600, 1, 600, "python3 -B %s Z9 Z9.1 1" % STRAY), (601, 600, 601, FANOUT % "/s/home"))
        self.assertEqual(self.owned(snap), {500, 501})

    def test_the_driver_outside_the_loops_bin_is_a_stranger(self):
        snap = rows((10, 1, 10, "bash %s/loop_until.sh 18:00" % BIN), (20, 1, 20, "bash /tmp/t/home/.claude/bin/loop_until.sh 18:00"))
        self.assertEqual(self.owned(snap, LP.DRIVER_RX), {10})

    def test_a_code_root_helper_is_owned_by_the_run_its_environment_names(self):
        helper = "python3 -B /Users/you/Brother/scripts/diag_round.py --limit 6"
        self.assertEqual(self.owned(rows((700, 1, 700, helper, {"run_dir": RUNS + "/run-20260929-1737-1"}))), {700})

    def test_a_code_root_helper_with_no_run_marker_is_a_stranger(self):
        helper = "python3 -B /tmp/t/scripts/diag_round.py --limit 6"
        self.assertEqual(self.owned(rows((700, 1, 700, helper), (701, 1, 701, helper, {"run_dir": ""}))), set())

    def test_a_run_marker_outside_this_loops_runs_root_is_a_stranger(self):
        helper = "python3 -B /tmp/t/scripts/diag_round.py --limit 6"
        snap = rows((700, 1, 700, helper, {"run_dir": "/tmp/t/home/.claude/evidence/loop-runs/run-1"}),
                    (701, 1, 701, helper, {"run_dir": RUNS + "-old/run-1"}),   # a sibling sharing the root's prefix
                    (702, 1, 702, helper, {"run_dir": RUNS}))                  # the root itself names no run
        self.assertEqual(self.owned(snap), set())

    def test_a_relative_program_word_is_never_in_the_bin_whatever_the_stops_cwd(self):
        """Adversarial review finding 1 (2026-09-30): in_loop_bin resolved a RELATIVE program word against the STOP's own
        cwd, so a stranger's `python3 bin/unit_runner.py`, run from anywhere, was owned whenever the stop happened to run
        from $HOME/.claude. Every production launch names the bin absolutely; a relative word falls to the run marker."""
        d = tempfile.mkdtemp(prefix="stop-run-identity-cwd-")
        self.addCleanup(shutil.rmtree, d, True)
        home = os.path.join(d, "home")
        os.makedirs(os.path.join(home, ".claude", "bin"))
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(os.path.join(home, ".claude"))
        env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
        env["HOME"] = home
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(set(LP.owned(rows((500, 1, 500, "python3 bin/unit_runner.py Z Z.1")), LP.RUNNERS_RX, self_pid=1)), set())

    def test_the_runs_root_the_driver_exports_is_the_one_read(self):
        helper = "python3 -B /tmp/t/scripts/diag_round.py --limit 6"
        snap = rows((700, 1, 700, helper, {"run_dir": "/elsewhere/loop-runs/run-1"}))
        env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
        env.update(HOME=HOME, BROTHER_RUNS_ROOT="/elsewhere/loop-runs")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(set(LP.owned(snap, LP.RUNNERS_RX, self_pid=1)), {700})


class SnapshotReadsTheRunMarker(unittest.TestCase):
    """snapshot() reads a run marker (ps -E) for a loop tool OUTSIDE the bin only, and an unreadable read is NO-DATA
    (None), never "no run". One fake ps per case; each case isolates one outcome of the per pid read."""
    HELPER = "python3 -B /Users/you/Brother/scripts/diag_round.py --limit 6"
    RUN = RUNS + "/run-20260929-1737-1"

    def snapshot(self, table, per_pid):
        """per_pid: what the `ps -E ... -p PID` read does: a (returncode, stdout, stderr) tuple, or an exception"""
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if "-E" not in argv:
                return subprocess.CompletedProcess(argv, 0, table, "")
            if isinstance(per_pid, BaseException):
                raise per_pid
            return subprocess.CompletedProcess(argv, per_pid[0], per_pid[1], per_pid[2])
        env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
        env["HOME"] = HOME
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(LP.subprocess, "run", side_effect=fake_run):
            return LP.snapshot(), calls

    def test_a_code_root_tools_run_marker_is_read_from_its_environment(self):
        snap, _ = self.snapshot("700 1 700 %s\n" % self.HELPER, (0, "%s HOME=%s BROTHER_RUN_DIR=%s X=y\n" % (self.HELPER, HOME, self.RUN), ""))
        self.assertEqual([r.get("run_dir") for r in snap], [self.RUN])

    def test_a_tool_in_the_loops_bin_needs_no_environment_read(self):
        snap, calls = self.snapshot("500 1 500 python3 %s/unit_runner.py H3 H3.d\n" % BIN, AssertionError("the bin tool was read for a run marker"))
        self.assertEqual(([c for c in calls if "-E" in c], "run_dir" in snap[0]), ([], False))

    def test_a_tool_that_exited_between_the_two_reads_carries_no_run(self):
        snap, _ = self.snapshot("700 1 700 %s\n" % self.HELPER, (1, "", ""))   # gone: nothing on any stream
        self.assertEqual([r.get("run_dir") for r in snap], [""])

    def test_a_pid_now_running_another_command_carries_no_run(self):
        # the reused pid's command is LONGER than the helper's and its marker sits past that length, so only the
        # same-command check (not the slice) can refuse it (attack on 65ffa97b4, survivor M11: the old "vim notes.md"
        # line was shorter, so the slice cut the marker apart with or without the check)
        other = "/usr/bin/vim /Users/you/notes/a-file-name-long-enough-to-reach-past-the-helper-command.md"
        self.assertGreater(len(other), len(self.HELPER))
        snap, _ = self.snapshot("700 1 700 %s\n" % self.HELPER, (0, "%s BROTHER_RUN_DIR=%s\n" % (other, self.RUN), ""))
        self.assertEqual([r.get("run_dir") for r in snap], [""])

    def test_a_run_marker_in_the_arguments_is_never_read_as_the_run(self):
        # only the ENVIRONMENT after the command names the run; an argument spelling BROTHER_RUN_DIR=... is data
        # (attack on 65ffa97b4, survivor M10: parsing the whole ps -E line would own this stranger)
        cmd = "%s BROTHER_RUN_DIR=%s" % (self.HELPER, self.RUN)
        snap, _ = self.snapshot("700 1 700 %s\n" % cmd, (0, "%s HOME=%s X=y\n" % (cmd, HOME), ""))
        self.assertEqual([r.get("run_dir") for r in snap], [""])


    def test_an_unreadable_run_marker_read_makes_the_table_unknown(self):
        snap, _ = self.snapshot("700 1 700 %s\n" % self.HELPER, (1, "", "ps: could not read the process table\n"))
        self.assertIsNone(snap, "a per pid read that failed with a message read as 'no run' instead of NO-DATA")

    def test_a_run_marker_read_that_raises_makes_the_table_unknown(self):
        snap, _ = self.snapshot("700 1 700 %s\n" % self.HELPER, OSError("ps could not be started"))
        self.assertIsNone(snap, "a per pid read that raised read as 'no run' instead of NO-DATA")


FAKE_PS = r'''#!/usr/bin/env python3
import sys
ROWS = %r
a = sys.argv[1:]
out = sys.stdout.buffer
if "-p" in a:
    r = [x for x in ROWS if x[0] == int(a[a.index("-p") + 1])]
    if not r: sys.exit(1)
    out.write((b"%%d" %% r[0][2]) if "pgid=" in a else r[0][3]); out.write(b"\n"); sys.exit(0)
for p, pp, pg, c in ROWS: out.write(b"%%d %%d %%d " %% (p, pp, pg) + c + b"\n")
'''


class AnUndecodableCommandLine(unittest.TestCase):
    """The process table is decoded with replacement, so one byte that is not UTF-8 in a STRANGER's command line never
    turns the stop into NO-DATA (the attack on 65ffa97b4, finding 4: the strict table read raised, the stop read NO-DATA
    and the driver would skip its finisher, the incident's own consequence). A row the stop WOULD signal with such a byte
    stays NO-DATA: stop_loop.sh re-checks each pid by comparing its command text, which a replaced byte never matches, so
    it would print STOPPED while that process lived. Entry points: loop_procs.py's CLI and stop_loop.sh --dry, over a fake
    ps that prints raw bytes; no real process is listed, so nothing real can be selected."""

    def run_with(self, rows, argv):
        d = tempfile.mkdtemp(prefix="stop-bytes-")
        self.addCleanup(shutil.rmtree, d, True)
        home = os.path.join(d, "home"); os.makedirs(os.path.join(home, ".claude", "evidence"))
        runner = os.path.join(home, ".claude", "bin", "unit_runner.py").encode()
        table = [(pid, 1, pid, cmd.replace(b"RUNNER", runner)) for pid, cmd in rows]
        write(os.path.join(d, "fake", "ps"), FAKE_PS % (table,)); os.chmod(os.path.join(d, "fake", "ps"), 0o755)
        env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
        env.update(HOME=home, PATH=os.path.join(d, "fake") + os.pathsep + os.environ["PATH"])
        return subprocess.run(argv, env=env, capture_output=True, text=True, errors="replace", timeout=60)

    def test_a_strangers_undecodable_command_leaves_the_loops_table_readable(self):
        r = self.run_with([(620001, b"python3 RUNNER U U.1"), (777, b"python3 /tmp/viewer.py \xff stranger arg")],
                          [sys.executable, "-B", os.path.join(LOOP, "loop_procs.py"), "all"])
        self.assertEqual((r.returncode, [l.split()[0] for l in r.stdout.splitlines()]), (0, ["620001"]), r.stdout + r.stderr)

    def test_the_stop_is_not_blocked_by_a_strangers_undecodable_command(self):
        r = self.run_with([(620001, b"python3 RUNNER U U.1"), (777, b"python3 /tmp/viewer.py \xff stranger arg")],
                          ["bash", STOP, "--dry"])
        self.assertNotIn("NO-DATA", r.stdout + r.stderr)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("620001", r.stdout)
        self.assertNotIn("777 ", r.stdout)

    def test_a_row_the_stop_would_signal_with_an_undecodable_byte_is_no_data(self):
        r = self.run_with([(620001, b"python3 RUNNER U \xff")], [sys.executable, "-B", os.path.join(LOOP, "loop_procs.py"), "all"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO-DATA", r.stderr)


RUNNER_SRC = ("import os, subprocess, sys, time\n"
              "code, mark = sys.argv[1], sys.argv[2]\n"
              "p = subprocess.Popen([sys.executable, '-m', 'plugin.runtime.brother.core.or_fanout', mark + '.jobs', '--workers', '99'],\n"
              "                     cwd=code, start_new_session=True)\n"
              "with open(mark + '.part', 'w') as f: f.write(str(p.pid))\n"
              "os.replace(mark + '.part', mark)\n"
              "time.sleep(120)\n")
SLEEP_SRC = "import time\ntime.sleep(120)\n"


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    return path


def alive(proc_or_pid):
    if hasattr(proc_or_pid, "poll"):
        return proc_or_pid.poll() is None
    try:
        os.kill(proc_or_pid, 0)
    except OSError:
        return False
    # a stopped fan out is not this test's child, so launchd reaps it; a zombie still answers kill 0 for a moment
    r = subprocess.run(["ps", "-o", "stat=", "-p", str(proc_or_pid)], capture_output=True, text=True)
    return r.returncode == 0 and not r.stdout.strip().startswith("Z")


class RealProcesses(unittest.TestCase):
    """The incident's own shape on real processes: a runner of the run under test and the fixture's straggler copy,
    each with the or_fanout it started in its own session, plus a code root helper with and without its run."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="stop-run-identity-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = os.path.join(self.d, "home")
        self.runs = os.path.join(self.home, ".claude", "evidence", "loop-runs")
        os.makedirs(self.runs)
        self.procs, self.fanouts = [], []
        self.addCleanup(self.reap)
        self.env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}

    def reap(self):
        for p in self.procs:
            try: os.killpg(p.pid, signal.SIGKILL)
            except OSError: pass
            p.wait()
        for pid in self.fanouts:
            try: os.killpg(pid, signal.SIGKILL)
            except OSError: pass

    def spawn(self, argv, env=None, cwd=None):
        p = subprocess.Popen(argv, env=env or self.env, cwd=cwd, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def runner(self, bin_dir, code, name):
        """a unit runner at bin_dir/unit_runner.py that starts `python -m plugin...or_fanout` from its code root"""
        write(os.path.join(code, "plugin", "runtime", "brother", "core", "or_fanout.py"), SLEEP_SRC)
        mark = os.path.join(self.d, name + ".fanout")
        p = self.spawn([sys.executable, "-B", write(os.path.join(bin_dir, "unit_runner.py"), RUNNER_SRC), code, mark, "Z9", "Z9.1", "1"])
        for _ in range(200):
            if os.path.isfile(mark):
                break
            time.sleep(0.05)
        with open(mark) as f:
            fan = int(f.read())
        self.fanouts.append(fan)
        return p, fan

    def stop(self, *args):
        env = dict(self.env, HOME=self.home, STOP_LOOP_ONLY=self.d)
        return subprocess.run(["bash", STOP] + list(args), env=env, capture_output=True, text=True, timeout=90)

    def family(self):
        """({name: pid} of the run under test, {name: pid} of processes of no run of this loop)"""
        run = os.path.join(self.runs, "run-20260929-173700-1")
        mine, mine_fan = self.runner(os.path.join(self.home, ".claude", "bin"), os.path.join(self.d, "code"), "mine")
        stray_root = os.path.join(self.home, ".claude", "brother-scratch", "straggler-opk9ub3f")
        stray, stray_fan = self.runner(os.path.join(stray_root, "bin"), os.path.join(stray_root, "code"), "stray")
        helper = self.spawn([sys.executable, "-B", write(os.path.join(self.d, "code", "scripts", "diag_round.py"), SLEEP_SRC), "--limit", "6"],
                            env=dict(self.env, BROTHER_RUN_DIR=run))
        stray_helper = self.spawn([sys.executable, "-B", write(os.path.join(self.d, "other", "scripts", "diag_round.py"), SLEEP_SRC), "--limit", "6"])
        time.sleep(0.5)
        return ({"runner": mine.pid, "its fan out": mine_fan, "the helper of the run": helper.pid},
                {"straggler runner": stray.pid, "straggler fan out": stray_fan, "helper of no run": stray_helper.pid})

    def test_the_live_runner_count_reads_no_stranger(self):
        self.family()
        count = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "loop_procs.py"), "unit-runners"],
                               env=dict(self.env, HOME=self.home), capture_output=True, text=True, timeout=60)
        self.assertEqual((count.returncode, count.stdout.strip()), (0, "1"), "the live runner count read a stranger: " + count.stdout + count.stderr)

    def test_dry_lists_the_runs_family_and_no_stranger(self):
        owners, strangers = self.family()
        dry = self.stop("--dry")
        listed = {int(m) for m in re.findall(r"^(\d+) ", dry.stdout, re.M)}
        self.assertEqual(dry.returncode, 1, dry.stdout + dry.stderr)
        self.assertTrue(set(owners.values()) <= listed, "the run's own processes were not all listed: " + dry.stdout)
        self.assertFalse(set(strangers.values()) & listed, "--dry counted a stranger as the loop's: " + dry.stdout)

    def test_the_stop_kills_the_runs_family_leaves_the_stranger_and_reports_stopped(self):
        owners, strangers = self.family()
        r = self.stop("--runners-only")
        time.sleep(0.5)
        for name, pid in owners.items():
            self.assertFalse(alive(pid), "%s survived the stop: %s" % (name, r.stdout))
        for name, pid in strangers.items():
            self.assertTrue(alive(pid), "the stop signalled the %s, which belongs to no run of this loop: %s" % (name, r.stdout))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("STOPPED: nothing of the loop is alive", r.stdout)

        after = self.stop("--dry")
        self.assertEqual(after.returncode, 0, "the finisher's own check would still refuse: " + after.stdout + after.stderr)

    def test_a_strangers_undecodable_environment_is_neither_no_data_nor_the_loops(self):
        """Adversarial review finding 10 (2026-09-30): the run marker read decoded with the stop's strict mode, so one
        invalid byte in a STRANGER's environment raised, loop_procs died with a traceback, and the whole stop read NO-DATA
        (exit 2, the finisher skipped: the incident's consequence again). Only the BROTHER_RUN_DIR token is read from that
        output and the command prefix was already decoded strictly by the table read, so the environment is decoded with
        replace."""
        env = dict(self.env)
        env[b"BROTHER_ODD"] = b"\xff\xfe odd"   # a bytes environment value is legal on POSIX; it is not UTF-8
        stray = self.spawn([sys.executable, "-B", write(os.path.join(self.d, "other", "scripts", "diag_round.py"), SLEEP_SRC), "--limit", "6"], env=env)
        time.sleep(0.5)
        dry = self.stop("--dry")
        listed = {int(m) for m in re.findall(r"^(\d+) ", dry.stdout, re.M)}
        self.assertNotIn("NO-DATA", dry.stdout + dry.stderr, "one stranger's odd byte made the whole table unreadable: " + dry.stdout + dry.stderr)
        self.assertNotIn("Traceback", dry.stderr, dry.stderr)
        self.assertEqual((dry.returncode, stray.pid in listed), (0, False), dry.stdout + dry.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
