#!/usr/bin/env python3
"""CUT2.c edges: the cut prepares and checks its generated files before it freezes, once. prepare_release is driven
directly and through the real driver entry (cut_driver.run), whose freeze phase runs the generators bare, then
their --check modes, then freeze_phase. A generator that changed tracked files refuses the freeze naming them: the
candidate is never frozen with a tree other than the proven one. Fixtures and a scripted runner only, through
test_cut_driver's Fixture: nothing here runs a real generator, cut, rehearsal, export, push, tag or gh call.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_driver as D  # noqa: E402
from test_cut_driver import GREEN, V, Fixture, git  # noqa: E402

SHA = "a" * 40


def ops(calls, generate="PASS", validate="PASS", freeze=SHA):
    def step(name, answer):
        def op():
            calls.append(name)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return op
    return {"generate": step("generate", generate), "validate": step("validate", validate),
            "freeze": step("freeze", freeze)}


class PrepareRelease(unittest.TestCase):
    def test_a_validate_that_raises_never_freezes(self):
        calls = []
        got = D.prepare_release(ops(calls, validate=OSError("synthetic")))
        self.assertEqual(calls, ["generate", "validate"])
        self.assertEqual(got["verdict"], "NO-DATA")
        self.assertIn("validate raised OSError", got["reason"])

    def test_a_freeze_naming_no_identity_is_not_a_pass(self):
        for bad in ("", "x", "A" * 40, "a" * 39, "a" * 41, None, 7, ("PASS", SHA)):
            calls = []
            got = D.prepare_release(ops(calls, freeze=bad))
            self.assertEqual(calls, ["generate", "validate", "freeze"], bad)
            self.assertEqual(got["verdict"], "NO-DATA", bad)
            self.assertNotIn("identity", got, bad)

    def test_a_freeze_that_raises_is_no_data(self):
        got = D.prepare_release(ops([], freeze=RuntimeError("disk")))
        self.assertEqual(got["verdict"], "NO-DATA")
        self.assertIn("freeze raised RuntimeError", got["reason"])

    def test_a_failed_generate_stops_before_validate(self):
        for verdict in ("FAIL", "NO-DATA", ("FAIL", "bundle_runtime exit 1")):
            calls = []
            got = D.prepare_release(ops(calls, generate=verdict))
            self.assertEqual(calls, ["generate"], verdict)
            self.assertEqual(got["verdict"], verdict[0] if isinstance(verdict, tuple) else verdict)
        self.assertIn("bundle_runtime exit 1", got["reason"])

    def test_an_answer_that_is_not_a_verdict_never_freezes(self):
        for odd in ("REUSED", "pass", True, None, 0, ("PASS",)):
            calls = []
            got = D.prepare_release(ops(calls, validate=odd))
            self.assertEqual(calls, ["generate", "validate"], odd)
            self.assertEqual(got["verdict"], "NO-DATA", odd)

    def test_a_missing_or_uncallable_operation_runs_nothing(self):
        for table in (None, {}, {"generate": lambda: "PASS", "validate": lambda: "PASS"},
                      {"generate": lambda: "PASS", "validate": "PASS", "freeze": lambda: SHA}, [1, 2, 3]):
            got = D.prepare_release(table)
            self.assertEqual(got["verdict"], "NO-DATA", table)
            self.assertIn("nothing ran", got["reason"])
        calls = []
        table = ops(calls)
        table["validate"] = None
        self.assertEqual(D.prepare_release(table)["verdict"], "NO-DATA")
        self.assertEqual(calls, [])


class TheDriverPreparesBeforeItFreezes(Fixture):
    """Through cut_driver.run, the CD1 entry: the freeze phase is prepared, the generators run first."""

    def setUp(self):
        super().setUp()
        self.mirror = os.path.join(self.root, "bundle", "runtime", "mirror.py")
        os.makedirs(os.path.dirname(self.mirror))
        self.write("stale\n")
        self.commit("the runtime mirror, stale")

    def write(self, text):
        with open(self.mirror, "w") as fh:
            fh.write(text)

    def commit(self, message):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        git(self.root, "push", "-q", "hub", "main")
        self.cand = git(self.root, "rev-parse", "HEAD")
        self.tree = git(self.root, "rev-parse", "HEAD^{tree}")

    def generators(self, check_rc=None, checks=None):
        """Handlers answering both generators, placed before GREEN so a --check needle is matched before its bare
        one. By default they model the real pair: bundle_runtime --check exits 0 only on a fresh mirror and its
        bare run writes the fresh bytes. check_rc fixes every bundle_runtime --check exit; checks is a callable
        answering each --check instead."""
        def check(cmd, cwd):
            if checks:
                return checks(cmd)
            if check_rc is not None:
                return check_rc, "stale: bundle/runtime/mirror.py\n"
            with open(self.mirror) as fh:
                return (0, "") if fh.read() == "fresh\n" else (1, "stale: bundle/runtime/mirror.py\n")

        def bare(cmd, cwd):
            if checks is None and check_rc is None:
                self.write("fresh\n")
            return 0, "wrote\n"
        return [("bundle_runtime.py --check", check), ("system_doc.py --check", (0, "")),
                ("bundle_runtime.py", bare), ("system_doc.py", (0, ""))] + GREEN

    def scripts(self, runner):
        return [" ".join(c[2:]) for c in runner.calls if c[0] != "git"]

    def test_generate_runs_once_before_validate_and_freeze_once_after(self):
        said = []

        def checks(cmd):   # stale on the first look only; the regenerated bytes equal the committed ones
            said.append(cmd)
            return (1, "stale\n") if len(said) == 1 else (0, "")
        code, lines, runner = self.drive(self.generators(checks=checks))
        self.assertTrue(lines[0].startswith("PHASE freeze PASS "), lines)
        self.assertIn("frozen %s" % self.cand[:12], lines[0])
        seen = self.scripts(runner)
        self.assertEqual(seen[:6], ["scripts/bundle_runtime.py --check", "scripts/bundle_runtime.py",
                                    "scripts/system_doc.py --check", "scripts/bundle_runtime.py --check",
                                    "scripts/system_doc.py --check",
                                    "scripts/close_ceremony_check.py --require-current"], seen)
        self.assertEqual(seen.count("scripts/bundle_runtime.py"), 1, seen)
        self.assertEqual(len(runner.saw("close_ceremony_check.py")), 1, seen)

    def test_a_fresh_tree_is_checked_and_never_rewritten(self):
        self.write("fresh\n")
        self.commit("the runtime mirror, fresh")
        code, lines, runner = self.drive(self.generators())
        self.assertTrue(lines[0].startswith("PHASE freeze PASS "), lines)
        seen = self.scripts(runner)
        self.assertEqual([s for s in seen if s in ("scripts/bundle_runtime.py", "scripts/system_doc.py")], [], seen)
        self.assertEqual(seen.count("scripts/bundle_runtime.py --check"), 2, seen)

    def test_a_stale_mirror_is_repaired_refused_then_frozen_once_committed(self):
        code, lines, runner = self.drive(self.generators())
        self.assertEqual(code, 1, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze FAIL "), lines)
        self.assertIn("the generators changed 1 paths (bundle/runtime/mirror.py)", lines[0])
        self.assertEqual(runner.saw("close_ceremony_check.py"), [])
        with open(self.mirror) as fh:
            self.assertEqual(fh.read(), "fresh\n")       # repaired through the generator, not frozen
        self.commit("the runtime mirror, regenerated")
        code, lines, runner = self.drive(self.generators())
        self.assertTrue(lines[0].startswith("PHASE freeze PASS "), lines)
        self.assertIn("frozen %s" % self.cand[:12], lines[0])
        self.assertEqual(len(runner.saw("close_ceremony_check.py")), 1)
        self.assertEqual(self.scripts(runner).count("scripts/bundle_runtime.py"), 0)

    def test_a_failing_check_never_freezes(self):
        code, lines, runner = self.drive(self.generators(check_rc=1))
        self.assertEqual(code, 1, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze FAIL "), lines)
        self.assertIn("scripts/bundle_runtime.py --check exit 1", lines[0])
        self.assertEqual(runner.saw("close_ceremony_check.py"), [])
        self.assertEqual(runner.saw("ls-remote"), [])

    def test_a_check_that_raises_in_validate_is_no_data_and_never_freezes(self):
        said = []

        def checks(cmd):   # generate's look passes; validate's raises
            said.append(cmd)
            if len(said) > 1:
                raise OSError("synthetic disk full")
            return 0, ""
        code, lines, runner = self.drive(self.generators(checks=checks))
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze NO-DATA "), lines)
        self.assertIn("validate raised OSError", lines[0])
        self.assertEqual(runner.saw("close_ceremony_check.py"), [])

    def test_a_missing_generator_is_not_a_pass(self):
        handlers = [h for h in self.generators() if "system_doc" not in h[0]]   # the runner answers 127
        code, lines, runner = self.drive(handlers)
        self.assertNotEqual(code, 0, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze FAIL "), lines)
        self.assertIn("scripts/system_doc.py exit 127", lines[0])
        self.assertEqual(runner.saw("close_ceremony_check.py"), [])

    def test_a_dirty_tree_generates_nothing(self):
        self.write("hand edit\n")
        code, lines, runner = self.drive(self.generators())
        self.assertEqual(code, 1, lines)
        self.assertIn("nothing was generated", lines[0])
        self.assertEqual(self.scripts(runner), [])

    def test_an_identity_that_cannot_be_read_is_not_a_pass(self):
        self.write("fresh\n")
        self.commit("the runtime mirror, fresh")
        armed = []

        def phase(*a, **kw):   # freeze_phase passes; the identity read right after it is garbled
            armed.append(1)
            return "PASS", "clean"

        def head(cmd, cwd):
            if armed and cmd == ["git", "rev-parse", "HEAD"]:
                del armed[:]
                return 0, "not-a-sha\n"
            return D.real_runner(cmd, cwd)
        with mock.patch.object(D, "freeze_phase", side_effect=phase):
            code, lines, _ = self.drive([("git rev-parse", head)] + self.generators())
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze NO-DATA "), lines)
        self.assertIn("freeze named no 40 hex identity", lines[0])

    def test_a_head_that_moves_after_the_freeze_check_is_not_frozen(self):
        self.write("fresh\n")
        self.commit("the runtime mirror, fresh")
        armed = []

        def phase(*a, **kw):   # freeze_phase passes; HEAD has moved by the identity read right after it
            armed.append(1)
            return "PASS", "clean"

        def head(cmd, cwd):
            if armed and cmd == ["git", "rev-parse", "HEAD"]:
                del armed[:]
                return 0, "b" * 40 + "\n"
            return D.real_runner(cmd, cwd)
        with mock.patch.object(D, "freeze_phase", side_effect=phase):
            code, lines, _ = self.drive([("git rev-parse", head)] + self.generators(), candidate=self.cand)
        self.assertEqual(code, 1, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze FAIL "), lines)
        self.assertIn("is not the candidate %s" % self.cand[:12], lines[0])

    def test_a_kept_freeze_is_reused_and_generates_nothing(self):
        D.save_state(D.state_path(V, self.cand), self.state(self.passed("ready", "freeze")))
        code, lines, runner = self.drive(self.generators(), candidate=self.cand)
        self.assertEqual([s for s in self.scripts(runner) if "bundle_runtime" in s or "system_doc" in s], [])


if __name__ == "__main__":
    unittest.main()
