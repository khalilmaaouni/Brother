"""test_required_fast.py: drives scripts/required_fast.sh BACKWARDS.

Never runs the real check suites (that would make this test itself minutes
long, defeating its own purpose as a fast pre-merge guard's own regression
test). Instead it builds a TEMP COPY of the script with the real run_check
invocations swapped for stub commands (`true` / `false` / `sh -c "exit 2"`),
reusing the script's own run_check function and summary logic verbatim, and
asserts the summary line and the exit code for three cases: all pass, one
fail, and a mix that includes NO-DATA (exit 2).

This is the same "drive it backwards, never trust a single green" method the
rest of this estate's self-tests use (see scripts/test_battery_verdict.py).
"""
import os
import json
import shutil
import re
import stat
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "required_fast.sh")

with open(SCRIPT) as f:
    _SOURCE = f.read()

# The script is HEADER (shebang, comments, cd, counters, the run_check
# function, the two intro echo lines) + a fixed block of real run_check
# calls + FOOTER (the summary block and exit). Split on the script's own
# literal anchors so a stub build never depends on which real checks are
# currently listed.
_CHECKS_START = 'run_check "version-truth"'
# ACC2: the check list runs inside a two-phase loop, so the footer starts at
# the loop's own closing line (done, then drain the checks still running).
_FOOTER_START = '\ndone\ndrain_checks\n'

_start = _SOURCE.index(_CHECKS_START)
_end = _SOURCE.index(_FOOTER_START)
HEADER = _SOURCE[:_start]
FOOTER = _SOURCE[_end:]

assert HEADER.strip(), "could not locate the header before the first real check"
assert FOOTER.strip(), "could not locate the summary footer"


_FIXTURES = tempfile.TemporaryDirectory(prefix="required-fast-fixtures-")


def install_obligation_fixture(scripts_dir, optional=()):
    shutil.copyfile(os.path.join(HERE, "evidence_obligation.py"),
                    os.path.join(scripts_dir, "evidence_obligation.py"))
    # The gate's own header runs this guard before any check (required_fast.sh,
    # git_location_guard.py --assert-clean), so a stub without it stops at exit 2.
    shutil.copyfile(os.path.join(HERE, "git_location_guard.py"),
                    os.path.join(scripts_dir, "git_location_guard.py"))
    data = {"schema": "brother.gate-obligations/v1",
            "default": "REQUIRED_FOR_MERGE", "checks": {
                name: {"obligation": "OPTIONAL", "reason": "test optional evidence"}
                for name in optional}}
    with open(os.path.join(scripts_dir, "gate_obligations.json"), "w") as handle:
        json.dump(data, handle)


def build_stub_script(stub_lines, optional=()):
    """stub_lines: list of 'run_check "name" <stub command>' strings."""
    body = HEADER + "\n".join(stub_lines) + "\n" + FOOTER
    root = tempfile.mkdtemp(dir=_FIXTURES.name)
    scripts_dir = os.path.join(root, "scripts")
    os.makedirs(scripts_dir)
    install_obligation_fixture(scripts_dir, optional)
    path = os.path.join(scripts_dir, "required_fast.sh")
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def run(path, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(["sh", path], capture_output=True, text=True,
                          timeout=20, env=env)
    return proc.returncode, proc.stdout + proc.stderr


def real_rows(name):
    """The real script's own lines for one check name: its run_check line, or
    the whole if/else/fi that chooses between two of them. Read from the
    script, never typed here, so the cases below drive the gate's own text."""
    lines = _SOURCE.splitlines()
    hits = [i for i, line in enumerate(lines) if 'run_check "%s"' % name in line]
    assert hits, "required_fast.sh declares no run_check %r" % name
    lo, hi = hits[0], hits[-1]
    if lines[lo].startswith(" "):
        while not lines[lo].startswith("if "):
            lo -= 1
        while lines[hi] != "fi":
            hi += 1
    return lines[lo:hi + 1]


def build_tree(rows, tracked=(), marker=False, real_map=False):
    """A tree holding the gate (header, `rows`, footer), the two tools its
    header and footer run, the discoverer, and only what a case adds:
    `tracked` files as {relative path: text}, the hub's edition marker, and
    the real obligations map in place of the stub one. Returns the root."""
    root = tempfile.mkdtemp(dir=_FIXTURES.name)
    scripts_dir = os.path.join(root, "scripts")
    os.makedirs(scripts_dir)
    install_obligation_fixture(scripts_dir)
    if real_map:
        shutil.copyfile(os.path.join(HERE, "gate_obligations.json"),
                        os.path.join(scripts_dir, "gate_obligations.json"))
    shutil.copyfile(os.path.join(HERE, "plugin_runtime_fast_discover.py"),
                    os.path.join(scripts_dir, "plugin_runtime_fast_discover.py"))
    for rel, text in dict(tracked).items():
        dest = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as handle:
            handle.write(text)
    if marker:
        with open(os.path.join(root, ".brother-edition"), "w") as handle:
            handle.write("edition: public-core\nvault: none\n")
    path = os.path.join(scripts_dir, "required_fast.sh")
    with open(path, "w") as handle:
        handle.write(HEADER + "\n".join(rows) + "\n" + FOOTER)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return root


def run_tree(root):
    """The gate in `root` the way the cut preflight's export tree gate runs
    it: HOME an empty directory, no git variable, nothing forcing the width.
    Its temp files (the failure keep among them) land under this suite's own
    fixture folder and leave with it."""
    home = tempfile.mkdtemp(dir=_FIXTURES.name)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "BROTHER_", "REQUIRED_FAST_"))
           and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GITHUB_ACTIONS")}
    env.update(HOME=home, BROTHER_HEAVY_SLOT="off", TMPDIR=tempfile.mkdtemp(dir=_FIXTURES.name))
    proc = subprocess.run(["sh", os.path.join(root, "scripts", "required_fast.sh")],
                          capture_output=True, text=True, timeout=120, env=env)
    return proc.returncode, proc.stdout + proc.stderr


class RequiredFastScript(unittest.TestCase):
    def test_script_exists_and_is_shell(self):
        self.assertTrue(os.path.exists(SCRIPT))
        with open(SCRIPT) as f:
            self.assertTrue(f.readline().startswith("#!/bin/sh"))

    def test_all_pass_exits_zero_and_counts_match(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-b" true',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 3   fail 0   no-data 0", out)
        self.assertNotIn("FAILED:", out)

    def test_the_summary_reports_the_width_the_gate_really_ran_at(self):
        # 2026-10-04: donecheck_acc2 reads this to refuse a "side by side" run the environment forced serial
        path = build_stub_script(['run_check "stub-a" true'])
        try:
            # every forcing variable blanked (an empty value reads as unset): the caller's own environment, the hermetic
            # export box included, may set one, and this case is about the plain width (push gate, 2026-10-04)
            plain = dict.fromkeys(("BROTHER_JEV_STATE_DIR", "BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
                                   "BROTHER_MACHINE_RESERVATION_PATH", "BROTHER_LOAD_REFUSALS_LOG", "REQUIRED_FAST_ORDER_PIN"), "")
            _, one = run(path, dict(plain, REQUIRED_FAST_JOBS="1", BROTHER_HEAVY_SLOT="off"))
            _, forced = run(path, dict(plain, REQUIRED_FAST_JOBS="4", BROTHER_HEAVY_SLOT="off", BROTHER_JEV_STATE_DIR="/tmp/x"))
        finally:
            os.remove(path)
        self.assertRegex(one, r"(?m)^width 1$")
        self.assertRegex(forced, r"(?m)^width 1 \(forced by BROTHER_JEV_STATE_DIR\)$")
        # the summary line stays byte identical: four readers anchor it at the end of the line
        for out in (one, forced):
            self.assertRegex(out, r"(?m)^pass 1   fail 0   no-data 0$")

    def test_one_fail_exits_one_and_is_named(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-fails" false',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 2   fail 1   no-data 0", out)
        self.assertIn("FAILED:", out)
        self.assertIn("stub-fails", out)
        # the failing check's full output is captured to a named file
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        self.assertTrue(os.path.exists(m.group(1)), out)
        os.remove(m.group(1))

    def test_failure_detail_is_silent_by_default(self):
        """The gap this closes: on a GitHub runner the [full: /tmp/...]
        file is discarded with the job, so without the opt-in a failure's
        real output is unreachable. Plain local runs must stay unchanged:
        the detail block only appears when asked for."""
        path = build_stub_script([
            'run_check "stub-fails" sh -c "echo boom-detail-line; exit 1"',
        ])
        try:
            code, out = run(path, extra_env={"REQUIRED_FAST_PRINT_FAILURES": "",
                                             "GITHUB_ACTIONS": ""})
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertNotIn("failure detail", out, out)
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        os.remove(m.group(1))

    def test_print_failures_env_var_prints_the_saved_detail(self):
        path = build_stub_script([
            'run_check "stub-fails" sh -c "echo boom-detail-line; exit 1"',
        ])
        try:
            code, out = run(path, extra_env={"REQUIRED_FAST_PRINT_FAILURES": "1"})
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("---- stub-fails failure detail ----", out, out)
        self.assertIn("boom-detail-line", out, out)
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        os.remove(m.group(1))

    def test_github_actions_alone_also_prints_the_saved_detail(self):
        """The runner sets GITHUB_ACTIONS itself; nobody there types the
        opt-in flag, so it must trigger the same as the explicit var."""
        path = build_stub_script([
            'run_check "stub-fails" sh -c "echo boom-detail-line; exit 1"',
        ])
        try:
            code, out = run(path, extra_env={"GITHUB_ACTIONS": "true",
                                             "REQUIRED_FAST_PRINT_FAILURES": ""})
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("---- stub-fails failure detail ----", out, out)
        self.assertIn("boom-detail-line", out, out)
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        os.remove(m.group(1))

    def test_required_no_data_blocks_without_rewriting_the_verdict(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-nodata" sh -c "exit 2"',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 2   fail 0   no-data 1", out)
        self.assertIn("NO-DATA:", out)
        self.assertIn("stub-nodata", out)
        self.assertNotIn("FAILED:", out)
        self.assertIn("stub-nodata\tNO-DATA\tREQUIRED_FOR_MERGE\tBLOCKED", out)

    def test_optional_no_data_allows_without_rewriting_the_verdict(self):
        path = build_stub_script([
            'run_check "stub-nodata" sh -c "exit 2"',
        ], optional=("stub-nodata",))
        code, out = run(path)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 0   fail 0   no-data 1", out)
        self.assertIn("stub-nodata\tNO-DATA\tOPTIONAL\tALLOWED", out)

    def test_fail_and_no_data_together_still_fails_on_the_fail(self):
        path = build_stub_script([
            'run_check "stub-fails" false',
            'run_check "stub-nodata" sh -c "exit 2"',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 0   fail 1   no-data 1", out)


class ParallelPhase(unittest.TestCase):
    """ACC2, 2026-09-26: audited checks run REQUIRED_FAST_JOBS at a time. The
    stub names below are on the audited list on purpose (version-truth,
    bundle-runtime, surface); any other name runs in the serial phase."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_FIXTURES.name)

    def timed(self, stubs, optional=(), **env):
        env.setdefault("TMPDIR", self.tmp)
        path = build_stub_script(stubs, optional=optional)
        full = dict(os.environ)
        for k in ("BROTHER_JEV_STATE_DIR", "BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
                  "BROTHER_MACHINE_RESERVATION_PATH", "BROTHER_LOAD_REFUSALS_LOG",
                  "REQUIRED_FAST_JOBS"):
            full.pop(k, None)
        full.update(env)
        started = time.time()
        proc = subprocess.run(["sh", path], capture_output=True, text=True, timeout=30, env=full)
        return proc.returncode, proc.stdout + proc.stderr, time.time() - started

    SLEEPERS = ['run_check "version-truth" sh -c "sleep 1.5"',
                'run_check "bundle-runtime" sh -c "sleep 1.5"']

    def test_audited_checks_run_side_by_side(self):
        code, out, wall = self.timed(self.SLEEPERS, REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 0, out)
        self.assertIn("pass 2   fail 0   no-data 0", out)
        self.assertLess(wall, 2.8, "two 1.5 s checks did not overlap")

    def test_one_job_runs_them_one_after_another(self):
        code, out, wall = self.timed(self.SLEEPERS, REQUIRED_FAST_JOBS="1")
        self.assertEqual(code, 0, out)
        self.assertGreaterEqual(wall, 3.0)

    def test_caller_shared_state_forces_one_job(self):
        shared = os.path.join(self.tmp, "caller-jev")
        os.makedirs(shared)
        code, out, wall = self.timed(self.SLEEPERS, REQUIRED_FAST_JOBS="2",
                                     BROTHER_JEV_STATE_DIR=shared)
        self.assertEqual(code, 0, out)
        self.assertGreaterEqual(wall, 3.0, "a caller-named Jev directory was shared by parallel checks")

    def test_a_parallel_failure_is_counted_and_named(self):
        code, out, _ = self.timed(['run_check "version-truth" sh -c "exit 1"',
                                   'run_check "bundle-runtime" true'], REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 1, out)
        self.assertIn("pass 1   fail 1   no-data 0", out)
        self.assertIn("FAILED: version-truth", out)

    def test_a_parallel_no_data_is_counted_never_passed(self):
        code, out, _ = self.timed(['run_check "version-truth" sh -c "exit 2"'],
                                  optional=("version-truth",), REQUIRED_FAST_JOBS="2")
        self.assertIn("pass 0   fail 0   no-data 1", out)
        self.assertIn("NO-DATA: version-truth", out)
        self.assertEqual(code, 0, out)

    def test_a_required_parallel_no_data_still_blocks_the_merge(self):
        code, out, _ = self.timed(['run_check "version-truth" sh -c "exit 2"'],
                                  REQUIRED_FAST_JOBS="2")
        self.assertIn("pass 0   fail 0   no-data 1", out)
        # The obligation step's own verdict row must name the check: an empty
        # results file also exits 1, for the wrong reason.
        self.assertIn("version-truth\tNO-DATA\tREQUIRED_FOR_MERGE\tBLOCKED", out)
        self.assertEqual(code, 1, out)

    def test_serial_checks_start_after_the_parallel_ones_finish(self):
        marker = os.path.join(self.tmp, "parallel-done")
        code, out, _ = self.timed([
            'run_check "version-truth" sh -c "sleep 1; touch %s"' % marker,
            'run_check "stub-serial" test -f %s' % marker,
        ], REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 0, out)
        self.assertIn("pass 2   fail 0", out)

    def test_each_parallel_check_gets_its_own_jev_state(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        code, out, _ = self.timed([
            'run_check "version-truth" sh -c \'echo "$BROTHER_JEV_STATE_DIR" > %s\'' % a,
            'run_check "surface" sh -c \'echo "$BROTHER_JEV_STATE_DIR" > %s\'' % b,
        ], REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 0, out)
        with open(a) as fa, open(b) as fb:
            one, two = fa.read().strip(), fb.read().strip()
        self.assertTrue(one and two)
        self.assertNotEqual(one, two, "two parallel checks shared one Jev state directory")

    def test_the_two_longest_checks_start_first(self):
        """Longest first: measured 2026-09-26, the gate side by side took 254 s
        against 496 s one at a time (0.51) with these two started 7th and 20th."""
        order = [m for m in re.findall(r'^run_check "([^"]+)"', _SOURCE, re.M)]
        self.assertEqual(order[1:3], ["brother-run", "export-public"], order[:5])

    def test_a_bad_job_count_is_no_data(self):
        code, out, _ = self.timed(self.SLEEPERS, REQUIRED_FAST_JOBS="many")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA: REQUIRED_FAST_JOBS must be a positive integer", out)

    def test_the_pool_directory_is_removed_at_the_end(self):
        code, out, _ = self.timed(self.SLEEPERS, REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 0, out)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith("required-fast-pool.")], [])


class TheLongSuitesRunAsTwoShards(unittest.TestCase):
    LONG = {"brother-run": "test_brother_run.py", "export-public": "test_export_public.py"}

    def _rows(self) -> dict:
        return dict(re.findall(r'^run_check "([^"]+)"\s+(.+)$', _SOURCE, re.M))

    def test_each_long_suite_is_declared_as_two_shards(self) -> None:
        rows = self._rows()
        for name, filename in self.LONG.items():
            self.assertEqual(rows.get(name),
                             "env BROTHER_TEST_SHARD=1/2 python3 scripts/%s -v" % filename)
            self.assertEqual(rows.get(name + "-2"),
                             "env BROTHER_TEST_SHARD=2/2 python3 scripts/%s -v" % filename)
            matching = sorted(n for n, cmd in rows.items() if "scripts/%s" % filename in cmd)
            self.assertEqual(matching, sorted([name, name + "-2"]))

    def test_every_shard_starts_in_the_parallel_phase(self) -> None:
        start = _SOURCE.index('  case "$1" in')
        end = _SOURCE.index(') check_phase=parallel ;;', start)
        words = re.findall(r"[a-z0-9-]+", _SOURCE[start:end])
        for name in self.LONG:
            self.assertIn(name, words)
            self.assertIn(name + "-2", words)

    def test_the_four_shards_start_right_after_version_truth(self) -> None:
        names = re.findall(r'^run_check "([^"]+)"', _SOURCE, re.M)
        self.assertEqual(names[:5], ["version-truth", "brother-run", "export-public",
                                     "brother-run-2", "export-public-2"])

    def test_both_long_suites_hand_their_loader_to_suite_shard(self) -> None:
        import ast
        for filename in self.LONG.values():
            path = os.path.join(HERE, filename)
            with open(path) as fh:
                tree = ast.parse(fh.read())
            funcs = [n for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == "load_tests"]
            self.assertEqual(len(funcs), 1, "%s: expected exactly one load_tests" % filename)
            fn = funcs[0]
            self.assertEqual([a.arg for a in fn.args.args], ["loader", "tests", "pattern"])
            returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
            self.assertEqual(len(returns), 1, "%s: expected exactly one return" % filename)
            self.assertEqual(ast.unparse(returns[0].value),
                             "suite_shard.select(tests, os.environ.get('BROTHER_TEST_SHARD'))")


class ReceiptSuitesRunSideBySide(unittest.TestCase):
    SLEEPERS = ParallelPhase.SLEEPERS
    setUp = ParallelPhase.setUp
    timed = ParallelPhase.timed

    def test_each_parallel_check_gets_its_own_reservation_and_refusal_paths(self) -> None:
        first = os.path.join(self.tmp, "first-paths")
        second = os.path.join(self.tmp, "second-paths")
        code, out, _ = self.timed([
            'run_check "version-truth" sh -c \'echo "$BROTHER_MACHINE_RESERVATION_PATH $BROTHER_LOAD_REFUSALS_LOG" > %s\'' % first,
            'run_check "surface" sh -c \'echo "$BROTHER_MACHINE_RESERVATION_PATH $BROTHER_LOAD_REFUSALS_LOG" > %s\'' % second,
        ], REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 0, out)
        with open(first) as f1, open(second) as f2:
            one = f1.read().split()
            two = f2.read().split()
        self.assertEqual(len(one), 2, one)
        self.assertEqual(len(two), 2, two)
        all_paths = one + two
        for path in all_paths:
            self.assertTrue(path.startswith(os.path.join(self.tmp, "required-fast-pool.")), path)
        self.assertEqual(len(set(all_paths)), 4, all_paths)

    def test_a_caller_named_reservation_path_forces_one_job(self) -> None:
        caller = os.path.join(self.tmp, "caller-reservation.json")
        code, out, wall = self.timed(self.SLEEPERS, REQUIRED_FAST_JOBS="2",
                                     BROTHER_MACHINE_RESERVATION_PATH=caller)
        self.assertEqual(code, 0, out)
        self.assertGreaterEqual(wall, 3.0, "the caller-named reservation path did not force one job")

    def test_a_caller_named_refusal_log_forces_one_job(self) -> None:
        caller = os.path.join(self.tmp, "caller-refusals.jsonl")
        code, out, wall = self.timed(self.SLEEPERS, REQUIRED_FAST_JOBS="2",
                                     BROTHER_LOAD_REFUSALS_LOG=caller)
        self.assertEqual(code, 0, out)
        self.assertGreaterEqual(wall, 3.0, "the caller-named refusal log did not force one job")

    def test_the_receipt_suites_start_side_by_side(self) -> None:
        code, out, wall = self.timed([
            'run_check "receipt-door" sh -c "sleep 1.5"',
            'run_check "receipt-contract-v1" sh -c "sleep 1.5"',
        ], REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 0, out)
        self.assertIn("pass 2   fail 0   no-data 0", out)
        self.assertLess(wall, 2.8, "the two receipt suites did not overlap")

    FOUR = ['run_check "version-truth" sh -c "sleep 1.5"',
            'run_check "bundle-runtime" sh -c "sleep 1.5"',
            'run_check "surface" sh -c "sleep 1.5"',
            'run_check "packs" sh -c "sleep 1.5"']

    def test_the_default_runs_four_side_by_side(self) -> None:
        code, out, wall = self.timed(self.FOUR)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 4   fail 0   no-data 0", out)
        self.assertLess(wall, 2.8, "the default did not run four checks side by side")

    def test_the_default_stops_at_four(self) -> None:
        code, out, wall = self.timed(self.FOUR + ['run_check "mobile-design" sh -c "sleep 1.5"'])
        self.assertEqual(code, 0, out)
        self.assertIn("pass 5   fail 0   no-data 0", out)
        self.assertGreaterEqual(wall, 3.0, "the default ran a fifth check side by side")


class TwoWorktreesShareOneTempDirectory(unittest.TestCase):
    """Row E100. Lane BM2's gate run read a traceback out of lane AW2's tree,
    because the failure capture was keyed by check name and pid inside one
    shared $TMPDIR. These drive the real script from two differently named
    parent directories, sharing one temp root, which is the collision itself.
    """

    def build_in_lane(self, lane, stub_lines):
        """A stub of the real script under <root>/<lane>/scripts, so the
        script's own `cd $(dirname $0)/..` resolves to a lane directory and
        its worktree key is that lane's name."""
        root = tempfile.mkdtemp(prefix="two-worktrees-")
        scripts_dir = os.path.join(root, lane, "scripts")
        os.makedirs(scripts_dir)
        install_obligation_fixture(scripts_dir)
        path = os.path.join(scripts_dir, "required_fast.sh")
        with open(path, "w") as fh:
            fh.write(HEADER + "\n".join(stub_lines) + "\n" + FOOTER)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def run_sharing(self, path, shared_tmp, **extra):
        env = dict(os.environ)
        env["TMPDIR"] = shared_tmp
        env.update(extra)
        proc = subprocess.run(["sh", path], capture_output=True, text=True,
                              timeout=20, env=env)
        return proc.returncode, proc.stdout + proc.stderr

    def test_two_lanes_never_write_the_same_failure_file(self):
        shared = tempfile.mkdtemp(prefix="shared-tmpdir-")
        one = self.build_in_lane(
            "lane-one", ['run_check "export-public" sh -c '
                         '"echo LANE-ONE-TRACEBACK; exit 1"'])
        two = self.build_in_lane(
            "lane-two", ['run_check "export-public" sh -c '
                         '"echo LANE-TWO-TRACEBACK; exit 1"'])
        code_one, out_one = self.run_sharing(one, shared)
        code_two, out_two = self.run_sharing(two, shared)
        self.assertEqual((code_one, code_two), (1, 1), out_one + out_two)

        kept = sorted(n for n in os.listdir(shared)
                      if n.startswith("required-fast-fail-"))
        self.assertEqual(len(kept), 2, kept)
        self.assertTrue(any("lane-one" in n for n in kept), kept)
        self.assertTrue(any("lane-two" in n for n in kept), kept)
        for name in kept:
            with open(os.path.join(shared, name)) as fh:
                body = fh.read()
            lane = "ONE" if "lane-one" in name else "TWO"
            other = "TWO" if lane == "ONE" else "ONE"
            self.assertIn("LANE-%s-TRACEBACK" % lane, body)
            self.assertNotIn("LANE-%s-TRACEBACK" % other, body)

    def test_a_run_that_dies_early_says_no_data_and_not_a_pass(self):
        shared = tempfile.mkdtemp(prefix="shared-tmpdir-")
        path = self.build_in_lane("lane-killed", [
            'run_check "stub-a" true',
            'exit 7',
        ])
        # One at a time, so "after 1 check" is well defined (ACC2 runs audited
        # checks first when jobs > 1; the twin below covers that phase).
        code, out = self.run_sharing(path, shared, REQUIRED_FAST_JOBS="1")
        self.assertEqual(code, 7, out)
        self.assertIn("NO-DATA: required-fast stopped after 1 check(s)", out)
        self.assertIn("lane-killed", out)
        self.assertIn("This is NOT a pass", out)
        self.assertNotIn("pass 1   fail 0", out)

    def test_a_run_that_dies_in_the_parallel_phase_is_still_no_data(self):
        shared = tempfile.mkdtemp(prefix="shared-tmpdir-")
        path = self.build_in_lane("lane-killed-parallel", [
            'run_check "version-truth" true',
            'exit 7',
        ])
        code, out = self.run_sharing(path, shared, REQUIRED_FAST_JOBS="2")
        self.assertEqual(code, 7, out)
        self.assertIn("NO-DATA: required-fast stopped after", out)
        self.assertIn("This is NOT a pass", out)
        self.assertNotIn("pass 1   fail 0", out)

    def test_a_finished_run_says_nothing_about_no_data(self):
        """The positive control: without it the trap could fire always, or
        never, and the test above would pass either way."""
        shared = tempfile.mkdtemp(prefix="shared-tmpdir-")
        path = self.build_in_lane("lane-finished", ['run_check "stub-a" true'])
        code, out = self.run_sharing(path, shared)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 1   fail 0   no-data 0", out)
        self.assertNotIn("required-fast stopped after", out)


PLUGIN_ROW = "plugin-runtime-tests-fast"
ONE_PASSING_TEST = {"plugin/runtime/brother/core/test_one.py":
                    "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_one(self):\n        pass\n"}


class ThePluginRuntimeRowWhereItsTestsDoNotShip(unittest.TestCase):
    """The public export carries the plugin runtime modules the loop imports
    and none of their tests (docs/plan/EXPORT-ALLOWLIST.txt, owner ruling
    2026-09-26), and the public repository's CI and the cut preflight's
    export tree gate both run this same file on that tree. Reached for the
    first time on 2026-10-06, the row read FAIL there ("no test modules
    found"): a red for an input the tree is not meant to hold.

    Every case drives the gate's OWN rows for this check (real_rows), in a
    tree that differs from the next case in one thing only."""

    def summary(self, **tree):
        code, out = run_tree(build_tree(real_rows(PLUGIN_ROW), **tree))
        row = next((line for line in out.splitlines()
                    if PLUGIN_ROW in line and " exit " in line), "")
        return code, row, out

    def test_an_export_shaped_tree_reads_no_data_with_the_reason(self):
        code, row, out = self.summary()
        self.assertIn("pass 0   fail 0   no-data 1", out, out)
        self.assertTrue(row.startswith("NO-DATA exit 2"), row)
        self.assertIn("NO-DATA: this tree carries no plugin/runtime/brother test module", out)
        self.assertNotIn("FAILED:", out)

    def test_the_hub_without_its_tests_still_fails(self):
        # the edition marker is the one thing this tree has that the case above does not
        code, row, out = self.summary(marker=True)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 0   fail 1   no-data 0", out, out)
        self.assertTrue(row.startswith("FAIL    exit 1"), row)
        self.assertIn("FAILED: %s" % PLUGIN_ROW, out)

    def test_a_tree_that_carries_a_test_runs_it_whatever_its_edition(self):
        for marker in (False, True):
            with self.subTest(marker=marker):
                code, row, out = self.summary(tracked=ONE_PASSING_TEST, marker=marker)
                self.assertEqual(code, 0, out)
                self.assertIn("pass 1   fail 0   no-data 0", out, out)
                self.assertTrue(row.startswith("PASS    exit 0"), row)

    # The two cases below read the REAL scripts/gate_obligations.json: what the
    # merge transition does with that NO-DATA is the map's decision, not the row's.

    def test_the_real_map_allows_that_no_data_in_an_export_shaped_tree_and_says_why(self):
        code, row, out = self.summary(real_map=True)
        self.assertEqual(code, 0, out)
        self.assertIn("%s\tNO-DATA\tREQUIRED_FOR_MERGE\tALLOWED\tNO-DATA_ALLOWED: "
                      "the plugin runtime module tests are not shipped in the public edition" % PLUGIN_ROW, out)
        self.assertIn("transition: ALLOWED", out)
        self.assertNotIn("%s\tPASS" % PLUGIN_ROW, out)

    def test_the_real_map_blocks_that_no_data_wherever_the_hub_marker_is(self):
        # a row that answers NO-DATA in the hub (the gate's own rows never do, see above): the map still refuses it
        stub = ['run_check "%s" sh -c \'echo "NO-DATA: stub"; exit 2\'' % PLUGIN_ROW]
        code, out = run_tree(build_tree(stub, marker=True, real_map=True))
        self.assertEqual(code, 1, out)
        self.assertIn("%s\tNO-DATA\tREQUIRED_FOR_MERGE\tBLOCKED\tNO-DATA_BLOCKING" % PLUGIN_ROW, out)
        self.assertIn("transition: BLOCKED (%s)" % PLUGIN_ROW, out)


class PluginManifestUnderTheMergeGatePath(unittest.TestCase):
    """C10, 2026-10-10: scripts/gate_merge_seq.sh pins PATH to the system directories, so the plugin-manifest row's
    `command -v claude` read NO-DATA under the merge gate and every verified merge read a contradiction. These cases run
    the REAL row (read from required_fast.sh, never typed here) with exactly that PATH and an empty HOME, so the CLI can
    only come from where each case puts it."""

    GATE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

    def setUp(self):
        self.home = tempfile.mkdtemp(dir=_FIXTURES.name)
        bindir = tempfile.mkdtemp(dir=_FIXTURES.name)
        self.cli = os.path.join(bindir, "claude")
        with open(self.cli, "w") as handle:
            handle.write('#!/bin/sh\n[ "$*" = "plugin validate ." ] || exit 9\necho "fixture: validated"\n')
        os.chmod(self.cli, 0o755)

    def run_row(self, extra=None):
        with open(os.path.join(HERE, "brother_paths.py")) as handle:
            resolver = handle.read()
        root = build_tree(real_rows("plugin-manifest"), tracked={"scripts/brother_paths.py": resolver})
        env = {"PATH": self.GATE_PATH, "HOME": self.home, "BROTHER_HEAVY_SLOT": "off",
               "TMPDIR": tempfile.mkdtemp(dir=_FIXTURES.name)}
        env.update(extra or {})
        proc = subprocess.run(["sh", os.path.join(root, "scripts", "required_fast.sh")],
                              capture_output=True, text=True, timeout=120, env=env)
        return proc.returncode, proc.stdout + proc.stderr

    def test_the_cli_resolved_only_through_the_owners_pin_reads_pass(self):
        code, out = self.run_row({"BROTHER_CLAUDE_BIN": self.cli})
        self.assertEqual(code, 0, out)
        self.assertIn("pass 1   fail 0   no-data 0", out)

    def test_no_cli_anywhere_reads_no_data_never_pass(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("brother_paths_c10", os.path.join(HERE, "brother_paths.py"))
        paths = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(paths)
        found = paths.claude_candidates({"PATH": self.GATE_PATH, "HOME": self.home})
        if found:
            self.skipTest("NO-DATA: a Claude Code CLI is installed outside the fixture at %s" % found[0])
        code, out = self.run_row()
        self.assertNotEqual(code, 0, out)
        self.assertIn("pass 0   fail 0   no-data 1", out)
        self.assertIn("plugin-manifest\tNO-DATA", out)

    def test_a_relative_pin_reads_no_data_and_is_named(self):
        code, out = self.run_row({"BROTHER_CLAUDE_BIN": "claude"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("pass 0   fail 0   no-data 1", out)
        self.assertIn("BROTHER_CLAUDE_BIN", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
