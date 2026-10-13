"""JW3.a2: the loop labels each shadow decision from its own verdict.

jev_checks is replaced by a stub whose three checks write a submitted and
an answered terminal row through the real writers into a throwaway ledger
and return a shadow SeamResult carrying only the attempt id, exactly what
the real seam returns. HOME and BROTHER_JEV_STATE_DIR point at temp folders
before any product import; no test makes a Jev call or touches the real
~/.brother/jev ledger.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="jev-loop-outcomes-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as C  # noqa: E402
import jev_outcome as O  # noqa: E402
import jev_seam  # noqa: E402
import loop_bridge as B  # noqa: E402
import scope_audit  # noqa: E402

FRAMING = "fedcba9876543210"
MODEL = "fixture-model"
SHADOW = {"modes": {"J063": "shadow", "J102": "shadow", "J117": "shadow"}}
OFF = {"modes": {"J063": "off", "J102": "off", "J117": "off"}}
QUOTED = "$ python3 -B scripts/test_x.py\nRan 3 tests\nOK (3 tests)"
UNQUOTED = "done, it works now"


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_attempt(ledger, entry, prob, terminal=True):
    os.makedirs(ledger, exist_ok=True)
    path = O.attempts_path(ledger)
    aid = jev_seam.mint_attempt_id()
    C.append_submission(path, {
        "schema": "attempt/v1", "phase": "submitted", "attempt_id": aid,
        "parent_id": None, "entry_id": entry, "qtype": None, "framing": None,
        "model": "", "mode": "shadow", "at": _now()})
    if terminal:
        h = uuid.uuid4().hex
        did = "%s:%s:%s" % (entry, h[:16], h[16:])
        C.append_decision(os.path.join(ledger, "decisions.jsonl"), {
            "id": did, "family": entry, "qtype": "noul", "framing": FRAMING,
            "answer": prob > 0.5, "prob": prob, "confidence": prob,
            "model": MODEL, "cost": 0.0, "at": _now()})
        C.append_terminal(path, {
            "schema": "attempt/v1", "phase": "answered", "attempt_id": aid,
            "parent_id": None, "entry_id": entry, "mode": "shadow", "at": _now(),
            "family": entry, "qtype": "noul", "framing": FRAMING, "model": MODEL,
            "answer": prob > 0.5, "prob": prob, "confidence": prob, "cost": 0.0,
            "reason": None, "audit": False, "decision_id": did})
    return aid


def outcomes(ledger):
    path = os.path.join(ledger, "outcomes.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class StubChecks(object):
    """Stands in for jev_checks: each call writes its rows synchronously
    and returns what a shadow consult returns (answer is the caller's,
    jev and decision_id None, only the attempt id)."""

    def __init__(self, ledger, probs):
        self.DEFAULT_LEDGER_DIR = ledger
        self.probs = probs
        self.calls = []
        self.ids = {}

    def _result(self, entry, current_answer, seams_config=None, **kw):
        if jev_seam.resolve_mode(seams_config, entry) == jev_seam.OFF:
            return jev_seam.SeamResult(current_answer, None, "off", "off", False, None, "")
        aid = write_attempt(self.DEFAULT_LEDGER_DIR, entry, self.probs[entry])
        self.calls.append(entry)
        self.ids.setdefault(entry, []).append(aid)
        return jev_seam.SeamResult(current_answer, None, "shadow", "shadow: submitted",
                                   False, None, aid)

    def check_scope_audit(self, objective, path, current_answer, **kw):
        return self._result("J117", current_answer, **kw)

    def check_worker_completion_claim(self, note, paths, current_answer, **kw):
        return self._result("J063", current_answer, **kw)

    def check_done_claim_verification(self, note, quotes, current_answer, **kw):
        return self._result("J102", current_answer, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jev-loop-outcomes-")
        self.ledger = os.path.join(self.root, "ledger")
        B._JEV_PENDING[:] = []
        B._JEV_FLUSH_STATE.update(thread=None, started=float("-inf"))
        self._saved = (B.jev_checks, B._jev_seams_config, B._jev_registry)

    def tearDown(self):
        B.jev_checks, B._jev_seams_config, B._jev_registry = self._saved
        B._JEV_PENDING[:] = []
        shutil.rmtree(self.root, ignore_errors=True)

    def repo(self):
        d = os.path.join(self.root, "repo")
        os.makedirs(d)
        git = lambda *a: subprocess.run(["git"] + list(a), cwd=d, capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "a@b.c")
        git("config", "user.name", "t")
        with open(os.path.join(d, "declared.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        return d

    def run_node(self, seams, verdict="FAIL", note=UNQUOTED,
                 probs=None, stub=True):
        repo = self.repo()
        probs = probs or {"J117": 0.95, "J102": 0.10, "J063": 0.92}
        self.stub = StubChecks(self.ledger, probs)
        if stub:
            B.jev_checks = self.stub
        B._jev_seams_config = lambda: seams
        B._jev_registry = lambda: {}

        class Worker(object):
            def run(self, unit, cwd=None):
                with open(os.path.join(repo, "declared.txt"), "a", encoding="utf-8") as fh:
                    fh.write("work\n")
                subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
                subprocess.run(["git", "commit", "-q", "-m", "w"], cwd=repo, capture_output=True)
                return {"status": "ok", "note": note}

        class Verify(object):
            @staticmethod
            def verify(unit, cwd=None):
                return {"verdict": verdict, "reason": "its own check said so"}

            @staticmethod
            def is_pass(v):
                return v.get("verdict") == "PASS"

        class Repair(object):
            @staticmethod
            def repair(*a, **k):
                return {"outcome": "gave up", "attempts": [], "reason": "fixture",
                        "final_verdict": {"verdict": verdict}}

        node = {"id": "U1", "name": "append to the declared file", "done_check": "x",
                "owns": ["declared.txt"]}
        rec = B.run_node(node, {"verify": Verify, "repair": Repair}, Worker(), cwd=repo)
        thread = B._JEV_FLUSH_STATE["thread"]
        if thread is not None:
            thread.join(10)
        return rec

    @staticmethod
    def strip(record):
        out = dict(record)
        out.pop("jev", None)
        return out


class RunNodeLabels(Base):
    """REQ-JW3-08: the three row fixture."""

    def test_three_outcomes(self):
        rec = self.run_node(SHADOW)
        self.assertEqual(sorted(self.stub.calls), ["J063", "J102", "J117"])
        rows = {r["attempt_id"]: r for r in outcomes(self.ledger)}
        self.assertEqual(len(rows), 3)
        self.assertIs(rows[self.stub.ids["J117"][0]]["correct"], True)
        self.assertIs(rows[self.stub.ids["J102"][0]]["correct"], False)
        self.assertIs(rows[self.stub.ids["J063"][0]]["correct"], False)
        self.assertEqual(B._JEV_PENDING, [])
        self.assertEqual(rec["jev"]["j063"]["attempt_id"], self.stub.ids["J063"][0])
        self.assertEqual(rec["jev"]["j117"][0]["attempt_id"], self.stub.ids["J117"][0])

    def test_final_flush_in_main_after_drain(self):
        """main() flushes once with final=True, after jev_seam.drain()."""
        aid = write_attempt(self.ledger, "J063", 0.95, terminal=False)
        B._JEV_PENDING.append({"attempt_id": aid, "entry": "J063", "rule_says": True,
                               "queued_at": time.time()})
        B.jev_checks = StubChecks(self.ledger, {})
        order = []
        real_flush = B.flush_jev_labels

        class Seam(object):
            @staticmethod
            def drain(timeout=None):
                order.append("drain")

        def flush(ledger_dir=None, final=False):
            order.append(("flush", final))
            return real_flush(ledger_dir, final)

        def fake_run(plan, parts, worker, cwd=None, max_attempts=3, max_in_flight=None, lanes=None):
            return {"dispatched": [{"id": "U1", "verdict": "PASS", "scope": {"verdict": "OK"}}],
                    "isolation": {}}

        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                             "products", "brothermode", "tools")
        fake_plan = {"batch": [{"id": "U1", "owns": ["a.txt"], "done_check": "x"}],
                     "deferred": [], "blocked": []}
        argv = ["--claims", os.path.join(self.root, "claims.json"), "--cwd", self.root,
                "--tools", os.path.normpath(tools), "--owner", "owner-jw3", "--null-worker",
                "--plan", "x"]
        with mock.patch.object(B, "jev_seam", Seam), \
                mock.patch.object(B, "flush_jev_labels", flush), \
                mock.patch.object(B.graph_loop, "load", lambda *a, **k: {}), \
                mock.patch.object(B.graph_loop, "plan", lambda *a, **k: fake_plan), \
                mock.patch.object(B, "run", fake_run):
            code = B.main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["drain", ("flush", True)])
        self.assertEqual(B._JEV_PENDING, [])


class RuleAnswers(Base):
    """REQ-JW3-09 and REQ-JW3-10, straight at queue_jev_labels."""

    def rules(self, scope_jev=(), completion=None, scope=None, verdict=None, worker=None):
        B._JEV_PENDING[:] = []
        n = B.queue_jev_labels(list(scope_jev), completion or {}, scope, verdict or {}, worker or {})
        items = list(B._JEV_PENDING)
        self.assertEqual(n, len(items))
        return [(i["entry"], i["rule_says"]) for i in items]

    def test_j117_scope(self):
        sj = [{"path": "a", "attempt_id": "a" * 32}, {"path": "b", "attempt_id": "b" * 32}]
        self.assertEqual(self.rules(sj, scope={"verdict": scope_audit.CLEAN}),
                         [("J117", True), ("J117", True)])
        self.assertEqual(self.rules(sj, scope={"verdict": scope_audit.QUARANTINE, "undeclared": ["b"]}),
                         [("J117", True), ("J117", False)])
        for bad in ({"verdict": scope_audit.NO_DATA}, None, {"verdict": "MAYBE"}, "CLEAN",
                    {"verdict": scope_audit.QUARANTINE, "undeclared": "b"}):
            self.assertEqual(self.rules(sj, scope=bad), [], bad)
        self.assertEqual(self.rules([{"path": "a"}], scope={"verdict": scope_audit.CLEAN}), [])

    def test_j063_first_verdict(self):
        c = {"j063": {"attempt_id": "c" * 32}}
        self.assertEqual(self.rules(completion=c, verdict={"verdict": "PASS"}), [("J063", True)])
        self.assertEqual(self.rules(completion=c, verdict={"verdict": "FAIL"}), [("J063", False)])
        for bad in ({"verdict": "NO-DATA"}, {"verdict": None}, {}, "PASS"):
            self.assertEqual(self.rules(completion=c, verdict=bad), [], bad)

    def test_j102_quote_flag_not_unit_verdict(self):
        c = {"j102": {"attempt_id": "d" * 32}}
        self.assertEqual(self.rules(completion=c, verdict={"verdict": "PASS"},
                                    worker={"note": QUOTED}), [("J102", False)])
        self.assertEqual(self.rules(completion=c, verdict={"verdict": "FAIL"},
                                    worker={"note": UNQUOTED}), [("J102", True)])
        self.assertEqual(self.rules(completion=c, verdict={"verdict": "FAIL"},
                                    worker={"note": QUOTED}), [("J102", False)])
        self.assertEqual(self.rules(completion=c, worker={}), [("J102", True)])

    def test_no_attempt_id_queues_nothing(self):
        c = {"j063": {"mode": "shadow"}, "j102": {"attempt_id": ""}}
        self.assertEqual(self.rules(completion=c, verdict={"verdict": "PASS"}), [])

    def test_nodata_run_node_writes_no_j063_label(self):
        self.run_node(SHADOW, verdict="NO-DATA")
        ids = {r["attempt_id"] for r in outcomes(self.ledger)}
        self.assertNotIn(self.stub.ids["J063"][0], ids)
        self.assertEqual(len(ids), 2)

    def test_forty_paths(self):
        sj = [{"path": "p%d" % i, "attempt_id": uuid.uuid4().hex} for i in range(40)]
        self.assertEqual(len(self.rules(sj, scope={"verdict": scope_audit.CLEAN})), 40)

    def test_never_raises(self):
        self.assertEqual(B.queue_jev_labels(None, None, None, None, None), 0)
        self.assertEqual(B.queue_jev_labels([1, "x"], {"j063": 3}, {"verdict": 3}, [], 7), 0)


class RecordUnchanged(Base):
    """REQ-JW3-11 and REQ-JW3-14."""

    def test_record_identical_with_labelling_on_off_and_raising(self):
        on = self.strip(self.run_node(SHADOW, verdict="PASS", probs={"J117": 0.05, "J102": 0.95, "J063": 0.05}))
        self.assertEqual(len(outcomes(self.ledger)), 3)
        shutil.rmtree(self.root)
        os.makedirs(self.root)
        with mock.patch.object(O, "label_attempt", side_effect=RuntimeError("boom")):
            raising = self.strip(self.run_node(SHADOW, verdict="PASS", probs={"J117": 0.05, "J102": 0.95, "J063": 0.05}))
        self.assertEqual(outcomes(self.ledger), [])
        shutil.rmtree(self.root)
        os.makedirs(self.root)
        with mock.patch.object(O, "terminal_rows", side_effect=RuntimeError("boom")):
            raising2 = self.strip(self.run_node(SHADOW, verdict="PASS", probs={"J117": 0.05, "J102": 0.95, "J063": 0.05}))
        shutil.rmtree(self.root)
        os.makedirs(self.root)
        off = self.run_node(OFF, verdict="PASS")
        self.assertEqual(on, off)
        self.assertEqual(raising, off)
        self.assertEqual(raising2, off)
        self.assertIs(on["integrable"], True)
        self.assertEqual(on["verdict"], "PASS")

    def test_all_off_queues_nothing_and_adds_no_key(self):
        rec = self.run_node(OFF)
        self.assertNotIn("jev", rec)
        self.assertEqual(self.stub.calls, [])
        self.assertEqual(B._JEV_PENDING, [])
        self.assertEqual(outcomes(self.ledger), [])


class Flush(Base):
    """REQ-JW3-12 and the failure edges of the flush."""

    def item(self, aid, age=0.0, rule=True):
        return {"attempt_id": aid, "entry": "J063", "rule_says": rule,
                "queued_at": time.time() - age}

    def test_pending_survives_then_final_drops_it(self):
        aid = write_attempt(self.ledger, "J063", 0.95, terminal=False)
        B._JEV_PENDING.append(self.item(aid))
        c = B.flush_jev_labels(self.ledger)
        self.assertEqual((c["PENDING"], c["expired"]), (1, 0))
        self.assertEqual(len(B._JEV_PENDING), 1)
        c = B.flush_jev_labels(self.ledger, final=True)
        self.assertEqual((c["PENDING"], c["expired"]), (0, 1))
        self.assertEqual(B._JEV_PENDING, [])
        self.assertEqual(outcomes(self.ledger), [])

    def test_older_than_an_hour_expires(self):
        aid = write_attempt(self.ledger, "J063", 0.95, terminal=False)
        B._JEV_PENDING.append(self.item(aid, age=3601))
        self.assertEqual(B.flush_jev_labels(self.ledger)["expired"], 1)
        self.assertEqual(B._JEV_PENDING, [])

    def test_unreadable_ledger_counts_a_refusal(self):
        os.makedirs(O.attempts_path(self.ledger))
        B._JEV_PENDING.append(self.item(uuid.uuid4().hex))
        err = []
        with mock.patch.object(sys, "stderr") as fake:
            fake.write.side_effect = err.append
            c = B.flush_jev_labels(self.ledger)
        self.assertEqual(c["REFUSED"], 1)
        self.assertEqual(B._JEV_PENDING, [])
        self.assertIn("1 refused", "".join(err))

    def test_labelled_and_skipped_leave_the_queue(self):
        a = write_attempt(self.ledger, "J063", 0.95)
        b = write_attempt(self.ledger, "J063", 0.5)
        B._JEV_PENDING.extend([self.item(a), self.item(b)])
        c = B.flush_jev_labels(self.ledger)
        self.assertEqual((c["LABELLED"], c["SKIPPED"]), (1, 1))
        self.assertEqual(B._JEV_PENDING, [])
        self.assertEqual(len(outcomes(self.ledger)), 1)

    def test_one_raising_label_does_not_stop_the_rest(self):
        a = write_attempt(self.ledger, "J063", 0.95)
        b = write_attempt(self.ledger, "J063", 0.95)
        B._JEV_PENDING.extend([self.item(a), self.item(b)])
        real = O.label_attempt
        calls = []

        def flaky(*args, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("first one breaks")
            return real(*args, **kw)

        with mock.patch.object(O, "label_attempt", flaky), mock.patch.object(sys, "stderr"):
            c = B.flush_jev_labels(self.ledger)
        self.assertEqual((c["REFUSED"], c["LABELLED"]), (1, 1))
        self.assertEqual(B._JEV_PENDING, [])

    def test_flush_never_raises(self):
        B._JEV_PENDING.append(self.item(uuid.uuid4().hex))
        with mock.patch.object(O, "terminal_rows", side_effect=RuntimeError("boom")):
            c = B.flush_jev_labels(self.ledger)
        self.assertEqual(c["LABELLED"], 0)

    def test_background_flush_is_rate_limited_and_never_raises(self):
        a = write_attempt(self.ledger, "J063", 0.95)
        B._JEV_PENDING.append(self.item(a))
        B.jev_checks = StubChecks(self.ledger, {})
        t = B._flush_jev_labels_soon()
        self.assertIsNotNone(t)
        t.join(10)
        self.assertEqual(len(outcomes(self.ledger)), 1)
        B._JEV_PENDING.append(self.item(write_attempt(self.ledger, "J063", 0.95)))
        self.assertIsNone(B._flush_jev_labels_soon(), "a second flush inside a minute")
        B._JEV_FLUSH_STATE["started"] = float("-inf")
        with mock.patch.object(B.threading, "Thread", side_effect=RuntimeError("no threads")):
            self.assertIsNone(B._flush_jev_labels_soon())
        B._JEV_PENDING[:] = []
        B._JEV_FLUSH_STATE["started"] = float("-inf")
        self.assertIsNone(B._flush_jev_labels_soon(), "nothing queued, nothing started")

    def test_empty_queue(self):
        self.assertEqual(B.flush_jev_labels(self.ledger)["LABELLED"], 0)
        self.assertFalse(os.path.exists(self.ledger))


class Concurrency(Base):
    """REQ-JW3-13: lanes queue from threads while a flush rewrites the
    queue. The rewrite is held open for a moment, so an unlocked queue
    loses whatever it appended in that window."""

    def test_eight_threads_lose_nothing(self):
        in_rewrite = threading.Event()

        class SlowList(list):
            def __setitem__(self, key, value):
                in_rewrite.set()
                time.sleep(0.3)
                list.__setitem__(self, key, value)

        old = write_attempt(self.ledger, "J063", 0.95, terminal=False)
        slow = SlowList([{"attempt_id": old, "entry": "J063", "rule_says": True,
                          "queued_at": time.time() - 7200}])
        with mock.patch.object(B, "_JEV_PENDING", slow):
            flusher = threading.Thread(target=B.flush_jev_labels, args=(self.ledger,))
            flusher.start()
            self.assertTrue(in_rewrite.wait(5))
            scope = {"verdict": scope_audit.CLEAN}

            def lane():
                sj = [{"path": "p", "attempt_id": uuid.uuid4().hex} for _ in range(25)]
                B.queue_jev_labels(sj, {}, scope, {}, {})

            lanes = [threading.Thread(target=lane) for _ in range(8)]
            for t in lanes:
                t.start()
            for t in lanes:
                t.join(10)
            flusher.join(10)
            self.assertEqual(len(slow), 200)
            self.assertNotIn(old, [i["attempt_id"] for i in slow])


if __name__ == "__main__":
    unittest.main()
