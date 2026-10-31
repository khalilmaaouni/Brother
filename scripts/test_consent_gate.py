#!/usr/bin/env python3
"""FIRST1: the consent gate and the runner decision, exercised rather than named.

WHY THIS FILE EXISTS, and it is not a second copy of the frozen harness.
scripts/test_first_task.py is the unit's done check and it is frozen by sha256 in the
plan (HARN1 refuses a lane that edits it), which is right. But its Consent class is six
cases and every assertion in it is `has(...)`, a module level symbol lookup through ast.
Three empty functions named consent_question, answer_consent and cmd_consent turn all
six green while nothing works. That is the same defect PROOF1 closed in the verdict
door on 2026-10-29: a check that cannot tell the work from no work.

So the frozen harness stays untouched and this file is the adversary beside it. Every
case here CALLS the thing and judges what it did: the bytes of the question, the keys on
disk after a yes, the absence of a file after a no, the exit code of an answer typed by
an agent, and the command string the generator writes for each shape of test file.

EACH CASE ISOLATES ONE CONDITION. A fixture that trips two guards proves neither, which
is this estate's own measured lesson: on 2026-09-21 every failure fixture in a different
suite returned both a non zero exit and an empty body, so the first guard masked the
second and deleting the second changed nothing.

THE THIRD PROBE IS LOAD BEARING. A consent gate that refused every answer would satisfy
"an agent cannot consent" perfectly, so there is a case for a person who IS accepted. A
guard is only proven by a pair.

AND IT DOES NOT ALLOCATE A PTY. It used to. The pre push gate's hermetic row refused the
push with `OSError: out of pty devices`: this machine carries twelve worktrees and had
none left, so the two load bearing cases ERRORED rather than ran, and a check that cannot
run is not a pass. The person probe is now tested as what it is, a pure comparison, with
fake streams and a patched os.ttyname, and the acceptance path is driven in process with
that probe replaced. Both run anywhere, and together they cover MORE than the pty did:
the two terminals that do not match was a branch no pty case ever reached.

    python3 -B scripts/test_consent_gate.py
    python3 -B scripts/test_consent_gate.py --selftest     (same thing, for check_all.sh)

Python 3.9 compatible, standard library only.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SETUP = os.path.join(ROOT, "products/brothermode/scripts/setup.py")
PROJECT = os.path.join(ROOT, "products/brothermode/tools/bm_project.py")


def _load(path, name):
    """Load a module by path, importing nothing else from the tree."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Sandbox(unittest.TestCase):
    """A throwaway HOME, so no case can read or write the maintainer's own config.

    This is not caution for its own sake: on 2026-10-29 a journey walk in this estate
    inherited the maintainer's BROTHERMODE_VAULT into every "fresh install" and was
    measuring this laptop rather than the product."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="consent-gate-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.config = os.path.join(self.home, ".brotherme", "config.json")
        self.env = dict(os.environ)
        self.env["HOME"] = self.home
        self.env["BROTHERME_CONFIG"] = self.config
        self.setup = _load(SETUP, "setup_under_test")
        self.project = _load(PROJECT, "project_under_test")

    @contextlib.contextmanager
    def streams(self, stdin_tty, stdout_tty, names, ttyname=None):
        """Fake stdin and stdout with a chosen tty answer, and a chosen os.ttyname.

        The person probe is a pure comparison over three facts, so it is tested as one
        rather than by allocating a terminal: a pty is a scarce machine resource and the
        push gate refused on `out of pty devices` when this used one."""
        class _Stream(object):
            def __init__(self, tty, fd):
                self._tty, self._fd = tty, fd

            def isatty(self):
                return self._tty

            def fileno(self):
                return self._fd

        resolve = ttyname if ttyname is not None else (lambda fd: names[fd])
        with mock.patch.object(self.project, "sys") as fake_sys:
            fake_sys.stdin = _Stream(stdin_tty, 0)
            fake_sys.stdout = _Stream(stdout_tty, 1)
            with mock.patch.object(self.project.os, "ttyname", resolve):
                yield

    def consent_in_process(self, args, person):
        """(stdout, exit code) from cmd_consent run in this process with the person probe
        replaced. Driving the ENTRY POINT, not a helper: the route is where the refusal
        and the arming actually live."""
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": self.home,
                                          "BROTHERME_CONFIG": self.config}):
            with mock.patch.object(self.project, "_a_person_is_here", lambda: person):
                with contextlib.redirect_stdout(buf):
                    code = self.project.cmd_consent(list(args))
        return buf.getvalue(), code

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.tmp], check=False)

    def written(self):
        """The config on disk, or None when nothing was written."""
        try:
            with io.open(self.config, encoding="utf-8") as fh:
                return json.load(fh)
        except (IOError, OSError, ValueError):
            return None

    def run_consent(self, args, stdin=None, stdout=None):
        kw = {"env": self.env, "cwd": ROOT, "capture_output": stdin is None}
        if stdin is not None:
            kw = {"env": self.env, "cwd": ROOT, "stdin": stdin, "stdout": stdout,
                  "stderr": subprocess.PIPE}
        return subprocess.run([sys.executable, "-B", PROJECT, "consent"] + list(args),
                              text=True, **kw)


class TheQuestion(_Sandbox):
    """REQ-FIRST1C-01 and 02: it fits, it says what it is asking, and it is fast."""

    def test_the_question_fits_600_bytes(self):
        n = len(self.setup.consent_question().encode("utf-8"))
        self.assertLessEqual(n, 600, "the question is %d bytes, over the 600 D-2 allows" % n)

    def test_the_question_says_what_gets_written_and_how_to_stop_it(self):
        """A question that fits but says nothing is not consent. An empty string passes a
        length bar perfectly, so the bar alone decides nothing."""
        q = self.setup.consent_question()
        for named in ("config.json", "lessons", "receipts", "BM_FENCE_MODE"):
            self.assertIn(named, q, "the question never names %s, so it does not say what "
                                    "is being consented to" % named)

    def test_the_question_has_no_dash(self):
        # Written as escapes on purpose: a literal dash here would make this file
        # itself a hit for every dash scan that runs over the tree.
        for dash in ("\u2014", "\u2013"):
            self.assertNotIn(dash, self.setup.consent_question())

    def test_asking_is_under_20_seconds(self):
        """REQ-FIRST1C-02, timed here rather than asserted in prose."""
        start = time.time()
        r = self.run_consent([])
        spent = time.time() - start
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(spent, 20.0, "asking took %.1f s, over the 20 s D-2 allows" % spent)

    def test_asking_prints_the_question_and_writes_nothing(self):
        r = self.run_consent([])
        self.assertIn("one question", r.stdout)
        self.assertIsNone(self.written(), "asking the question wrote a config")


class TheYesAndTheNo(_Sandbox):
    """REQ-FIRST1C-03: only a yes arms, and a no leaves the disk alone."""

    def test_a_yes_arms_enforced_mode_and_binds_lessons(self):
        cfg = self.setup.answer_consent(True, path=self.config)
        self.assertEqual(cfg.get("fence_mode"), "enforced")
        self.assertTrue(cfg.get("lessons_root", "").endswith(os.path.join(".brother", "lessons")),
                        "lessons_root is %r" % cfg.get("lessons_root"))
        self.assertEqual(self.written(), cfg, "the returned dict is not what reached disk")

    def test_a_no_writes_nothing_at_all(self):
        self.assertEqual(self.setup.answer_consent(False, path=self.config), {})
        self.assertIsNone(self.written(), "a no wrote a config")

    def test_a_truthy_value_that_is_not_true_arms_nothing(self):
        """Fail closed, the same rule is_consented keeps: a stray 1 or a non empty string
        is not a person saying yes, and must not arm the fence."""
        for value in (1, "yes", "no", [1], object()):
            self.assertEqual(self.setup.answer_consent(value, path=self.config), {},
                             "%r armed the fence" % (value,))
            self.assertIsNone(self.written(), "%r wrote a config" % (value,))

    def test_a_yes_keeps_a_vault_the_person_already_chose(self):
        self.setup.write_config("/already/chosen", "clone", path=self.config)
        cfg = self.setup.answer_consent(True, path=self.config)
        self.assertEqual(cfg.get("vault_path"), "/already/chosen",
                         "answering the question relocated an existing vault")

    def test_the_five_key_config_is_unchanged_for_every_other_caller(self):
        """The consent record keys are omitted, not written empty, when no consent is
        being recorded: every existing caller and fixture must see the config it knows."""
        cfg = self.setup.write_config("/v", "clone", path=self.config)
        self.assertEqual(sorted(cfg), ["installation_mode", "privacy_notice_version",
                                       "security_mode", "setup_complete", "vault_path"])


class OnlyAPersonAnswers(_Sandbox):
    """REQ-FIRST1C-05, in both directions. One direction alone proves nothing."""

    def test_an_agent_typing_yes_with_no_terminal_is_refused(self):
        r = self.run_consent(["yes"])
        self.assertNotEqual(r.returncode, 0, "an argv yes with no terminal was accepted")
        self.assertIsNone(self.written(), "the refused yes still wrote a config")

    def test_the_refusal_says_how_a_person_answers(self):
        r = self.run_consent(["yes"])
        self.assertIn("own terminal", r.stderr)
        self.assertIn("consent", r.stderr)

    def test_the_person_probe_accepts_one_terminal_on_both_ends(self):
        """THE THIRD PROBE, as a pure comparison. No pty: see the module docstring."""
        with self.streams(True, True, {0: "/dev/ttys009", 1: "/dev/ttys009"}):
            self.assertTrue(self.project._a_person_is_here())

    def test_the_person_probe_refuses_two_different_terminals(self):
        """A branch the old pty case could never reach: both ends are terminals and they
        are not the SAME terminal, which is a redirected session, not a person."""
        with self.streams(True, True, {0: "/dev/ttys009", 1: "/dev/ttys010"}):
            self.assertFalse(self.project._a_person_is_here())

    def test_the_person_probe_refuses_a_pipe_on_either_end(self):
        for stdin_tty, stdout_tty in ((False, True), (True, False), (False, False)):
            with self.streams(stdin_tty, stdout_tty, {0: "/dev/ttys009", 1: "/dev/ttys009"}):
                self.assertFalse(self.project._a_person_is_here(),
                                 "stdin tty %s, stdout tty %s was read as a person"
                                 % (stdin_tty, stdout_tty))

    def test_the_person_probe_fails_closed_when_it_cannot_tell(self):
        """An environment this cannot read must never be mistaken for a person."""
        def boom(_fd):
            raise OSError("no tty")
        with self.streams(True, True, {}, ttyname=boom):
            self.assertFalse(self.project._a_person_is_here())

    def test_a_person_who_is_here_is_accepted(self):
        """THE OTHER HALF OF THE PAIR, driven through the entry point with the person
        probe replaced: without this a gate that refused every answer would pass the
        case above and the unit would ship a question nobody can answer."""
        out, code = self.consent_in_process(["yes"], person=True)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.written() or {}).get("fence_mode"), "enforced",
                         "a person's yes armed nothing: %s" % out)

    def test_the_arming_says_it_is_not_tamper_proof(self):
        """A control that only reports must say which one it is. This case exists so the
        admission cannot be removed without something going red."""
        out, code = self.consent_in_process(["yes"], person=True)
        self.assertEqual(code, 0, out)
        self.assertIn("not yet tamper proof", out)
        self.assertIn("HD1.d", out)

    def test_an_unknown_answer_is_refused_with_the_usage_line(self):
        r = self.run_consent(["maybe"])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("yes|no", r.stderr)
        self.assertIsNone(self.written())


class TheAnswerFile(_Sandbox):
    """The route for a person with no terminal: the file the session cannot write."""

    def answer(self, text):
        path = os.path.join(self.home, ".brother", "consent", "answer")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_a_filed_yes_arms(self):
        self.answer("yes\n")
        r = self.run_consent([])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.written() or {}).get("fence_mode"), "enforced")

    def test_a_filed_no_writes_nothing_and_asks_nothing(self):
        self.answer("no\n")
        r = self.run_consent([])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("one question", r.stdout, "it asked again after a no")
        self.assertIsNone(self.written())

    def test_a_filed_rubbish_answer_is_refused(self):
        self.answer("probably\n")
        r = self.run_consent([])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIsNone(self.written())


class OneQuestionOnly(_Sandbox):
    """REQ-FIRST1C-04: asked again after an answer, it reports and asks nothing."""

    def test_a_second_ask_after_a_yes_asks_nothing(self):
        self.setup.answer_consent(True, path=self.config)
        r = self.run_consent([])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("one question", r.stdout, "the question was asked a second time")
        self.assertIn("enforced", r.stdout)

    def test_a_corrupt_config_reads_as_not_armed(self):
        """Fail closed: an unreadable config must not be read as consent."""
        os.makedirs(os.path.dirname(self.config), exist_ok=True)
        with io.open(self.config, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIsNone(self.setup.consented_fence_mode(path=self.config))


class _Lead(object):
    """The two module level helpers bm_project's generator asks its lead for. A stub, so
    a case here measures the generator and not bm_lead's own tree walk."""

    @staticmethod
    def _skip_tree_dirs():
        return frozenset((".git", "__pycache__"))

    @staticmethod
    def _test_suites(root):
        return []


class TheRunnerDecides(_Sandbox):
    """FIRST1.d: the generated check names the runner that actually runs the file."""

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_a_pytest_style_file_gets_a_runner_that_collects(self):
        p = self.write("test_p.py", "def test_one():\n    assert 1 == 1\n")
        self.assertEqual(self.project._test_file_runner(p), "python3 -m pytest -q %s")

    def test_a_self_running_file_keeps_the_plain_interpreter(self):
        p = self.write("test_s.py", "import unittest\n\n\nclass TestS(unittest.TestCase):\n"
                                    "    def test_one(self):\n        pass\n\n\n"
                                    "if __name__ == '__main__':\n    unittest.main()\n")
        self.assertEqual(self.project._test_file_runner(p), "python3 %s")

    def test_a_case_class_with_no_main_block_gets_a_runner_that_collects(self):
        p = self.write("test_c.py", "import unittest\n\n\nclass TestC(unittest.TestCase):\n"
                                    "    def test_one(self):\n        pass\n")
        self.assertEqual(self.project._test_file_runner(p), "python3 -m pytest -q %s")

    def test_a_file_that_defines_no_test_names_no_runner(self):
        """The measured defect: `print('ok')` in a file called test_x.py used to become
        `python3 test_x.py`, which exits 0 whatever the code does."""
        p = self.write("test_n.py", "print('ok')\n")
        self.assertIsNone(self.project._test_file_runner(p))

    def test_a_file_that_does_not_parse_names_no_runner(self):
        p = self.write("test_b.py", "def f(:\n")
        self.assertIsNone(self.project._test_file_runner(p))

    def test_an_unreadable_file_names_no_runner(self):
        self.assertIsNone(self.project._test_file_runner(
            os.path.join(self.tmp, "absent", "test_m.py")))

    def test_the_generator_writes_no_check_for_a_file_that_defines_no_test(self):
        """THE ENTRY POINT, not the helper. The runner decision only matters where the
        generator USES it: this estate measured eleven mutations surviving a suite that
        covered four pure helpers and never the function the work happens in."""
        repo = os.path.join(self.tmp, "repo1")
        os.makedirs(repo)
        self.write(os.path.join("repo1", "test_n.py"), "print('ok')\n")
        self.assertEqual(self.project._repo_success_checks(_Lead, repo), [],
                         "the generator wrote a check for a file that defines no test")

    def test_the_generator_names_the_collecting_runner_for_a_pytest_file(self):
        repo = os.path.join(self.tmp, "repo2")
        os.makedirs(repo)
        self.write(os.path.join("repo2", "test_p.py"), "def test_one():\n    assert 1 == 1\n")
        checks = self.project._repo_success_checks(_Lead, repo)
        self.assertEqual([c["command"] for c in checks], ["python3 -m pytest -q test_p.py"])

    def test_the_generator_keeps_the_plain_interpreter_for_a_self_running_file(self):
        repo = os.path.join(self.tmp, "repo3")
        os.makedirs(repo)
        self.write(os.path.join("repo3", "test_s.py"),
                   "import unittest\n\n\nclass TestS(unittest.TestCase):\n"
                   "    def test_one(self):\n        pass\n\n\n"
                   "if __name__ == '__main__':\n    unittest.main()\n")
        checks = self.project._repo_success_checks(_Lead, repo)
        self.assertEqual([c["command"] for c in checks], ["python3 test_s.py"])

    def test_the_checker_is_found_at_the_installed_runtime_path(self):
        """REQ-FIRST1D-02, behaviourally. The frozen harness asserts the word "runtime"
        appears near _contract_checker, which a docstring satisfies: the mutation that
        drops CONTRACT_CHECK_INSTALLED from the candidates SURVIVED that assertion
        (measured 2026-10-29). This one loads from the installed path or fails.

        Both constants are pointed at throwaway files, so the case measures which
        candidates the function consults and not what is installed on this laptop."""
        installed = os.path.join(self.tmp, "runtime", "contract_check.py")
        os.makedirs(os.path.dirname(installed))
        with io.open(installed, "w", encoding="utf-8") as fh:
            fh.write("MARKER = 'installed'\n")
        self.project.CONTRACT_CHECK_CHECKOUT = os.path.join(self.tmp, "absent.py")
        self.project.CONTRACT_CHECK_INSTALLED = installed
        mod = self.project._contract_checker()
        self.assertIsNotNone(mod, "the checker present at the installed runtime path was "
                                  "not found, so an installed plugin reads NO-DATA")
        self.assertEqual(getattr(mod, "MARKER", None), "installed")

    def test_the_checkout_checker_still_wins_when_both_are_present(self):
        """The pair. A function that only ever looked at the installed path would pass the
        case above, and would stop finding the checkout's checker in a clone."""
        for which in ("checkout", "runtime"):
            path = os.path.join(self.tmp, which, "contract_check.py")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write("MARKER = %r\n" % which)
        self.project.CONTRACT_CHECK_CHECKOUT = os.path.join(self.tmp, "checkout",
                                                            "contract_check.py")
        self.project.CONTRACT_CHECK_INSTALLED = os.path.join(self.tmp, "runtime",
                                                             "contract_check.py")
        mod = self.project._contract_checker()
        self.assertEqual(getattr(mod, "MARKER", None), "checkout")

    def test_no_checker_in_either_place_is_no_data(self):
        self.project.CONTRACT_CHECK_CHECKOUT = os.path.join(self.tmp, "a.py")
        self.project.CONTRACT_CHECK_INSTALLED = os.path.join(self.tmp, "b.py")
        self.assertIsNone(self.project._contract_checker())

    def test_the_collecting_command_resolves_back_to_its_file(self):
        """The reader and the writer have to agree, or the receipt points at nothing."""
        self.assertEqual(
            self.project._check_evidence_path(self.tmp, "python3 -m pytest -q a/test_p.py"),
            "a/test_p.py")

    def test_the_plain_command_still_resolves_back_to_its_file(self):
        self.assertEqual(
            self.project._check_evidence_path(self.tmp, "python3 a/test_s.py"),
            "a/test_s.py")


def main(argv):
    argv = [a for a in argv if a != "--selftest"]
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
