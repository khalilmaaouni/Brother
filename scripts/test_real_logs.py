"""What scripts/real_logs.py must keep true (row M3, the 2026-09-07
reflection): PASS when nothing grew, FAIL naming the path that grew, NO-DATA
for a path that does not exist -- never a pass.

Every case drives the real module through the same seams a real hook uses: a
temp dir stands in for the config dir via BM_HOOK_OUTCOMES (the recall hook's
own override), HOME (repeat_guard.py's STATE_DIR is pathlib.Path.home() /
".claude" / "repeat-guard", with no dedicated env var of its own -- see
real_logs.py's module docstring), and ATTEMPT_LEDGER (attempt_ledger.py's
own override). Every test exercises the CLI via subprocess so real_logs.py's
argparse-free dispatch and exit codes are proven too, the same reason
sibling suites in this tree (test_repeat_guard.py, test_attempt_hook.py) run
their subject as a subprocess rather than only importing it.

Proving command: python3 scripts/test_real_logs.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import real_logs as RL  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

SCRIPT = os.path.join(HERE, "real_logs.py")


def _env(tmp, home=None):
    """Redirects all three real logs under tmp: BM_HOOK_OUTCOMES and
    ATTEMPT_LEDGER explicitly (each hook's own override), HOME for the
    repeat guard's state dir (its only seam, since it has no dedicated
    variable -- see real_logs.py's docstring)."""
    env = dict(os.environ)
    env["BM_HOOK_OUTCOMES"] = os.path.join(tmp, "hook-outcomes.jsonl")
    env["ATTEMPT_LEDGER"] = os.path.join(tmp, "attempt-ledger", "attempts.jsonl")
    env["HOME"] = home if home is not None else tmp
    # Never let an ambient BROTHER_CONFIG_DIR/CLAUDE_CONFIG_DIR from the real
    # machine leak into a path this test did not choose.
    env.pop("BROTHER_CONFIG_DIR", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CODEX_HOME", None)
    return env


def _snapshot_cli(env):
    p = subprocess.run([sys.executable, SCRIPT, "snapshot"],
                        capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, "snapshot exited %d: %s" % (p.returncode, p.stderr)
    return json.loads(p.stdout)


def _compare_cli(before_path, env):
    p = subprocess.run([sys.executable, SCRIPT, "compare", before_path],
                        capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout, p.stderr


def _write_before(tmp, snap):
    path = os.path.join(tmp, "before.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh)
    return path


def _seed_all(tmp, env):
    """All three real logs present, non-empty, so a PASS run has something
    real to compare rather than three NO-DATA verdicts hiding the case."""
    os.makedirs(os.path.dirname(env["BM_HOOK_OUTCOMES"]), exist_ok=True)
    with open(env["BM_HOOK_OUTCOMES"], "w", encoding="utf-8") as fh:
        fh.write('{"row": 1}\n')
    os.makedirs(os.path.dirname(env["ATTEMPT_LEDGER"]), exist_ok=True)
    with open(env["ATTEMPT_LEDGER"], "w", encoding="utf-8") as fh:
        fh.write('{"problem": "p", "class": "c", "outcome": "passed"}\n')
    guard_dir = os.path.join(tmp, ".claude", "repeat-guard")
    os.makedirs(guard_dir, exist_ok=True)
    with open(os.path.join(guard_dir, "s1.jsonl"), "w", encoding="utf-8") as fh:
        fh.write('{"sig": "x", "ok": true}\n')


class Unchanged(unittest.TestCase):
    def test_pass_when_nothing_grew(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            _seed_all(tmp, env)
            snap = _snapshot_cli(env)
            before = _write_before(tmp, snap)
            code, out, err = _compare_cli(before, env)
            self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
            self.assertIn("PASS", out)


class Grew(unittest.TestCase):
    def test_fail_names_the_path_that_grew(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            _seed_all(tmp, env)
            snap = _snapshot_cli(env)
            before = _write_before(tmp, snap)
            # A fixture row lands in the real hook-outcomes log between
            # snapshot and compare, exactly the 2026-09-06 defect.
            with open(env["BM_HOOK_OUTCOMES"], "a", encoding="utf-8") as fh:
                fh.write('{"row": 2}\n')
            code, out, err = _compare_cli(before, env)
            self.assertEqual(code, 1, "stdout=%r stderr=%r" % (out, err))
            self.assertIn("FAIL", out)
            self.assertIn("hook_outcomes", out)

    def test_warn_names_a_new_file_in_a_directory_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            _seed_all(tmp, env)
            snap = _snapshot_cli(env)
            before = _write_before(tmp, snap)
            # A new fixture session file, never an appended row: the repeat
            # guard's state is a directory of per-session files, so growth
            # must be caught by file COUNT, not only by byte size. But
            # repeat_guard_state is SHARED_BY_LIVE_SESSIONS -- every live
            # session's hook appends to it on every tool call -- so its
            # growth alone is advisory (WARN at exit 0), never the verdict.
            guard_dir = os.path.join(tmp, ".claude", "repeat-guard")
            with open(os.path.join(guard_dir, "s2.jsonl"), "w", encoding="utf-8") as fh:
                fh.write('{"sig": "y", "ok": false}\n')
            code, out, err = _compare_cli(before, env)
            self.assertEqual(code, 0, "stdout=%r stderr=%r" % (out, err))
            self.assertIn("WARN", out)
            self.assertIn("repeat_guard_state", out)
            self.assertNotIn("FAIL", out)

    def test_fail_names_hook_outcomes_alongside_advisory_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            _seed_all(tmp, env)
            snap = _snapshot_cli(env)
            before = _write_before(tmp, snap)
            # Both a decisive log (hook_outcomes) and the shared, advisory
            # one (repeat_guard_state) grow in the same window: the
            # decisive growth still FAILs at exit 1, the shared growth
            # never masks it and never softens it to a WARN.
            with open(env["BM_HOOK_OUTCOMES"], "a", encoding="utf-8") as fh:
                fh.write('{"row": 2}\n')
            guard_dir = os.path.join(tmp, ".claude", "repeat-guard")
            with open(os.path.join(guard_dir, "s2.jsonl"), "w", encoding="utf-8") as fh:
                fh.write('{"sig": "y", "ok": false}\n')
            code, out, err = _compare_cli(before, env)
            self.assertEqual(code, 1, "stdout=%r stderr=%r" % (out, err))
            self.assertIn("FAIL", out)
            self.assertIn("hook_outcomes", out)

    def test_assert_unchanged_raises_naming_the_path(self):
        """The in-process helper every hook suite's tearDownModule calls,
        proven directly rather than only through the CLI."""
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            _seed_all(tmp, env)
            saved = dict(os.environ)
            os.environ.update(env)
            try:
                before = RL.snapshot()
                with open(env["ATTEMPT_LEDGER"], "a", encoding="utf-8") as fh:
                    fh.write('{"problem": "p2", "class": "c2", "outcome": "failed"}\n')
                with self.assertRaises(AssertionError) as ctx:
                    RL.assert_unchanged(before, context="a fixture suite")
                self.assertIn("attempt_ledger", str(ctx.exception))
            finally:
                os.environ.clear()
                os.environ.update(saved)


class SharedLogIsAdvisory(unittest.TestCase):
    """compare() driven directly with dict fixtures, no subprocess and no
    real filesystem: row M3's actual rule -- repeat_guard_state alone is
    WARN, hook_outcomes is FAIL and is listed first even when both grew."""

    def _fixture(self):
        base = {
            "hook_outcomes": {"path": "/x/hook-outcomes.jsonl", "exists": True,
                               "size": 10, "count": 1, "mtime": 1.0},
            "repeat_guard_state": {"path": "/x/repeat-guard", "exists": True,
                                    "size": 10, "count": 1, "mtime": 1.0},
            "attempt_ledger": {"path": "/x/attempts.jsonl", "exists": True,
                                "size": 10, "count": 1, "mtime": 1.0},
        }
        return {name: dict(v) for name, v in base.items()}

    def test_repeat_guard_state_grown_alone_gives_warn(self):
        before = self._fixture()
        after = self._fixture()
        after["repeat_guard_state"]["size"] = 20
        verdict, detail = RL.compare(before, after)
        self.assertEqual(verdict, "WARN")
        self.assertEqual([d[0] for d in detail], ["repeat_guard_state"])

    def test_hook_outcomes_grown_gives_fail_listing_it_first(self):
        before = self._fixture()
        after = self._fixture()
        after["hook_outcomes"]["size"] = 20
        after["repeat_guard_state"]["size"] = 20
        verdict, detail = RL.compare(before, after)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual([d[0] for d in detail],
                          ["hook_outcomes", "repeat_guard_state"])


class MissingPath(unittest.TestCase):
    def test_no_data_for_a_path_that_never_existed(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            # Seed only two of three; the attempt ledger is never created.
            os.makedirs(os.path.dirname(env["BM_HOOK_OUTCOMES"]), exist_ok=True)
            with open(env["BM_HOOK_OUTCOMES"], "w", encoding="utf-8") as fh:
                fh.write('{"row": 1}\n')
            guard_dir = os.path.join(tmp, ".claude", "repeat-guard")
            os.makedirs(guard_dir, exist_ok=True)
            with open(os.path.join(guard_dir, "s1.jsonl"), "w", encoding="utf-8") as fh:
                fh.write('{"sig": "x", "ok": true}\n')
            snap = _snapshot_cli(env)
            self.assertFalse(snap["attempt_ledger"]["exists"])
            before = _write_before(tmp, snap)
            code, out, err = _compare_cli(before, env)
            self.assertEqual(code, 2, "stdout=%r stderr=%r" % (out, err))
            self.assertIn("NO-DATA", out)
            self.assertIn("attempt_ledger", out)
            self.assertNotIn("PASS", out)

    def test_no_data_never_reads_as_pass_even_alongside_unchanged_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            _seed_all(tmp, env)
            snap = _snapshot_cli(env)
            # The attempt ledger existed at snapshot time and is removed
            # before compare: still NO-DATA, never a pass, because compare()
            # cannot vouch for a path it cannot see now.
            os.remove(env["ATTEMPT_LEDGER"])
            before = _write_before(tmp, snap)
            code, out, err = _compare_cli(before, env)
            self.assertEqual(code, 2, "stdout=%r stderr=%r" % (out, err))
            self.assertIn("NO-DATA", out)


class Sandboxed(unittest.TestCase):
    """snapshot_for_tests() (2026-09-11): live sessions' installed hooks grow
    the real logs during any suite run, so a suite is judged on a private
    sandbox its own writers are redirected into. Driven both ways: ambient
    growth of the real files passes, and the recall hook's own
    _append_outcome plus a child's attempt_ledger.record, neither
    redirected by the test, fail."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = _env(self.tmp)
        _seed_all(self.tmp, self.env)
        self.saved = dict(os.environ)
        os.environ.update(self.env)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def test_ambient_growth_of_the_real_files_does_not_fail(self):
        before = RL.snapshot_for_tests()
        # Another session's hook appends to the real paths by name, never
        # through this process's env.
        for path in (self.env["BM_HOOK_OUTCOMES"], self.env["ATTEMPT_LEDGER"]):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"row": "ambient"}\n')
        RL.assert_unchanged(before, context="a fixture suite")
        self.assertEqual(os.environ["BM_HOOK_OUTCOMES"], self.env["BM_HOOK_OUTCOMES"])
        self.assertEqual(os.environ["ATTEMPT_LEDGER"], self.env["ATTEMPT_LEDGER"])

    def test_unredirected_writes_fail_in_process_and_in_a_child(self):
        import importlib.util
        before = RL.snapshot_for_tests()
        hook = os.path.join(os.path.dirname(HERE), "products", "brothermode",
                            "tools", "vault_recall_hook.py")
        spec = importlib.util.spec_from_file_location("_rl_recall_hook", hook)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._append_outcome("sess-x", 1, 100)
        p = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, %r); "
             "import attempt_ledger as A; A.record('p', 'c', 'failed')" % HERE],
            capture_output=True, text=True, env=dict(os.environ), timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        with self.assertRaises(AssertionError) as ctx:
            RL.assert_unchanged(before, context="a fixture suite")
        self.assertIn("hook_outcomes", str(ctx.exception))
        self.assertIn("attempt_ledger", str(ctx.exception))
        self.assertEqual(os.environ["BM_HOOK_OUTCOMES"], self.env["BM_HOOK_OUTCOMES"])


if __name__ == "__main__":
    unittest.main()
