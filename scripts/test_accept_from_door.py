#!/usr/bin/env python3
"""ACC1's two sub-unit gates, written because neither existed.

WHY THIS FILE EXISTS, and it is a finding about the plan rather than a choice.
ACC1's two sub-done-checks are `test_accept_delivery.py FromTheDoor` and
`test_accept_from_door.py DeliverAsks`. Measured 2026-10-29:

  scripts/test_accept_delivery.py is frozen by sha in the plan (a65b61d3...), the sha
  still matches, and the file contains SEVEN classes and NONE of them is FromTheDoor.
  Its 50 tests are green. So ACC1.a's done check selects a class that does not exist
  and dies with AttributeError, which is red for the wrong reason: the discipline these
  harnesses are written under refuses a class that is red by an import error instead of
  by an assertion.

  scripts/test_accept_from_door.py did not exist at all. The plan records its sha as
  "NO-DATA: absent until HX118 writes it", so the plan expects it and nothing froze it.

  The three tests the plan names by name (test_an_agent_cannot_accept_alone,
  test_the_runtime_ships_accept_delivery, test_the_deliver_skill_asks) existed only as
  STRINGS inside docs/plan/specs/1.1.9/plan119_units_x2.py, the spec generator. They had
  never been written as tests anywhere.

So FromTheDoor lives here rather than in the frozen file, because HARN1 refuses a lane
that changes a frozen harness and adding a class would change its sha. ACC1.a's
sub-done-check is repointed at this file in the same change, and the reason is recorded
as a decision: a done_check naming a command that errors is not a check, since nothing
ever re-runs it.

WHO THE ADVERSARY IS, stated because it matters. HARN1 wants the session to write the
harness and a different builder to build against it, and in this change the session is
both. The mitigation is not a promise to be careful: scripts/journey/check_accept_route.py
was written earlier the same day, before this unit was chosen for building, it judges the
INSTALLED product rather than this source tree, and it is ACC1's own unit done check. It
is the gate that decides, and this file is the narrower one.

    python3 -B scripts/test_accept_from_door.py FromTheDoor
    python3 -B scripts/test_accept_from_door.py DeliverAsks

Python 3.9 compatible, standard library only.
"""
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(name, rel):
    """Load a module under scripts/ by path, with scripts/ importable first: these
    modules import siblings, and a load without that raises ModuleNotFoundError."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def defines(path, name):
    """Does this file define a module level function called `name`? Parsed, never
    imported, so an absent symbol is a False to assert on rather than an error to trip
    over. The same shape scripts/test_first_task.py uses, for the same reason."""
    import ast
    try:
        with io.open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == name for n in tree.body)


class FromTheDoor(unittest.TestCase):
    """ACC1.a: the tool reaches an install, an agent cannot answer alone, and a reject
    records a refusal without pushing."""

    def setUp(self):
        self.accept = load("accept_under_test", "scripts/accept_delivery.py")
        self.runtime = load("bundle_runtime_under_test", "scripts/bundle_runtime.py")
        self.tmp = tempfile.mkdtemp(prefix="acc1-")

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.tmp], check=False)

    def test_the_runtime_ships_accept_delivery(self):
        """REQ-ACC1A: an installed plugin carries the tool, or there is nothing for a
        person to accept through. Asked of compute_closure, which is what the install
        actually copies, not of the constant it reads."""
        closure = self.runtime.compute_closure()
        self.assertIn("accept_delivery.py", closure,
                      "the installed runtime closure does not carry accept_delivery.py, "
                      "so a stranger who finishes a run has no accept route")

    def test_an_agent_cannot_accept_alone(self):
        """REQ-ACC1A: an agent-recorded acceptance without a named delegation is
        refused before anything is written."""
        ok, reason = self.accept.record(
            name="a delivery", ref="owner/repo#1", accepted_by="a person",
            accepted_at="2026-10-29", recorded_by="agent", delegation=None,
            directory=self.tmp)
        self.assertFalse(ok, "an agent recorded an acceptance with no delegation")
        self.assertIn("delegation", reason)
        self.assertEqual(os.listdir(self.tmp), [],
                         "the refused acceptance still wrote a file")

    def test_a_person_can_still_accept(self):
        """THE PAIRED CASE. Without it, a record() that refused everything would satisfy
        the case above and nobody could ever accept anything."""
        ok, path = self.accept.record(
            name="a delivery", ref="owner/repo#2", accepted_by="a person",
            accepted_at="2026-10-29", recorded_by="person", directory=self.tmp)
        self.assertTrue(ok, path)
        self.assertTrue(os.path.isfile(path), path)

    def test_a_reject_is_recorded_as_a_refusal(self):
        """REQ-ACC1A: a reject is an answer and leaves a record saying so, not silence.
        A rejection nobody recorded is indistinguishable from nobody being asked."""
        self.assertTrue(defines(os.path.join(ROOT, "scripts/accept_delivery.py"),
                                "reject"),
                        "scripts/accept_delivery.py does not define reject yet, so a "
                        "person who wants to refuse a run has nothing to refuse with")
        ok, path = self.accept.reject(
            name="a delivery", ref="owner/repo#3", rejected_by="a person",
            rejected_at="2026-10-29", recorded_by="person", directory=self.tmp)
        self.assertTrue(ok, path)
        with io.open(path, encoding="utf-8") as fh:
            entry = json.load(fh)
        self.assertEqual(entry.get("verdict"), "rejected", entry)
        self.assertEqual(entry.get("rejected_by"), "a person", entry)

    def test_a_reject_is_not_counted_as_an_acceptance(self):
        """The pair for the case above: a refusal must not reach the acceptance list,
        or a rejected delivery would be counted as delivered."""
        self.assertTrue(defines(os.path.join(ROOT, "scripts/accept_delivery.py"),
                                "reject"),
                        "scripts/accept_delivery.py does not define reject yet")
        self.accept.reject(name="a delivery", ref="owner/repo#4",
                           rejected_by="a person", rejected_at="2026-10-29",
                           recorded_by="person", directory=self.tmp)
        accepted = self.accept.load_all(self.tmp)
        self.assertEqual([e for e in accepted if e.get("verdict") != "rejected"], [],
                         "a rejected delivery was counted among the acceptances")

    def test_an_agent_cannot_reject_alone_either(self):
        """The same gate on the other answer. An agent that may not accept for a person
        may not refuse for them either: both are the person's decision."""
        self.assertTrue(defines(os.path.join(ROOT, "scripts/accept_delivery.py"),
                                "reject"),
                        "scripts/accept_delivery.py does not define reject yet")
        ok, reason = self.accept.reject(
            name="a delivery", ref="owner/repo#5", rejected_by="a person",
            rejected_at="2026-10-29", recorded_by="agent", delegation=None,
            directory=self.tmp)
        self.assertFalse(ok, "an agent rejected with no delegation")
        self.assertIn("delegation", reason)

    def test_a_reject_pushes_nothing(self):
        """REQ-ACC1A, and the journey row's own words. Observed against a real remote: a
        reject must leave the world alone, so a bare repository set as origin must
        receive nothing."""
        self.assertTrue(defines(os.path.join(ROOT, "scripts/accept_delivery.py"),
                                "reject"),
                        "scripts/accept_delivery.py does not define reject yet")
        work = os.path.join(self.tmp, "work")
        bare = os.path.join(self.tmp, "remote.git")
        os.makedirs(work)
        subprocess.run(["git", "init", "--bare", "-q", bare], check=True,
                       capture_output=True)
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "T"],
                     ["remote", "add", "origin", bare]):
            subprocess.run(["git"] + args, cwd=work, check=True, capture_output=True)
        with io.open(os.path.join(work, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("a\n")
        subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=work, check=True,
                       capture_output=True)
        self.accept.reject(name="a delivery", ref="owner/repo#6",
                           rejected_by="a person", rejected_at="2026-10-29",
                           recorded_by="person", directory=self.tmp, cwd=work)
        count = subprocess.run(["git", "-C", bare, "rev-list", "--all", "--count"],
                               capture_output=True, text=True)
        self.assertEqual((count.stdout or "").strip(), "0",
                         "the reject pushed something to the remote")


class DeliverAsks(unittest.TestCase):
    """ACC1.b: the deliver verb a stranger reads asks the one question."""

    def setUp(self):
        self.surface = load("codex_surface_under_test", "scripts/codex_surface.py")

    def _deliver(self):
        for name, description, body in self.surface.VERBS:
            if name == "deliver":
                return description, body
        self.fail("VERBS carries no deliver verb")

    def test_the_deliver_skill_asks(self):
        """REQ-ACC1B: the generated deliver skill names the accept route, so the reader
        is asked to accept or reject rather than told the work is finished. On the base
        the body names no accept command at all."""
        _description, body = self._deliver()
        # render_skill joins paths with the / operator, so root is a Path and not a
        # string: passing a string raises TypeError, which would be red for the wrong
        # reason rather than a measurement.
        rendered = self.surface.render_skill(
            "deliver", _description, body, pathlib.Path(ROOT))
        self.assertIn("accept_delivery.py", rendered,
                      "the generated deliver skill names no accept command, so nothing "
                      "asks the one question and no answer is ever recorded")

    def test_the_question_offers_both_answers(self):
        """A question with one answer is not a question: the text has to offer the
        refusal as plainly as the acceptance."""
        _description, body = self._deliver()
        self.assertIn("accept", body.lower())
        self.assertIn("reject", body.lower())

    def test_the_deliver_verb_still_names_the_receipt(self):
        """THE PAIRED CASE. The accept question is an addition, not a replacement: a
        deliver verb that stopped naming the receipt would satisfy the cases above and
        lose the thing the verb exists for."""
        _description, body = self._deliver()
        self.assertIn("receipt", body.lower())


if __name__ == "__main__":
    unittest.main()
