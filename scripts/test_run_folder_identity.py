"""A unit runner never works inside another run's folder, and never runs beside a live runner of its own sub unit.

WHAT IT PROTECTS (RR lane C, 2026-09-27, reproduced by an adversarial audit on hub main e7e17784c). unit_runner.py
named its folder <sub>-HHMMSS under ~/.claude/evidence/unit-runs and created it with exist_ok, so:
  - recycled: a later run at the same HHMMSS (any later day) reused an old folder, and its grade and probe stages
    read the old run's PASS grade and CLEAN probe verdict as their own: unverified bytes were marked READY;
  - duplicate: two runners of one sub unit in the same second shared one folder and one PID file, and both went on
    to buy a brief and a round.
The name format is kept (ten readers parse it). The folder is now created EXCLUSIVELY, moving to the next free
second on a clash, and a per sub unit lock refuses a second live runner before any folder exists.

Entry point: `python3 unit_runner.py <unit> <sub> 1` as a subprocess, over the same stub bin the straggler suite uses
(scripts/test_runner_straggler_settles.py), with the expected value gate stubbed to stop at round 0 so no fan out
starts. Nothing calls a model. Run from the repository root: python3 -B scripts/test_run_folder_identity.py
"""
import ast, glob, json, os, shutil, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, HERE)
import test_runner_straggler_settles as ST  # noqa: E402  the stub bin and code tree of the runner's own suite

SCRATCH = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
OLD_STATUS = "QUARANTINE refused at the gates alone, an OLD run's verdict\n"


class Fixture(object):
    """A stub bin holding the REAL unit_runner.py, a code root, a launch worktree and a HOME, all under one scratch dir."""

    def __init__(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.d = tempfile.mkdtemp(prefix="run-identity-", dir=SCRATCH)
        self.code, self.wt, self.bin = (os.path.join(self.d, n) for n in ("code", "wt", "bin"))
        self.home = os.path.join(self.d, "home")
        self.runs = os.path.join(self.home, ".claude", "evidence", "unit-runs")
        os.makedirs(self.bin); os.makedirs(self.runs)
        ST.tree(self.code); ST.tree(self.wt)
        for name in ST.REAL_LOOP:
            shutil.copy(os.path.join(LOOP, name), self.bin)
        for name, body in ST.STUBS.items():
            with open(os.path.join(self.bin, name), "w") as f: f.write(body)
        os.chmod(os.path.join(self.bin, "grade_lane.sh"), 0o755)
        os.makedirs(os.path.join(self.wt, "docs", "plan", "specs"))
        with open(os.path.join(self.wt, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as f:
            json.dump({"units": [{"id": "Z9", "state": "OPEN", "spec": "docs/plan/specs/Z9.md", "sub_units": ["Z9.1"],
                                  "evidence": "", "command_runners": []}]}, f)
        self.rec = os.path.join(self.d, "record.txt"); open(self.rec, "w").close()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "GIT_")) and k != "RUNNER_HINT"}
        self.env.update(HOME=self.home, BROTHER_CODE_ROOT=self.code, BROTHER_OR_STATE_ROOT=os.path.join(self.d, "state"),
                        WORKERS_PER_ROUND="1", BROTHER_BUILD_PLAN_MODEL="off", BROTHER_REPAIR_ADVISOR_MODEL="off",
                        FAKE_FANOUT_RECORD=self.rec, FAKE_FANOUT_MODE="finishes", FAKE_EV_STOP="1",
                        PYTHONDONTWRITEBYTECODE="1")

    def start(self):
        return subprocess.Popen([sys.executable, "-B", os.path.join(self.bin, "unit_runner.py"), "Z9", "Z9.1", "1"],
                                cwd=self.wt, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def folders(self):
        return sorted(p for p in glob.glob(os.path.join(self.runs, "Z9.1-*")) if os.path.isdir(p))

    def cleanup(self):
        shutil.rmtree(self.d, ignore_errors=True)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


class ARecycledNameIsNeverInherited(unittest.TestCase):
    """Old run folders sit at every second the runner could pick; they hold an old run's verdict and no live runner.
    Only the exclusive creation can refuse this fixture: the per sub unit lock is free, no runner holds it."""

    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()
        now = time.time()
        cls.planted = set()
        for i in range(-2, 26):   # the runner claims its folder a second or so after it starts; 25 s of margin
            p = os.path.join(cls.f.runs, "Z9.1-%s" % time.strftime("%H%M%S", time.localtime(now + i)))
            os.makedirs(os.path.join(p, "round0", "grades"), exist_ok=True)
            with open(os.path.join(p, "STATUS"), "w") as fh: fh.write(OLD_STATUS)
            with open(os.path.join(p, "round0", "grades", "Z9.1-r0.txt"), "w") as fh: fh.write("PASS old bytes\n")
            cls.planted.add(p)
        p = cls.f.start()
        cls.out, _ = p.communicate(timeout=120)
        cls.rc, cls.pid = p.returncode, p.pid

    @classmethod
    def tearDownClass(cls):
        cls.f.cleanup()

    def test_no_old_folder_was_written_into(self):
        touched = sorted(os.path.basename(p) for p in self.planted
                         if _read(os.path.join(p, "STATUS")) != OLD_STATUS or os.path.exists(os.path.join(p, "PID")))
        self.assertEqual(touched, [], "the runner worked inside an old run's folder: %s\n%s" % (touched, self.out[-800:]))

    def test_the_runner_made_exactly_one_new_folder_of_its_own(self):
        mine = [p for p in self.f.folders() if p not in self.planted]
        self.assertEqual(len(mine), 1, "expected one new run folder, found %s\n%s" % (mine, self.out[-800:]))
        self.assertEqual((_read(os.path.join(mine[0], "PID")) or "").strip(), str(self.pid))
        self.assertTrue((_read(os.path.join(mine[0], "STATUS")) or "").startswith("EXHAUSTED by the expected value gate"),
                        _read(os.path.join(mine[0], "STATUS")))
        self.assertEqual(self.rc, 1, self.out[-800:])


class ASecondLiveRunnerOfOneSubUnitRefuses(unittest.TestCase):
    """Runner A holds the brief stage for five seconds; runner B of the same sub unit starts meanwhile. Only the per sub
    unit lock can refuse this fixture: exclusive creation alone would give B a folder of its own and let it buy."""

    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()
        cls.entered = os.path.join(cls.f.d, "entered.txt")
        with open(os.path.join(cls.f.bin, "build_brief.py"), "w") as fh:
            fh.write("import os, sys, time\nwith open(%r, 'a') as f: f.write('%%d\\n' %% os.getppid())\n"
                     "time.sleep(5)\nopen(sys.argv[4], 'w').write('BRIEF\\n')\n" % cls.entered)
        a = cls.f.start()
        until = time.time() + 30
        while time.time() < until and not (_read(cls.entered) or "").strip():
            time.sleep(0.05)
        b = cls.f.start()
        try:
            cls.b_out, _ = b.communicate(timeout=60)
        finally:
            if b.poll() is None: b.kill(); b.wait()
        cls.b_rc, cls.b_pid = b.returncode, b.pid
        cls.entered_after_b = (_read(cls.entered) or "").split()
        cls.a_out, _ = a.communicate(timeout=60)
        cls.a_rc, cls.a_pid = a.returncode, a.pid

    @classmethod
    def tearDownClass(cls):
        cls.f.cleanup()

    def test_the_second_runner_never_reaches_the_brief(self):
        self.assertEqual(self.entered_after_b, [str(self.a_pid)],
                         "a second live runner of one sub unit entered the brief stage: %s\n%s" % (self.entered_after_b, self.b_out[-800:]))

    def test_the_second_runner_refuses_by_name_with_exit_two(self):
        self.assertEqual(self.b_rc, 2, self.b_out[-800:])
        self.assertIn("REFUSED", self.b_out, self.b_out[-800:])

    def test_the_refused_runner_made_no_folder_and_the_first_finished_in_its_own(self):
        folders = self.f.folders()
        self.assertEqual(len(folders), 1, folders)
        self.assertEqual((_read(os.path.join(folders[0], "PID")) or "").strip(), str(self.a_pid))
        self.assertEqual(self.a_rc, 1, self.a_out[-800:])


class TheRunnerRefusesRatherThanReuse(unittest.TestCase):
    """The runner's own refusals, each leaving no folder a reader could take for a run."""

    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.cleanup)

    def test_every_candidate_name_taken_refuses_with_exit_two(self):
        now, planted = time.time(), set()
        for i in range(-2, 75):   # more than the runner's 60 tries from wherever in this window it starts
            p = os.path.join(self.f.runs, "Z9.1-%s" % time.strftime("%H%M%S", time.localtime(now + i)))
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "STATUS"), "w") as fh: fh.write(OLD_STATUS)
            planted.add(p)
        p = self.f.start()
        out, _ = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 2, out[-800:])
        self.assertIn("REFUSED", out)
        self.assertEqual(set(self.f.folders()), planted, "a folder was made or reused")
        self.assertEqual([q for q in planted if _read(os.path.join(q, "STATUS")) != OLD_STATUS], [])

    def test_a_pid_that_cannot_be_written_takes_its_own_folder_back(self):
        import resource

        def no_file_growth():
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))   # python ignores SIGXFSZ, so a write raises instead
        p = subprocess.Popen([sys.executable, "-B", os.path.join(self.f.bin, "unit_runner.py"), "Z9", "Z9.1", "1"],
                             cwd=self.f.wt, env=self.f.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             preexec_fn=no_file_growth)
        out, _ = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 2, out[-800:])
        self.assertIn("PID file cannot be written", out, out[-800:])
        self.assertEqual(self.f.folders(), [], "a folder with no PID and no STATUS was left behind")


def lifted_claim():
    """claim_run_dir lifted out of the runner's source (the runner runs its whole pass at import)."""
    path = os.path.join(LOOP, "unit_runner.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "claim_run_dir"]
    ns = {"os": os, "time": time}
    if fn:
        exec(compile(ast.Module(body=fn, type_ignores=[]), path, "exec"), ns)
    return ns.get("claim_run_dir")


class TheClaimItself(unittest.TestCase):
    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.d = tempfile.mkdtemp(prefix="run-claim-", dir=SCRATCH)
        self.addCleanup(shutil.rmtree, self.d, True)
        self.claim = lifted_claim()
        self.assertIsNotNone(self.claim, "unit_runner.py has no claim_run_dir")
        self.now = 1_700_000_000.0

    def name(self, i):
        return "Z9.1-%s" % time.strftime("%H%M%S", time.localtime(self.now + i))

    def test_a_taken_second_moves_to_the_next_free_one_and_keeps_the_format(self):
        os.mkdir(os.path.join(self.d, self.name(0)))
        with open(os.path.join(self.d, self.name(0), "STATUS"), "w") as fh: fh.write(OLD_STATUS)
        got = self.claim(self.d, "Z9.1", self.now, 5)
        self.assertEqual(got, os.path.join(self.d, self.name(1)))
        self.assertEqual(_read(os.path.join(self.d, self.name(0), "STATUS")), OLD_STATUS)

    def test_every_second_taken_refuses_with_none_and_creates_nothing(self):
        for i in range(3):
            os.mkdir(os.path.join(self.d, self.name(i)))
        self.assertIsNone(self.claim(self.d, "Z9.1", self.now, 3))
        self.assertEqual(sorted(os.listdir(self.d)), sorted(self.name(i) for i in range(3)))


class TheDiagnosticianReadsTheNewestRunByTheClock(unittest.TestCase):
    """midnight: M.1-235000 is last night's EXHAUSTED run, M.1-001000 is this morning's READY one. diag_brief ordered
    by the folder NAME, so the READY sub unit was sent to the diagnostician (a paid call) and its READY run was marked
    DIAGNOSED. Entry point: `diag_brief.py <out>` as a subprocess over a scratch HOME and a scratch repo root."""

    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.d = tempfile.mkdtemp(prefix="run-midnight-", dir=SCRATCH)
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home, self.repo = os.path.join(self.d, "home"), os.path.join(self.d, "repo")
        runs = os.path.join(self.home, ".claude", "evidence", "unit-runs")
        self.old, self.new = os.path.join(runs, "M.1-235000"), os.path.join(runs, "M.1-001000")
        for path, text, when in ((self.old, "EXHAUSTED after 5 rounds\n", 1_000_000.0), (self.new, "READY /x/b.json\n", 2_000_000.0)):
            os.makedirs(path)
            with open(os.path.join(path, "STATUS"), "w") as fh: fh.write(text)
            os.utime(path, (when, when))
        os.makedirs(os.path.join(self.home, ".claude", "bin"))
        with open(os.path.join(self.home, ".claude", "bin", "grade_build.py"), "w") as fh:
            fh.write("def private_hits(text): return []\n")
        os.makedirs(os.path.join(self.repo, "docs", "plan"))
        with open(os.path.join(self.repo, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as fh:
            json.dump({"units": [{"id": "M", "state": "OPEN", "sub_units": ["M.1"], "spec": "spec.md", "owns": [], "evidence": ""}]}, fh)
        with open(os.path.join(self.repo, "spec.md"), "w") as fh: fh.write("# M.1\nA fixture spec.\n")

    def test_newest_status_reads_the_clock_not_the_name(self):
        sys.path.insert(0, LOOP)
        import diag_brief
        got = diag_brief.newest_status([self.new + "/", self.old + "/"], lambda d: _read(os.path.join(d, "STATUS")) or "")
        self.assertEqual(got["M.1"], "READY", "the name order returned last night's EXHAUSTED run")

    def test_a_ready_sub_unit_is_never_sent_to_the_diagnostician(self):
        out = os.path.join(self.d, "diag")
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "diag_brief.py"), out], cwd=self.repo, env=env,
                           capture_output=True, text=True, timeout=60)
        jobs = json.load(open(os.path.join(out, "jobs.json"))) if os.path.isfile(os.path.join(out, "jobs.json")) else None
        self.assertEqual(jobs, [], "a READY sub unit was briefed for diagnosis: %s\n%s" % (jobs, r.stdout[-600:] + r.stderr[-600:]))
        self.assertFalse(os.path.exists(os.path.join(self.new, "DIAGNOSED")), "the READY run was marked DIAGNOSED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
