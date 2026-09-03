"""What the priority ordering must keep true.

The dangerous failure here is not a wrong order, it is MANUFACTURED COVERAGE: a
tool that decides a node closes a complaint when nobody said so would report the
team's problems as handled while nobody was working on them. Several tests exist
only to prove it does not do that.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import priority as P  # noqa: E402


def board(nodes, verdicts):
    return {"rows": [], "features": nodes,
            "team_complaints": {"P_series_verified_2026_08_29":
                                {k: {"verdict": v} for k, v in verdicts.items()}}}


def node(nid, hours=1, closes=None, status="SCHEDULED", in_ship=False):
    return {"id": nid, "name": nid, "status": status, "effort_hours": hours,
            "closes_complaint": closes if closes is not None else [],
            "in_ship_v1": in_ship}


VERDICTS = {"P1": "NOT-ADDRESSED", "P2": "PARTIAL", "P3": "ADDRESSED"}


class ItNeverInventsCoverage(unittest.TestCase):
    """The most expensive possible error: reporting a complaint as handled
    because a name looked similar."""

    def test_a_node_that_declares_nothing_scores_zero(self):
        s, _ = P.rank(board([node("A")], VERDICTS))
        self.assertEqual(s[0]["coverage"], 0)

    def test_a_node_whose_NAME_matches_a_complaint_still_scores_zero(self):
        """No keyword matching. A link is a declaration or it is not a link."""
        n = node("A")
        n["name"] = "P1 setting up needs a developer"
        s, _ = P.rank(board([n], VERDICTS))
        self.assertEqual(s[0]["coverage"], 0)

    def test_a_declared_complaint_that_does_not_exist_is_reported_not_scored(self):
        s, _ = P.rank(board([node("A", closes=["P99"])], VERDICTS))
        self.assertEqual(s[0]["coverage"], 0)
        self.assertEqual(s[0]["detail"]["unknown"], ["P99"])

    def test_closing_an_ALREADY_ADDRESSED_complaint_scores_zero(self):
        """Work on something already fixed is not priority, whatever it claims."""
        s, _ = P.rank(board([node("A", closes=["P3"])], VERDICTS))
        self.assertEqual(s[0]["coverage"], 0)


class UntouchedOutranksHalfDone(unittest.TestCase):
    def test_a_NOT_ADDRESSED_complaint_outranks_a_PARTIAL(self):
        s, _ = P.rank(board([node("PART", closes=["P2"]),
                             node("NONE", closes=["P1"])], VERDICTS))
        self.assertEqual([x["id"] for x in s], ["NONE", "PART"])

    def test_two_complaints_outrank_one(self):
        s, _ = P.rank(board([node("ONE", closes=["P1"]),
                             node("TWO", closes=["P1", "P2"])], VERDICTS))
        self.assertEqual(s[0]["id"], "TWO")


class TiesBreakTowardTheCheaperNode(unittest.TestCase):
    """So a coverage score cannot be used to justify a quarter of work over an
    afternoon of it."""

    def test_equal_coverage_puts_the_cheaper_first(self):
        s, _ = P.rank(board([node("BIG", hours=40, closes=["P1"]),
                             node("SMALL", hours=4, closes=["P1"])], VERDICTS))
        self.assertEqual([x["id"] for x in s], ["SMALL", "BIG"])

    def test_coverage_still_beats_cheapness(self):
        """Cheap is the tiebreak, never the primary key: a one hour node that
        nobody asked for does not outrank real coverage."""
        s, _ = P.rank(board([node("CHEAP", hours=1),
                             node("WANTED", hours=40, closes=["P1"])], VERDICTS))
        self.assertEqual(s[0]["id"], "WANTED")


class TheGapIsTheOutput(unittest.TestCase):
    def test_a_complaint_no_node_claims_is_reported_uncovered(self):
        self.assertEqual(P.uncovered(board([node("A", closes=["P1"])], VERDICTS)),
                         ["P2"])

    def test_an_ADDRESSED_complaint_is_not_reported_as_a_gap(self):
        self.assertNotIn("P3", P.uncovered(board([node("A")], VERDICTS)))

    def test_a_complaint_claimed_by_a_CLOSED_node_is_still_a_gap(self):
        """A done node's claim does not cover live work. If the complaint is
        still open, somebody has to be on it."""
        n = node("A", closes=["P1"], status="DONE")
        self.assertIn("P1", P.uncovered(board([n], VERDICTS)))


class ClosedNodesAreNotRanked(unittest.TestCase):
    def test_DONE_and_SUPERSEDED_are_excluded(self):
        s, _ = P.rank(board([node("A"), node("B", status="DONE"),
                             node("C", status="SUPERSEDED")], VERDICTS))
        self.assertEqual([x["id"] for x in s], ["A"])


class NoDataIsNeverAPass(unittest.TestCase):
    def test_a_board_with_no_complaints_refuses_to_order(self):
        """Ordering by what people asked for, with nothing anyone asked for, is
        not an order. It is a made up one."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rows": [], "features": [node("A")]}, fh)
        self.assertEqual(P.main(["--roadmap", path]), 2)

    def test_an_unreadable_board_is_NO_DATA(self):
        self.assertEqual(P.main(["--roadmap", "/no/such/file.json"]), 2)


class TheRealBoardOrders(unittest.TestCase):
    def test_the_live_board_produces_an_order_and_exits_zero(self):
        self.assertEqual(P.main([]), 0)

    def test_every_ranked_node_carries_a_declaration_field(self):
        """A node with no closes_complaint key at all is invisible to this
        ordering, which would silently drop it to the bottom without saying so."""
        with open(P.ROADMAP, encoding="utf-8") as fh:
            doc = json.load(fh)
        missing = [n.get("id") for n in P.open_nodes(doc)
                   if n.get("closes_complaint") is None]
        self.assertEqual(missing, [], "these declare nothing at all: %s" % missing)


if __name__ == "__main__":
    unittest.main()
