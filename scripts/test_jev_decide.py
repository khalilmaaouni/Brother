"""What scripts/jev_decide.py must keep true.

Every test injects a fake runner and, where relevant, a fake gate
decision: no test here ever touches the network, a real subprocess, or
the keychain. NO_DATA causes are pinned one test per cause, per the JEV-01
worker contract's test rule, and several tests assert the fake runner is
never called at all, proving the gate and the resolver run BEFORE any
process is launched.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_content_gate as gate  # noqa: E402
import jev_decide as J  # noqa: E402


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
        J.DEFAULT_BRIDGE_PATH = "/does/not/exist/or_ask.py"
        runner = RecordingRunner(returncode=0, stdout="{}")
        result = J.decide({}, NOUL_Q, "test", runner=runner)
        self.assertEqual(result[0], J.NO_DATA)
        self.assertIn("no decision bridge configured", result[1])
        self.assertEqual(runner.calls, [])

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
