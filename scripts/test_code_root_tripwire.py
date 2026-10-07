#!/usr/bin/env python3
"""U3 (B5-04, B5-08, B5-15, objection 17): every Lane W execution site runs the frozen code root, never the landing tree.

THE SHAPE, from DESIGN-FINAL.md W-1. C is the candidate staged from THIS working tree by deploy_stamped.stage_candidate,
so a mutation under test is inside it, with a one line WITNESS at the top of each claimed entry. T is a landing tree
whose claimed code paths hold only a TRIPWIRE line that records itself and exits 97. A site that runs landing tree code
trips T; a site that runs the code root leaves a witness from C, or is recorded by the subprocess seam with cwd == C.

SCENARIO 1 runs land_batch.main exactly as loop_pass.sh:183 runs it (`python3 ~/.claude/bin/land_batch.py <build>`,
cwd the landing tree) with a gate history and a run directory, so gate_order, record_gate and the closing pass all run.
It is not the whole loop_pass.sh: that script's own six sites (loop_pass.sh:22, 133, 141, 169, 178, 216) are Lane D's
and run landing tree code on this base by design, so a pass level tripwire cannot be clean before Lane D lands.
SCENARIO 2 drives check_wave, ev_gate, repair_wave, probe_wave, self_check, probe_round, diag_round and worktree_sentry
at their own entry points, with every subprocess recorded: the fan out runs `-m` with cwd == C and every path it is
handed, in argv and inside its jobs file, is absolute (a relative one would be read against C, which holds code only).
SCENARIO 3 is the fail direction: in a proof phase with no BROTHER_CODE_ROOT every site refuses before it spends or
runs anything; it never falls back to the landing tree.
SCENARIO 4 is a static lint over the Lane W files: no string names a candidate entry by a cwd relative path, and no
sys.path entry is the cwd.
Run: python3 -B scripts/test_code_root_tripwire.py"""
import ast, contextlib, importlib.util, io, json, os, runpy, shutil, subprocess, sys, tempfile, time, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
import deploy_stamped  # noqa: E402  the staging function the deploy itself uses (Lane F)

# a boxed module cannot write the witness log (review 17: the gate policy runs in the sandbox), so a refused write is no witness
WITNESS = ('import os as _wos\ntry:\n    _wos.environ.get("WITNESS_LOG") and '
           'open(_wos.environ["WITNESS_LOG"], "a").write(_wos.path.realpath(__file__) + "\\n")\nexcept OSError:\n    pass')
TRIPWIRE = ('import os as _tos, sys as _tsys; open(_tos.environ.get("TRIPWIRE_LOG", _tos.devnull), "a")'
            '.write(_tos.path.realpath(__file__) + "\\n"); _tsys.exit(97)\n')
CORE = "plugin/runtime/brother/core/"
WITNESSED = ("scripts/close_unit.py", CORE + "dream_gate.py", CORE + "dream_gate_policy.py", CORE + "openrouter_ledger.py",
             "scripts/worktree_sentry.py")
PLUGIN_INITS = ("plugin/__init__.py", "plugin/runtime/__init__.py", "plugin/runtime/brother/__init__.py", CORE + "__init__.py")
POISONED = ("scripts/close_unit.py", "scripts/loop/commit_scan.py", "scripts/jev_decide.py", CORE + "dream_gate.py",
            CORE + "dream_gate_policy.py", CORE + "openrouter_ledger.py", CORE + "or_fanout.py") + PLUGIN_INITS
W_FILES = ("scripts/loop/land_batch.py", "scripts/loop/check_wave.py", "scripts/loop/repair_wave.py",
           "scripts/loop/probe_wave.py", "scripts/loop/self_check.py", "scripts/loop/ev_gate.py", "scripts/probe_round.py",
           "scripts/diag_round.py", "scripts/worktree_sentry.py")

# THE 26 SITES (DESIGN-FINAL.md U3 item 4, lines as read at 8de64b498), each claimed ONCE: Lane W's by a test here.
SITES = {
    "scripts/loop/ev_gate.py:53": "test_ev_gate_imports_the_ledger_from_the_code_root",
    "scripts/loop/land_batch.py:198": "test_scenario_1_landing_runs_the_code_root",
    "scripts/loop/land_batch.py:227": "test_scenario_1_landing_runs_the_code_root",
    "scripts/loop/land_batch.py:851": "test_scenario_1_landing_runs_the_code_root",
    "scripts/loop/land_batch.py:856": "test_scenario_1_landing_runs_the_code_root",
    "scripts/loop/check_wave.py:99": "test_check_wave_runs_jev_decide_from_the_code_root",
    "scripts/loop/repair_wave.py:285": "test_repair_wave_fans_out_from_the_code_root",
    "scripts/loop/probe_wave.py:151": "test_probe_wave_fans_out_from_the_code_root",
    "scripts/loop/self_check.py:74": "test_self_check_fans_out_from_the_code_root",
    "scripts/probe_round.py:185": "test_probe_round_fans_out_from_the_code_root",
    "scripts/diag_round.py:33": "test_diag_round_fans_out_from_the_code_root",
    "scripts/worktree_sentry.py:38": "test_worktree_sentry_run_from_the_code_root_watches_the_landing_tree",
    "scripts/loop/loop_until.sh:186": "Lane D", "scripts/loop/loop_until.sh:199": "Lane D",
    "scripts/loop/loop_until.sh:254": "Lane D", "scripts/loop/loop_until.sh:292": "Lane D",
    "scripts/loop/loop_pass.sh:22": "Lane D", "scripts/loop/loop_pass.sh:133": "Lane D",
    "scripts/loop/loop_pass.sh:141": "Lane D", "scripts/loop/loop_pass.sh:169": "Lane D",
    "scripts/loop/loop_pass.sh:178": "Lane D", "scripts/loop/loop_pass.sh:216": "Lane D",
    "scripts/loop/model_call.py:50": "Lane M2", "scripts/loop/unit_runner.py:312": "Lane M2",
    "scripts/loop/unit_runner.py:492": "Lane M2", "scripts/loop/burn_guard.py:167": "Lane M1",
}

ROOT = C = None


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LANDINGS = load("tw_landings", os.path.join(LOOP, "test_land_batch_landings.py"))
CONTRACT = load("tw_repair_contract", os.path.join(HERE, "test_repair_wave_contract.py"))


def mark(path, line):
    """Insert line after the module docstring and any __future__ import, so the file still compiles and keeps __doc__."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    after = 0
    for i, node in enumerate(ast.parse(src).body):
        doc = i == 0 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
            and isinstance(node.value.value, str)
        if doc or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            after = node.end_lineno
        else:
            break
    lines = src.splitlines(True)
    lines.insert(after, line + "\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))


def setUpModule():
    global ROOT, C
    ROOT = tempfile.mkdtemp(prefix="tripwire-")
    C = os.path.join(ROOT, "bin", "candidate")
    # the pinned HOME's hooks root is a closure root; the same stand in hook test_candidate_stage.py stages with
    home = os.path.join(ROOT, "stage-home")
    LANDINGS.write(os.path.join(home, ".claude", "hooks", "bm_session_cap.py"),
                   '"""Scratch stand in for the machine installed session cap hook."""\nimport json\n')
    with mock.patch.dict(os.environ, {"HOME": home}):
        deploy_stamped.stage_candidate(REPO, C)
    for rel in WITNESSED:
        mark(os.path.join(C, rel), WITNESS)


def tearDownModule():
    shutil.rmtree(ROOT, True)


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip()]
    except OSError:
        return []


@contextlib.contextmanager
def environ(**kv):
    """os.environ with no code root, proof phase or launch worktree from outside, then kv."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE", "BROTHER_LAUNCH_WORKTREE")}
    env["BROTHER_PROBES"] = "on"   # these scenarios exercise the probe machinery, off by default since plan E step 2c
    env.update(kv)
    with mock.patch.dict(os.environ, env, clear=True):
        yield


@contextlib.contextmanager
def cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class Recorder(object):
    """subprocess.run and Popen stand in: every call is kept with its keyword arguments."""

    def __init__(self, handler=None):
        self.calls, self.handler = [], handler

    def run(self, argv, **kw):
        self.calls.append((list(argv), kw))
        out = self.handler(list(argv), kw) if self.handler else None
        return out if out is not None else subprocess.CompletedProcess(argv, 0, "", "")

    def fanouts(self):
        return [(a, k) for a, k in self.calls if "-m" in a and "plugin.runtime.brother.core.or_fanout" in a]

    def popen(self, argv, **kw):
        self.calls.append((list(argv), kw))

        class Proc(object):
            pid, returncode = 0, 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass
        return Proc()


class Tripwire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tw-", dir=ROOT)

    def assert_fanout_from_code_root(self, recorder):
        fan = recorder.fanouts()
        self.assertEqual(len(fan), 1, "one fan out was recorded: %r" % (recorder.calls,))
        argv, kw = fan[0]
        self.assertEqual(kw.get("cwd"), C, "the fan out runs with cwd == the code root: %r" % (kw.get("cwd"),))
        paths = [a for a in argv[3:] if a.endswith(".json")]
        self.assertTrue(paths, argv)
        for a in paths:
            self.assertTrue(os.path.isabs(a), "every path argument is absolute: %r" % (argv,))
        with open(argv[3], encoding="utf-8") as fh:
            jobs = json.load(fh)
        self.assertTrue(jobs)
        for job in jobs:
            for key in ("prompt_file", "out"):
                if key in job:
                    self.assertTrue(os.path.isabs(job[key]), "job %s is absolute: %r" % (key, job))
        return argv, kw, jobs

    # ---- scenario 1 --------------------------------------------------------------------------------------------
    def landing(self, code_root, extra_env=None):
        # A LANDING BOXES ITS CHECKS, AND A BOX CANNOT NEST (2026-10-03): under the pre-push gate's hermetic rerun this
        # suite already runs inside sandbox.sb, so the landing's own sandboxed done check reads "a sandbox inside a
        # sandbox" and the build is DROPPED for a reason that is the host's, not the lander's. NO-DATA by name there,
        # the way test_grade_build_guard reads it; on an open host the scenario runs in full.
        _G = load("tw_grade_build", os.path.join(LOOP, "grade_build.py"))   # the loop's grader by path, never a same named stub
        if _G.sandbox_ready():
            self.skipTest("NO-DATA: %s" % _G.sandbox_ready())
        f = LANDINGS.Fixture(os.path.join(self.tmp, "land"), code_root=code_root,
                             tree_files={rel: TRIPWIRE for rel in POISONED}, extra_env=dict(
                                 extra_env or {}, WITNESS_LOG=os.path.join(self.tmp, "witness"),
                                 TRIPWIRE_LOG=os.path.join(self.tmp, "tripwire")))
        sys.path.insert(0, REPO)
        from plugin.runtime.brother.core import dream_gate
        LANDINGS.write(os.path.join(f.home, ".claude", "evidence", "loop-runs", "r0", "gates.tsv"),
                       "".join(dream_gate.format_gate_line("landing." + g, 0, 50) for g in ("registration", "system_doc", "bundle")))
        return f

    def test_scenario_1_landing_runs_the_code_root(self):
        f = self.landing(C)
        rc, out = f.land()
        tripped = read_lines(os.path.join(self.tmp, "tripwire"))
        self.assertEqual(tripped, [], "no landing tree code ran: %r\n%s" % (tripped, out))
        self.assertEqual(rc, 0, out)
        self.assertIn("CLOSED  U1", out, "the closing pass closed the unit")
        seen = set(read_lines(os.path.join(self.tmp, "witness")))
        self.assertIn(os.path.realpath(os.path.join(C, "scripts/close_unit.py")), seen, "the closer ran from the code root: %s" % out)
        # THE GATE POLICY RAN FROM THE CODE ROOT, IN THE BOX (review 17 finding 1): it ordered the gates, and its witness
        # write, aimed outside the sandbox, never landed; the recorder imports nothing at all
        self.assertIn("ORDER   D13 order from", out, "the policy ordered the gates from the code root")
        for rel in (CORE + "dream_gate.py", CORE + "dream_gate_policy.py"):
            self.assertNotIn(os.path.realpath(os.path.join(C, rel)), seen, "%s ran outside the box: %s" % (rel, out))
        scans = [os.path.realpath(p) for p in read_lines(os.path.join(f.root, "scan.log"))]
        self.assertEqual(scans, [os.path.realpath(os.path.join(f.bin, "commit_scan.py"))] * 2,
                         "the landing's scan and the closure's scan both ran from the frozen bin")
        self.assertTrue(os.path.getsize(os.path.join(f.run_dir, "gates.tsv")) > 0, "record_gate wrote through the code root")
        self.assertEqual([n for n in os.listdir(f.tmpdir) if n.startswith("gate-history-")], [],
                         "gate_order read the history through the code root and left no temp directory")

    # ---- scenario 2 --------------------------------------------------------------------------------------------
    def test_check_wave_runs_jev_decide_from_the_code_root(self):
        cw = load("tw_check_wave", os.path.join(LOOP, "check_wave.py"))
        rec = Recorder(lambda a, k: subprocess.CompletedProcess(a, 1, "", ""))
        with environ(BROTHER_CODE_ROOT=C):
            self.assertIsNone(cw.jev_verdict("brief", "review", runner=rec.run))
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(rec.calls[0][0][2], os.path.join(C, "scripts", "jev_decide.py"))

    def ev_gate_probe(self, **env):
        tree = tempfile.mkdtemp(dir=self.tmp)
        subprocess.run(["git", "init", "-q", tree], check=True, capture_output=True)
        for rel in PLUGIN_INITS + (CORE + "openrouter_ledger.py",):
            LANDINGS.write(os.path.join(tree, rel), TRIPWIRE)
        code = ("import sys, inspect; sys.path.insert(0, %r); import ev_gate; f, E = ev_gate._real_actual_costs_for_model(); "
                "print(inspect.getsourcefile(f) if f else 'NONE')" % LOOP)
        full = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_CODE_ROOT", "BROTHER_PROOF"))}
        full.update(env, WITNESS_LOG=os.path.join(self.tmp, "witness"), TRIPWIRE_LOG=os.path.join(self.tmp, "tripwire"),
                    PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", "-c", code], cwd=tree, env=full, capture_output=True, text=True, timeout=120)
        return r, read_lines(os.path.join(self.tmp, "tripwire"))

    def test_ev_gate_imports_the_ledger_from_the_code_root(self):
        r, tripped = self.ev_gate_probe(BROTHER_CODE_ROOT=C)
        self.assertEqual(tripped, [], r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.path.realpath(r.stdout.strip()), os.path.realpath(os.path.join(C, CORE + "openrouter_ledger.py")))

    def test_repair_wave_fans_out_from_the_code_root(self):
        bw, pw, nw = CONTRACT.waves(CONTRACT.Path(self.tmp))
        rec = Recorder()
        with environ(BROTHER_CODE_ROOT=C):
            code, _, out = CONTRACT.run_wave([str(bw), str(pw), str(nw), "1"], popen=rec.popen, env={"BROTHER_CODE_ROOT": C})
        self.assertEqual(code, 0, out)
        self.assert_fanout_from_code_root(rec)

    def probe_wave_run(self, **env):
        base = tempfile.mkdtemp(dir=self.tmp)
        tree, home = os.path.join(base, "tree"), os.path.join(base, "home")
        LANDINGS.write(os.path.join(tree, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"),
                       json.dumps({"units": [{"id": "L", "sub_units": ["L1"], "spec": "docs/spec-l1.md"}]}))
        LANDINGS.write(os.path.join(tree, "docs/spec-l1.md"), "### L1\n")
        LANDINGS.write(os.path.join(home, ".brothersbe-private-names"), "")
        os.makedirs(os.path.join(home, ".claude", "evidence"))
        wave, pw = os.path.join(base, "wave"), os.path.join(base, "wave-probes")
        LANDINGS.write(os.path.join(wave, "grades", "L1-r0.txt"), "PASS\nexit=0\n")   # the grader's own form (grade_lane.sh rule)
        LANDINGS.write(os.path.join(wave, "out", "L1-r0-build.json"), json.dumps({"edits": [], "tests": []}))

        def handler(argv, kw):
            if argv[1].endswith("probe_brief.py"):
                LANDINGS.write(argv[-1], "a probe brief\n")
        rec = Recorder(handler)
        mods = {"grade_build": types.SimpleNamespace(private_hits=lambda *a: 0)}
        code = 0
        with environ(HOME=home, **env), cwd(tree), mock.patch.dict(sys.modules, mods), \
                mock.patch.object(subprocess, "run", rec.run), mock.patch.object(subprocess, "Popen", rec.popen), \
                mock.patch.object(sys, "argv", [os.path.join(LOOP, "probe_wave.py"), wave, pw]), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                runpy.run_path(os.path.join(LOOP, "probe_wave.py"), run_name="__main__")
            except SystemExit as exc:
                code = exc.code
        return code, rec, pw

    def test_probe_wave_fans_out_from_the_code_root(self):
        code, rec, _ = self.probe_wave_run(BROTHER_CODE_ROOT=C)
        self.assertIn(code, (0, None))
        self.assert_fanout_from_code_root(rec)

    def self_check_run(self, **env):
        work = tempfile.mkdtemp(dir=self.tmp)
        bad = {"edits": [{"path": "scripts/x.py", "new_file_content": "import socket\nsocket.create_connection(('x', 80))\n"}],
               "tests": [], "done_check": "python3 scripts/test_x.py", "mutations": []}
        LANDINGS.write(os.path.join(work, "round", "out", "S-r0-build.json"), json.dumps(bad))
        LANDINGS.write(os.path.join(work, "round", "prompts", "p.md"), "build it\n")
        LANDINGS.write(os.path.join(work, "round", "jobs.json"), json.dumps(
            [{"id": "S-r0", "model": "deepseek", "prompt_file": "round/prompts/p.md", "out": "round/out/S-r0-build.json"}]))
        sc = load("tw_self_check", os.path.join(LOOP, "self_check.py"))
        rec = Recorder()
        with environ(**env), cwd(work), mock.patch.object(subprocess, "run", rec.run), \
                contextlib.redirect_stdout(io.StringIO()):
            result = sc.screen("round")
        return result, rec

    def test_self_check_fans_out_from_the_code_root(self):
        result, rec = self.self_check_run(BROTHER_CODE_ROOT=C)
        self.assertEqual(result[0], 1)
        self.assert_fanout_from_code_root(rec)

    def probe_round_run(self, **env):
        base = tempfile.mkdtemp(dir=self.tmp)
        tree, runs = os.path.join(base, "tree"), os.path.join(base, "runs")
        LANDINGS.write(os.path.join(tree, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"),
                       json.dumps({"units": [{"id": "P", "sub_units": ["P1.a"], "spec": "docs/spec-p.md"}]}))
        build = os.path.join(runs, "P1.a-000001", "round0", "out", "P1.a-r0-build.json")
        LANDINGS.write(build, "{}")
        LANDINGS.write(os.path.join(runs, "P1.a-000001", "STATUS"), "READY-UNPROBED %s\n" % build)

        def handler(argv, kw):
            if argv[1].endswith("probe_brief.py"):
                LANDINGS.write(argv[-1], "a probe brief\n")
        rec = Recorder(handler)
        pr = load("tw_probe_round", os.path.join(HERE, "probe_round.py"))
        pr.RUNS = runs
        mods = {"grade_build": types.SimpleNamespace(private_hits=lambda *a: 0)}
        with environ(**env), cwd(tree), mock.patch.dict(sys.modules, mods), mock.patch.object(subprocess, "run", rec.run), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = pr.main(["--out", "rel-out"])
        return rc, rec, tree

    def test_probe_round_fans_out_from_the_code_root(self):
        rc, rec, _ = self.probe_round_run(BROTHER_CODE_ROOT=C)
        self.assertEqual(rc, 2, "no adversary answered the recorded fan out, so the round is NO-DATA")
        self.assert_fanout_from_code_root(rec)

    def diag_round_run(self, **env):
        def handler(argv, kw):
            if argv[1].endswith("diag_brief.py"):
                out = argv[2]
                LANDINGS.write(os.path.join(out, "jobs.json"), json.dumps(
                    [{"id": "diag-S1", "model": "deepseek", "prompt_file": os.path.join(out, "S1.md"),
                      "out": os.path.join(out, "out", "S1.json")}]))
                return subprocess.CompletedProcess(argv, 0, "LANES 1\n", "")
            if argv[1].endswith("diag_apply.py"):
                return subprocess.CompletedProcess(argv, 0, "ROUND PROVEN 0 | UNPROVEN 0 | REFUSED 0 | NO-DATA 0\n", "")
        rec = Recorder(handler)
        dr = load("tw_diag_round", os.path.join(HERE, "diag_round.py"))
        work = tempfile.mkdtemp(dir=self.tmp)
        with environ(**env), cwd(work), mock.patch.object(subprocess, "run", rec.run), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = dr.main(["--out", "rel-diag"])
        return rc, rec

    def test_diag_round_fans_out_from_the_code_root(self):
        rc, rec = self.diag_round_run(BROTHER_CODE_ROOT=C)
        self.assertEqual(rc, 1, "nothing was proven")
        self.assert_fanout_from_code_root(rec)

    def sentry(self, *args, **env):
        full = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_CODE_ROOT", "BROTHER_LAUNCH", "BROTHER_PROOF"))}
        full.update(env, PYTHONDONTWRITEBYTECODE="1", BROTHER_WORKTREE_CLAIM=os.path.join(self.tmp, "claim"))
        where = full.pop("CWD", None) or self.tree
        r = subprocess.run([sys.executable, "-B", os.path.join(C, "scripts", "worktree_sentry.py")] + list(args),
                           cwd=where, env=full, capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stdout + r.stderr).strip()

    def sentry_tree(self):
        self.tree = os.path.join(self.tmp, "sentry-tree")
        os.makedirs(self.tree)
        env = LANDINGS.git_env()
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "base"]):
            subprocess.run(["git"] + args, cwd=self.tree, env=env, check=True, capture_output=True)

    def test_worktree_sentry_run_from_the_code_root_watches_the_landing_tree(self):
        self.sentry_tree()
        rc, fp1 = self.sentry("snapshot")
        self.assertEqual(rc, 0, fp1)
        LANDINGS.write(os.path.join(self.tree, "foreign.txt"), "a write nobody in this pass made\n")
        rc, fp2 = self.sentry("snapshot")
        self.assertEqual(rc, 0, fp2)
        self.assertNotEqual(fp1, fp2, "a write in the landing tree moves the fingerprint the code root copy takes")
        os.remove(os.path.join(self.tree, "foreign.txt"))
        rc, fp3 = self.sentry("snapshot", CWD=C, BROTHER_LAUNCH_WORKTREE=self.tree)
        self.assertEqual((rc, fp3), (0, fp1), "BROTHER_LAUNCH_WORKTREE names the tree, whatever the cwd")
        rc, out = self.sentry("claim", str(os.getpid()))
        self.assertEqual(rc, 0, out)
        self.assertIn("root=%s\n" % os.path.realpath(self.tree), open(os.path.join(self.tmp, "claim")).read())

    def test_worktree_sentry_refuses_a_launch_worktree_that_is_not_a_directory(self):
        self.sentry_tree()
        rc, out = self.sentry("snapshot", BROTHER_LAUNCH_WORKTREE=os.path.join(self.tmp, "missing"))
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)

    def test_worktree_sentry_outside_any_checkout_is_no_data(self):
        self.tree = self.tmp
        rc, out = self.sentry("snapshot", CWD=C)
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)

    def test_worktree_sentry_a_dead_claimant_reads_stale(self):
        self.sentry_tree()
        dead = subprocess.Popen(["true"])
        dead.wait()
        LANDINGS.write(os.path.join(self.tmp, "claim"), "pid=%d\nat=%f\nstart=\nroot=x\n" % (dead.pid, time.time()))
        rc, out = self.sentry("check")
        self.assertEqual(rc, 0, out)
        self.assertIn("STALE", out)

    def test_worktree_sentry_a_live_claimant_with_a_future_stamp_is_not_evicted(self):
        self.sentry_tree()
        LANDINGS.write(os.path.join(self.tmp, "claim"), "pid=1\nat=%f\nstart=\nroot=x\n" % (time.time() + 3600))
        rc, out = self.sentry("claim", str(os.getpid()))
        self.assertEqual(rc, 1, out)
        self.assertIn("FUTURE", out)

    # ---- scenario 3: a proof phase with no code root refuses at every site ------------------------------------
    def test_proof_phase_without_a_code_root_refuses_at_every_site(self):
        proof = {"BROTHER_PROOF_PHASE": "RB"}
        cw = load("tw_check_wave_p", os.path.join(LOOP, "check_wave.py"))
        rec = Recorder()
        with environ(**proof):
            self.assertIsNone(cw.jev_verdict("brief", "review", runner=rec.run))
        self.assertEqual(rec.calls, [], "check_wave ran nothing")
        r, tripped = self.ev_gate_probe(**proof)
        self.assertEqual((r.returncode, r.stdout.strip(), tripped), (0, "NONE", []), r.stderr)
        bw, pw, nw = CONTRACT.waves(CONTRACT.Path(os.path.join(self.tmp, "rw")))
        rec = Recorder()
        with environ(**proof):
            code, _, out = CONTRACT.run_wave([str(bw), str(pw), str(nw), "1"], popen=rec.popen, env=proof)
        self.assertEqual(code, 3, out)
        self.assertEqual(rec.fanouts(), [])
        self.assertFalse((nw / "jobs.json").exists(), "repair_wave wrote no job")
        code, rec, pw2 = self.probe_wave_run(**proof)
        self.assertEqual(code, 3)
        self.assertEqual(rec.fanouts(), [])
        result, rec = self.self_check_run(**proof)
        self.assertEqual((result, rec.calls), ((1, 0), []))
        rc, rec, tree = self.probe_round_run(**proof)
        self.assertEqual((rc, rec.fanouts()), (2, []))
        self.assertFalse(os.path.exists(os.path.join(tree, "rel-out", "jobs.json")))
        rc, rec = self.diag_round_run(**proof)
        self.assertEqual((rc, rec.calls), (2, []))
        lb = load("tw_land_batch_p", os.path.join(LOOP, "land_batch.py"))
        rec = Recorder()
        with environ(**proof), contextlib.redirect_stdout(io.StringIO()):
            lines = lb.closing_pass(["U1"], lambda argv, *a: rec.run(argv), None, "hub", "main")
            order, why = lb.gate_order(runs_root=self.tmp)
        self.assertEqual(rec.calls, [], "the closing pass ran nothing")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("CLOSE-RED U1") and "code root" in lines[0], lines)
        # the gate recorder runs no code since review 17 (it writes dream_gate's line itself); the policy is the site
        self.assertTrue(why.startswith("fixed order") and list(order) == list(lb.GATES), why)

    # ---- scenario 4: static lint over the Lane W files -------------------------------------------------------
    def test_no_lane_w_file_names_candidate_code_by_a_cwd_relative_path(self):
        forbidden = set(deploy_stamped.CANDIDATE_ENTRIES) | {"scripts/loop/commit_scan.py"}
        bad = []
        for rel in W_FILES:
            with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
                src = fh.read()
            if "sys.path.insert(0, os.getcwd())" in src:
                bad.append("%s: puts the cwd on sys.path" % rel)
            # an ARGV is a list that also holds sys.executable or an interpreter flag; a path in one of those is code
            # the child runs, resolved against the cwd when it is relative
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, (ast.List, ast.Tuple)):
                    continue
                argv = any((isinstance(e, ast.Attribute) and e.attr == "executable") or
                           (isinstance(e, ast.Constant) and e.value in ("-B", "-m", "python3")) for e in node.elts)
                for e in node.elts if argv else ():
                    if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value in forbidden:
                        bad.append("%s:%d runs %r relative to the cwd" % (rel, e.lineno, e.value))
        self.assertEqual(bad, [])

    def test_every_site_is_claimed_once(self):
        self.assertEqual(len(SITES), 26)
        mine = {s: t for s, t in SITES.items() if t.startswith("test_")}
        self.assertEqual(len(mine), 12)
        for site, name in mine.items():
            self.assertTrue(callable(getattr(self, name, None)), "%s names a test that exists: %s" % (site, name))


class LanderNeverRunsTheLandingTree(unittest.TestCase):
    """Review 15 finding 1 (2026-10-03), executed by the reviewer: a dream_gate_policy.py in the landing tree wrote a marker
    on import, because gate_order and record_gate did sys.path.insert(0, code_root()) and model_router.code_root falls back
    to the checkout outside a proof, which inside the lander is the tree a build just wrote into. The lander's code root is
    BROTHER_CODE_ROOT (or the proof's refusal), else the frozen copy of the base commit, never the landing tree; the gate
    policy, the gate recorder, the closer and the failure ledger all route through it. Every case runs the lander's entry
    in a fresh process with cwd = a landing tree whose plugin gate modules are poisoned, so a marker is the defect itself."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tw-lander-", dir=ROOT)   # under the module's own scratch: an empty HOME has no brother-scratch
        self.tree, self.frozen, self.runs = (os.path.join(self.tmp, n) for n in ("tree", "frozen", "runs"))
        self.marker = os.path.join(self.tmp, "marker")
        shutil.copytree(os.path.join(REPO, CORE), os.path.join(self.frozen, CORE))   # the frozen copy holds the real gate modules
        os.makedirs(os.path.join(self.frozen, "scripts"))
        shutil.copy2(os.path.join(REPO, "scripts", "gate_order.py"), os.path.join(self.frozen, "scripts"))   # the policy's one import past the core
        for init in PLUGIN_INITS:
            LANDINGS.write(os.path.join(self.frozen, init), ""); LANDINGS.write(os.path.join(self.tree, init), "")
        for mod in ("dream_gate_policy.py", "dream_gate.py"):   # the landing tree's are a build's writes: a marker on import
            LANDINGS.write(os.path.join(self.tree, CORE, mod), "open(%r, 'a').write(%r + '\\n')\n" % (self.marker, mod))
        sys.path.insert(0, REPO)
        from plugin.runtime.brother.core import dream_gate
        LANDINGS.write(os.path.join(self.runs, "r0", "gates.tsv"),
                       "".join(dream_gate.format_gate_line("landing." + g, 0, 50) for g in ("registration", "system_doc", "bundle")))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lander(self, code, **env):
        """`code` run against land_batch in a fresh interpreter, cwd the landing tree, the live run's env shape (no code root,
        no proof phase) unless a case sets one."""
        e = {k: v for k, v in os.environ.items() if k not in ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE")}; e.update(env)
        r = subprocess.run([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, %r); import land_batch as LB\n%s" % (LOOP, code)],
                           cwd=self.tree, env=e, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def marked(self):
        return read_lines(self.marker)

    def test_the_gate_order_reads_the_policy_from_the_frozen_copy_never_the_tree(self):
        # the policy runs boxed since review 17, and a box cannot nest (inside the hermetic push check): NO-DATA by name there
        _G = load("tw_grade_build_go", os.path.join(LOOP, "grade_build.py"))
        if _G.sandbox_ready():
            self.skipTest("NO-DATA: %s" % _G.sandbox_ready())
        rc, out = self.lander("print(LB.gate_order(runs_root=%r, frozen=%r)[1])" % (self.runs, self.frozen))
        self.assertEqual(rc, 0, out)
        self.assertIn("D13 order from 1 gate log(s)", out, out)
        self.assertEqual(self.marked(), [], "the landing tree's gate policy was imported: %r\n%s" % (self.marked(), out))

    def test_without_a_frozen_copy_the_order_is_fixed_and_the_tree_is_not_imported(self):
        rc, out = self.lander("print(LB.gate_order(runs_root=%r)[1])" % self.runs)
        self.assertEqual(rc, 0, out)
        self.assertIn("fixed order: the policy refused (inside the lander the code root is never the landing tree", out)
        self.assertEqual(self.marked(), [], "the landing tree's gate policy was imported: %r" % self.marked())

    def test_the_gate_recorder_imports_nothing_and_keeps_dream_gates_line_rules(self):
        # review 17 finding 1: the writer was imported in process; the lander now writes dream_gate's line itself
        run_dir = os.path.join(self.tmp, "run"); os.makedirs(run_dir)
        rc, out = self.lander("print(LB.record_gate(%r, 'landing.x', 0, 10)); print(LB.record_gate(%r, 'bad\\tname', 1, 10)); "
                              "print(LB.record_gate(%r, 'landing.y', -9, 10))" % (run_dir, run_dir, run_dir))
        self.assertEqual(rc, 0, out)
        self.assertEqual([l for l in out.splitlines() if l in ("True", "False")], ["True", "False", "False"], out)
        self.assertEqual(read_lines(os.path.join(run_dir, "gates.tsv")), ["landing.x\t0\t10"])
        from plugin.runtime.brother.core import dream_gate
        self.assertEqual([r["check_name"] for r in dream_gate.read_gates(os.path.join(run_dir, "gates.tsv"))], ["landing.x"])
        self.assertEqual(self.marked(), [], "a gate module was imported: %r" % self.marked())

    def test_the_code_root_is_never_the_landing_tree(self):
        probe = ("import os\n"
                 "def ask(**kw):\n"
                 "    try: return LB.code_root(**kw)\n"
                 "    except Exception as exc: return 'REFUSED ' + type(exc).__name__ + ': ' + str(exc)[:70]\n"
                 "print('bare', ask()); print('frozen', ask(frozen=%r)); print('tree', ask(frozen=os.getcwd()))\n"
                 "os.environ['BROTHER_CODE_ROOT'] = %r; print('env', ask())\n"
                 "os.environ['BROTHER_CODE_ROOT'] = os.getcwd(); print('envtree', ask())\n"
                 "del os.environ['BROTHER_CODE_ROOT']; os.environ['BROTHER_PROOF_PHASE'] = 'RB'; print('proof', ask())\n"
                 % (self.frozen, self.frozen))
        rc, out = self.lander(probe)
        self.assertEqual(rc, 0, out)
        got = dict(l.split(" ", 1) for l in out.strip().splitlines() if " " in l)
        self.assertTrue(got["bare"].startswith("REFUSED Refused: inside the lander the code root is never the landing tree"), out)
        self.assertEqual(got["frozen"], self.frozen, out)
        self.assertTrue(got["tree"].startswith("REFUSED Refused: inside the lander the code root is never the landing tree"), out)
        self.assertEqual(got["env"], self.frozen, out)
        self.assertTrue(got["envtree"].startswith("REFUSED Refused: inside the lander the code root is never the landing tree"), out)
        self.assertTrue(got["proof"].startswith("REFUSED Refused: a proof phase runs frozen code only"), out)

    def test_the_closer_and_the_failure_ledger_run_from_the_frozen_copy(self):
        code = ("calls = []\n"
                "class R:\n"
                "    returncode, stdout, stderr = 1, 'U NOT CLOSED: red check\\n', ''\n"
                "def sh(argv, log, *a, **k):\n"
                "    calls.append(list(argv)); return R()\n"
                "LB.boxed = sh   # the ledger runs boxed since review 17: recorded with the closer\n"
                "lines = LB.closing_pass(['U'], sh, None, 'hub', 'b', frozen=%r)\n"
                "print('LINES', lines)\n"
                "for c in calls: print('CALL', next((a for a in c if a.endswith('.py')), c))\n" % self.frozen)
        rc, out = self.lander(code)
        self.assertEqual(rc, 0, out)
        scripts = [l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("CALL ") and l.endswith(".py")]
        self.assertTrue(any(s == os.path.join(self.frozen, "scripts", "close_unit.py") for s in scripts), out)
        self.assertTrue(any(s == os.path.join(self.frozen, "scripts", "failure_ledger.py") for s in scripts), out)
        self.assertFalse(any(s.startswith(self.tree + os.sep) for s in scripts), "a landing tree script ran: %s" % out)
        rc, out = self.lander(code.replace("frozen=%r" % self.frozen, "frozen=None"))
        self.assertEqual(rc, 0, out)
        self.assertIn("CLOSE-RED U: the code root is refused (inside the lander the code root is never the landing tree", out)
        self.assertNotIn("CALL ", out, "nothing runs when the code root is refused: " + out)


if __name__ == "__main__":
    unittest.main()
