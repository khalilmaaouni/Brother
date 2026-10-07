#!/usr/bin/env python3
"""C0.1: fail fast through an isolated in-process runner, only inside a
verified perturb_pool work copy, never weaker than the plain run.

Every test drives scripts/release_note_perturb.py, the production file this
unit edits, and through its own run_suite boundary the new runner
scripts/c01_failfast_runner.py. Nothing here imports scripts/cut.py,
scripts/perturb_pool.py, scripts/release_note_from_tree.py or
scripts/test_release_note_perturb.py, and this module launches no process of
its own: every real run goes through the subject's run_suite.

A work copy is shaped by hand the way perturb_pool.py writes one (a real
`.git/HEAD` holding a detached 40-hex commit, and a sibling
`.made-by-perturb-pool` marker naming it). Branch checks answer run_suite
from a canned queue; runner behaviour, start-state equivalence, timing and
process lifecycle are measured on real processes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_note_perturb as P  # noqa: E402

SHA_A = "a" * 40
SHA_B = "b" * 40
PFX = "C0.1 fail-fast: "
ENGAGED_OK = PFX + "engaged; result: success"
ENGAGED_FAIL = PFX + "engaged; result: failure"
ENGAGED_ERR = PFX + "engaged; result: error"
ENGAGED_UNEXP = PFX + "engaged; result: unexpected-success"
ENGAGED_UNKNOWN = PFX + "engaged; result: unknown"
NOT_ENGAGED = PFX + "not-engaged; result: plain"
ABSENT = P._C01_STATUS_ABSENT
SUITE = "scripts/test_unit.py"
CAND = "scripts/mod_called.py"

CALLED_MODULE = '''"""A module the fixture suites drive."""


def greet():
    return "hi"


if __name__ == "__main__":
    print(greet())
'''

#: A unittest suite that really calls the candidate: red once it is perturbed.
UNIT_SUITE = '''import unittest

import mod_called


class T(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(mod_called.greet(), "hi")


if __name__ == "__main__":
    unittest.main()
'''

#: A unittest suite that imports the candidate and never calls it.
UNCOVERED_SUITE = '''import unittest

import mod_called


class T(unittest.TestCase):
    def test_nothing(self):
        self.assertTrue(mod_called is not None)


if __name__ == "__main__":
    unittest.main()
'''

#: A plain script suite, no unittest at all.
GREEN_SCRIPT = '''import mod_called

assert mod_called.greet() == "hi"
print("OK")
'''

RED_SCRIPT = '''import sys
print("this suite is red before anything is perturbed")
sys.exit(1)
'''

ARGV_SUITE = '''import sys
if len(sys.argv) > 50:
    pass
print("OK")
'''

COMMENT_AND_STRING_SUITE = '''# this suite ignores -f and never reads sys.argv
FLAG_TEXT = "-f"
FAILFAST_TEXT = "--failfast"
ARGV_TEXT = "sys.argv"
FIXTURE_MODEL = """
    import sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    print(prompt)
"""
print("OK")
'''


def _write(path, body):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _make_copy(tmp, name="copy", sha=SHA_A, marker_sha=None, attached=False,
               no_marker=False, root_symlink=False, git_symlink=False,
               marker_symlink=False, malformed=False):
    """A work copy shape by hand, or one deliberately broken piece of it."""
    target = os.path.join(tmp, name + "-real") if root_symlink else os.path.join(tmp, name)
    root = os.path.join(tmp, name)
    os.makedirs(os.path.join(target, "scripts"), exist_ok=True)
    git_dir = os.path.join(target, ".git")
    head_text = (sha if not attached else "ref: refs/heads/main") + "\n"
    if git_symlink:
        outside_git = os.path.join(tmp, name + "-git-outside")
        _write(os.path.join(outside_git, "HEAD"), head_text)
        os.symlink(outside_git, git_dir)
    else:
        _write(os.path.join(git_dir, "HEAD"), head_text)
    if root_symlink:
        os.symlink(target, root)
    marker = root.rstrip(os.sep) + P._C01_MARKER_SUFFIX
    if not no_marker:
        content = "not a commit\n" if malformed else (
            (sha if marker_sha is None else marker_sha) + "\n")
        if marker_symlink:
            outside_marker = os.path.join(tmp, name + "-marker-outside.txt")
            _write(outside_marker, content)
            os.symlink(outside_marker, marker)
        else:
            _write(marker, content)
    return root


def _fixtures(root, suite_body=UNIT_SUITE):
    _write(os.path.join(root, CAND), CALLED_MODULE)
    _write(os.path.join(root, SUITE), suite_body)
    _write(os.path.join(root, "scripts", "test_red.py"), RED_SCRIPT)
    _write(os.path.join(root, "scripts", "test_argv.py"), ARGV_SUITE)
    _write(os.path.join(root, "scripts", "test_uncovered.py"), UNCOVERED_SUITE)
    _write(os.path.join(root, "scripts", "test_script.py"), GREEN_SCRIPT)


class _RunLog(object):
    """Canned run_suite: records (rel_path, root, fail_fast) and answers from
    a queue, then from a per-mode default."""

    def __init__(self, answers=None, plain=(0, "OK"), fast=(0, ENGAGED_OK)):
        self.calls = []
        self._answers = list(answers or [])
        self._plain, self._fast = plain, fast

    def __call__(self, rel_path, root=None, timeout=None, *, fail_fast=False):
        self.calls.append((rel_path, root, fail_fast))
        if self._answers:
            return self._answers.pop(0)
        return self._fast if fail_fast else self._plain

    def flags(self):
        return [c[2] for c in self.calls]


class _Recorder(object):
    """Delegates to the REAL run_suite and records each call and result."""

    def __init__(self, real, after=None):
        self.real, self.after, self.calls = real, after, []

    def __call__(self, rel_path, root=None, timeout=None, *, fail_fast=False):
        rc, tail = self.real(rel_path, root=root, timeout=timeout,
                             fail_fast=fail_fast)
        self.calls.append((rel_path, fail_fast, rc, tail))
        if self.after:
            self.after()
        return rc, tail

    def flags(self):
        return [c[1] for c in self.calls]


ORPHAN_DELAY = 3


def _orphan_suite(root, name, mode):
    """A suite that starts a real grandchild in its own process group (stdio
    detached, so a completed parent really completes) which writes a marker
    after ORPHAN_DELAY seconds, then finishes green, red or hangs."""
    marker = os.path.join(root, name + "-marker.txt")
    child = os.path.join(root, "scripts", name + "_grandchild.py")
    _write(child, "import time\ntime.sleep(%d)\n"
                  "with open(%r, 'w') as _fh:\n    _fh.write('here')\n"
                  % (ORPHAN_DELAY, marker))
    lines = ["import subprocess", "import sys", "import time",
             "subprocess.Popen([sys.executable, %r], stdout=subprocess.DEVNULL, "
             "stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)" % child]
    lines.append({"green": "print('OK')", "red": "sys.exit(1)",
                  "hang": "while True:\n    time.sleep(1)"}[mode])
    _write(os.path.join(root, "scripts", name + ".py"), "\n".join(lines) + "\n")
    return "scripts/%s.py" % name, marker


class _ProcProxy(object):
    """A real Popen whose communicate/wait can be made to fail. Every real
    process behind one is reaped, and its pipe closed, by the test cleanup."""
    made = []

    def __init__(self, real, communicate_raises=None, wait_raises=None):
        self._real = real
        _ProcProxy.made.append(real)
        self._comm, self._wait = communicate_raises, wait_raises

    @property
    def pid(self):
        return self._real.pid

    @property
    def returncode(self):
        return self._real.returncode

    def communicate(self, timeout=None):
        if self._comm is not None:
            raise self._comm
        return self._real.communicate(timeout=timeout)

    def wait(self, timeout=None):
        if self._wait is not None:
            raise self._wait
        return self._real.wait(timeout=timeout)


class TestC01KGuard(unittest.TestCase):

    def setUp(self):
        saved = (P.run_suite, P.sha256_bytes, P._C01_RUNNER,
                 subprocess.Popen, os.killpg)
        P.reset_ledger()

        def restore():
            (P.run_suite, P.sha256_bytes, P._C01_RUNNER,
             subprocess.Popen, os.killpg) = saved
            P.reset_ledger()
            while _ProcProxy.made:
                real = _ProcProxy.made.pop()
                if real.stdout is not None:
                    real.stdout.close()
                real.wait(timeout=30)
        self.addCleanup(restore)
        self.real_run_suite = saved[0]
        self.tmp = tempfile.mkdtemp(prefix="c01-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _seed(self, root, suite_rel=SUITE, duration=5.0):
        P._C01_CALIBRATED[(os.path.realpath(root), suite_rel)] = duration
        P._LAST_DURATION[suite_rel] = duration

    def _copy(self, name="copy", body=UNIT_SUITE, seed=True):
        root = _make_copy(self.tmp, name)
        _fixtures(root, body)
        if seed:
            self._seed(root)
        return root

    # ---------------------------------------------------------------- 1

    def test_legacy_call_shapes_and_default_argv(self):
        root = self._copy(seed=False)
        seen = []
        real_popen = subprocess.Popen

        def recording(argv, *a, **k):
            seen.append(list(argv))
            return real_popen(argv, *a, **k)

        subprocess.Popen = recording
        try:
            result = P.run_suite(SUITE, root, 60)  # old positional shape
            flagged = P.run_suite(SUITE, root, 60, fail_fast=True)
        finally:
            subprocess.Popen = real_popen
        path = os.path.join(root, SUITE)
        self.assertEqual(seen[0], [sys.executable, path])
        self.assertEqual(seen[1], [sys.executable, P._C01_RUNNER, path])
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], 0)
        self.assertEqual(flagged, (0, ENGAGED_OK))

        # the pool's shape: a plain timed baseline, then covers(baseline=0)
        P.reset_ledger()
        rc, _tail = P.run_suite(SUITE, root=root, timeout=60)
        self.assertEqual(rc, 0)
        self.assertIn((os.path.realpath(root), SUITE), P._C01_CALIBRATED)
        rec = _Recorder(self.real_run_suite)
        P.run_suite = rec
        verdict = P.covers(SUITE, CAND, root, 0)  # old positional shape
        self.assertIsInstance(verdict, tuple)
        self.assertEqual(len(verdict), 2)
        self.assertIs(verdict[0], True, verdict)
        self.assertEqual(rec.flags(), [True, True], rec.calls)
        self.assertEqual(rec.calls[0][3], ENGAGED_OK)
        self.assertEqual(rec.calls[1][3], ENGAGED_ERR)  # the perturbed call raises

    # ---------------------------------------------------------------- 2

    def _argv_fixture(self, root, records):
        """The counterexample: a helper the suite imports branches on
        len(sys.argv) and calls the candidate; the suite never reads argv
        and never calls the candidate. The suite sits in tests/, a symlink,
        outside the runner's directory."""
        _write(os.path.join(root, CAND), CALLED_MODULE)
        real_tests = os.path.join(root, "tests-real")
        _write(os.path.join(real_tests, "helper_argv.py"), '''import json
import sys

import __main__ as M

with open(%r, "a", encoding="utf-8") as _fh:
    _fh.write(json.dumps({
        "argv": sys.argv, "orig_argv": getattr(sys, "orig_argv", None),
        "path0": sys.path[0], "name": M.__name__,
        "file": getattr(M, "__file__", None),
        "loader": type(getattr(M, "__loader__", None)).__name__,
        "package": repr(getattr(M, "__package__", "absent")),
        "spec": repr(getattr(M, "__spec__", "absent")),
        "cached": repr(getattr(M, "__cached__", "absent")),
    }) + "\\n")

sys.path.insert(1, %r)
import mod_called

if len(sys.argv) > 1:
    mod_called.greet()
''' % (records, os.path.join(root, "scripts")))
        _write(os.path.join(real_tests, "test_calls.py"), '''import unittest

import helper_argv


class T(unittest.TestCase):
    def test_imports(self):
        self.assertTrue(helper_argv is not None)


if __name__ == "__main__":
    unittest.main(argv=["test_calls"])
''')
        os.symlink(real_tests, os.path.join(root, "tests"))
        return "tests/test_calls.py"

    def test_runner_preserves_plain_argv(self):
        copy = _make_copy(self.tmp, "copy")
        plain_tree = os.path.join(self.tmp, "plain")
        records = os.path.join(self.tmp, "records.jsonl")
        suite = self._argv_fixture(copy, records)
        self._argv_fixture(plain_tree, os.path.join(self.tmp, "plain.jsonl"))

        self.assertEqual(P.run_suite(suite, root=copy, timeout=60)[0], 0)
        self.assertEqual(P.run_suite(suite, root=copy, timeout=60,
                                     fail_fast=True), (0, ENGAGED_OK))
        with open(records, encoding="utf-8") as fh:
            plain_rec, runner_rec = [json.loads(ln) for ln in fh]
        self.assertEqual(runner_rec, plain_rec)
        self.assertEqual(plain_rec["argv"], [os.path.join(copy, suite)])

        # the full plain chain (not a copy) and the fast cut must agree
        full = P.covers(suite, CAND, root=plain_tree)
        rec = _Recorder(self.real_run_suite)
        P.run_suite = rec
        fast = P.covers(suite, CAND, root=copy, baseline=0)
        self.assertIs(full[0], False, full)
        self.assertIs(fast[0], full[0], (fast, full))
        self.assertIn(True, rec.flags(), "the fast cut never used the runner")
        self.assertEqual(rec.calls[0][3], ENGAGED_OK)

    # ---------------------------------------------------------------- 3

    def test_flagged_run_stops_after_first_failure(self):
        marker = os.path.join(self.tmp, "second.txt")
        _write(os.path.join(self.tmp, "scripts", "test_two.py"), '''import unittest


class T(unittest.TestCase):
    def test_a(self):
        self.fail("first test fails")

    def test_b(self):
        with open(%r, "w") as fh:
            fh.write("ran")


if __name__ == "__main__":
    unittest.main()
''' % marker)
        rc, tail = P.run_suite("scripts/test_two.py", root=self.tmp,
                               timeout=60, fail_fast=True)
        self.assertEqual((rc, tail), (1, ENGAGED_FAIL))
        self.assertFalse(os.path.exists(marker), "fail fast never engaged")
        rc, _tail = P.run_suite("scripts/test_two.py", root=self.tmp, timeout=60)
        self.assertEqual(rc, 1)
        self.assertTrue(os.path.exists(marker), "control: plain run skipped test_b")

    # ---------------------------------------------------------------- 4

    def test_own_runner_is_plain_and_reports_not_engaged(self):
        marker = os.path.join(self.tmp, "own-second.txt")
        root = self._copy(body='''import sys
import unittest

import mod_called


class T(unittest.TestCase):
    def test_a(self):
        self.assertEqual(mod_called.greet(), "hi")

    def test_b(self):
        with open(%r, "w") as fh:
            fh.write("ran")


result = unittest.TextTestRunner().run(
    unittest.defaultTestLoader.loadTestsFromTestCase(T))
sys.exit(not result.wasSuccessful())
''' % marker, seed=False)
        cand = os.path.join(root, CAND)
        original = _read(cand)
        _write(cand, P.perturbed_source(original.decode("utf-8")))
        try:
            rc, tail = P.run_suite(SUITE, root=root, timeout=60, fail_fast=True)
        finally:
            with open(cand, "wb") as fh:
                fh.write(original)
        self.assertEqual((rc, tail), (1, NOT_ENGAGED))
        self.assertTrue(os.path.exists(marker), "test_b did not run: fail fast leaked")

        self.assertEqual(P.run_suite(SUITE, root=root, timeout=60)[0], 0)
        rec = _Recorder(self.real_run_suite)
        P.run_suite = rec
        verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
        self.assertIs(verdict, True, detail)
        # probe (runner, not engaged), plain clean fallback, ONE plain perturbed run
        self.assertEqual(rec.flags(), [True, False, False], rec.calls)
        self.assertEqual(rec.calls[0][3], NOT_ENGAGED)

    # ---------------------------------------------------------------- 5

    def test_runner_status_classification(self):
        head = "import os\nimport sys\nimport unittest\n\n\n"
        cases = {
            "failure": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                        "        self.fail('x')\n\n\nunittest.main()\n", 1, ENGAGED_FAIL),
            "error": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                      "        raise ValueError('x')\n\n\nunittest.main()\n", 1, ENGAGED_ERR),
            "sysexit": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                        "        sys.exit(3)\n\n\nunittest.main()\n", 1, ENGAGED_ERR),
            "setupclass": ("class T(unittest.TestCase):\n    @classmethod\n"
                           "    def setUpClass(cls):\n        raise ValueError('x')\n\n"
                           "    def test_a(self):\n        pass\n\n\nunittest.main()\n",
                           1, ENGAGED_ERR),
            "unexpected": ("class T(unittest.TestCase):\n    @unittest.expectedFailure\n"
                           "    def test_a(self):\n        pass\n\n\nunittest.main()\n",
                           1, ENGAGED_UNEXP),
            "skips": ("class T(unittest.TestCase):\n    @unittest.skip('s')\n"
                      "    def test_a(self):\n        pass\n\n    @unittest.expectedFailure\n"
                      "    def test_b(self):\n        self.fail('x')\n\n\nunittest.main()\n",
                      0, ENGAGED_OK),
            "explicit": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                         "        pass\n\n\nunittest.main(failfast=False)\n", 0, NOT_ENGAGED),
            "ownexit": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                        "        pass\n\n\nunittest.main(exit=False)\nsys.exit(1)\n",
                        1, ENGAGED_UNKNOWN),
            "twoprograms": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                            "        pass\n\n\nunittest.main(exit=False)\n"
                            "unittest.main(exit=False)\n", 0, ENGAGED_UNKNOWN),
            "customresult": ("class R(unittest.TextTestResult):\n    pass\n\n\n"
                             "unittest.TextTestRunner.resultclass = R\n\n\n"
                             "class T(unittest.TestCase):\n    def test_a(self):\n"
                             "        pass\n\n\nunittest.main()\n", 0, ENGAGED_UNKNOWN),
            "customrunner": ("class R(unittest.TextTestRunner):\n    pass\n\n\n"
                             "class T(unittest.TestCase):\n    def test_a(self):\n"
                             "        pass\n\n\nunittest.main(testRunner=R)\n",
                             0, ENGAGED_UNKNOWN),
            "failfastoff": ("class Prog(unittest.TestProgram):\n"
                            "    def parseArgs(self, argv):\n"
                            "        unittest.TestProgram.parseArgs(self, argv)\n"
                            "        self.failfast = False\n\n\n"
                            "class T(unittest.TestCase):\n    def test_a(self):\n"
                            "        pass\n\n\nProg()\n", 0, ENGAGED_UNKNOWN),
            "mixedprograms": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                              "        pass\n\n\nunittest.main(failfast=False, exit=False)\n"
                              "unittest.main()\n", 0, ENGAGED_UNKNOWN),
            "osexit": ("class T(unittest.TestCase):\n    def test_a(self):\n"
                       "        os._exit(1)\n\n\nunittest.main()\n", 1, ABSENT),
        }
        # the runner's own entry point refuses any other argument count
        # before it touches anything, and so prints no status line
        import c01_failfast_runner as RUN
        with open(os.devnull, "w") as sink:
            saved_err, sys.stderr = sys.stderr, sink
            try:
                self.assertEqual(RUN.main([]), 2)
                self.assertEqual(RUN.main(["a.py", "b.py"]), 2)
            finally:
                sys.stderr = saved_err
        for name, (body, want_rc, want_tail) in sorted(cases.items()):
            rel = "scripts/test_%s.py" % name
            _write(os.path.join(self.tmp, rel), head + body)
            with self.subTest(case=name):
                self.assertEqual(P.run_suite(rel, root=self.tmp, timeout=60,
                                             fail_fast=True),
                                 (want_rc, want_tail))

    # ---------------------------------------------------------------- 6

    def test_timing_guard_restores_both_dicts(self):
        root = self._copy(seed=False)
        suite = "scripts/test_uncovered.py"
        key = (os.path.realpath(root), suite)
        P._C01_CALIBRATED[key] = 7.0
        P._LAST_DURATION["scripts/other.py"] = 3.0
        before_dur = dict(P._LAST_DURATION)
        before_cal = dict(P._C01_CALIBRATED)
        written = []
        rec = _Recorder(self.real_run_suite, after=lambda: written.append(
            (dict(P._LAST_DURATION), dict(P._C01_CALIBRATED))))
        P.run_suite = rec
        verdict, detail = P.covers(suite, CAND, root=root, baseline=0)
        self.assertIs(verdict, False, detail)
        self.assertEqual(rec.flags(), [True, True, False], rec.calls)
        # the green plain comparison really wrote both dicts
        dur, cal = written[-1]
        self.assertIn(suite, dur)
        self.assertNotEqual(cal[key], 7.0)
        self.assertEqual(P._LAST_DURATION, before_dur)
        self.assertEqual(P._C01_CALIBRATED, before_cal)
        self.assertEqual(P.wall_for(suite), P.TIMEOUT)

    # ---------------------------------------------------------------- 7

    def test_red_fallback_probe_is_no_data(self):
        root = self._copy()
        cand = os.path.join(root, CAND)
        before = _read(cand)
        for probe in ((0, NOT_ENGAGED), (1, ENGAGED_FAIL)):
            for fallback in ((3, "the clean suite is red"), (None, "unavailable")):
                with self.subTest(probe=probe, fallback=fallback):
                    log = _RunLog([probe, fallback, (5, "would read as covered")])
                    P.run_suite = log
                    verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
                    self.assertIsNone(verdict, detail)
                    self.assertEqual(log.flags(), [True, False], log.calls)
                    self.assertEqual(_read(cand), before)

    # ---------------------------------------------------------------- 8

    def test_probe_failures_fall_back_without_permission(self):
        root = self._copy()
        for flagged in ((None, "timed out"), (None, "could not run it"),
                        (0, ABSENT), (0, PFX + "engaged; result: great"),
                        (0, ENGAGED_OK + " extra"), (0, ENGAGED_OK + "\n"),
                        (0, "OK"),
                        (0, NOT_ENGAGED), (0, ENGAGED_UNKNOWN)):
            with self.subTest(flagged=flagged):
                log = _RunLog([flagged, (0, "plain ok")])
                P.run_suite = log
                self.assertEqual(P.baseline_probe(SUITE, root=root),
                                 (0, False, "plain ok"))
                self.assertEqual(log.flags(), [True, False])

        # missing or unparseable source, or no calibration: no runner call
        _write(os.path.join(root, "scripts", "test_broken.py"), "def (:\n")
        for rel in ("scripts/test_missing.py", "scripts/test_broken.py",
                    "scripts/test_argv.py"):
            with self.subTest(rel=rel):
                log = _RunLog()
                P.run_suite = log
                self._seed(root, rel)
                self.assertIs(P.baseline_probe(rel, root=root)[1], False)
                self.assertEqual(log.flags(), [False])
        other = self._copy("uncalibrated", seed=False)
        log = _RunLog()
        P.run_suite = log
        self.assertIs(P.baseline_probe(SUITE, root=other)[1], False)
        self.assertEqual(log.flags(), [False])

        # a missing runner file: real run, no status line, refused
        runner = P._C01_RUNNER
        P._C01_RUNNER = os.path.join(self.tmp, "no_such_runner.py")
        rec = _Recorder(self.real_run_suite)
        P.run_suite = rec
        rc, ok, _tail = P.baseline_probe(SUITE, root=root)
        P._C01_RUNNER = runner
        self.assertEqual((rc, ok), (0, False))
        _suite, fast, _rc, tail = rec.calls[0]
        self.assertEqual((fast, tail), (True, ABSENT))

        # a suite that forges a status line AFTER the runner's own
        forged = "scripts/test_forged.py"
        _write(os.path.join(root, forged),
               "import atexit\natexit.register(print, %r)\nprint('OK')\n"
               % ENGAGED_OK)
        self._seed(root, forged)
        rec = _Recorder(self.real_run_suite)
        P.run_suite = rec
        rc, ok, _tail = P.baseline_probe(forged, root=root)
        self.assertEqual((rc, ok), (0, False))
        self.assertEqual(rec.calls[0][3], ABSENT)

        # a green plain fallback keeps the row measurable through the plain path
        log = _RunLog([(None, "timed out"), (0, "OK"), (4, "caught")])
        P.run_suite = log
        verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
        self.assertIs(verdict, True, detail)
        self.assertEqual(log.flags(), [True, False, False])

    # ---------------------------------------------------------------- 9

    def test_copy_boundary_and_path_escapes(self):
        tmp = self.tmp
        self.assertFalse(P._c01_work_copy(os.path.join(tmp, "nowhere")))
        bad = [_make_copy(tmp, "attached", attached=True,
                          marker_sha="ref: refs/heads/main"),
               _make_copy(tmp, "no-marker", no_marker=True),
               _make_copy(tmp, "mismatch", marker_sha=SHA_B),
               _make_copy(tmp, "malformed", malformed=True),
               _make_copy(tmp, "root-link", root_symlink=True),
               _make_copy(tmp, "git-link", git_symlink=True),
               _make_copy(tmp, "marker-link", marker_symlink=True)]
        head_link = _make_copy(tmp, "head-link")
        head_file = os.path.join(head_link, ".git", "HEAD")
        _write(os.path.join(tmp, "head-outside"), SHA_A + "\n")
        os.remove(head_file)
        os.symlink(os.path.join(tmp, "head-outside"), head_file)
        bad.append(head_link)
        # non UTF-8 bytes, one read each: HEAD, then the sibling marker
        binary = []
        for name, piece in (("head-bytes", "head"), ("marker-bytes", "marker")):
            root = _make_copy(tmp, name)
            path = (os.path.join(root, ".git", "HEAD") if piece == "head"
                    else root + P._C01_MARKER_SUFFIX)
            with open(path, "wb") as fh:
                fh.write(b"\xff\xfe" + SHA_A.encode("ascii") + b"\n")
            binary.append(root)
        bad.extend(binary)
        for root in bad:
            with self.subTest(root=os.path.basename(root)):
                self.assertFalse(P._c01_work_copy(root))
                _fixtures(root)
                self._seed(root)
                log = _RunLog()
                P.run_suite = log
                P.covers(SUITE, CAND, root=root, baseline=0)
                self.assertNotIn(True, log.flags(), "runner used without copy evidence")

        # the default run_suite path consults the copy check after a green run
        for root in binary:
            with self.subTest(plain_green=os.path.basename(root)):
                _fixtures(root)  # own fixtures: never rely on the loop above
                P.run_suite = self.real_run_suite
                rc, _tail = P.run_suite("scripts/test_script.py", root=root)
                self.assertEqual(rc, 0, _tail)

        valid = self._copy("valid")
        self.assertTrue(P._c01_work_copy(valid))
        saved_root = P.ROOT
        P.ROOT = valid
        try:
            self.assertTrue(P._c01_work_copy(P.ROOT))
        finally:
            P.ROOT = saved_root
        log = _RunLog([(0, ENGAGED_OK), (1, ENGAGED_FAIL)])
        P.run_suite = log
        self.assertIs(P.covers(SUITE, CAND, root=valid, baseline=0)[0], True)
        self.assertEqual(log.flags(), [True, True])

        outside = os.path.join(tmp, "escape.py")
        _write(outside, "def value():\n    return 1\n")
        before = _read(outside)
        os.symlink(outside, os.path.join(valid, "scripts", "escape_link.py"))
        for rel, suite in (("../escape.py", SUITE), (outside, SUITE),
                           ("scripts/escape_link.py", SUITE),
                           (CAND, "../escape.py")):
            with self.subTest(rel=rel, suite=suite):
                log = _RunLog()
                P.run_suite = log
                verdict, detail = P.covers(suite, rel, root=valid, baseline=0)
                self.assertIsNone(verdict, detail)
                self.assertIn("escapes", detail)
                self.assertEqual(log.calls, [])
                self.assertEqual(_read(outside), before)

    # ---------------------------------------------------------------- 10

    def test_missing_or_cross_root_calibration_is_no_data(self):
        copy_a = self._copy("copy-a", seed=False)
        copy_b = self._copy("copy-b")  # only b is calibrated
        before = _read(os.path.join(copy_a, CAND))
        log = _RunLog()
        P.run_suite = log
        verdict, detail = P.covers(SUITE, CAND, root=copy_a, baseline=0)
        self.assertIsNone(verdict, detail)
        self.assertIn("calibration", detail)
        self.assertEqual(log.calls, [])
        self.assertEqual(_read(os.path.join(copy_a, CAND)), before)

        P.run_suite = self.real_run_suite
        self.assertEqual(P.run_suite(SUITE, root=copy_a, timeout=60)[0], 0)
        self.assertIn((os.path.realpath(copy_a), SUITE), P._C01_CALIBRATED)
        self.assertIn((os.path.realpath(copy_b), SUITE), P._C01_CALIBRATED)
        log = _RunLog([(0, ENGAGED_OK), (0, ENGAGED_OK), (0, "OK")])
        P.run_suite = log
        verdict, detail = P.covers(SUITE, CAND, root=copy_a, baseline=0)
        self.assertIs(verdict, False, detail)

    # ---------------------------------------------------------------- 11

    def test_clean_probe_precedes_perturbation(self):
        root = self._copy()
        cand = os.path.join(root, CAND)
        original = _read(cand)
        seen = []

        def recording(rel_path, root=None, timeout=None, *, fail_fast=False):
            seen.append((fail_fast, _read(cand)))
            return (0, ENGAGED_OK) if fail_fast else (0, "OK")

        P.run_suite = recording
        verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
        self.assertIs(verdict, False, detail)
        self.assertEqual([s[0] for s in seen], [True, True, False])
        self.assertEqual(seen[0][1], original)
        for _fast, data in seen[1:]:
            self.assertNotEqual(data, original)
        self.assertEqual(_read(cand), original)

    # ---------------------------------------------------------------- 12

    def test_red_or_unavailable_baseline_never_perturbs(self):
        root = self._copy()
        cand = os.path.join(root, CAND)
        before = _read(cand)
        for baseline, answers in ((3, []), (None, [(9, "red")]),
                                  (None, [(None, "cannot run")])):
            with self.subTest(baseline=baseline, answers=answers):
                log = _RunLog(answers)
                P.run_suite = log
                verdict, detail = P.covers(SUITE, CAND, root=root,
                                           baseline=baseline)
                self.assertIsNone(verdict, detail)
                self.assertNotIn(True, log.flags())
                self.assertEqual(len(log.calls), len(answers))
                self.assertEqual(_read(cand), before)

    # ---------------------------------------------------------------- 13

    def test_rejected_flag_uses_plain_measurement(self):
        root = self._copy()
        log = _RunLog([(0, NOT_ENGAGED), (0, "OK"), (6, "caught")])
        P.run_suite = log
        verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
        self.assertIs(verdict, True, detail)
        self.assertEqual(log.flags(), [True, False, False])

        self._seed(root, "scripts/test_argv.py")
        log = _RunLog(plain=(0, "still green"))
        P.run_suite = log
        verdict, detail = P.covers("scripts/test_argv.py", CAND, root=root,
                                   baseline=0)
        self.assertIs(verdict, False, detail)
        self.assertEqual(log.flags(), [False, False])

    # ---------------------------------------------------------------- 14

    def test_flag_source_is_parsed(self):
        self.assertTrue(P._c01_flag_safe(COMMENT_AND_STRING_SUITE))
        self.assertTrue(P._c01_flag_safe(
            '"""Mentions -f, --failfast and sys.argv only here."""\nprint(1)\n'))
        self.assertTrue(P._c01_flag_safe(
            'WRITER_MODEL = """\n    import sys\n    p = sys.argv[-1]\n"""\n'))
        for hostile in ("import sys\nif '-f' in sys.argv:\n    pass\n",
                        "import sys as s\nif s.argv:\n    pass\n",
                        "from sys import argv\nprint(argv)\n",
                        "from sys import argv as A\nprint(A)\n",
                        "import argparse\nargparse.ArgumentParser().parse_args()\n",
                        "import optparse\noptparse.OptionParser()\n",
                        "import getopt\ngetopt.getopt([], '')\n",
                        "def (:\n"):
            with self.subTest(source=hostile):
                self.assertFalse(P._c01_flag_safe(hostile))
        for name in ("test_receipt_door.py", "test_brother_run.py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                self.assertTrue(P._c01_flag_safe(fh.read()), name)

    # ---------------------------------------------------------------- 15

    def test_probe_permission_is_not_reused(self):
        root = self._copy("root")
        other = self._copy("other")
        _write(os.path.join(root, "scripts", "mod_second.py"),
               "def other():\n    return 2\n")
        _write(os.path.join(root, "scripts", "test_sibling.py"), UNIT_SUITE)
        self._seed(root, "scripts/test_sibling.py")
        rejecting = (1, ENGAGED_FAIL)
        for pairs in (((SUITE, CAND, root), (SUITE, "scripts/mod_second.py", root)),
                      ((SUITE, CAND, root), (SUITE, CAND, other)),
                      ((SUITE, CAND, root), ("scripts/test_sibling.py", CAND, root))):
            with self.subTest(pairs=pairs):
                log = _RunLog(fast=rejecting)
                P.run_suite = log
                for suite, cand, where in pairs:
                    P.covers(suite, cand, root=where, baseline=0)
                probes = [c for c in log.calls if c[2]]
                self.assertEqual([(c[0], c[1]) for c in probes],
                                 [(p[0], p[2]) for p in pairs])
        self.assertTrue(P._C01_CALIBRATED)
        P.reset_ledger()
        self.assertEqual(P._C01_CALIBRATED, {})
        self.assertEqual(P._LAST_DURATION, {})

    # ---------------------------------------------------------------- 16

    def test_only_plain_clean_runs_calibrate(self):
        root = self._copy(seed=False)
        key = (os.path.realpath(root), SUITE)
        P._LAST_DURATION[SUITE] = P._C01_CALIBRATED[key] = 12345.0
        _write(os.path.join(root, "scripts", "test_hangs.py"),
               "import time\nwhile True:\n    time.sleep(1)\n")
        for rel, kwargs, want in ((SUITE, {"fail_fast": True}, 0),
                                  ("scripts/test_red.py", {"fail_fast": True}, 1),
                                  ("scripts/test_hangs.py", {"timeout": 1}, None)):
            with self.subTest(rel=rel):
                self.assertEqual(P.run_suite(rel, root=root, **kwargs)[0], want)
                self.assertEqual(P._LAST_DURATION, {SUITE: 12345.0})
                self.assertEqual(P._C01_CALIBRATED, {key: 12345.0})
        self.assertEqual(P.run_suite(SUITE, root=root)[0], 0)
        self.assertNotEqual(P._LAST_DURATION[SUITE], 12345.0)
        self.assertNotEqual(P._C01_CALIBRATED[key], 12345.0)

    # ---------------------------------------------------------------- 17

    def test_flagged_failure_and_unknown_are_distinct(self):
        root = self._copy()
        # rows 3 and 4: a standard engaged red, or a run that was plain
        for status in (ENGAGED_FAIL, ENGAGED_ERR, ENGAGED_UNEXP, NOT_ENGAGED):
            with self.subTest(status=status):
                log = _RunLog([(0, ENGAGED_OK), (4, status)])
                P.run_suite = log
                verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
                self.assertIs(verdict, True, detail)
                self.assertEqual(log.flags(), [True, True])
        log = _RunLog([(0, ENGAGED_OK), (None, "hung mid perturbation")])
        P.run_suite = log
        verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
        self.assertIsNone(verdict, detail)
        self.assertEqual(log.flags(), [True, True])

    # ---------------------------------------------------------------- 18

    def test_nonstandard_nonzero_requires_plain_comparison(self):
        root = self._copy()
        for status in (ENGAGED_UNKNOWN, ENGAGED_OK, ABSENT):
            for plain, want in (((0, "OK"), False), ((2, "red"), True),
                                ((None, "hung"), None)):
                with self.subTest(status=status, plain=plain):
                    log = _RunLog([(0, ENGAGED_OK), (1, status), plain])
                    P.run_suite = log
                    verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
                    self.assertIs(verdict, want, detail)
                    self.assertEqual(log.flags(), [True, True, False])

    # ---------------------------------------------------------------- 19

    def test_flagged_green_requires_plain_agreement(self):
        root = self._copy()
        cand = os.path.join(root, CAND)
        original = _read(cand)
        for plain, want, reason in (((0, "OK"), False, "stayed green"),
                                    ((5, "caught"), None, "divergence"),
                                    ((None, "hung"), None, "could not reach")):
            with self.subTest(plain=plain):
                seen = []
                answers = [(0, ENGAGED_OK), (0, ENGAGED_OK), plain]

                def recording(rel_path, root=None, timeout=None, *, fail_fast=False):
                    seen.append((fail_fast, _read(cand)))
                    return answers.pop(0)

                P.run_suite = recording
                verdict, detail = P.covers(SUITE, CAND, root=root, baseline=0)
                self.assertIs(verdict, want, detail)
                self.assertIn(reason, detail)
                self.assertEqual([s[0] for s in seen], [True, True, False])
                self.assertEqual(seen[1][1], seen[2][1])
                self.assertNotEqual(seen[2][1], original)

    # ---------------------------------------------------------------- 20

    def test_orphan_marker_absent_on_every_started_path(self):
        root = self.tmp
        real_killpg = os.killpg
        pending = []
        # control: the same boundary with group cleanup disabled
        suite, marker = _orphan_suite(root, "control", "green")
        os.killpg = lambda pgid, sig: None
        try:
            self.assertEqual(P.run_suite(suite, root=root, timeout=30)[0], 0)
        finally:
            os.killpg = real_killpg
        pending.append(("control", marker, True))
        for mode, kwargs, want in (("green", {}, 0), ("red", {}, 1),
                                   ("hang", {"timeout": 1}, None),
                                   ("red", {"fail_fast": True}, 1)):
            name = "%s-%s" % (mode, "runner" if kwargs.get("fail_fast") else "plain")
            suite, marker = _orphan_suite(root, name.replace("-", "_"), mode)
            self.assertEqual(P.run_suite(suite, root=root, **kwargs)[0], want)
            pending.append((name, marker, False))
        suite, marker = _orphan_suite(root, "comm", "green")
        real_popen = subprocess.Popen
        subprocess.Popen = lambda *a, **k: _ProcProxy(
            real_popen(*a, **k), communicate_raises=OSError("simulated"))
        try:
            rc, tail = P.run_suite(suite, root=root, timeout=30)
        finally:
            subprocess.Popen = real_popen
        self.assertIsNone(rc)
        self.assertIn("communication", tail)
        pending.append(("comm", marker, False))
        time.sleep(ORPHAN_DELAY + 3)
        for name, marker, expected in pending:
            with self.subTest(path=name):
                self.assertIs(os.path.exists(marker), expected,
                              "marker presence wrong after the %s path" % name)

    # ---------------------------------------------------------------- 21

    def test_cleanup_errors_are_no_data(self):
        real_killpg = os.killpg

        def raise_lookup(pgid, sig):
            raise ProcessLookupError()

        def raise_permission(pgid, sig):
            raise PermissionError("simulated: not permitted")

        os.killpg = raise_lookup
        self.assertIsNone(P._c01_kill_group(123456))
        os.killpg = raise_permission
        self.assertIn("not permitted", P._c01_kill_group(123456))
        os.killpg = real_killpg

        _write(os.path.join(self.tmp, "scripts", "fast.py"), "print('OK')\n")
        recorded = []

        def recording(pgid, sig):
            recorded.append(pgid)
            return real_killpg(pgid, sig)

        real_popen = subprocess.Popen
        spawned = []

        def remembering(*a, **k):
            proc = real_popen(*a, **k)
            spawned.append(proc.pid)
            return proc

        subprocess.Popen, os.killpg = remembering, recording
        try:
            self.assertEqual(P.run_suite("scripts/fast.py", root=self.tmp), (0, "OK"))
        finally:
            subprocess.Popen, os.killpg = real_popen, real_killpg
        self.assertEqual(recorded, spawned, "cleanup did not use the saved pid")

        os.killpg = raise_permission
        try:
            rc, tail = P.run_suite("scripts/fast.py", root=self.tmp)
        finally:
            os.killpg = real_killpg
        self.assertIsNone(rc)
        self.assertIn("not permitted", tail)

        # bounded reap failures: timeout path and communication-error path
        for proxy_kwargs, reason in (
                ({"communicate_raises": subprocess.TimeoutExpired("x", 1)},
                 "did not die"),
                ({"communicate_raises": OSError("simulated"),
                  "wait_raises": subprocess.TimeoutExpired("x", 30)},
                 "could not be reaped")):
            with self.subTest(reason=reason):
                subprocess.Popen = lambda *a, **k: _ProcProxy(
                    real_popen(*a, **k), **proxy_kwargs)
                os.killpg = lambda pgid, sig: None
                try:
                    rc, tail = P.run_suite("scripts/fast.py", root=self.tmp,
                                           timeout=1)
                finally:
                    subprocess.Popen, os.killpg = real_popen, real_killpg
                self.assertIsNone(rc)
                self.assertIn(reason, tail)

    # ---------------------------------------------------------------- 22

    def test_restore_and_ledger_failures_propagate(self):
        root = self._copy(seed=False)
        cand = os.path.join(root, CAND)
        original = _read(cand)
        key = (os.path.realpath(root), SUITE)

        def boom(*a, **k):
            raise RuntimeError("the measurement itself raised")

        for answers in ([(0, ENGAGED_OK), (1, ENGAGED_FAIL)],
                        [(0, ENGAGED_OK), (0, ENGAGED_OK), (0, "OK")],
                        [(0, ENGAGED_OK), (None, "hung")],
                        [(0, NOT_ENGAGED), (0, "OK"), (1, "red")],
                        None):
            with self.subTest(answers=answers):
                P.reset_ledger()
                self._seed(root, duration=42.0)
                if answers is None:
                    calls = []

                    def raising(rel_path, root=None, timeout=None, *, fail_fast=False):
                        calls.append(fail_fast)
                        if len(calls) > 1:
                            boom()
                        return 0, ENGAGED_OK

                    P.run_suite = raising
                    with self.assertRaises(RuntimeError):
                        P.covers(SUITE, CAND, root=root, baseline=0)
                else:
                    P.run_suite = _RunLog(answers)
                    P.covers(SUITE, CAND, root=root, baseline=0)
                self.assertEqual(_read(cand), original)
                self.assertEqual(P._LAST_DURATION, {SUITE: 42.0})
                self.assertEqual(P._C01_CALIBRATED, {key: 42.0})

        # a restore that does not reproduce the digest raises RestoreFailed
        P.reset_ledger()
        self._seed(root)
        P.run_suite = _RunLog([(0, ENGAGED_OK), (5, ENGAGED_FAIL)])
        real_hash = P.sha256_bytes
        count = {"n": 0}

        def drifting(data):
            count["n"] += 1
            return real_hash(data) + ("x" if count["n"] > 1 else "")

        P.sha256_bytes = drifting
        try:
            with self.assertRaises(P.RestoreFailed):
                P.covers(SUITE, CAND, root=root, baseline=0)
        finally:
            P.sha256_bytes = real_hash

        # a later write after a clean restore is refused at the next step
        P.reset_ledger()
        self._seed(root)
        P.run_suite = _RunLog([(0, ENGAGED_OK), (0, ENGAGED_OK), (0, "OK")])
        self.assertIs(P.covers(SUITE, CAND, root=root, baseline=0)[0], False)
        with open(cand, "a", encoding="utf-8") as fh:
            fh.write("# a late writer\n")
        with self.assertRaises(P.RestoreFailed):
            P.covers(SUITE, CAND, root=root, baseline=0)
        with open(cand, "wb") as fh:
            fh.write(original)

        # cleanup (the group kill) happens while the candidate is still
        # perturbed, i.e. before restoration: real runs, observed at the kill
        P.reset_ledger()
        P.run_suite = self.real_run_suite
        self.assertEqual(P.run_suite(SUITE, root=root, timeout=60)[0], 0)
        at_kill = []
        real_killpg = os.killpg

        def observing(pgid, sig):
            at_kill.append(_read(cand))
            return real_killpg(pgid, sig)

        os.killpg = observing
        try:
            self.assertIs(P.covers(SUITE, CAND, root=root, baseline=0)[0], True)
        finally:
            os.killpg = real_killpg
        self.assertEqual(at_kill[0], original)  # the clean probe
        self.assertNotEqual(at_kill[-1], original)  # the perturbed run
        self.assertEqual(_read(cand), original)


if __name__ == "__main__":
    unittest.main()
