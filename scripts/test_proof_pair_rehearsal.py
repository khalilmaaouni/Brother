#!/usr/bin/env python3
"""D-9 (U12, objections 7 and 18): the proof pair rehearsed end to end on this machine, every paid call stubbed.

WHAT RUNS FOR REAL. The candidate is deployed into a scratch HOME's bin by the real scripts/loop/deploy_stamped.sh
(BROTHER_DEPLOY_TARGET; its canary answered by the same sitecustomize stub test_deploy_canary.py uses, and its own
parity check run). Then the launcher that ships in that bin, proof_pair.sh --rehearsal, runs the whole lifecycle:
preflight, the ledger baseline and the freeze under E, RB (proof start, sandbox observation, work, drain, ending,
settle, stop, end snapshot, receipt), the handoff, RC, and acceptance. Nothing is faked between those steps.

WHAT IS STUBBED, and only that. loop_pass.sh is replaced before the freeze by a zero cost pass that lands nothing,
starts nothing and exits 45 (PASS DID NOTHING); loop_report.py by one that takes 60 s (the end report must never hold
the handoff). osascript is shadowed on PATH. No model is called, no network is used, no Claude CLI exists here. The
disk, swap and file event readings are the named seams the lifecycle suite uses, so this machine's own state never
decides a case, and STOP_LOOP_ONLY confines every runner stop to this scratch box.

THE WINDOW. BROTHER_PROOF_MIN_WINDOW_S=1 (the rehearsal knob), so each run lasts about three to four minutes (its
deadline is launch + window + 180 s, rounded up to the minute). Acceptance must then refuse the pair on duration and
no_rehearsal_knob, which is correct for a rehearsal, while consecutive_windows is measured for real.

CASES, one condition each:
  PairRehearsal       the measured handoff: RC work start minus RB receipt end, read from the two receipts and printed
                      (REHEARSAL GAP), in [0, 30] s; consecutive_windows PASS; duration and no_rehearsal_knob FAIL for
                      both runs; the launcher's caller sees end of file within 5 s of its exit (the 60 s report is
                      detached); RB drained on pass exit 45 and ended DEADLINE, not UNPRODUCTIVE (D-6).
  RcDirPrecreated     the RC run directory exists before RC: no RC run, the refusal recorded, the pair not PASS.
  RbProofUnwritable   RB's proof directory becomes read only mid run: RECEIPT FAILURE and no RC.
  RcSetupDelayed      a 35 s stall in RC's setup (a python3 shim on PATH, first RC call): consecutive_windows FAIL.
  UnfundedMidRun      a runner holds a reservation that leaves no headroom: UNFUNDED, the ending barrier, settle sees
                      the runner release it (SETTLED), then the runner is stopped; no RC.
  HoldPresent         LOOP-HOLD.txt exists: refused before anything, the file untouched.
  RbLaunchFails       the loop lease is held by a live process: the RB driver refuses, the result records exit 2, no RC.
A machine without sandbox-exec fails with NO-DATA, never a pass.

Run: python3 -B scripts/test_proof_pair_rehearsal.py [-v] [PairRehearsal]      (about 25 minutes for every case)
"""
# hermetic-budget-seconds: 2400  (real-time waits: 473.7 s alone in a checkout, over 900 s on the export tree in the pre-push gate, 2026-09-27)
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
import deploy_stamped  # noqa: E402  the loop's own staging function, the one the deploy uses
import proof_accept  # noqa: E402
import tool_stamp  # noqa: E402

WINDOW = "1"
GAP = "5"
MAX_GAP = proof_accept.MAX_GAP_SECONDS
PAIR_LINE = re.compile(r"^PAIR (\S+): pair directory (.+)$")
HOOK = b'"""Scratch stand in for the machine installed session cap hook."""\nimport json\n'
CANARY_STUB = '''import os, subprocess
_real = subprocess.run
def _run(argv, *a, **kw):
    if isinstance(argv, (list, tuple)) and argv and str(argv[-1]).endswith("/loop_canary.py"):
        return subprocess.CompletedProcess(argv, 0, "CANARY WOULD LAND: rehearsal stub, judging path not replayed\\n", "")
    return _real(argv, *a, **kw)
subprocess.run = _run
'''
PS = ('#!/bin/sh\ncase "$*" in\n  *pgid=,command=*) printf "1 0 1 /sbin/launchd\\n"; exit 0;;\n  *) exit 1;;\nesac\n')
IDLE_PASS = '#!/bin/bash\necho "PASS DID NOTHING: rehearsal stub, nothing to land and nothing to start"\nexit 45\n'
SLOW_REPORT = "import time\ntime.sleep(60)\nprint('REHEARSAL REPORT')\n"
# The export does not carry docs/plan/{model-registry,loop-roles,loop-canary}.json (docs/plan/EXPORT-ALLOWLIST.txt
# has no line for them), so a hermetic run of this fixture on the exported tree has none of the three at ROOT. The
# tracked scripts/fixtures/*-fixture.json copies stand in for exactly those three, byte identical to the real ones
# in a checkout that ships them (config_source_dir prefers the real file whenever it exists).
CONFIG_FIXTURES = {"model-registry.json": "model-registry-fixture.json",
                    "loop-roles.json": "loop-roles-fixture.json",
                    "loop-canary.json": "loop-canary-fixture.json"}


def config_source_dir(box):
    """A directory holding the loop's three configs for this scratch run: the real docs/plan copy when this tree
    ships it, the tracked fixture shape when it does not. Test-only staging seam; deploy_stamped.py itself never
    falls back on its own (config_dir() there only takes BROTHER_DEPLOY_CONFIG_DIR when a caller sets it)."""
    dest = os.path.join(box, "config-source")
    os.makedirs(dest, exist_ok=True)
    for name, fixture in CONFIG_FIXTURES.items():
        real = os.path.join(ROOT, "docs", "plan", name)
        src = real if os.path.isfile(real) else os.path.join(HERE, "fixtures", fixture)
        shutil.copy2(src, os.path.join(dest, name))
    return dest


def w(path, body, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(body, bytes) else "w") as fh:
        fh.write(body)
    os.chmod(path, mode)


def git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout


def slurp(path, mode="r"):
    with open(path, mode) as fh:
        return fh.read()


def stamp(t):
    return datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))


class Scratch:
    """One scratch HOME holding a deployed (or staged) candidate, a pair intake record, a ledger and a landing tree.

    deploy=True runs the real deploy_stamped.sh; deploy=False copies scripts/loop and stages the candidate from this
    working tree with deploy_stamped.stage_candidate (the handoff suite's faster bin, same layout)."""

    def __init__(self, deploy=True, pass_body=IDLE_PASS, loop_report=SLOW_REPORT, pair_hours=20.0, record=None):
        root = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        os.makedirs(root, exist_ok=True)
        self.box = tempfile.mkdtemp(prefix="run-pair-", dir=root)
        self.config_source = config_source_dir(self.box)
        self.home = os.path.join(self.box, "home")
        self.tmp = os.path.join(self.box, "tmp")
        self.shim = os.path.join(self.box, "shim")
        self.fake = os.path.join(self.box, "fake")
        self.tree = os.path.join(self.box, "tree")
        self.bin = os.path.join(self.home, ".claude", "bin")
        self.evidence = os.path.join(self.home, ".claude", "evidence")
        self.state = os.path.join(self.home, ".claude", "brother-or-dispatch-state")
        self.runs = os.path.join(self.evidence, "loop-runs")
        for p in (self.tmp, self.shim, os.path.join(self.fake, "bin"), self.bin, self.state,
                  os.path.join(self.evidence, "loop-intake")):
            os.makedirs(p, exist_ok=True)
        w(os.path.join(self.shim, "osascript"), '#!/bin/bash\necho "$*" >> "%s/osascript.log"\nexit 0\n' % self.box, 0o755)
        w(os.path.join(self.fake, "ps"), PS, 0o755)
        w(os.path.join(self.fake, "inject", "sitecustomize.py"), CANARY_STUB)
        w(os.path.join(self.home, ".claude", "hooks", "bm_session_cap.py"), HOOK)
        self.make_tree()
        self.pair_until = (datetime.datetime.now().astimezone() + datetime.timedelta(hours=pair_hours)).replace(second=0, microsecond=0)
        w(os.path.join(self.state, "openrouter-ledger.jsonl"), "")
        # a fresh price catalog: read only when BOUNDED_ABANDON_COUNTS is on, when a missing one refuses every launch
        w(os.path.join(self.state, "openrouter-models.json"), json.dumps({"data": []}) + "\n")
        w(os.path.join(self.state, "cap-grant.json"),
          json.dumps({"daily_cap": 50.0, "until": self.pair_until.isoformat(), "grant_note": "rehearsal"}) + "\n")
        self.write_intake(record)
        # STAND IN MODEL EXECUTABLES (the freeze refuses a model executable that is not an absolute path to an executable
        # file, and this scratch HOME has no Claude CLI): pinned in the launch settings, the one place the pair's E reads.
        self.cli = {}
        for name in ("claude", "codex"):
            self.cli[name] = os.path.join(self.box, "cli", name)
            w(self.cli[name], "#!/bin/sh\necho %s scratch stand in\n" % name, 0o755)
        self.write_launch_env("export BROTHER_SCOPE='%s'\n" % proof_accept.SCOPE)
        self.parity = None
        if deploy:
            self.deploy()
            # the executed tools equal the versioned ones, measured on the fresh deployment before any stub replaces one
            self.parity = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_loop_tool_parity.py")],
                                         env=dict(self.base_env(), PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=600)
            # the installed lease raced on this deployment (under an empty HOME it has nothing to race: NOT APPLICABLE)
            self.guard_race = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_loop_guard_race.py")],
                                             env=dict(self.base_env(), PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=600)
        else:
            self.stage()
        w(os.path.join(self.bin, "loop_pass.sh"), pass_body, 0o755)
        if loop_report is not None:
            w(os.path.join(self.bin, "loop_report.py"), loop_report, 0o755)

    # ---- fixture pieces
    def make_tree(self):
        bare = os.path.join(self.box, "upstream.git")
        git(self.box, "init", "-q", "--bare", "-b", "main", bare)
        git(self.box, "init", "-q", "-b", "main", self.tree)
        for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.tree, "config", k, v)
        w(os.path.join(self.tree, "README"), "rehearsal landing tree\n")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "tree")
        git(self.tree, "remote", "add", "origin", bare)
        git(self.tree, "push", "-q", "-u", "origin", "main")

    def write_intake(self, record=None):
        now = datetime.datetime.now()
        # ONE PROVEN BINDING (as test_loop_until_ends.REACH): the real intake refuses a record with no reachability proof,
        # and an empty proof is no proof, so the pair record carries one bridge binding (no program fingerprint to drift,
        # no breaker open under this scratch HOME). Until 2026-10-04 this record had none and every pair was refused.
        reach = [{"role": "worker", "transport": "bridge", "model": "deepseek", "model_id": "deepseek/deepseek-v4.1-flash",
                  "program": "bridge", "fingerprint": "fixture", "version": "fixture"}]
        rec = {"verdict": "READY", "at": now.isoformat(timespec="seconds"), "epoch": time.time(),
               "deadline": self.pair_until.strftime("%Y-%m-%d %H:%M"), "budget_usd": 5.0, "scope": proof_accept.SCOPE,
               "roles": {}, "waived": [], "accepted_by": "", "lines": [], "pair": True, "pair_until": self.pair_until.isoformat(),
               "reach": reach, "programs": {}}
        if record:
            rec.update(record)
            rec = {k: v for k, v in rec.items() if v is not None}
        w(os.path.join(self.evidence, "loop-intake", "CURRENT.json"), json.dumps(rec, indent=1) + "\n")

    def write_launch_env(self, body, pins=True):
        """The launch settings: body, after the stand in model executables unless pins is False."""
        pinned = "".join("export BROTHER_%s_BIN='%s'\n" % (n.upper(), p) for n, p in sorted(self.cli.items())) if pins else ""
        w(os.path.join(self.evidence, "loop-intake", "launch-env.sh"), "# rehearsal launch settings\n" + pinned + body)

    def base_env(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BROTHER_", "PYTHON", "GIT_")) and k not in ("STOP_LOOP_ONLY", "LOOP_LEASE")}
        env.update(HOME=self.home, TMPDIR=self.tmp, GIT_OPTIONAL_LOCKS="0")
        return env

    def deploy(self):
        env = self.base_env()
        env.update(BROTHER_DEPLOY_TARGET=self.bin, BROTHER_DEPLOY_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE="1",
                   BROTHER_DEPLOY_CONFIG_DIR=self.config_source,
                   PYTHONPATH=os.path.join(self.fake, "inject"), PATH=self.fake + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run(["bash", os.path.join(LOOP, "deploy_stamped.sh")], env=env, cwd=self.box,
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError("the scratch deploy refused:\n" + (r.stdout + r.stderr)[-3000:])
        self.deploy_output = r.stdout

    def stage(self):
        # the deploy's own copier (files and package directories alike), never a second file-only loop: a
        # file-only copy here left scripts/loop/adapters/ out and loop_intake status died on import (2026-10-04)
        tool_stamp.copy_tools(LOOP, self.bin)
        # staged under this scratch HOME: the closure resolves the hooks root of the HOME it runs under
        r = subprocess.run([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import deploy_stamped; "
                            "deploy_stamped.stage_candidate(sys.argv[2], sys.argv[3])", LOOP, ROOT, os.path.join(self.bin, "candidate")],
                           env=dict(self.base_env(), PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError("staging the candidate refused:\n" + (r.stdout + r.stderr)[-3000:])
        for name in deploy_stamped.CONFIGS:
            shutil.copy2(os.path.join(self.config_source, name), os.path.join(self.home, ".claude", name))

    def caller_env(self, rehearsal=True, **extra):
        env = self.base_env()
        env.update(PATH=self.shim + os.pathsep + os.environ.get("PATH", ""), BROTHER_LAUNCH_WORKTREE=self.tree)
        if rehearsal:
            env.update(BROTHER_PROOF_MIN_WINDOW_S=WINDOW, BROTHER_DISK_FREE_KB="9999999", BROTHER_SWAP_USED_MB="100",
                       BROTHER_FSEVENTSD_KB="5000", STOP_LOOP_ONLY=self.box)
        env.update(extra)
        return env

    # ---- running the launcher
    def launch(self, rehearsal=True, timeout=1500, during=None, **extra):
        """Run bin/proof_pair.sh, reading its stdout on a thread: returns (exit code, lines, seconds from the
        launcher's exit to its caller's end of file). during(scratch) runs on another thread while it works."""
        argv = ["bash", os.path.join(self.bin, "proof_pair.sh")] + (["--rehearsal"] if rehearsal else []) + [GAP]
        p = subprocess.Popen(argv, cwd=self.box, env=self.caller_env(rehearsal, **extra), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        lines, eof = [], []
        def reader():
            for line in p.stdout:
                lines.append(line.rstrip("\n"))
                if os.environ.get("REHEARSAL_ECHO"):
                    sys.stderr.write("  | " + line)
            eof.append(time.monotonic())
        t = threading.Thread(target=reader, daemon=True); t.start()
        side = threading.Thread(target=during, args=(self,), daemon=True) if during else None
        if side:
            side.start()
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            raise
        exited = time.monotonic()
        t.join(timeout=30)
        p.stdout.close()
        if side:
            side.join(timeout=60)
        return code, lines, ((eof[0] - exited) if eof else None)

    def pair_dir(self, lines):
        for line in lines:
            m = PAIR_LINE.match(line)
            if m:
                return m.group(2)
        return None

    def read(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return json.load(fh)

    def remove(self):
        # stragglers of this box only: every process whose command line names it, by its own group when it leads one
        r = subprocess.run(["ps", "-axo", "pid=,pgid=,command="], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and self.box in parts[2] and int(parts[0]) != os.getpid():
                try:
                    os.killpg(int(parts[1]), signal.SIGTERM) if parts[0] == parts[1] else os.kill(int(parts[0]), signal.SIGTERM)
                except OSError:
                    pass
        for parent, dirs, _ in os.walk(self.box):
            for d in dirs:
                try:
                    os.chmod(os.path.join(parent, d), 0o755)
                except OSError:
                    pass
        if not os.environ.get("REHEARSAL_KEEP"):
            shutil.rmtree(self.box, ignore_errors=True)


def _ps_runs():
    """Inside the export sandbox ps cannot execute (execvp: Operation not permitted), and every pair case reads the
    process table (the launcher's stop and lease checks, the stub driver's alive reads). Where ps is denied each case
    is skipped with a named NO-DATA reason; where ps runs nothing changes. The same probe as test_repair_wave_contract
    and test_merge_precompute use; a TimeoutExpired is not caught: a hung ps is a finding, never a skip."""
    try:
        return str(os.getpid()) in subprocess.run(["ps", "-p", str(os.getpid()), "-o", "pid="], capture_output=True,
                                                  text=True, timeout=10).stdout
    except OSError:
        return False


PS_RUNS = _ps_runs()
NO_PS = "NO-DATA: the process table is not readable here: the pair fixture needs ps"


def require_sandbox(test):
    if not PS_RUNS:
        test.skipTest(NO_PS)
    if not shutil.which("sandbox-exec"):
        test.fail("NO-DATA: sandbox-exec is absent on this machine, so no proof run can start; the rehearsal cannot run")


def rows_of(verdict):
    out = [("pair", r["check"], r["verdict"]) for r in verdict["pair_checks"]]
    for run in verdict["runs"]:
        out += [(run["run"], r["check"], r["verdict"]) for r in run.get("checks", [])]
    return out


class PairRehearsal(unittest.TestCase):
    """The measured handoff, with a 60 s end report, and D-6 (a drained pass exit 45 is not terminal)."""

    @classmethod
    def setUpClass(cls):
        if not PS_RUNS:
            raise unittest.SkipTest(NO_PS)
        cls.s = Scratch(deploy=True)
        try:
            cls.parity = cls.s.parity
            cls.code, cls.lines, cls.eof = cls.s.launch()
            cls.pair = cls.s.pair_dir(cls.lines)
        except BaseException:
            cls.s.remove()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.s.remove()

    def setUp(self):
        require_sandbox(self)
        self.assertIsNotNone(self.pair, "\n".join(self.lines[-40:]))

    def test_the_deployed_bin_is_the_repository_s_loop(self):
        self.assertIn("PASS: deployed", self.s.deploy_output)
        self.assertEqual(self.parity.returncode, 0, self.parity.stdout[-2000:] + self.parity.stderr[-2000:])
        self.assertNotIn("NOT APPLICABLE", self.parity.stdout)
        race = self.s.guard_race
        self.assertEqual(race.returncode, 0, race.stdout[-2000:] + race.stderr[-2000:])
        self.assertNotIn("NOT APPLICABLE", race.stdout + race.stderr)

    def test_the_gap_from_rb_receipt_end_to_rc_work_start_is_within_30_seconds(self):
        pair = self.s.read(self.pair, "pair.json")
        rb = self.s.read(pair["rb_dir"], "receipt", "receipt.json")
        rc = self.s.read(pair["rc_dir"], "receipt", "receipt.json")
        gap = (stamp(rc["start"]) - stamp(rb["end"])).total_seconds()
        sys.stdout.write("REHEARSAL GAP %.3f s (RB receipt end %s, RC work start %s)\n" % (gap, rb["end"], rc["start"]))
        self.assertEqual((rb["end_state"], rc["end_state"]), ("DEADLINE", "DEADLINE"), "\n".join(self.lines[-40:]))
        self.assertGreaterEqual(gap, 0)
        self.assertLessEqual(gap, MAX_GAP)

    def test_acceptance_measures_the_windows_and_refuses_the_rehearsal(self):
        verdict = self.s.read(self.pair, "verdict.json")
        rows = rows_of(verdict)
        for row in rows:
            sys.stdout.write("REHEARSAL ROW %s %s %s\n" % row)
        pairs = {c: v for run, c, v in rows if run == "pair"}
        self.assertEqual(pairs["consecutive_windows"], "PASS")
        for run in ("RB", "RC"):
            got = {c: v for r, c, v in rows if r == run}
            self.assertEqual(got["duration"], "FAIL")
            self.assertEqual(got["no_rehearsal_knob"], "FAIL")
        self.assertNotEqual(verdict["verdict"], "PASS")
        self.assertEqual({pairs[k] for k in ("pair_record", "pair_result_RB", "pair_result_RC")}, {"PASS"})

    def test_the_callers_end_of_file_follows_the_launcher_exit_within_5_seconds(self):
        self.assertIsNotNone(self.eof)
        sys.stdout.write("REHEARSAL EOF %.3f s after the launcher exited\n" % self.eof)
        self.assertLessEqual(self.eof, 5.0)

    def test_rb_drained_on_pass_exit_45_and_ended_deadline(self):
        pair = self.s.read(self.pair, "pair.json")
        log = self.s.read(pair["rb_dir"], "receipt", "receipt.json")["driver_log_path"]
        text = slurp(log)
        self.assertIn("DRAINING", text)
        self.assertNotIn("UNPRODUCTIVE", text)


class RcDirPrecreated(unittest.TestCase):
    def test_no_rc_run_and_the_pair_is_not_pass(self):
        require_sandbox(self)
        s = Scratch(deploy=True)
        try:
            def precreate(s):
                end = time.time() + 300
                while time.time() < end:
                    found = [d for d in os.listdir(s.runs) if d.startswith("pair-")] if os.path.isdir(s.runs) else []
                    if found and os.path.isfile(os.path.join(s.runs, found[0], "pair.json")):
                        os.makedirs(s.read(s.runs, found[0], "pair.json")["rc_dir"])
                        return
                    time.sleep(0.2)
            code, lines, _ = s.launch(during=precreate)
            pair = s.pair_dir(lines)
            rec = s.read(pair, "pair.json")
            result = s.read(pair, "RC.result.json")
            reg = os.path.join(os.path.dirname(rec["rc_dir"]), ".proof-launches", os.path.basename(rec["rc_dir"]))
            self.assertEqual(result["exit_code"], 2, "\n".join(lines[-30:]))
            self.assertIsNone(result["receipt_sha256"])
            self.assertTrue(any(n.startswith("refused-") for n in os.listdir(reg)))
            self.assertEqual(os.listdir(rec["rc_dir"]), [])
            self.assertNotEqual(s.read(pair, "verdict.json")["verdict"], "PASS")
            self.assertNotEqual(code, 0)
        finally:
            s.remove()


class RbProofUnwritable(unittest.TestCase):
    def test_receipt_failure_and_no_rc(self):
        require_sandbox(self)
        s = Scratch(deploy=True, pass_body='#!/bin/bash\nchmod 555 "$BROTHER_RUN_DIR/proof"\nexit 45\n')
        try:
            code, lines, _ = s.launch()
            pair = s.pair_dir(lines)
            rec = s.read(pair, "pair.json")
            self.assertEqual(code, 3, "\n".join(lines[-30:]))
            self.assertIn("RECEIPT FAILURE", "\n".join(lines))
            self.assertEqual(s.read(pair, "RB.result.json")["exit_code"], 1)
            self.assertFalse(os.path.exists(rec["rc_dir"]))
            self.assertFalse(os.path.exists(os.path.join(pair, "RC.result.json")))
        finally:
            s.remove()


class RcSetupDelayed(unittest.TestCase):
    def test_a_35_second_stall_fails_consecutive_windows(self):
        require_sandbox(self)
        s = Scratch(deploy=True)
        mark = os.path.join(s.box, "rc-stalled")
        # The stall sits on RC's FIRST python3 call, which the driver makes between RB's receipt end and RC's work start. It
        # used to sit on find, whose only call moved to the end of the run (1bb7f5a5a, 2026-09-30), so the 35 s landed
        # outside the measured gap and this case read PASS (found 2026-10-04). The shim then runs the suite's interpreter.
        w(os.path.join(s.shim, "python3"), '#!/bin/bash\nif [ "${BROTHER_PROOF_PHASE:-}" = RC ] && [ ! -e "%s" ]; then : > "%s"; sleep 35; fi\n'
          'exec %s "$@"\n' % (mark, mark, sys.executable), 0o755)
        try:
            code, lines, _ = s.launch()
            pair = s.pair_dir(lines)
            rows = {c: v for run, c, v in rows_of(s.read(pair, "verdict.json")) if run == "pair"}
            gap = [r for r in s.read(pair, "verdict.json")["pair_checks"] if r["check"] == "consecutive_windows"][0]
            sys.stdout.write("REHEARSAL STALLED GAP %s s\n" % gap.get("gap_seconds"))
            self.assertTrue(os.path.exists(mark))
            self.assertEqual(rows["consecutive_windows"], "FAIL")
            self.assertGreater(gap["gap_seconds"], MAX_GAP)
        finally:
            s.remove()


class UnfundedMidRun(unittest.TestCase):
    def test_runners_stop_and_liability_settles(self):
        require_sandbox(self)
        s = Scratch(deploy=True, pass_body="")
        runner = os.path.join(s.fake, "bin", "unit_runner.py")
        w(runner, '''import os, sys, time
sys.path.insert(0, os.environ["BROTHER_CODE_ROOT"])
from plugin.runtime.brother.core import openrouter_ledger as L
root = os.environ["BROTHER_OR_STATE_ROOT"]
rid = L.reserve(root, 50.0, 49.6, "rehearsal-runner", timeout_seconds=30)
open(os.path.join(sys.argv[1], "reserved"), "w").write(rid)
ending = os.path.join(os.environ["BROTHER_RUN_DIR"], "proof", "ending.json")
while not os.path.exists(ending):
    time.sleep(0.2)
L.release(root, rid, "rehearsal-runner")
open(os.path.join(sys.argv[1], "released"), "w").write(rid)
open(os.path.join(sys.argv[1], "pid"), "w").write(str(os.getpid()))
time.sleep(600)
''')
        w(os.path.join(s.bin, "loop_pass.sh"),
          '#!/bin/bash\n[ -e "%s/reserved" ] && exit 0\n'
          'python3 -B -c "import subprocess, sys; subprocess.Popen([sys.executable, \'-B\', sys.argv[1], sys.argv[2]], '
          'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(sys.argv[2] + \'/runner.err\', \'a\'), start_new_session=True)" "%s" "%s"\n'
          'for i in $(seq 1 100); do [ -e "%s/reserved" ] && break; sleep 0.1; done\nexit 0\n' % (s.fake, runner, s.fake, s.fake), 0o755)
        try:
            code, lines, _ = s.launch()
            pair = s.pair_dir(lines)
            rec = s.read(pair, "pair.json")
            rb = rec["rb_dir"]
            receipt = s.read(rb, "receipt", "receipt.json")
            log = slurp(receipt["driver_log_path"])
            rid = slurp(os.path.join(s.fake, "reserved"))
            end_rows = [json.loads(l) for l in slurp(os.path.join(rb, "proof", "ledger-end.jsonl")).splitlines() if l.strip()]
            pid = int(slurp(os.path.join(s.fake, "pid")))
            self.assertEqual(receipt["end_state"], "UNFUNDED", "\n".join(lines[-30:]))
            self.assertEqual(slurp(os.path.join(s.fake, "released")), rid)
            self.assertIn(("RELEASE", rid), [(r.get("type"), r.get("reservation_id")) for r in end_rows])
            self.assertLess(log.index("SETTLED"), log.index("STOPPED"))
            self.assertEqual(receipt["openrouter_spend_usd"], 0)
            self.assertFalse(_alive(pid))
            self.assertEqual(code, 3)
            self.assertFalse(os.path.exists(rec["rc_dir"]))
        finally:
            s.remove()


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class HoldPresent(unittest.TestCase):
    def test_refused_before_anything_and_the_hold_untouched(self):
        require_sandbox(self)
        s = Scratch(deploy=False)
        hold = os.path.join(s.evidence, "LOOP-HOLD.txt")
        w(hold, "owner: not tonight\n")
        try:
            code, lines, _ = s.launch()
            self.assertEqual(code, 2, "\n".join(lines))
            self.assertTrue(any(l.startswith("PAIR REFUSED") and "not tonight" in l for l in lines))
            self.assertEqual(slurp(hold), "owner: not tonight\n")
            self.assertFalse(os.path.isdir(s.runs) and any(d.startswith("pair-") for d in os.listdir(s.runs)))
        finally:
            s.remove()


class RbLaunchFails(unittest.TestCase):
    def test_the_result_records_the_refusal_and_no_rc_starts(self):
        require_sandbox(self)
        s = Scratch(deploy=True)
        env = dict(s.base_env(), BROTHER_LAUNCH_WORKTREE=s.tree)
        got = subprocess.run(["bash", os.path.join(s.bin, "loop_guard.sh"), "acquire", str(os.getpid())], env=env,
                             capture_output=True, text=True, timeout=60)
        try:
            self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
            code, lines, _ = s.launch()
            pair = s.pair_dir(lines)
            result = s.read(pair, "RB.result.json")
            self.assertEqual(code, 3, "\n".join(lines[-30:]))
            self.assertEqual(result["exit_code"], 2)
            self.assertIsNone(result["receipt_sha256"])
            self.assertFalse(os.path.exists(os.path.join(pair, "RC.result.json")))
        finally:
            subprocess.run(["bash", os.path.join(s.bin, "loop_guard.sh"), "release", str(os.getpid())], env=env,
                           capture_output=True, text=True, timeout=60)
            s.remove()


if __name__ == "__main__":
    unittest.main()
