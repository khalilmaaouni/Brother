"""What the decision screen must keep true.

The point of this file is that a recommendation engine is exactly the kind of
tool that can look right while being wrong. A page with a confident 8.4 on it
reads as arithmetic whether or not any arithmetic happened, and a code excerpt
reads as current whether or not the file still says that. So these tests drive
the four things the module claims and refuses to take any of them on trust.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decide as D  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SPEC = {
    "title": "T",
    "criteria": [{"key": "a", "label": "A", "weight": 0.5},
                 {"key": "b", "label": "B", "weight": 0.5}],
    "options": [
        {"id": "x", "name": "X", "scores": {"a": 10, "b": 10}},
        {"id": "y", "name": "Y", "scores": {"a": 2, "b": 2}},
    ],
}


def clone(**over):
    s = json.loads(json.dumps(SPEC))
    s.update(over)
    return s


class TheScoreIsComputedNotTyped(unittest.TestCase):
    """A weighted total written by hand is an opinion wearing arithmetic's
    clothes. These prove the number moves when the inputs move."""

    def test_the_total_is_the_weighted_sum(self):
        _c, _n, scored, _close = D.rank(SPEC)
        self.assertAlmostEqual(scored[0]["total"], 10.0)
        self.assertAlmostEqual(scored[1]["total"], 2.0)

    def test_changing_one_mark_changes_the_total(self):
        s = clone()
        s["options"][0]["scores"]["a"] = 0
        _c, _n, scored, _close = D.rank(s)
        self.assertAlmostEqual(scored[0]["total"], 5.0)

    def test_changing_a_weight_changes_the_ranking(self):
        """The founder is invited to argue with a number rather than a verdict,
        so the verdict has to actually follow the numbers."""
        s = clone()
        s["options"][0]["scores"] = {"a": 10, "b": 0}
        s["options"][1]["scores"] = {"a": 0, "b": 10}
        self.assertEqual(D.rank(s)[2][0]["option"]["name"], "X")
        s["criteria"][1]["weight"] = 5.0          # B now dominates
        self.assertEqual(D.rank(s)[2][0]["option"]["name"], "Y")

    def test_weights_that_do_not_sum_to_one_are_normalised_and_SAID_SO(self):
        s = clone()
        s["criteria"] = [{"key": "a", "label": "A", "weight": 3},
                         {"key": "b", "label": "B", "weight": 1}]
        _c, note, scored, _close = D.rank(s)
        self.assertIn("4.00", note)
        self.assertAlmostEqual(scored[0]["total"], 10.0)

    def test_all_zero_weights_is_NO_DATA_rather_than_a_ranking(self):
        s = clone()
        for c in s["criteria"]:
            c["weight"] = 0
        note = D.rank(s)[1]
        self.assertIn(D.NODATA, note)


class AnUnmarkedCriterionIsNotAZero(unittest.TestCase):
    """Defaulting an unknown to zero, or to the middle, invents an opinion
    nobody held. It has to contribute nothing AND be named."""

    def test_it_contributes_nothing(self):
        s = clone()
        del s["options"][0]["scores"]["b"]
        self.assertAlmostEqual(D.rank(s)[2][0]["total"], 5.0)

    def test_it_is_named_rather_than_hidden(self):
        s = clone()
        del s["options"][0]["scores"]["b"]
        top = [x for x in D.rank(s)[2] if x["option"]["name"] == "X"][0]
        self.assertEqual(top["unmarked"], ["B"])

    def test_the_page_prints_the_unmarked_warning(self):
        s = clone()
        del s["options"][0]["scores"]["b"]
        self.assertIn("never marked on", D.render(s))


class ACloseCallIsNamedAsClose(unittest.TestCase):
    """Presenting 8.4 against 8.2 as a winner is how a recommendation engine
    starts lying politely."""

    def test_a_near_tie_is_flagged(self):
        s = clone()
        s["options"][1]["scores"] = {"a": 9.8, "b": 9.8}
        self.assertTrue(D.rank(s)[3])
        self.assertIn("does not separate the top two", D.render(s))

    def test_a_clear_winner_is_not_flagged(self):
        self.assertFalse(D.rank(SPEC)[3])
        self.assertNotIn("does not separate", D.render(SPEC))

    def test_the_page_never_claims_the_score_decides(self):
        page = D.render(SPEC)
        self.assertIn("The choice is yours", page)


class TheCodeIsReadFromTheLiveFile(unittest.TestCase):
    """A page promising look at the code while showing a stale copy is worse
    than one showing none."""

    def test_a_real_range_is_read_and_numbered(self):
        text, note = D.excerpt({"path": "scripts/decide.py", "lines": "1-3"})
        self.assertEqual(note, "")
        self.assertTrue(text.lstrip().startswith("1"))

    def test_a_missing_file_is_NO_DATA_not_silence(self):
        text, note = D.excerpt({"path": "scripts/nope_xyz.py", "lines": "1-3"})
        self.assertIsNone(text)
        self.assertIn(D.NODATA, note)

    def test_a_range_past_the_end_says_the_file_MOVED(self):
        """The rot case: the file is still there, the lines are not."""
        text, note = D.excerpt({"path": "scripts/decide.py", "lines": "999999"})
        self.assertIsNone(text)
        self.assertIn("moved under this page", note)

    def test_the_NO_DATA_reaches_the_rendered_page(self):
        s = clone()
        s["options"][0]["code"] = [{"path": "scripts/nope_xyz.py", "lines": "1"}]
        self.assertIn("is not present", D.render(s))


class ThePageIsSafeAndComplete(unittest.TestCase):
    def test_content_is_escaped(self):
        s = clone(title="<script>alert(1)</script>")
        page = D.render(s)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_both_themes_are_defined(self):
        page = D.render(SPEC)
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn('[data-theme="dark"]', page)

    def test_the_flow_is_kept_as_source_and_says_so(self):
        """E48, run 5 critic 1, section 5, 2026-09-03: the page used to fetch
        mermaid from a CDN so a plain browser could draw the flow. The flow
        source stays, as text a reader can render elsewhere, and the page
        says that is what it is looking at."""
        s = clone()
        s["options"][0]["flow_mermaid"] = "flowchart LR\n  A --> B"
        page = D.render(s)
        self.assertIn('<pre class="mermaid">', page)
        self.assertIn("flowchart LR", page)
        self.assertIn("flow source shown as text; render it with any "
                      "mermaid viewer", page)

    def test_the_page_fetches_nothing_when_it_is_opened(self):
        """The whole of E48, driven at the page: no script anywhere, so no
        src to point off the machine, and no http of any kind in the markup
        the generator wrote. A decision page opens with the network down."""
        s = clone()
        s["options"][0]["flow_mermaid"] = "flowchart LR\n  A --> B"
        page = D.render(s)
        # No script at all, so no script src; and no src of any kind, which
        # is what an image, a frame or a font would fetch. A link's href is
        # not a fetch: nothing is requested until a reader clicks it.
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("cdn", page.lower())
        self.assertNotIn("src=", page.lower())

    def test_an_unreadable_spec_is_NO_DATA_not_a_crash(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{ not json")
            path = fh.name
        try:
            self.assertEqual(D.main([path]), 2)
        finally:
            os.unlink(path)


class TheRealDecisionStillResolves(unittest.TestCase):
    """The regression that matters. Every code anchor in the shipped decision is
    a line number in a file somebody will edit, so this fails the day the page
    starts showing the wrong code, rather than the day somebody notices."""

    #: EVERY shipped decision, not one named file. Generalised the moment a
    #: second decision existed, because a guard that covers only the first
    #: decision silently stops guarding the moment the capability is used
    #: again, which is exactly when it starts to matter.
    DECISIONS = os.path.join(ROOT, "docs", "decisions")

    def specs(self):
        found = []
        for name in sorted(os.listdir(self.DECISIONS)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(self.DECISIONS, name), encoding="utf-8") as fh:
                found.append((name, json.load(fh)))
        return found

    def load(self):
        """The first spec, for the tests that only need one."""
        return self.specs()[0][1]

    def test_at_least_one_shipped_spec_exists_and_all_parse(self):
        found = self.specs()
        self.assertTrue(found, "no decision spec is shipped at all")
        for name, spec in found:
            self.assertTrue(spec.get("options"), name)

    def test_every_code_anchor_still_resolves(self):
        broken = []
        for name, spec in self.specs():
          for opt in spec["options"]:
            for c in opt.get("code") or []:
                text, note = D.excerpt(c)
                if text is None:
                    broken.append("%s: %s" % (opt["name"], note))
        self.assertEqual(broken, [], "code anchors have rotted: %s" % broken)

    #: A source can honestly live OUTSIDE this repository. A decision scored
    #: against the founder's own machine level files (his hooks, his global
    #: instructions, his spend guard) cites them, and copying those files into
    #: the tree is forbidden by the privacy rules, so the citation is the only
    #: honest form. Such a source declares scope "machine" and is skipped for
    #: existence. This test NEVER expands ~ and never reads the machine it runs
    #: on: a verdict that depends on which machine ran it is a recorded failure
    #: class here, and it would read green on the founder's laptop while every
    #: clone read red.
    MACHINE_SCOPE = "machine"

    def test_every_source_names_a_file_that_exists(self):
        """The flag is not a mute button, so it is checked in both directions: a
        machine level source must name a path that is outside this tree by
        construction, and an outside path carrying no flag still fails."""
        missing = []
        machine = []
        for name, spec in self.specs():
          for opt in spec["options"]:
            for s in opt.get("sources") or []:
                p = s.get("found_in")
                if not p:
                    continue
                outside = p.startswith("~") or os.path.isabs(p)
                if s.get("scope") == self.MACHINE_SCOPE:
                    self.assertTrue(
                        outside,
                        "%s: %r is declared machine level but is a repo relative "
                        "path, so it must exist in this tree" % (opt["name"], p))
                    machine.append(p)
                    continue
                self.assertFalse(
                    outside,
                    "%s: %r points outside this tree, so it must declare scope "
                    "%r; this test never expands ~ and never reads the machine "
                    "it runs on" % (opt["name"], p, self.MACHINE_SCOPE))
                if not os.path.isfile(os.path.join(ROOT, p)):
                    missing.append(p)
        if machine:
            print("%s: %d source(s) are machine level, so their existence was "
                  "not checked: %s" % (D.NODATA, len(machine), ", ".join(machine)))
        self.assertEqual(missing, [], "sources cite missing files: %s" % missing)

    def test_every_option_is_marked_on_every_criterion(self):
        """Not required by the module, required of a SHIPPED decision: an
        unmarked criterion would mean a real option was quietly under scored."""
        for name, spec in self.specs():
            keys = {c["key"] for c in spec["criteria"]}
            for opt in spec["options"]:
                self.assertEqual(set(opt.get("scores") or {}), keys,
                                 "%s: %s" % (name, opt["name"]))

    def test_every_outside_link_was_actually_checked(self):
        """The guard against a plausible URL pasted from memory. A link the
        founder clicks that 404s is worse than no link, so every one carries the
        date it was resolved and this fails if any does not."""
        unchecked = []
        for name, spec in self.specs():
          for opt in spec["options"]:
            for r in (opt.get("repos") or []) + (opt.get("docs") or []):
                if not r.get("checked"):
                    unchecked.append(r.get("url", "?"))
                if not str(r.get("url", "")).startswith("https://"):
                    unchecked.append("not https: %s" % r.get("url"))
        self.assertEqual(unchecked, [], "links not verified: %s" % unchecked)

    def test_the_chosen_option_is_one_that_exists(self):
        """A decision record naming an option that is not on the page would
        render a banner with nothing marked, which reads as no decision."""
        spec = self.load()
        for name, spec in self.specs():
            decided = spec.get("decided") or {}
            if not decided:
                continue
            ids = {o.get("id") for o in spec["options"]}
            self.assertIn(decided.get("choice"), ids, name)

    def test_every_option_carries_pros_cons_a_diagram_and_a_source(self):
        for name, spec in self.specs():
          for opt in spec["options"]:
            self.assertTrue(opt.get("pros"), opt["name"])
            self.assertTrue(opt.get("cons"), opt["name"])
            self.assertTrue(opt.get("flow_mermaid"), opt["name"])
            self.assertTrue(opt.get("sources"), opt["name"])
            self.assertTrue(opt.get("score_basis"), opt["name"])


if __name__ == "__main__":
    unittest.main()
