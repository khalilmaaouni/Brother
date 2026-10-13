"""JW1.b: the readout's prerequisites, budget and attempts lines, and the door.

HOME and BROTHER_JEV_STATE_DIR point at fresh temp folders before any product
import, so the terms file, the bridge path and the budget file all resolve
under a throwaway home; the real ~/.brother/jev ledger is never read. No test
asks a model anything.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="jev-status-doctor-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")
for _name in ("BROTHER_DECISION_BRIDGE", "BROTHER_TRANSPORTS", "BROTHER_JEV_SEAMS_OFF"):
    os.environ.pop(_name, None)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bundle_runtime  # noqa: E402
import codex_surface  # noqa: E402
import coe_outside_gate  # noqa: E402
import jev_decide  # noqa: E402
import jev_status as S  # noqa: E402

READOUT = "runtime/jev_status.py"
SENTENCE_START = " Second opinions:"
REGISTRY = [{"id": "J063", "question": {"type": "noul"}, "risk": "medium"}]
NAMES = ["registry", "seams", "bridge", "terms", "ledger"]


def _status_body():
    for verb in codex_surface.VERBS:
        if verb[0] == "status":
            return verb[2]
    raise AssertionError("no status verb")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jev-status-doctor-")
        self.ledger = os.path.join(self.root, "ledger")
        os.makedirs(self.ledger)
        self.registry = os.path.join(self.root, "registry.json")
        self.seams = os.path.join(self.root, "seams.json")
        self.write(self.registry, REGISTRY)
        self.write(self.seams, {"modes": {"J063": "shadow"}, "daily_call_budget": 50})
        self.env = {"BROTHER_DECISION_BRIDGE": "/fixture/bridge"}
        os.makedirs(os.path.dirname(coe_outside_gate.DEFAULT_TERMS_PATH), exist_ok=True)
        self.write(coe_outside_gate.DEFAULT_TERMS_PATH, {"terms": []})

    def tearDown(self):
        for path in (coe_outside_gate.DEFAULT_TERMS_PATH, jev_decide.DEFAULT_BRIDGE_PATH):
            if os.path.exists(path):
                os.remove(path)
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def write(path, obj):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))

    def states(self, seams=None, ledger=None, env=None):
        rows = S.prerequisites(self.registry, seams or self.seams, ledger or self.ledger,
                               env=self.env if env is None else env)
        self.assertEqual([r[0] for r in rows], NAMES)
        for row in rows:
            self.assertIn(row[1], ("OK", "MISSING"))
        return {r[0]: r[1] for r in rows}


class Prerequisites(Base):
    def test_all_present_reads_ok(self):
        self.assertEqual(self.states(), dict.fromkeys(NAMES, "OK"))

    def test_each_missing_prerequisite_is_named(self):
        def only(name, got):
            want = dict.fromkeys(NAMES, "OK")
            want[name] = "MISSING"
            self.assertEqual(got, want, name)

        good = open(self.registry).read()
        self.write(self.registry, "{torn")
        only("registry", self.states())
        self.write(self.registry, good)

        off = os.path.join(self.root, "seams-off.json")
        self.write(off, {"modes": {"J063": "off", "J064": "bogus"}})
        only("seams", self.states(seams=off))
        only("seams", self.states(seams=os.path.join(self.root, "absent.json")))

        only("bridge", self.states(env={}))

        os.remove(coe_outside_gate.DEFAULT_TERMS_PATH)
        only("terms", self.states())
        self.write(coe_outside_gate.DEFAULT_TERMS_PATH, {"terms": []})

        only("ledger", self.states(ledger=os.path.join(self.root, "no", "such", "ledger")))

    def test_bridge_file_or_variable_and_a_refusal(self):
        os.makedirs(os.path.dirname(jev_decide.DEFAULT_BRIDGE_PATH), exist_ok=True)
        self.write(jev_decide.DEFAULT_BRIDGE_PATH, "")
        self.assertEqual(self.states(env={})["bridge"], "OK")
        rows = S.prerequisites(self.registry, self.seams, self.ledger,
                               env={"BROTHER_DECISION_BRIDGE": "x",
                                    "BROTHER_TRANSPORTS": "no-such-transport"})
        bridge = [r for r in rows if r[0] == "bridge"][0]
        self.assertEqual(bridge[1], "MISSING")
        self.assertIn("BROTHER_TRANSPORTS", bridge[2])

    def test_ledger_not_created_yet_with_a_writable_parent_is_ok(self):
        self.assertEqual(self.states(ledger=os.path.join(self.root, "later"))["ledger"], "OK")

    def test_details_shorten_home(self):
        rows = S.prerequisites(self.registry, self.seams, self.ledger, env={})
        terms = [r for r in rows if r[0] == "terms"][0]
        self.assertTrue(terms[2].startswith("~"), terms)
        self.assertNotIn(_HOME, " ".join(r[2] for r in rows))


class Budget(Base):
    def budget(self, obj, today="2026-10-12", seams=None):
        path = os.path.join(self.root, "budget.json")
        self.write(path, obj)
        return S.budget_line(seams if seams is not None else {"daily_call_budget": 50},
                             budget_path=path, today=today)

    def test_budget_line_four_shapes(self):
        self.assertEqual(self.budget({"date": "2026-10-12", "calls": 7}),
                         "budget today: 7 of 50 calls (2026-10-12)")
        self.assertEqual(self.budget({"date": "2026-10-11", "calls": 7}),
                         "budget today: 0 of 50 calls (no call recorded today)")
        self.assertTrue(self.budget({"date": "2026-10-13", "calls": 7}).startswith("budget: NO-DATA ("))
        self.assertTrue(self.budget([1, 2]).startswith("budget: NO-DATA ("))

    def test_unusable_fields_read_no_data(self):
        for obj in ({"date": "2026-10-12", "calls": "7"}, {"date": "2026-10-12", "calls": True},
                    {"date": "2026-10-12", "calls": -1}, {"date": "12/10/2026", "calls": 1},
                    {"calls": 1}, "{torn"):
            self.assertTrue(self.budget(obj).startswith("budget: NO-DATA ("), obj)

    def test_missing_file_and_default_limit(self):
        missing = S.budget_line({}, budget_path=os.path.join(self.root, "none.json"))
        self.assertTrue(missing.startswith("budget: NO-DATA ("), missing)
        self.assertIn("of 2000 calls", self.budget({"date": "2026-10-12", "calls": 1}, seams={}))


class Breaker(Base):
    def attempts(self, phases, torn=True):
        path = os.path.join(self.ledger, "attempts.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for phase in phases:
                fh.write(json.dumps({"phase": phase, "attempt_id": "a"}) + "\n")
            if torn:
                fh.write('{"phase": "breaker_op')
        return path

    def test_breaker_line_counts_recent_phases(self):
        path = self.attempts(["submitted", "answered", "answered", "abstain", "no_data",
                              "breaker_open", "breaker_open", "breaker_open",
                              "budget_exhausted", "inflight_drop", "write_error"])
        self.assertEqual(S.breaker_line(path),
                         "attempts (recent): answered 2, abstain 1, no_data 1, breaker_open 3, "
                         "budget_exhausted 1, inflight_drop 1; the breaker itself is held in the "
                         "running process")

    def test_only_the_tail_is_read(self):
        path = self.attempts(["answered"] * 50 + ["breaker_open"] * 2, torn=False)
        line_len = len(json.dumps({"phase": "breaker_open", "attempt_id": "a"})) + 1
        line = S.breaker_line(path, tail_bytes=2 * line_len + 5)
        self.assertIn("answered 0,", line)
        self.assertIn("breaker_open 2,", line)
        exact = S.breaker_line(path, tail_bytes=2 * line_len)
        self.assertIn("breaker_open 2,", exact)

    def test_no_attempts_ledger(self):
        self.assertEqual(S.breaker_line(os.path.join(self.root, "none.jsonl")),
                         "attempts: NO-DATA (no attempts ledger)")


class Main(Base):
    def run_main(self, ledger):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = S.main(["--ledger-dir", ledger, "--seams-config", self.seams,
                           "--registry", self.registry])
        return code, out.getvalue().splitlines()

    def test_a_missing_ledger_still_prints_the_prerequisites(self):
        code, lines = self.run_main(os.path.join(self.root, "absent"))
        self.assertEqual(code, S.NO_DATA_EXIT)
        self.assertTrue(lines[0].startswith("NO-DATA: no second opinion ledger at "), lines)
        self.assertIn("(first missing prerequisite: bridge)", lines[0])
        for name in NAMES:
            self.assertEqual(len([l for l in lines if l.startswith("prerequisite %s: " % name)]), 1)
        self.assertIn("prerequisite bridge: MISSING (", "\n".join(lines))
        self.assertTrue([l for l in lines if l.startswith("budget")], lines)
        self.assertIn("attempts: NO-DATA (no attempts ledger); " + S.NOT_ACTED, lines)
        for line in lines:
            self.assertTrue(line.endswith("; " + S.NOT_ACTED), line)


class Door(unittest.TestCase):
    def test_the_status_body_names_the_readout_once_and_no_other_verb_does(self):
        for verb in codex_surface.VERBS:
            want = 1 if verb[0] == "status" else 0
            self.assertEqual("".join(verb[1:]).count(READOUT), want, verb[0])
            self.assertEqual("".join(verb[1:]).count(SENTENCE_START), want, verb[0])

    def test_the_generated_status_skill_carries_it(self):
        self.assertEqual(codex_surface.check(Path(ROOT)), [])
        for tree in ("skills", "codex-skills"):
            path = os.path.join(ROOT, "bundle", tree, "brother-status", "SKILL.md")
            with open(path, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read().count(READOUT), 1, path)

    def test_the_readout_ships_in_the_runtime_closure(self):
        self.assertIn("jev_status.py", bundle_runtime.compute_closure())
        with open(os.path.join(ROOT, "bundle", "runtime", "jev_status.py"), "rb") as a, \
                open(os.path.join(HERE, "jev_status.py"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_the_sentence_names_no_checkout_path(self):
        body = _status_body()
        sentence = body[body.index(SENTENCE_START):]
        self.assertIn("${BROTHER_PLUGIN_ROOT}/" + READOUT, sentence)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", sentence)
        self.assertNotIn("scripts/", sentence)
        self.assertNotIn("data/", sentence)
        self.assertIn("recorded, not acted on", sentence)


if __name__ == "__main__":
    unittest.main()
