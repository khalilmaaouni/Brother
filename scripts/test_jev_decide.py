"""What scripts/jev_decide.py must keep true.

Every test injects a fake runner and, where relevant, a fake gate
decision: no test here ever touches the network or the keychain.
NO_DATA causes are pinned one test per cause, per the JEV-01 worker
contract's test rule, and several tests assert the fake runner is never
called at all, proving the gate and the resolver run BEFORE any process
is launched.

The two classes near the bottom (m2/m3, Opus rereview5, 2026-09-19) are
the one deliberate exception: _default_runner's own process-group kill
and its timeout/unregister contract are properties of a REAL OS process
tree, which nothing short of launching one (a local sleeping script,
never the network) can prove."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_content_gate as gate  # noqa: E402
import jev_decide as J  # noqa: E402


def _pid_alive(pid):
    """True if `pid` is still a live process this test process can see.
    Never raises: a PermissionError (a real, unrelated process now holds
    that pid) is read the same conservative way as "still alive", since
    the property this helper exists to disprove is "the process is
    gone", not "we could double-check with kill -0". Mirrors
    test_jev_checks.py's own _pid_alive() (a sibling test file's small,
    self-contained helper, not worth importing across test modules)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


NOUL_Q = {"correct": {"type": "noul", "instructions": "Is the arithmetic right?"}}
CHOICE_Q = {
    "pick": {"type": "choice", "instructions": "Which is bigger?",
              "criteria": {"x": "seven", "y": "twelve"}}
}
SCORE_Q = {
    "rate": {"type": "score", "instructions": "Rate this from 1 to 10.",
              "criteria": {"scale": "1 to 10"}}
}


class RecordingRunner(object):
    """A fake runner that records whether it was ever called, and returns
    a pinned (returncode, stdout, stderr) triple."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def __call__(self, argv, stdin_text):
        self.calls.append((argv, stdin_text))
        return self.returncode, self.stdout, self.stderr


def allow_gate(*_args, **_kwargs):
    return gate.Verdict(gate.ALLOW, "test: allowed")


def refuse_gate(*_args, **_kwargs):
    return gate.Verdict(gate.REFUSE, "test: refused, category %r" % ("fake",))


class QuestionValidation(unittest.TestCase):
    """Cause 1 of the pinned set: local validation, before the gate and
    before any bridge resolution. The runner must never be called."""

    def setUp(self):
        self.runner = RecordingRunner(returncode=0, stdout="{}")

    def _assert_no_data_no_call(self, questions):
        result = J.decide({}, questions, "test", bridge=["fake"], runner=self.runner)
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertEqual(self.runner.calls, [])
        return result

    def test_empty_questions(self):
        self._assert_no_data_no_call({})

    def test_non_dict_questions(self):
        self._assert_no_data_no_call(["not", "a", "dict"])

    def test_unknown_type(self):
        self._assert_no_data_no_call({"q": {"type": "essay", "instructions": "write one"}})

    def test_missing_instructions(self):
        self._assert_no_data_no_call({"q": {"type": "noul"}})

    def test_blank_instructions(self):
        self._assert_no_data_no_call({"q": {"type": "noul", "instructions": "   "}})

    def test_choice_without_criteria(self):
        self._assert_no_data_no_call({"q": {"type": "choice", "instructions": "pick one"}})

    def test_score_without_criteria(self):
        self._assert_no_data_no_call({"q": {"type": "score", "instructions": "rate it"}})

    def test_noul_needs_no_criteria(self):
        # Sanity: noul questions are not held to the choice/score rule.
        # Push past validation with an allowing gate and a bridge that
        # itself fails, just to prove validation was never the blocker.
        with mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate)):
            result = J.decide({}, NOUL_Q, "test", bridge=["fake"],
                               runner=RecordingRunner(returncode=1, stderr="boom"))
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("exited 1", result[1])


class SerializationFailure(unittest.TestCase):
    def test_unserializable_state_is_no_data_before_gate(self):
        runner = RecordingRunner(returncode=0, stdout="{}")
        gate_calls = []
        with mock.patch.object(J._gate, "decide",
                                side_effect=lambda *a, **k: gate_calls.append(a) or allow_gate()):
            result = J.decide(object(), NOUL_Q, "test", bridge=["fake"], runner=runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("could not be serialized", result[1])
        self.assertEqual(gate_calls, [])
        self.assertEqual(runner.calls, [])


class GateRefusal(unittest.TestCase):
    """Cause 2 of the pinned set: the content gate. The runner must never
    be called on a refusal."""

    def test_gate_refusal_is_no_data_and_runner_never_called(self):
        runner = RecordingRunner(returncode=0, stdout="{}")
        with mock.patch.object(J._gate, "decide", side_effect=refuse_gate):
            result = J.decide({}, NOUL_Q, "test", bridge=["fake"], runner=runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("content gate refused", result[1])
        self.assertEqual(runner.calls, [])

    def test_gate_sees_destination_typesafe(self):
        seen = {}

        def spy(payload, destination, **kwargs):
            seen["destination"] = destination
            return allow_gate()

        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        with mock.patch.object(J._gate, "decide", side_effect=spy):
            J.decide({}, NOUL_Q, "test", bridge=["fake"], runner=runner)
        self.assertEqual(seen["destination"], "typesafe")


class A2HomePathIsMaskedBeforeItLeavesTheMachine(unittest.TestCase):
    """A2 (G1 review; item 5, A0.8 round 6): a home-directory path in
    `state` must never reach the gate or the bridge unmasked. Masked
    ONCE, here, in decide() itself -- the one helper every seam call's
    payload already passes through before the gate -- so every seam gets
    the same protection regardless of whether its own call site
    remembered any redaction of its own."""

    def test_expanduser_home_reaches_the_bridge_as_tilde(self):
        home = os.path.expanduser("~")
        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        with mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate)):
            J.decide({"path": os.path.join(home, "secret.txt")}, NOUL_Q, "test",
                      bridge=["fake"], runner=runner)
        self.assertEqual(len(runner.calls), 1)
        stdin_text = runner.calls[0][1]
        self.assertNotIn(home, stdin_text)
        self.assertIn("~/secret.txt", stdin_text)

    def test_real_passwd_home_is_masked_even_under_an_overridden_home(self):
        # The isolated-HOME test harness shape this estate already uses:
        # os.path.expanduser("~") reports the OVERRIDDEN value, so this
        # proves the REAL machine home (read via pwd, independent of
        # $HOME) is masked too -- A2's own probe: "leaked_real_home=True"
        # for exactly this case before the fix.
        try:
            import pwd
            real_home = pwd.getpwuid(os.getuid()).pw_dir
        except (ImportError, KeyError, OSError, AttributeError):
            self.skipTest("no pwd module / passwd entry / getuid on this platform")
        if not real_home or real_home in ("~", "/"):
            self.skipTest("no usable real home directory to probe")

        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = "/does/not/exist/overridden-home"
        try:
            with mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate)):
                J.decide({"path": os.path.join(real_home, "secret.txt")}, NOUL_Q, "test",
                          bridge=["fake"], runner=runner)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
        self.assertEqual(len(runner.calls), 1)
        self.assertNotIn(real_home, runner.calls[0][1])

    def test_gate_sees_the_masked_payload_not_the_raw_one(self):
        home = os.path.expanduser("~")
        seen = {}

        def spy(payload, destination, **kwargs):
            seen["payload"] = payload
            return allow_gate()

        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        with mock.patch.object(J._gate, "decide", side_effect=spy):
            J.decide({"path": os.path.join(home, "x")}, NOUL_Q, "test",
                      bridge=["fake"], runner=runner)
        self.assertNotIn(home, seen["payload"])

    def test_no_home_path_present_is_unaffected(self):
        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        with mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate)):
            J.decide({"note": "nothing sensitive here"}, NOUL_Q, "test",
                      bridge=["fake"], runner=runner)
        self.assertIn("nothing sensitive here", runner.calls[0][1])


class BridgeResolution(unittest.TestCase):
    """Cause 3 of the pinned set: no bridge configured."""

    def setUp(self):
        self._env = dict(os.environ)
        self._default_path = J.DEFAULT_BRIDGE_PATH
        self._gate_patch = mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate))
        self._gate_patch.start()
        os.environ.pop("BROTHER_DECISION_BRIDGE", None)

    def tearDown(self):
        self._gate_patch.stop()
        os.environ.clear()
        os.environ.update(self._env)
        J.DEFAULT_BRIDGE_PATH = self._default_path

    def test_no_bridge_configured_is_no_data(self):
        # A real call (no injected runner: the module's own
        # _default_runner would do the launching) still requires the
        # default bridge to exist on disk exactly as before the
        # JEV-DECIDE-01 fix: a missing bridge is NO_DATA, never a real
        # subprocess launch aimed at a path that is not there.
        J.DEFAULT_BRIDGE_PATH = "/does/not/exist/or_ask.py"
        with mock.patch("subprocess.run") as run:
            result = J.decide({}, NOUL_Q, "test")
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("no decision bridge configured", result[1])
        run.assert_not_called()

    def test_injected_runner_is_called_with_no_bridge_on_disk(self):
        # JEV-DECIDE-01 fix (independent review): decide() used to
        # resolve the bridge, isfile check and all, BEFORE it ever looked
        # at whether a runner was injected, so every caller that injects
        # a fake runner without also passing `bridge=` (jev_seam.py's two
        # call sites do exactly this) got NO_DATA on any machine missing
        # the real ~/.claude/bin/or_ask.py, the runner never called at
        # all. An injected runner never launches a real process, so the
        # default bridge's presence on this machine must not gate it.
        J.DEFAULT_BRIDGE_PATH = "/does/not/exist/or_ask.py"
        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        result = J.decide({}, NOUL_Q, "test", runner=runner)
        self.assertNotIsInstance(result, tuple)
        self.assertEqual(len(runner.calls), 1)
        argv = runner.calls[0][0]
        self.assertEqual(argv[:2], ["python3", "/does/not/exist/or_ask.py"])

    def test_env_var_is_used_and_split_as_a_command_line(self):
        J.DEFAULT_BRIDGE_PATH = "/does/not/exist/or_ask.py"
        os.environ["BROTHER_DECISION_BRIDGE"] = "python3 '/a path/with space.py'"
        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        J.decide({}, NOUL_Q, "test", runner=runner)
        argv = runner.calls[0][0]
        self.assertEqual(argv[:2], ["python3", "/a path/with space.py"])
        self.assertEqual(argv[2:], ["--decisions", "--model", "typesafe"])

    def test_malformed_env_var_is_no_data(self):
        os.environ["BROTHER_DECISION_BRIDGE"] = "python3 'unclosed"
        runner = RecordingRunner(returncode=0, stdout="{}")
        result = J.decide({}, NOUL_Q, "test", runner=runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertEqual(runner.calls, [])

    def test_bridge_argument_overrides_everything(self):
        os.environ["BROTHER_DECISION_BRIDGE"] = "should-not-be-used"
        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        J.decide({}, NOUL_Q, "test", bridge=["explicit", "cmd"], runner=runner)
        self.assertEqual(runner.calls[0][0][:2], ["explicit", "cmd"])

    def test_default_path_used_when_it_exists(self, ):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py") as tmp:
            J.DEFAULT_BRIDGE_PATH = tmp.name
            runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
            J.decide({}, NOUL_Q, "test", runner=runner)
            self.assertEqual(runner.calls[0][0][:2], ["python3", tmp.name])

    def test_blank_env_var_falls_through_to_default_path(self):
        # Muse review, JEV-01: a blank BROTHER_DECISION_BRIDGE is not a
        # broken configuration, it is the same as unset.
        import tempfile
        os.environ["BROTHER_DECISION_BRIDGE"] = "   "
        with tempfile.NamedTemporaryFile(suffix=".py") as tmp:
            J.DEFAULT_BRIDGE_PATH = tmp.name
            runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
            result = J.decide({}, NOUL_Q, "test", runner=runner)
            self.assertNotIsInstance(result, tuple)
            self.assertEqual(runner.calls[0][0][:2], ["python3", tmp.name])


class RunnerOutcomes(unittest.TestCase):
    """Causes 4-7 of the pinned set: launch failure, nonzero exit,
    unparseable output, missing answer, wrong model."""

    def setUp(self):
        self._gate_patch = mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate))
        self._gate_patch.start()

    def tearDown(self):
        self._gate_patch.stop()

    def _decide(self, runner, questions=NOUL_Q, state=None):
        return J.decide(state, questions, "test", bridge=["fake"], runner=runner)

    def test_launch_failure_is_no_data(self):
        runner = RecordingRunner()
        runner.returncode = None
        runner.stderr = "FileNotFoundError: no such file"
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("could not be invoked", result[1])

    def test_nonzero_exit_is_no_data(self):
        runner = RecordingRunner(returncode=45, stderr="NO-DATA: no key")
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("exited 45", result[1])

    def test_unparseable_stdout_is_no_data(self):
        runner = RecordingRunner(returncode=0, stdout="not json at all {{{")
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("could not be parsed", result[1])

    def test_stdout_not_an_object_is_no_data(self):
        runner = RecordingRunner(returncode=0, stdout="[1, 2, 3]")
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("not a JSON object", result[1])

    def test_missing_answers_object_is_no_data(self):
        runner = RecordingRunner(returncode=0, stdout='{"model": "typesafe/jev-1.13-x"}')
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("no answers object", result[1])

    def test_missing_one_answer_in_batch_fails_whole_batch(self):
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"correct": {"type": "noul", "noul": 0.9}},
        }
        two_questions = dict(NOUL_Q)
        two_questions["other"] = {"type": "noul", "instructions": "another one?"}
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = self._decide(runner, questions=two_questions)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("other", result[1])

    def test_answer_missing_its_own_type_field_is_no_data(self):
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"correct": {"type": "noul", "choice": "wrong shape"}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("no %r value" % "noul", result[1])

    def test_wrong_model_prefix_is_no_data(self):
        import json
        payload = {
            "model": "meta/muse-spark-1.3-contributor",
            "answers": {"correct": {"type": "noul", "noul": 0.9}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("typesafe/jev", result[1])

    def test_missing_model_key_is_no_data(self):
        import json
        payload = {"answers": {"correct": {"type": "noul", "noul": 0.9}}}
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)

    def test_boolean_noul_value_is_no_data_not_a_probability(self):
        # bool is a subclass of int: a stray True must never be read as
        # the number 1.0 (Muse review, JEV-01).
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"correct": {"type": "noul", "noul": True}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("non-numeric noul value", result[1])

    def test_nan_noul_value_is_no_data(self):
        # NaN is a legal Python float but not legal JSON; float("nan")
        # round-trips through json.loads with the non-standard NaN token.
        runner = RecordingRunner(returncode=0, stdout=(
            '{"model": "typesafe/jev-1.13-20260917", '
            '"answers": {"correct": {"type": "noul", "noul": NaN}}}'
        ))
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("non-numeric noul value", result[1])

    def test_string_noul_value_is_no_data(self):
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"correct": {"type": "noul", "noul": "0.99"}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = self._decide(runner)
        self.assertEqual(result[0], J.NO_DATA)

    def test_non_scalar_choice_value_is_no_data(self):
        # A list where a choice answer belongs is not a legal answer to
        # look up in probabilities: fails the batch rather than crashing
        # on an unhashable dict key or silently losing the probability.
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"pick": {"type": "choice", "choice": ["y"],
                                  "probabilities": {"x": 0.2, "y": 0.8}}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = self._decide(runner, questions=CHOICE_Q)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("non-scalar choice value", result[1])


class AnswerMustBeADeclaredOption(unittest.TestCase):
    """C3 (review 70268c3): act mode acted on "unknown" and on an answer
    that is not a declared option at all. Fix at the source in decide():
    a choice answer outside question["criteria"]'s keys, or a score
    answer outside a declared list of levels, fails the whole batch."""

    def setUp(self):
        self._gate_patch = mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate))
        self._gate_patch.start()

    def tearDown(self):
        self._gate_patch.stop()

    def test_choice_answer_unknown_when_not_a_declared_option_is_no_data(self):
        # Reviewer's probe 1: "unknown" is not among CHOICE_Q's declared
        # options {"x", "y"}.
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"pick": {"type": "choice", "choice": "unknown",
                                  "probabilities": {"x": 0.05, "y": 0.05}}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = J.decide({}, CHOICE_Q, "smoke", bridge=["fake"], runner=runner)
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("declared criteria", result[1])

    def test_choice_answer_not_an_option_at_all_is_no_data(self):
        # Reviewer's probe 2: a made-up answer that was never an option.
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"pick": {"type": "choice", "choice": "zzz_not_an_option",
                                  "probabilities": {"x": 0.5, "y": 0.5}}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = J.decide({}, CHOICE_Q, "smoke", bridge=["fake"], runner=runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("declared criteria", result[1])

    def test_choice_answer_that_is_declared_still_succeeds(self):
        result = J.decide({}, CHOICE_Q, "smoke", bridge=["fake"],
                           runner=RecordingRunner(returncode=0, stdout=_success_payload(CHOICE_Q)))
        self.assertNotIsInstance(result, tuple)

    def test_score_answer_outside_declared_levels_is_no_data(self):
        import json
        levels_q = {"rate": {"type": "score", "instructions": "Rate 1-3.",
                              "criteria": ["1", "2", "3"]}}
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"rate": {"type": "score", "score": "9"}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = J.decide({}, levels_q, "smoke", bridge=["fake"], runner=runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("declared levels", result[1])

    def test_score_answer_within_declared_levels_succeeds(self):
        import json
        levels_q = {"rate": {"type": "score", "instructions": "Rate 1-3.",
                              "criteria": ["1", "2", "3"]}}
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"rate": {"type": "score", "score": "2"}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        result = J.decide({}, levels_q, "smoke", bridge=["fake"], runner=runner)
        self.assertNotIsInstance(result, tuple)
        self.assertEqual(result[0]["answer"], "2")

    def test_score_with_non_list_criteria_is_left_unvalidated(self):
        # SCORE_Q's criteria is a description, not an enumerable list of
        # levels (as jev_registry.callable() never produces for score
        # outside its own list shape): no level check applies, same as
        # before this fix.
        result = J.decide({}, SCORE_Q, "smoke", bridge=["fake"],
                           runner=RecordingRunner(returncode=0, stdout=_success_payload(SCORE_Q)))
        self.assertNotIsInstance(result, tuple)
        self.assertEqual(result[0]["answer"], 7.5)


class SuccessRecords(unittest.TestCase):
    def setUp(self):
        self._gate_patch = mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate))
        self._gate_patch.start()

    def tearDown(self):
        self._gate_patch.stop()

    def test_noul_probability_is_the_noul_value(self):
        runner = RecordingRunner(returncode=0, stdout=_success_payload(NOUL_Q))
        records = J.decide({}, NOUL_Q, "smoke", bridge=["fake"], runner=runner)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["id"], "correct")
        self.assertEqual(rec["family"], "smoke")
        self.assertEqual(rec["type"], "noul")
        self.assertEqual(rec["answer"], 0.99)
        self.assertEqual(rec["probability"], 0.99)
        self.assertTrue(rec["model"].startswith("typesafe/jev"))
        self.assertIsInstance(rec["framing_hash"], str)
        self.assertGreaterEqual(rec["latency_seconds"], 0)

    def test_choice_probability_is_looked_up_by_chosen_option(self):
        runner = RecordingRunner(returncode=0, stdout=_success_payload(CHOICE_Q))
        records = J.decide({}, CHOICE_Q, "smoke", bridge=["fake"], runner=runner)
        rec = records[0]
        self.assertEqual(rec["type"], "choice")
        self.assertEqual(rec["answer"], "y")
        self.assertEqual(rec["probability"], 0.8)
        self.assertEqual(rec["confidence"], 1)

    def test_score_has_no_probability(self):
        runner = RecordingRunner(returncode=0, stdout=_success_payload(SCORE_Q))
        records = J.decide({}, SCORE_Q, "smoke", bridge=["fake"], runner=runner)
        rec = records[0]
        self.assertEqual(rec["type"], "score")
        self.assertEqual(rec["answer"], 7.5)
        self.assertIsNone(rec["probability"])

    def test_cost_is_split_evenly_across_the_batch(self):
        import json
        two_questions = dict(NOUL_Q)
        two_questions["other"] = {"type": "noul", "instructions": "another one?"}
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {
                "correct": {"type": "noul", "noul": 0.9},
                "other": {"type": "noul", "noul": 0.1},
            },
            "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.02},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        records = J.decide({}, two_questions, "smoke", bridge=["fake"], runner=runner)
        self.assertEqual(len(records), 2)
        for rec in records:
            self.assertAlmostEqual(rec["cost_share"], 0.01)

    def test_missing_cost_leaves_cost_share_unset(self):
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"correct": {"type": "noul", "noul": 0.9}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        records = J.decide({}, NOUL_Q, "smoke", bridge=["fake"], runner=runner)
        self.assertIsNone(records[0]["cost_share"])

    def test_non_finite_cost_leaves_cost_share_unset_not_nan(self):
        # NaN/Infinity usage.cost must not become a NaN cost_share, which
        # would be an unencodable (invalid) JSON value on the CLI's own
        # output line.
        runner = RecordingRunner(returncode=0, stdout=(
            '{"model": "typesafe/jev-1.13-20260917", '
            '"answers": {"correct": {"type": "noul", "noul": 0.9}}, '
            '"usage": {"cost": Infinity}}'
        ))
        records = J.decide({}, NOUL_Q, "smoke", bridge=["fake"], runner=runner)
        self.assertIsNone(records[0]["cost_share"])

    def test_malformed_probability_entry_leaves_probability_unset(self):
        # A non-numeric entry under the chosen option in "probabilities"
        # is a lesser problem than a missing answer: this field is
        # derived, never depended on for the answer itself, so the batch
        # still succeeds with an unset probability.
        import json
        payload = {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"pick": {"type": "choice", "choice": "y",
                                  "probabilities": {"x": 0.2, "y": "high"}}},
        }
        runner = RecordingRunner(returncode=0, stdout=json.dumps(payload))
        records = J.decide({}, CHOICE_Q, "smoke", bridge=["fake"], runner=runner)
        self.assertEqual(records[0]["answer"], "y")
        self.assertIsNone(records[0]["probability"])

    def test_two_framings_of_the_same_wording_hash_differently(self):
        shared_instructions = "Is the arithmetic right?"
        noul_q = {"q": {"type": "noul", "instructions": shared_instructions}}
        choice_q = {"q": {"type": "choice", "instructions": shared_instructions,
                           "criteria": {"yes": "correct", "no": "incorrect"}}}
        r1 = RecordingRunner(returncode=0, stdout=_success_payload(noul_q))
        r2 = RecordingRunner(returncode=0, stdout=_success_payload(choice_q, choice="yes",
                                                                    probabilities={"yes": 0.9, "no": 0.1}))
        rec1 = J.decide({}, noul_q, "smoke", bridge=["fake"], runner=r1)[0]
        rec2 = J.decide({}, choice_q, "smoke", bridge=["fake"], runner=r2)[0]
        self.assertNotEqual(rec1["framing_hash"], rec2["framing_hash"])


class OptionOrderReachesThePayload(unittest.TestCase):
    """JEV-01 fix (Muse review): json.dumps(..., sort_keys=True) on the
    wire payload silently erased a choice question's option ORDER, which
    is exactly what jev_registry.both_orders()/jev_seam.py's near-
    threshold probe sends two calls to catch (Jev is measured sensitive
    to option order). Both the bytes actually sent to the bridge and the
    framing_hash must track the caller's own criteria order, never a
    sorted one."""

    def setUp(self):
        self._gate_patch = mock.patch.object(J, "_gate", new=mock.Mock(decide=allow_gate))
        self._gate_patch.start()

    def tearDown(self):
        self._gate_patch.stop()

    @staticmethod
    def _both_orders():
        import jev_registry as R
        return R.both_orders(CHOICE_Q["pick"])

    def test_forward_and_reversed_criteria_are_actually_different_orders(self):
        # Sanity on the fixture itself, before trusting it to prove anything.
        forward, reversed_q = self._both_orders()
        self.assertEqual(set(forward["criteria"]), set(reversed_q["criteria"]))
        self.assertNotEqual(list(forward["criteria"]), list(reversed_q["criteria"]))

    def test_reversed_criteria_order_reaches_the_bridge_stdin_unsorted(self):
        import json
        forward, reversed_q = self._both_orders()
        forward_runner = RecordingRunner(returncode=0, stdout=_success_payload({"pick": forward}))
        reversed_runner = RecordingRunner(returncode=0, stdout=_success_payload({"pick": reversed_q}))
        J.decide({}, {"pick": forward}, "smoke", bridge=["fake"], runner=forward_runner)
        J.decide({}, {"pick": reversed_q}, "smoke", bridge=["fake"], runner=reversed_runner)

        forward_stdin = json.loads(forward_runner.calls[0][1])
        reversed_stdin = json.loads(reversed_runner.calls[0][1])
        forward_keys = list(forward_stdin["questions"]["pick"]["criteria"].keys())
        reversed_keys = list(reversed_stdin["questions"]["pick"]["criteria"].keys())
        self.assertEqual(forward_keys, list(forward["criteria"].keys()))
        self.assertEqual(reversed_keys, list(reversed_q["criteria"].keys()))
        self.assertNotEqual(forward_keys, reversed_keys)

    def test_reversed_criteria_order_changes_the_framing_hash(self):
        forward, reversed_q = self._both_orders()
        forward_runner = RecordingRunner(returncode=0, stdout=_success_payload({"pick": forward}))
        reversed_runner = RecordingRunner(returncode=0, stdout=_success_payload({"pick": reversed_q}))
        rec_forward = J.decide({}, {"pick": forward}, "smoke",
                                bridge=["fake"], runner=forward_runner)[0]
        rec_reversed = J.decide({}, {"pick": reversed_q}, "smoke",
                                 bridge=["fake"], runner=reversed_runner)[0]
        self.assertNotEqual(rec_forward["framing_hash"], rec_reversed["framing_hash"])

    def test_same_order_asked_twice_hashes_the_same(self):
        # The determinism half: dropping sort_keys must not turn the hash
        # into something that varies call to call for an unchanged question.
        forward, _reversed_q = self._both_orders()
        r1 = RecordingRunner(returncode=0, stdout=_success_payload({"pick": forward}))
        r2 = RecordingRunner(returncode=0, stdout=_success_payload({"pick": forward}))
        rec1 = J.decide({}, {"pick": forward}, "smoke", bridge=["fake"], runner=r1)[0]
        rec2 = J.decide({}, {"pick": forward}, "smoke", bridge=["fake"], runner=r2)[0]
        self.assertEqual(rec1["framing_hash"], rec2["framing_hash"])


class CommandLine(unittest.TestCase):
    def _run(self, stdin_text, family="smoke", decide_result=None):
        import io
        out, err = io.StringIO(), io.StringIO()
        argv = ["--family", family]
        with mock.patch("sys.stdin", io.StringIO(stdin_text)), \
             mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            if decide_result is not None:
                with mock.patch.object(J, "decide", return_value=decide_result):
                    code = J.main(argv)
            else:
                code = J.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_malformed_stdin_json_is_no_data_exit(self):
        code, out, err = self._run("not json")
        self.assertEqual(code, J.EXIT_NO_DATA)
        self.assertIn("not valid JSON", err)

    def test_stdin_without_questions_is_no_data_exit(self):
        code, out, err = self._run('{"state": {}}')
        self.assertEqual(code, J.EXIT_NO_DATA)

    def test_decide_no_data_prints_reason_and_exits_no_data(self):
        code, out, err = self._run(
            '{"state": {}, "questions": {"q": {"type": "noul", "instructions": "x"}}}',
            decide_result=(J.NO_DATA, "content gate refused: test"),
        )
        self.assertEqual(code, J.EXIT_NO_DATA)
        self.assertIn("content gate refused", err)
        self.assertEqual(out, "")

    def test_decide_success_prints_one_json_line_per_record_and_exits_zero(self):
        records = [
            {"id": "q", "family": "smoke", "type": "noul", "framing_hash": "abc",
             "answer": 0.9, "probability": 0.9, "confidence": None,
             "model": "typesafe/jev-1.13-x", "cost_share": None, "latency_seconds": 0.1},
        ]
        code, out, err = self._run(
            '{"state": {}, "questions": {"q": {"type": "noul", "instructions": "x"}}}',
            decide_result=records,
        )
        self.assertEqual(code, 0)
        lines = [ln for ln in out.strip().split("\n") if ln]
        self.assertEqual(len(lines), 1)
        import json
        self.assertEqual(json.loads(lines[0])["id"], "q")

    def test_non_finite_field_in_a_record_is_no_data_not_invalid_json(self):
        # decide() itself never returns a non-finite numeric field for
        # noul/probability/cost_share, but a "score" answer is not
        # numerically validated: this is the CLI's own last-line defense
        # against ever printing a JSON line float("nan") would produce.
        records = [
            {"id": "q", "family": "smoke", "type": "score", "framing_hash": "abc",
             "answer": float("nan"), "probability": None, "confidence": None,
             "model": "typesafe/jev-1.13-x", "cost_share": None, "latency_seconds": 0.1},
        ]
        code, out, err = self._run(
            '{"state": {}, "questions": {"q": {"type": "score", "instructions": "x", '
            '"criteria": {"scale": "1-10"}}}}',
            decide_result=records,
        )
        self.assertEqual(code, J.EXIT_NO_DATA)
        self.assertEqual(out, "")
        self.assertIn("could not be encoded", err)


class M3RunnerTimeoutKillsAndUnregisters(unittest.TestCase):
    """m3 (Opus rereview5, 2026-09-19): before this test existed, no test
    could go red for either half of _default_runner's timeout path.
    r5_timeout_no_kill (removes the process-group kill on timeout) and
    r5_runner_no_unregister (removes the finally block's
    _unregister_proc call) both survived every suite. Uses a REAL local
    sleeping script as the bridge, launched with a patched
    DEFAULT_TIMEOUT_SECONDS -- never the network, never an injected
    runner (an injected runner never reaches _default_runner at all)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="jev-decide-m3-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_timeout_returns_inside_the_bound_kills_and_unregisters(self):
        pidfile = os.path.join(self._tmp, "bridge.pid")
        bridge = os.path.join(self._tmp, "bridge.py")
        with open(bridge, "w", encoding="utf-8") as fh:
            fh.write(
                "import os, time\n"
                "with open(%r, 'w') as f:\n"
                "    f.write(str(os.getpid()))\n"
                "time.sleep(6)\n"
                % pidfile
            )
        self.assertEqual(J._ACTIVE_PROCS, [], "a leftover process from an earlier test")
        t0 = time.monotonic()
        with mock.patch.object(J, "DEFAULT_TIMEOUT_SECONDS", 1.0):
            rc, out, err = J._default_runner([sys.executable, bridge], "{}")
        elapsed = time.monotonic() - t0
        self.assertIsNone(rc)
        self.assertIn("TimeoutExpired", err)
        # Generous bound: the configured timeout is 1s; killing and
        # reaping a local process should never approach the bridge's own
        # 6s sleep (r5_timeout_no_kill would wait the whole 6s out).
        self.assertLess(elapsed, 4.0,
                         "runner took %.2fs to return -- the timeout kill did not actually stop "
                         "the bridge" % elapsed)
        self.assertEqual(J._ACTIVE_PROCS, [],
                          "the timed-out process must be unregistered from _ACTIVE_PROCS "
                          "(r5_runner_no_unregister)")
        deadline = time.monotonic() + 5.0
        pid = None
        while time.monotonic() < deadline and pid is None:
            if os.path.exists(pidfile):
                with open(pidfile, encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    pid = int(text)
            if pid is None:
                time.sleep(0.05)
        self.assertIsNotNone(pid, "the bridge never even started")
        time.sleep(0.3)  # give the OS a moment to actually reap the killed process
        self.assertFalse(_pid_alive(pid),
                          "the bridge process must be killed on timeout (r5_timeout_no_kill)")


class M2ProcessGroupKillsTheWholeTree(unittest.TestCase):
    """m2 (Opus rereview5, 2026-09-19, regression in e1f4c669c): the
    rewritten _default_runner stopped timing out a bridge whose child
    holds the output pipe, and the exit kill reached only the direct
    child. Every test here launches a REAL wrapper bridge (a shell
    script that starts a python grandchild which sleeps) as a REAL
    subprocess -- no injected runner, no mock: whether a grandchild
    survives is a property of the real OS process tree, which nothing
    short of one can prove. start_new_session=True (the fix) makes the
    wrapper's own pid its process group id too, so os.killpg reaches the
    grandchild along with it."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="jev-decide-m2-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _write_wrapper_bridge(self, sleep_s):
        """A shell script bridge that starts a REAL python grandchild
        (never exec'd into the wrapper's own place) which writes its own
        pid to a file, then sleeps -- the wrapper's own stdout/stderr
        pipes stay open for as long as the grandchild runs, the exact
        shape m2's own probe found: `sh` wrapper bridges wait via
        process.wait() on POSIX, not communicate(), because the
        grandchild keeps the pipe open."""
        pidfile = os.path.join(self._tmp, "grandchild.pid")
        child_py = os.path.join(self._tmp, "grandchild.py")
        with open(child_py, "w", encoding="utf-8") as fh:
            fh.write(
                "import os, time\n"
                "with open(%r, 'w') as f:\n"
                "    f.write(str(os.getpid()))\n"
                "time.sleep(%r)\n"
                % (pidfile, sleep_s)
            )
        wrapper = os.path.join(self._tmp, "wrapper.sh")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n%s %s\n" % (sys.executable, child_py))
        st = os.stat(wrapper)
        os.chmod(wrapper, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return wrapper, pidfile

    def _wait_for_pid(self, pidfile, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(pidfile):
                with open(pidfile, encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    return int(text)
            time.sleep(0.05)
        return None

    def test_timeout_kills_the_grandchild_not_just_the_wrapper(self):
        wrapper, pidfile = self._write_wrapper_bridge(30.0)
        self.assertEqual(J._ACTIVE_PROCS, [], "a leftover process from an earlier test")
        with mock.patch.object(J, "DEFAULT_TIMEOUT_SECONDS", 1.5):
            rc, out, err = J._default_runner([wrapper], "{}")
        self.assertIsNone(rc)
        self.assertIn("TimeoutExpired", err)
        self.assertEqual(J._ACTIVE_PROCS, [])
        pid = self._wait_for_pid(pidfile)
        self.assertIsNotNone(pid, "the grandchild never even started")
        time.sleep(0.3)  # give the OS a moment to actually reap the killed grandchild
        self.assertFalse(_pid_alive(pid),
                          "the grandchild must not survive the runner's own timeout kill "
                          "-- proc.kill() alone only ever reached the wrapper, not this")

    def test_process_exit_kills_the_grandchild_via_kill_active_processes(self):
        # Mirrors what jev_seam._atexit_drain() does with a worker thread
        # abandoned at process exit: the bridge subprocess is still
        # registered and still running, and kill_active_processes() is
        # the only thing left standing between it and becoming an orphan.
        wrapper, pidfile = self._write_wrapper_bridge(30.0)
        proc = subprocess.Popen(
            [wrapper], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        J._register_proc(proc)
        try:
            pid = self._wait_for_pid(pidfile)
            self.assertIsNotNone(pid, "the grandchild never even started")
            J.kill_active_processes()
            time.sleep(0.3)
            self.assertFalse(_pid_alive(pid),
                              "kill_active_processes() must kill the whole process group, not "
                              "just the direct wrapper child")
        finally:
            J._unregister_proc(proc)
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass


def _success_payload(questions, choice=None, probabilities=None):
    """A well-formed bridge stdout JSON string answering every question in
    `questions` with a fixed value per type, for tests that only care
    about the shape of a successful call."""
    import json
    answers = {}
    for qid, question in questions.items():
        qtype = question["type"]
        if qtype == "noul":
            answers[qid] = {"type": "noul", "noul": 0.99, "confidence": 1}
        elif qtype == "choice":
            answers[qid] = {
                "type": "choice",
                "choice": choice if choice is not None else "y",
                "probabilities": probabilities if probabilities is not None else {"x": 0.2, "y": 0.8},
                "confidence": 1,
            }
        else:
            answers[qid] = {"type": "score", "score": 7.5, "confidence": 1}
    return json.dumps({"model": "typesafe/jev-1.13-20260917", "answers": answers})


if __name__ == "__main__":
    unittest.main()
