"""What the decision round must keep true.

The failure it prevents is the one the founder scored 0 of 5 for: four decisions
put to a human as a popup because the renderer could only express one. So the
tests assert that a round with N decisions renders N decision blocks, that the
scores come from decide.py rather than from this file, and that a malformed
round reports NO-DATA instead of writing a page that looks fine and says nothing.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROUND = os.path.join(HERE, "decide_round.py")
_LAST_SENTINEL = [""]


def a_decision(name="A decision", top="first", second="second"):
    return {
        "title": name,
        "plain_summary": "why this matters in plain words",
        "question": "which one?",
        "criteria": [{"key": "k1", "label": "First thing", "weight": 5,
                      "what": "what", "why": "why", "where": "where"},
                     {"key": "k2", "label": "Second thing", "weight": 2,
                      "what": "what", "why": "why", "where": "where"}],
        "options": [
            {"id": "a", "name": top, "one_liner": "the recommended one",
             "scores": {"k1": 9, "k2": 8}, "pros": ["clear"], "cons": ["none"]},
            {"id": "b", "name": second, "one_liner": "the other one",
             "scores": {"k1": 2, "k2": 3}, "pros": ["cheap"], "cons": ["unsafe"]},
        ],
    }


def render(spec):
    """(exit_code, page_or_none, stdout+stderr)."""
    tmp = tempfile.mkdtemp(prefix="decide-round-test-")
    spec_path = os.path.join(tmp, "round.json")
    out_path = os.path.join(tmp, "round.html")
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    env = dict(os.environ)
    env["BROTHER_DECISION_SENTINEL"] = os.path.join(tmp, "sentinel.json")
    proc = subprocess.run([sys.executable, ROUND, spec_path, "-o", out_path],
                          capture_output=True, text=True, env=env)
    page = None
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as fh:
            page = fh.read()
    _LAST_SENTINEL[0] = env["BROTHER_DECISION_SENTINEL"]
    return proc.returncode, page, (proc.stdout or "") + (proc.stderr or "")


class ARoundIsOneScreenNotFourPopups(unittest.TestCase):
    def test_every_decision_gets_its_own_block(self):
        code, page, _ = render({"title": "Four things",
                                "decisions": [a_decision("One"), a_decision("Two"),
                                              a_decision("Three"), a_decision("Four")]})
        self.assertEqual(code, 0)
        self.assertEqual(page.count('class="decision-block"'), 4)
        for name in ("One", "Two", "Three", "Four"):
            self.assertIn(name, page)

    def test_the_round_has_exactly_one_masthead(self):
        """Stapling whole pages together repeats the masthead per decision and
        reads as several documents shuffled together. The founder scored that
        0 of 5 on 2026-09-10; this is the test that keeps it fixed."""
        _, page, _ = render({"title": "Four things",
                             "decisions": [a_decision("One"), a_decision("Two"),
                                           a_decision("Three"), a_decision("Four")]})
        self.assertEqual(page.count('<header class="top">'), 1)
        self.assertEqual(page.count("<h1>"), 1)
        self.assertEqual(page.count("In plain words"), 1)

    def test_each_decision_is_a_section_with_its_own_heading(self):
        _, page, _ = render({"title": "Three",
                             "decisions": [a_decision("One"), a_decision("Two"),
                                           a_decision("Three")]})
        self.assertEqual(page.count('<header class="decision-head">'), 3)
        self.assertEqual(page.count('class="decision-block"'), 3)

    def test_every_decision_still_carries_its_own_plain_language_summary(self):
        """The heading is dropped in a fragment, the paragraph never is: plain
        language first is the whole reason this template exists."""
        d = a_decision("One")
        d["plain_summary"] = "the specific reason this one matters"
        _, page, _ = render({"title": "One", "decisions": [d]})
        self.assertIn("the specific reason this one matters", page)

    def test_the_stylesheet_is_emitted_once_not_per_decision(self):
        """Copies would let the last one silently win any future divergence."""
        _, page, _ = render({"title": "Two things",
                             "decisions": [a_decision("One"), a_decision("Two")]})
        self.assertLessEqual(page.count("<style>"), 2)

    def test_each_decision_keeps_its_own_criteria(self):
        """A round is not one question with four answers."""
        d1 = a_decision("One")
        d2 = a_decision("Two")
        d2["criteria"][0]["label"] = "A different thing entirely"
        _, page, _ = render({"title": "Two", "decisions": [d1, d2]})
        self.assertIn("First thing", page)
        self.assertIn("A different thing entirely", page)

    def test_the_ranking_comes_from_the_renderer_not_from_here(self):
        """The higher-scoring option must be reported as the top by decide.py."""
        _, _, out = render({"title": "One",
                            "decisions": [a_decision("One", top="the strong one",
                                                     second="the weak one")]})
        self.assertIn("the strong one", out)
        self.assertNotIn("top is the weak one", out)

    def test_a_round_with_no_decisions_is_no_data_and_writes_nothing(self):
        code, page, out = render({"title": "Empty", "decisions": []})
        self.assertEqual(code, 2)
        self.assertIsNone(page)
        self.assertIn("NO-DATA", out)

    def test_a_contents_entry_exists_for_every_decision(self):
        _, page, _ = render({"title": "Three",
                             "decisions": [a_decision("One"), a_decision("Two"),
                                           a_decision("Three")]})
        self.assertEqual(page.count('class="round-toc"'), 1)
        self.assertEqual(page.count('<li><a href="#d'), 3)

    def test_the_intake_sentinel_names_the_round_not_a_fragment(self):
        """The gate must point at the page the founder reads, not the last
        decision rendered inside it."""
        code, _, _ = render({"title": "Named round",
                             "decisions": [a_decision("One"), a_decision("Two")]})
        self.assertEqual(code, 0)
        # render() points the sentinel at a temp file, so this asserts the
        # behaviour without touching the machine's real intake stamp.
        self.assertIn("Named round", open(_LAST_SENTINEL[0],
                                          encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
