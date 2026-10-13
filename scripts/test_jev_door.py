"""JW4.a: what scripts/jev_door.py must keep true.

The cases drive the REAL seam (jev_seam.consult through
jev_checks.check_gate_failure_flake and check_done_claim_verification) with
an injected runner that returns a bridge shaped answer, in a throwaway
BROTHER_JEV_STATE_DIR and HOME set before any product import, with the
private content gate's term list replaced by a fixture. No case calls a
model: the default bridge path resolves under the throwaway HOME, where no
bridge exists, and the default runner is replaced by a recorder wherever a
case leaves the runner unset.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="jev-door-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")
for _var in ("BROTHER_TRANSPORTS", "BROTHER_DECISION_BRIDGE", "BROTHER_JEV_SEAMS_OFF"):
    os.environ.pop(_var, None)
os.makedirs(os.path.join(_HOME, ".claude"))
_TERMS_PATH = os.path.join(_HOME, ".claude", "coe-outside-gate-terms.json")
with open(_TERMS_PATH, "w", encoding="utf-8") as _fh:
    json.dump({"vendor-fixture": ["ACMEWIDGET"]}, _fh)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import coe_outside_gate  # noqa: E402
import jev_checks  # noqa: E402
import jev_decide  # noqa: E402
import jev_door  # noqa: E402
import jev_outcome  # noqa: E402
import jev_registry  # noqa: E402
import jev_seam  # noqa: E402

NOT_ACTED = "(recorded, not acted on)"
TERMS = {"vendor-fixture": ["ACMEWIDGET"]}
SHADOW = {"modes": {"J065": "shadow", "J102": "shadow"}}
LOG = "FAILED test_upload (tests.test_api)\nConnectionResetError: peer reset\n"


def _registry():
    for path in (os.path.join(ROOT, "data", "jev-registry.json"),
                 os.path.join(ROOT, "bundle", "runtime", "data", "jev-registry.json"),
                 os.path.join(HERE, "fixtures", "jev-registry-fixture.json")):
        if os.path.isfile(path):
            return jev_registry.load(path)
    raise AssertionError("no Jev registry copy in this tree")


REGISTRY = _registry()


class Runner(object):
    """A bridge shaped answer for whatever questions it is handed; records each call."""

    def __init__(self, prob, returncode=0, gate=None):
        self.prob = prob
        self.returncode = returncode
        self.gate = gate
        self.calls = []

    def __call__(self, argv, stdin_text):
        self.calls.append(stdin_text)
        if self.gate is not None:
            self.gate.wait(10)
        if self.returncode:
            return self.returncode, "", "fixture bridge failure"
        payload = json.loads(stdin_text)
        answers = {qid: {"noul": self.prob, "confidence": self.prob}
                   for qid in payload["questions"]}
        return 0, json.dumps({"model": jev_decide.MODEL_PREFIX + "-fixture",
                              "answers": answers, "usage": {"cost": 0.0}}), ""


class Recorder(object):
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("must not be called")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jev-door-")
        self.ledger = os.path.join(self.root, "ledger")
        patcher = mock.patch.object(jev_decide, "_default_runner", Recorder())
        self.default_runner = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        jev_seam.drain(timeout=10)
        shutil.rmtree(self.root, ignore_errors=True)

    def review(self, text=LOG, runner=None, **kw):
        kw.setdefault("seams_config", SHADOW)
        kw.setdefault("registry", REGISTRY)
        kw.setdefault("gate_terms", TERMS)
        kw.setdefault("wait_s", 10.0)
        return jev_door.review_second_opinion(text, ledger_dir=self.ledger, runner=runner, **kw)

    def deliver(self, note, quotes, runner=None, **kw):
        kw.setdefault("seams_config", SHADOW)
        kw.setdefault("registry", REGISTRY)
        kw.setdefault("gate_terms", TERMS)
        kw.setdefault("wait_s", 10.0)
        return jev_door.deliver_second_opinion(note, quotes, ledger_dir=self.ledger,
                                               runner=runner, **kw)

    def assertOneLine(self, line):
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith("second opinion: "), line)
        self.assertTrue(line.endswith(NOT_ACTED), line)

    def terminal_count(self):
        path = jev_outcome.attempts_path(self.ledger)
        if not os.path.exists(path):
            return 0
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for raw in fh if json.loads(raw).get("phase") == "answered")


class ReviewLines(Base):
    """REQ-JW4-01, REQ-JW4-12."""

    def test_flake(self):
        state, line = self.review(runner=Runner(0.91))
        self.assertEqual(state, "OK")
        self.assertEqual(line, "second opinion: this failure reads as an infra flake (p=0.91); "
                               "the check's own exit code still decides " + NOT_ACTED)

    def test_real(self):
        state, line = self.review(runner=Runner(0.07))
        self.assertEqual(state, "OK")
        self.assertEqual(line, "second opinion: this failure reads as a real failure (p=0.07); "
                               "the check's own exit code still decides " + NOT_ACTED)

    def test_the_question_asked_is_j065(self):
        runner = Runner(0.91)
        self.review(runner=runner)
        self.assertEqual(len(runner.calls), 1)
        qids = list(json.loads(runner.calls[0])["questions"])
        self.assertTrue(qids and all(q.startswith("J065:") for q in qids), qids)

    def test_ask_flake_is_what_the_line_is_built_from(self):
        out = jev_door.ask_flake(LOG, ledger_dir=self.ledger, seams_config=SHADOW,
                                 registry=REGISTRY, gate_terms=TERMS, wait_s=10.0,
                                 runner=Runner(0.91))
        self.assertEqual(out, ("OK", True, 0.91, ""))
        state, line = self.review(runner=Runner(0.91))
        self.assertEqual(state, out[0])
        self.assertIn("infra flake (p=0.91)", line)

    def test_one_line_of_output(self):
        state, line = self.review("one line only", runner=Runner(0.07))
        self.assertEqual(state, "OK")
        self.assertOneLine(line)

    def test_running_twice_asks_twice(self):
        self.review(runner=Runner(0.91))
        self.review(runner=Runner(0.91))
        self.assertEqual(self.terminal_count(), 2)


class DeliverLines(Base):
    """REQ-JW4-01; M-JW4-VERDICT: a disagreement is still state OK."""

    def test_four_sentences(self):
        cases = [
            (0.91, False, "agrees: no verifying command is quoted (p=0.91)"),
            (0.07, True, "agrees: a verification is quoted (p=0.07)"),
            (0.91, True, "disagrees: it finds no verifying command quoted, the done check read one (p=0.91)"),
            (0.07, False, "disagrees: it reads a verification as quoted, the done check found none (p=0.07)"),
        ]
        for prob, quotes, words in cases:
            state, line = self.deliver("Done. All tests pass.", quotes, runner=Runner(prob))
            self.assertEqual(state, "OK", (prob, quotes, line))
            self.assertEqual(line, "second opinion: %s %s" % (words, NOT_ACTED))

    def test_quotes_must_be_a_bool(self):
        for bad in ("yes", None, 1):
            runner = Runner(0.91)
            with mock.patch.object(jev_checks, "check_done_claim_verification", Recorder()) as rec:
                state, line = self.deliver("Done.", bad, runner=runner)
            self.assertEqual(state, "NO-DATA", bad)
            self.assertOneLine(line)
            self.assertEqual(rec.calls, 0)
            self.assertEqual(runner.calls, [])


class NeverSent(Base):
    """REQ-JW4-02, REQ-JW4-06."""

    def test_a_private_term_is_never_asked(self):
        runner = Runner(0.91)
        with mock.patch.object(jev_checks, "check_gate_failure_flake", Recorder()) as rec:
            state, line = self.review("error in ACMEWIDGET client\n", runner=runner)
        self.assertEqual(state, "NO-DATA")
        self.assertTrue(line.startswith("second opinion: NO-DATA (not asked: "), line)
        self.assertOneLine(line)
        self.assertEqual(rec.calls, 0)
        self.assertEqual(runner.calls, [])
        with mock.patch.object(jev_checks, "check_done_claim_verification", Recorder()) as rec:
            state, _ = self.deliver("Done for ACMEWIDGET.", True, runner=runner)
        self.assertEqual(state, "NO-DATA")
        self.assertEqual(rec.calls, 0)

    def test_a_missing_term_list_refuses(self):
        runner = Runner(0.91)
        with mock.patch.object(coe_outside_gate, "DEFAULT_TERMS_PATH",
                               os.path.join(self.root, "absent.json")):
            state, line = self.review(runner=runner, gate_terms=None)
        self.assertEqual(state, "NO-DATA")
        self.assertIn("not asked: ", line)
        self.assertEqual(runner.calls, [])

    def test_an_oversized_text_refuses(self):
        runner = Runner(0.91)
        big = ("x" * 399 + "\n") * 200
        with mock.patch.object(coe_outside_gate, "MAX_CONTENT_BYTES", 1000):
            state, line = self.review(big, runner=runner)
        self.assertEqual(state, "NO-DATA")
        self.assertIn("not asked: ", line)
        self.assertEqual(runner.calls, [])

    def test_the_extract_names_files_by_basename(self):
        runner = Runner(0.91)
        text = ("Traceback in /opt/builds/deepdir/run_tests.py:12\n"
                "see %s/work/notes.txt\n" % _HOME)
        self.review(text, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        sent = runner.calls[0]
        self.assertIn("run_tests.py:12", sent)
        self.assertNotIn("/opt/builds", sent)
        self.assertNotIn("deepdir", sent)
        self.assertNotIn(_HOME, sent)

    def test_reduce_text(self):
        reduced = jev_door.reduce_text("a /usr/local/lib/x.py b\n%s/secret/y.txt\n" % _HOME)
        self.assertEqual(reduced, "a x.py b\ny.txt")
        self.assertNotIn(_HOME, reduced)
        many = "\n".join("line %d" % i for i in range(5000))
        self.assertEqual(len(jev_door.reduce_text(many).splitlines()), jev_door.MAX_LINES)
        self.assertEqual(jev_door.MAX_LINES, 200)
        self.assertEqual(len(jev_door.reduce_text("z" * 1000)), 400)


class NoDataBranches(Base):
    """REQ-JW4-03."""

    def test_nothing_to_ask(self):
        for text in ("", "   \n\t", "bin\x00ary", None):
            runner = Runner(0.91)
            state, line = self.review(text, runner=runner)
            self.assertEqual(state, "NO-DATA", repr(text))
            self.assertOneLine(line)
            self.assertEqual(runner.calls, [])

    def test_off_mode(self):
        runner = Runner(0.91)
        state, line = self.review(runner=runner, seams_config={"modes": {}})
        self.assertEqual(state, "NO-DATA")
        self.assertIn("J065", line)
        self.assertOneLine(line)
        self.assertEqual(runner.calls, [])

    def test_the_shipped_config_keeps_j065_off(self):
        with open(os.path.join(ROOT, "data", "jev-seams.json"), "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["modes"].get("J065"), "off")
        runner = Runner(0.91)
        state, line = self.review(runner=runner, seams_config=None)
        self.assertEqual(state, "NO-DATA")
        self.assertIn("J065", line)
        self.assertEqual(runner.calls, [])

    def test_an_absent_registry(self):
        runner = Runner(0.91)
        with mock.patch.object(jev_seam, "DEFAULT_REGISTRY_PATH",
                               os.path.join(self.root, "no-registry.json")):
            state, line = self.review(runner=runner, registry=None)
        self.assertEqual(state, "NO-DATA")
        self.assertIn("Jev is not installed here", line)
        self.assertOneLine(line)
        self.assertEqual(runner.calls, [])

    def test_a_call_the_seam_did_not_dispatch(self):
        marker = os.path.join(self.root, "canary-reset.json")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("{}")
        runner = Runner(0.91)
        seams = dict(SHADOW, canary_reset_marker_path=marker)
        state, line = self.review(runner=runner, seams_config=seams)
        self.assertEqual(state, "NO-DATA")
        self.assertIn("not asked: canary reset marker present", line)
        self.assertOneLine(line)
        self.assertEqual(runner.calls, [])

    def test_no_answer_in_time_then_it_lands(self):
        for wait in (0.0, -1.0, float("nan")):
            gate = threading.Event()
            runner = Runner(0.91, gate=gate)
            before = self.terminal_count()
            try:
                state, line = self.review(runner=runner, wait_s=wait)
            finally:
                gate.set()
            self.assertEqual(state, "NO-DATA", wait)
            self.assertIn("no answer within 0 s; it is recorded if it lands", line)
            self.assertOneLine(line)
            jev_seam.drain(timeout=10)
            self.assertEqual(self.terminal_count(), before + 1)

    def test_unsure_answer(self):
        state, line = self.review(runner=Runner(0.5))
        self.assertEqual(state, "OK")
        self.assertIn("no opinion", line)
        self.assertOneLine(line)

    def test_bridge_failure_is_no_opinion(self):
        state, line = self.review(runner=Runner(0.91, returncode=1))
        self.assertEqual(state, "OK")
        self.assertIn("no opinion: Jev was unsure or could not answer (no_data)", line)
        self.assertOneLine(line)


class Noul(unittest.TestCase):
    """REQ-JW4-05, REQ-JW4-07."""

    def test_both_door_questions_are_noul(self):
        for entry_id in ("J065", "J102"):
            entry = jev_registry.get(REGISTRY, entry_id)
            self.assertEqual(entry["question"]["type"], "noul", entry_id)

    def test_the_new_check_names_j065(self):
        import test_jev_seam_wiring as W
        ids = W.check_function_ids(os.path.join(HERE, "jev_checks.py"))
        self.assertEqual(ids.get("check_gate_failure_flake"), "J065")


class Main(Base):
    """REQ-JW4-04: exit 0 for every outcome; a bad argument exits 2."""

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = jev_door.main(argv)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertOneLine(lines[0])
        return code, lines[0]

    def write(self, name, data):
        path = os.path.join(self.root, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_unreadable_file(self):
        code, line = self.run_main(["review", "--output", os.path.join(self.root, "nope.log")])
        self.assertEqual(code, 0)
        self.assertIn("NO-DATA", line)
        code, _ = self.run_main(["deliver", "--note", os.path.join(self.root, "nope.md"),
                                 "--quotes", "yes"])
        self.assertEqual(code, 0)

    def test_shipped_config_off(self):
        path = self.write("out.log", LOG.encode("utf-8"))
        code, line = self.run_main(["review", "--output", path])
        self.assertEqual(code, 0)
        self.assertIn("NO-DATA", line)

    def test_nul_byte_and_undecodable_bytes(self):
        path = self.write("bin.log", b"\xff\xfe\x00junk")
        code, line = self.run_main(["review", "--output", path])
        self.assertEqual(code, 0)
        self.assertIn("NO-DATA", line)

    def test_shadow_without_a_bridge_still_exits_zero(self):
        seams = self.write("seams.json", json.dumps(SHADOW).encode("utf-8"))
        path = self.write("out.log", LOG.encode("utf-8"))
        note = self.write("note.md", b"Done. Ran the suite: OK")
        with mock.patch.object(jev_seam, "DEFAULT_SEAMS_CONFIG_PATH", seams):
            code, _ = self.run_main(["review", "--output", path, "--wait", "10",
                                     "--max-lines", "50"])
            self.assertEqual(code, 0)
            code, _ = self.run_main(["deliver", "--note", note, "--quotes", "no", "--wait", "10"])
            self.assertEqual(code, 0)
        self.assertEqual(self.default_runner.calls, 0)

    def test_bad_arguments_exit_2(self):
        for argv in (["review"], ["deliver", "--note", "x", "--quotes", "maybe"], ["bogus"]):
            with self.assertRaises(SystemExit) as ctx, \
                    contextlib.redirect_stderr(io.StringIO()):
                jev_door.main(argv)
            self.assertEqual(ctx.exception.code, 2, argv)


if __name__ == "__main__":
    unittest.main()
