"""repair_wave.py's result contract, offline (owner witness 2026-09-26, review items 2, 4, 5, 6).

Every model, subprocess and fan-out seam is replaced before the script runs: no network, credential or spend.
The script under test is the one beside this file (repo relative), never a launch tree path.
  1. a failed grading or probing stage ends in a nonzero exit (it printed the failure and exited 0);
  2. an unreadable model registry refuses the wave before any job is written (it silently bought the default model);
  3. with no worker count given, a lane gets 3 workers, as the usage line says (it defaulted to 8);
  4. a fan-out that outlives its deadline is drained with its whole process group, grandchildren included, and
     reaped (it killed the direct child only, and a grandchild survived).
Run from the repository root: python3 -B scripts/test_repair_wave_contract.py
"""
import contextlib, importlib.util, io, json, os, re, runpy, shutil, signal, subprocess, sys, tempfile, time, types, unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent / "loop" / "repair_wave.py"
REAL_POPEN = subprocess.Popen


def _ps_runs():
    """Inside the export sandbox ps cannot execute (execvp: Operation not permitted), so repair_wave's process-table
    drain reports NO-DATA there by design. The one case that expects the drain to reach a grandchild asserts that
    report instead where ps is denied: the same probe scripts/test_merge_precompute.py uses."""
    try:
        return str(os.getpid()) in subprocess.run(["ps", "-p", str(os.getpid()), "-o", "pid="], capture_output=True,
                                                  text=True, timeout=10).stdout
    except OSError:
        return False


PS_RUNS = _ps_runs()
# The REAL code root reader (U3): repair_wave asks it where the fan out runs, and the environment decides the answer.
_spec = importlib.util.spec_from_file_location("repair_contract_model_router", str(SOURCE.parent / "model_router.py"))
REAL_ROUTER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REAL_ROUTER)


def modules(registry=lambda: {"deepseek": {}}):
    def _raise():
        raise RuntimeError("registry unreadable (fixture)")
    return {
        "probe_build": types.SimpleNamespace(classify=lambda *a: "CRASH", REFUSAL_VALUE=re.compile("^REFUSED$"), lessons=lambda: ""),
        "worker_mix": types.SimpleNamespace(picks=lambda per, *a, **k: ["deepseek"] * per, arm_stats=lambda: {}),
        # model_round_cost (F21, 2026-09-26): repair_wave.round_cost_for_arms calls this for every
        # arm before the EV gate; a fixture ev_gate lacking it broke every green case in this file
        # with an AttributeError the moment F21 landed. None matches the real function's own
        # cold start answer (no measured history yet), so round_cost_for_arms falls back to its
        # flat per-arm constant, exactly as it does against the real module.
        "ev_gate": types.SimpleNamespace(history=lambda *a: (0, 0), should_continue=lambda *a, **k: (True, "fixture"),
                                          model_round_cost=lambda *a, **k: None),
        "repair_advisor": types.SimpleNamespace(enabled=lambda *a: False),
        "model_router": types.SimpleNamespace(registry=_raise if registry is None else registry,
                                              # the seating list repair_wave reads since the FX-31.7 follow-up: every name of
                                              # the fixture registry seats, and an unreadable registry refuses here too
                                              seatable_names=_raise if registry is None else (lambda: list(registry())),
                                              code_root=REAL_ROUTER.code_root, Refused=REAL_ROUTER.Refused),
        "brief_fit": types.SimpleNamespace(fit=lambda *a, **k: "\n".join(a)),
        "grade_build": types.SimpleNamespace(private_hits=lambda *a: 0),
    }


def waves(root):
    bw, pw, nw = (root / n for n in ("build", "probe", "repair"))
    for p in (bw / "grades", bw / "out", pw / "logs"):
        p.mkdir(parents=True)
    prompt = root / "prompt.txt"
    prompt.write_text("Fixture: repair one deliberate failure.")
    (bw / "jobs.json").write_text(json.dumps([{"id": "F1", "prompt_file": str(prompt)}]))
    (bw / "grades/F1-r0.txt").write_text("PASS\nexit=0\n")   # grade_one.sh's real shape: the verdict, then the exit code
    (bw / "out/F1-r0-build.json").write_text(json.dumps({"edits": [{"path": "scripts/fixture.py", "new_file_content": "x = 1"}], "tests": []}))
    (pw / "logs/F1.done").write_text("DIRTY")
    (pw / "logs/F1-test.log").write_text("CRASH fixture ValueError forced\n")
    return bw, pw, nw


def run_wave(argv_tail, grade_rc=0, probe_rc=0, popen=None, registry=lambda: {"deepseek": {}}, env=None, held=False, ps_fail=False, ps_timeout=False, ps_fake_lines=None, mods=None):
    calls = []
    # A HOME of its own: this machine may carry the owner's real LOOP-HOLD.txt, and the wave reads the hold first.
    home = tempfile.mkdtemp(prefix="repair-home-")
    os.makedirs(os.path.join(home, ".claude", "evidence"))
    if held:
        with open(os.path.join(home, ".claude", "evidence", "LOOP-HOLD.txt"), "w") as f:
            f.write("owner stop (fixture)\n")
    # THE DRIVER NAMES THE CODE ROOT (U3): the fan out runs there. A proof phase case names none, so it refuses.
    code = {} if (env or {}).get("BROTHER_PROOF_PHASE") or os.environ.get("BROTHER_PROOF_PHASE") else \
        {"BROTHER_CODE_ROOT": str(SOURCE.parent.parent.parent)}
    env = dict(code, **dict(env or {}, HOME=home))

    def fake_run(argv, **kw):
        name = Path(argv[0]).name if len(argv) == 1 else Path(argv[1]).name
        if Path(argv[0]).name == "ps":
            if ps_timeout:
                raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 60)
            if ps_fail:
                raise OSError("process table unreadable (fixture)")
            if ps_fake_lines is not None:
                _line = ps_fake_lines.pop(0) if ps_fake_lines else ""
                return subprocess.CompletedProcess(argv, 0, stdout=_line, stderr="")
            p = REAL_POPEN(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out, err = p.communicate()
            return subprocess.CompletedProcess(argv, p.returncode, stdout=out, stderr=err)
        if name == "judge_calibrate.py":
            return subprocess.CompletedProcess(argv, 0, stdout="CALIB fixture\n", stderr="")
        if Path(argv[0]).name == "grade_lanes_par.sh":
            calls.append(("grade", grade_rc)); return subprocess.CompletedProcess(argv, grade_rc)
        if name == "probe_wave.py":
            calls.append(("probe", probe_rc)); kw["stdout"].write("probe fixture\n"); kw["stdout"].flush()
            return subprocess.CompletedProcess(argv, probe_rc)
        raise AssertionError("unexpected subprocess: %r" % (argv,))

    class FakeFanout:
        def __init__(self, argv, **kw):
            calls.append(("fanout", 0))
        def wait(self, timeout=None):
            return 0
        def poll(self):
            return 0
        def kill(self):
            pass
        pid = 0

    out = io.StringIO()
    try:
        with patch.dict(sys.modules, mods or modules(registry)), patch.object(sys, "argv", [str(SOURCE)] + argv_tail), \
                patch.object(subprocess, "run", fake_run), patch.object(subprocess, "Popen", popen or FakeFanout), \
                patch.dict(os.environ, env or {}, clear=False), contextlib.redirect_stdout(out):
            try:
                runpy.run_path(str(SOURCE), run_name="__main__"); code = 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        shutil.rmtree(home, True)   # the HOME of one wave run is never left in the system temp folder
    return code, calls, out.getvalue()


class Contract(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repair-contract-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)   # nothing is left in the system temp folder
        self.bw, self.pw, self.nw = waves(self.root)
        self.tail = [str(self.bw), str(self.pw), str(self.nw)]

    def test_all_green_exits_zero(self):
        code, calls, _ = run_wave(self.tail + ["1"])
        self.assertEqual(code, 0)
        self.assertIn(("grade", 0), calls)

    def test_an_unreadable_cost_ledger_refuses_the_round_before_any_dispatch(self):
        """Entry point (2026-09-30): the ledger exists but cannot be read, so the round's cost is unknown; the wave
        says so and dispatches nothing, never prices the round at the cold start fallback."""
        class CostUnreadable(Exception):
            pass

        def unreadable(*a, **k):
            raise CostUnreadable("the cost ledger for deepseek cannot be read (fixture)")
        mods = modules()
        mods["ev_gate"] = types.SimpleNamespace(history=lambda *a: (0, 0), should_continue=lambda *a, **k: (True, "fixture"),
                                                model_round_cost=unreadable, CostUnreadable=CostUnreadable)
        code, calls, out = run_wave(self.tail + ["1"], mods=mods)
        self.assertIn("SKIP EV: the round is refused, its cost is unknown", out)
        self.assertNotIn(("fanout", 0), calls, out)

    def test_failed_grading_is_a_nonzero_exit(self):
        code, _, out = run_wave(self.tail + ["1"], grade_rc=2)
        self.assertNotEqual(code, 0, out)

    def test_failed_probing_is_a_nonzero_exit(self):
        code, _, out = run_wave(self.tail + ["1"], probe_rc=3)
        self.assertNotEqual(code, 0, out)

    def test_unreadable_registry_refuses_before_any_job(self):
        code, calls, out = run_wave(self.tail + ["1"], registry=None)
        self.assertNotEqual(code, 0, out)
        self.assertNotIn(("fanout", 0), calls, "a wave with an unreadable registry must buy nothing")
        self.assertFalse((self.nw / "jobs.json").exists())
        self.assertIn("unreadable", out, "the refusal names its own cause, so each guard has a case only it answers")

    def test_empty_registry_refuses_before_any_job(self):
        code, calls, out = run_wave(self.tail + ["1"], registry=lambda: {})
        self.assertEqual(code, 3, out)
        self.assertIn("empty", out)
        self.assertNotIn(("fanout", 0), calls)

    def test_a_held_loop_buys_nothing(self):
        code, calls, out = run_wave(self.tail + ["1"], held=True)
        self.assertEqual(code, 5, out)
        self.assertNotIn(("fanout", 0), calls)
        self.assertFalse((self.nw / "jobs.json").exists())

    def test_default_is_three_workers_per_lane(self):
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_WORKERS_PER_ROUND"}
        with patch.dict(os.environ, env, clear=True):
            code, _, out = run_wave(self.tail)
        jobs = json.loads((self.nw / "jobs.json").read_text())
        self.assertEqual(len(jobs), 3, out)


def fanout_exiting(code):
    """A fan-out stand-in whose wait() and poll() report `code`, with no process behind it."""
    class Fanout:
        pid = 0
        def __init__(self, argv, **kw):
            pass
        def wait(self, timeout=None):
            return code
        def poll(self):
            return code
        def kill(self):
            pass
    return Fanout


class StageContract(unittest.TestCase):
    """Review 2026-09-26 (REVIEW-FANOUT-EXIT): a fan-out that failed or ran past its deadline still ended the wave with
    exit 0 when grading and probing returned 0, and a wave whose dirty work could not be dispatched (no usable worker
    arm, zero or negative workers) printed "nothing to repair" and exited 0. Each case isolates one condition."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repair-stage-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)   # nothing is left in the system temp folder
        self.bw, self.pw, self.nw = waves(self.root)
        self.tail = [str(self.bw), str(self.pw), str(self.nw)]

    def test_a_failed_fanout_is_a_nonzero_exit(self):
        code, calls, out = run_wave(self.tail + ["1"], popen=fanout_exiting(7))
        self.assertNotEqual(code, 0, out)
        self.assertIn("fan-out exit 7", out)

    def test_a_fanout_past_its_deadline_is_a_nonzero_exit(self):
        class Late:
            pid = 0
            def __init__(self, argv, **kw):
                self.waited = 0
            def wait(self, timeout=None):
                self.waited += 1
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fanout", timeout)
                return -9
            def poll(self):
                return None
            def kill(self):
                pass
        with patch.object(os, "killpg", lambda pid, sig: None):
            code, _, out = run_wave(self.tail + ["1"], popen=Late, env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("deadline", out)

    def test_no_usable_worker_arm_refuses_dirty_work(self):
        code, calls, out = run_wave(self.tail + ["1"], registry=lambda: {"jev": {}})
        self.assertEqual(code, 3, out)
        self.assertIn("no usable worker", out)
        self.assertNotIn(("fanout", 0), calls)

    def test_zero_workers_refuse(self):
        code, calls, out = run_wave(self.tail + ["0"])
        self.assertEqual(code, 3, out)
        self.assertNotIn("nothing to repair", out)
        # its own reason, so the no-usable-arm refusal cannot answer for it (mutation M-RW-PER survived without this)
        self.assertIn("workers per lane", out)

    def test_negative_workers_refuse(self):
        code, calls, out = run_wave(self.tail + ["-2"])
        self.assertEqual(code, 3, out)
        self.assertIn("workers per lane", out)

    def test_a_clean_wave_is_still_exit_zero(self):
        (self.pw / "logs/F1.done").write_text("CLEAN")
        code, calls, out = run_wave(self.tail + ["1"])
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to repair", out)


class TimeoutDrain(unittest.TestCase):
    def test_a_late_fanout_is_drained_with_its_grandchildren(self):
        root = Path(tempfile.mkdtemp(prefix="repair-drain-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)
        pidf = root / "grandchild.pid"
        child = ("import subprocess, sys, time\n"
                 "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                 "open(%r, 'w').write(str(g.pid)); time.sleep(5)\n" % str(pidf))
        seen = {}

        def popen(argv, **kw):
            p = REAL_POPEN([sys.executable, "-c", child], **kw)
            seen["p"] = p
            for _ in range(100):
                if pidf.exists() and pidf.read_text().strip():
                    break
                time.sleep(0.05)
            return p

        code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=popen, env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        gc = int(pidf.read_text())
        time.sleep(0.5)
        try:
            os.kill(gc, 0); alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive:
            os.kill(gc, signal.SIGKILL)       # the fixture's own process only
        self.assertFalse(alive, "the fan-out's grandchild survived the timeout: " + out[-300:])
        self.assertIsNotNone(seen["p"].poll(), "the killed fan-out was not reaped")

    def test_killpg_permission_error_still_drains_grandchild(self):
        root = Path(tempfile.mkdtemp(prefix="repair-drain-perm-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)
        pidf = root / "grandchild.pid"
        child = ("import subprocess, sys, time\n"
                 "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                 "open(%r, 'w').write(str(g.pid)); time.sleep(5)\n" % str(pidf))
        seen = {}

        def popen(argv, **kw):
            p = REAL_POPEN([sys.executable, "-c", child], **kw)
            seen["p"] = p
            for _ in range(100):
                if pidf.exists() and pidf.read_text().strip():
                    break
                time.sleep(0.05)
            return p

        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=popen, env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        gc = int(pidf.read_text())
        time.sleep(0.5)
        try:
            os.kill(gc, 0); alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive:
            os.kill(gc, signal.SIGKILL)
        if PS_RUNS:
            self.assertFalse(alive, "the fan-out's grandchild survived the process-table drain: " + out[-300:])
        else:
            # NO-DATA by name, never a pass: with no process table the drain cannot reach the grandchild and must SAY so;
            # the wave then fails on that report. The kill itself is asserted only where ps runs (the branch above).
            self.assertIn("the fan-out's group kill is NO-DATA", out, "no process table here, yet the drain did not report NO-DATA: " + out[-300:])
        self.assertIsNotNone(seen["p"].poll(), "the killed fan-out was not reaped")
        self.assertNotEqual(code, 0, out)

    def test_os_kill_permission_error_on_group_member_is_no_data(self):
        root = Path(tempfile.mkdtemp(prefix="repair-drain-killperm-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)

        class Late:
            pid = 0
            def __init__(self, argv, **kw):
                pass
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fanout", timeout)
                return -9
            def poll(self):
                return None
            def kill(self):
                pass

        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")), \
             patch.object(os, "kill", side_effect=PermissionError("fixture denied")):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=Late,
                                    ps_fake_lines=["11111 0\n"],
                                    env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("NO-DATA", out, out)
        self.assertIn("could not be signalled", out, out)

    def test_an_unreadable_process_table_line_is_no_data_not_an_empty_group(self):
        # `ps -axo pid=,pgid=` prints no header, so a line that is not two integers is anomalous; skipping it could
        # hide a live member of the fan-out's group and let the drain read as empty.
        root = Path(tempfile.mkdtemp(prefix="repair-drain-badline-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)

        class Late:
            pid = 0
            def __init__(self, argv, **kw):
                pass
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fanout", timeout)
                return -9
            def poll(self):
                return None
            def kill(self):
                pass

        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=Late,
                                    ps_fake_lines=["11111 zero\n"],
                                    env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("process table could not be read", out, out)
        self.assertIn("NO-DATA", out, out)

    def test_a_group_member_that_survives_the_kill_pass_is_no_data(self):
        root = Path(tempfile.mkdtemp(prefix="repair-drain-survive-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)

        class Late:
            pid = 0
            def __init__(self, argv, **kw):
                pass
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fanout", timeout)
                return -9
            def poll(self):
                return None
            def kill(self):
                pass

        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")), \
             patch.object(os, "kill", side_effect=ProcessLookupError()):
            # three reads: before the TERM, the fresh one before the KILL (RR lane C), and the survivor check after it
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=Late,
                                    ps_fake_lines=["11111 0\n", "11111 0\n", "11111 0\n"],
                                    env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("NO-DATA", out, out)
        self.assertIn("survived", out, out)

    def test_ps_timeout_is_no_data(self):
        root = Path(tempfile.mkdtemp(prefix="repair-drain-pstimeout-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)

        class Late:
            pid = 0
            def __init__(self, argv, **kw):
                pass
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fanout", timeout)
                return -9
            def poll(self):
                return None
            def kill(self):
                pass

        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=Late, ps_timeout=True,
                                    env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("NO-DATA", out, out)
        self.assertIn("timed out", out, out)

    def test_unreadable_process_table_makes_drain_no_data(self):
        root = Path(tempfile.mkdtemp(prefix="repair-drain-nodata-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)

        class Late:
            pid = 0
            def __init__(self, argv, **kw):
                pass
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fanout", timeout)
                return -9
            def poll(self):
                return None
            def kill(self):
                pass

        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=Late, ps_fail=True,
                                    env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("NO-DATA", out, out)

    def test_wave_reports_drain_no_data_as_failure(self):
        root = Path(tempfile.mkdtemp(prefix="repair-drain-nodata-caller-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)

        class Late:
            pid = 0
            def __init__(self, argv, **kw):
                pass
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fanout", timeout)
                return -9
            def poll(self):
                return None
            def kill(self):
                pass

        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=Late, ps_fail=True,
                                    env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("fan-out group drain NO-DATA", out, out)
        self.assertIn("REPAIR WAVE FAILED", out, out)


class CleanExitDrain(unittest.TestCase):
    """Finding m (s6 adversary, 2026-09-26, reproduced by execution): a fan-out that exits 0 BEFORE its deadline was
    never drained, so a member of its own session (a paid bridge call it did not wait for) kept running while the
    wave reported clean. The fan-out runs in its own session, so any member left after the leader exits is its."""

    def spawn(self, child, pidf=None):
        seen = {}

        def popen(argv, **kw):
            p = REAL_POPEN([sys.executable, "-c", child], **kw)
            seen["p"] = p
            for _ in range(100):
                if pidf is None or (pidf.exists() and pidf.read_text().strip()):
                    break
                time.sleep(0.05)
            return p
        return popen, seen

    def test_a_clean_fanout_that_leaves_a_member_running_is_drained_and_fails(self):
        root = Path(tempfile.mkdtemp(prefix="repair-clean-leak-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)
        pidf = root / "leftover.pid"
        child = ("import subprocess, sys\n"
                 "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                 "open(%r, 'w').write(str(g.pid))\n" % str(pidf))
        popen, seen = self.spawn(child, pidf)
        code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=popen)
        left = int(pidf.read_text())
        time.sleep(0.5)
        try:
            os.kill(left, 0); alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive:
            os.kill(left, signal.SIGKILL)     # the fixture's own process only
        self.assertFalse(alive, "a member of the clean fan-out's group survived the wave: " + out[-300:])
        self.assertNotEqual(code, 0, "a wave that had to kill leftover fan-out work is not clean: " + out[-300:])
        self.assertIn("left", out, out)
        self.assertIn("REPAIR WAVE FAILED", out, out)

    def test_a_clean_fanout_with_nothing_left_stays_clean(self):
        root = Path(tempfile.mkdtemp(prefix="repair-clean-empty-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)
        popen, seen = self.spawn("import sys\nsys.exit(0)\n")
        code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=popen)
        self.assertEqual(code, 0, out[-400:])

    def test_a_group_probe_refused_is_not_a_clean_wave(self):
        root = Path(tempfile.mkdtemp(prefix="repair-clean-perm-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)
        popen, seen = self.spawn("import sys\nsys.exit(0)\n")
        real_killpg = os.killpg

        def killpg(pgid, sig):
            if pgid == seen["p"].pid:
                raise PermissionError("fixture denied")
            return real_killpg(pgid, sig)
        with patch.object(os, "killpg", side_effect=killpg):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"], popen=popen, ps_fail=True)
        self.assertNotEqual(code, 0, "a group that could not be checked cannot read as drained: " + out[-300:])
        self.assertIn("REPAIR WAVE FAILED", out, out)

    def test_a_fanout_without_a_real_pid_signals_nothing(self):
        # The contract fixtures' fan-out reports pid 0; os.killpg(0, ...) would signal THIS process group.
        root = Path(tempfile.mkdtemp(prefix="repair-clean-pid0-"))
        self.addCleanup(shutil.rmtree, str(root), True)   # nothing is left in the system temp folder
        bw, pw, nw = waves(root)
        sent = []
        with patch.object(os, "killpg", side_effect=lambda pgid, sig: sent.append((pgid, sig))):
            code, _, out = run_wave([str(bw), str(pw), str(nw), "1"])
        self.assertEqual([s for s in sent if s[0] <= 0], [], "a non positive pid was signalled")


if __name__ == "__main__":
    unittest.main(verbosity=1)
