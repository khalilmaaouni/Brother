"""Drive floor_score backwards as well as forwards.

The fixtures matter more than the live file here. A test that only runs the
shipped board proves the board, not the instrument: it would still pass if
BEHIND never exited 1, because today no mandatory capability reads BEHIND. So
every rule gets a fixture that fails without it, and the live file gets exactly
one test of its own, that every row id it cites is a row that exists on the
board.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "floor_score.py")
LIVE = os.path.join(ROOT, "docs", "plan", "FLOOR-2026-09-05.json")
BOARD = os.path.join(ROOT, "docs", "plan", "READINESS-ROADMAP-2026-08-29.json")

NO_DATA_CELL = {"score": None, "basis": "not measured on this estate"}
COMPETITORS = ["gsd", "superpowers", "compound", "bmad", "claude_code",
               "codex", "cursor", "opencode"]


def cells(**measured):
    out = {key: dict(NO_DATA_CELL) for key in COMPETITORS}
    for key, score in measured.items():
        out[key] = {"score": score, "basis": "fixture round 1"}
    return out


def capability(name, role, brother, mandatory=False, **measured):
    return {
        "capability": name,
        "role": role,
        "competitive_target": "parity",
        "derivation": "fixture",
        "brother": {"score": brother, "basis": "fixture evidence", "cites": []},
        "competitors": cells(**measured),
    }


def board(capabilities, mandatory=()):
    return {
        "the_floor_rule": "no material category more than 0.15 behind the best "
                          "current competitor",
        "mandatory_parity": {"sentence": "fixture",
                             "capabilities": list(mandatory)},
        "competitors": {key: {"display": key} for key in COMPETITORS},
        "capabilities": capabilities,
    }


def run(doc_or_text):
    """(exit, stdout, stderr) of the real script over a written fixture."""
    handle, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            if isinstance(doc_or_text, str):
                fh.write(doc_or_text)
            else:
                json.dump(doc_or_text, fh)
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--source", path],
            capture_output=True, text=True, timeout=120, check=False)
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        os.unlink(path)


class MandatoryBehind(unittest.TestCase):
    def test_a_behind_mandatory_capability_exits_1(self):
        doc = board([capability("Release/CI", "MUST MATCH", 0.2, gsd=0.9)],
                    mandatory=["Release/CI"])
        code, out, _ = run(doc)
        self.assertEqual(code, 1, out)
        self.assertIn("FLOOR: FAIL", out)
        self.assertIn("Release/CI", out)

    def test_the_same_gap_on_a_non_mandatory_capability_does_not_exit_1(self):
        """The exit code is about the mandatory list, not about any BEHIND."""
        doc = board([capability("Review depth", "MUST MATCH", 0.2, gsd=0.9)],
                    mandatory=[])
        code, out, _ = run(doc)
        self.assertEqual(code, 0, out)
        self.assertIn("BEHIND", out)
        self.assertIn("FLOOR: PASS", out)

    def test_exactly_at_the_floor_is_a_match_not_behind(self):
        doc = board([capability("Release/CI", "MUST MATCH", 0.85, gsd=1.0)],
                    mandatory=["Release/CI"])
        code, out, _ = run(doc)
        self.assertEqual(code, 0, out)
        self.assertIn("MATCH", out)
        self.assertNotIn("BEHIND, measured", out)

    def test_one_point_past_the_floor_is_behind(self):
        doc = board([capability("Release/CI", "MUST MATCH", 0.84, gsd=1.0)],
                    mandatory=["Release/CI"])
        code, out, _ = run(doc)
        self.assertEqual(code, 1, out)


class NoDataIsNeverCounted(unittest.TestCase):
    def test_a_no_data_competitor_makes_the_row_no_data_not_behind(self):
        doc = board([capability("Worktree isolation", "MUST MATCH", 0.1)],
                    mandatory=["Worktree isolation"])
        code, out, _ = run(doc)
        self.assertEqual(code, 0, out)
        self.assertNotIn("BEHIND, measured", out)
        self.assertIn("Worktree isolation             MUST MATCH     0.10", out)
        self.assertIn("NO-DATA", out)

    def test_a_no_data_competitor_never_reads_as_brother_ahead(self):
        """A perfect Brother score with nobody measured is still not a lead."""
        doc = board([capability("Active memory", "DOMINATE", 1.0)])
        code, out, _ = run(doc)
        self.assertEqual(code, 0, out)
        self.assertNotIn("Brother leads", out)
        self.assertIn("Differentiation Score:   0.0%", out)

    def test_a_no_data_row_lowers_the_floor_score_rather_than_vanishing(self):
        doc = board([capability("A", "MUST MATCH", 1.0, gsd=1.0),
                     capability("B", "MUST MATCH", 1.0)])
        code, out, _ = run(doc)
        self.assertEqual(code, 0, out)
        self.assertIn("Competitive Floor Score: 50.0% (1 of 2", out)
        self.assertIn("MUST MATCH NO-DATA: 1 of 2 - B", out)

    def test_a_mandatory_no_data_prints_no_data_and_never_pass(self):
        doc = board([capability("Install/update/uninstall", "MUST MATCH", 1.0)],
                    mandatory=["Install/update/uninstall"])
        code, out, _ = run(doc)
        self.assertEqual(code, 0, out)
        self.assertIn("FLOOR: NO-DATA", out)
        self.assertNotIn("FLOOR: PASS", out)

    def test_a_measured_tie_at_the_top_counts_as_brother_leading(self):
        doc = board([capability("Parallel work", "DOMINATE", 1.0, gsd=1.0,
                                bmad=1.0)])
        code, out, _ = run(doc)
        self.assertEqual(code, 0, out)
        self.assertIn("Brother leads", out)
        self.assertIn("+1 tied", out)


class Malformed(unittest.TestCase):
    def test_absent_file_exits_2(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--source",
             os.path.join(ROOT, "docs", "plan", "no-such-floor-file.json")],
            capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("NO-DATA", proc.stderr)

    def test_unparseable_file_exits_2(self):
        code, _, err = run("{not json at all")
        self.assertEqual(code, 2)
        self.assertIn("not JSON", err)

    def test_no_capabilities_exits_2(self):
        doc = board([])
        code, _, err = run(doc)
        self.assertEqual(code, 2)
        self.assertIn("no capabilities", err)

    def test_an_unknown_role_exits_2(self):
        cap = capability("A", "MUST MATCH", 1.0, gsd=1.0)
        cap["role"] = "NICE TO HAVE"
        code, _, err = run(board([cap]))
        self.assertEqual(code, 2)
        self.assertIn("NICE TO HAVE", err)

    def test_a_score_above_one_exits_2(self):
        code, _, err = run(board([capability("A", "MUST MATCH", 1.4)]))
        self.assertEqual(code, 2)
        self.assertIn("not a score", err)

    def test_a_null_brother_score_exits_2(self):
        """The file must say 0.0 and why, never leave the cell empty."""
        cap = capability("A", "MUST MATCH", 1.0)
        cap["brother"]["score"] = None
        code, _, err = run(board([cap]))
        self.assertEqual(code, 2)
        self.assertIn("scores 0.0 and says so", err)

    def test_a_brother_score_with_no_evidence_exits_2(self):
        cap = capability("A", "MUST MATCH", 1.0)
        cap["brother"]["basis"] = "   "
        code, _, err = run(board([cap]))
        self.assertEqual(code, 2)
        self.assertIn("granted by evidence, never by assertion", err)

    def test_a_missing_competitor_cell_exits_2(self):
        cap = capability("A", "MUST MATCH", 1.0, gsd=1.0)
        del cap["competitors"]["cursor"]
        code, _, err = run(board([cap]))
        self.assertEqual(code, 2)
        self.assertIn("cursor", err)

    def test_an_empty_competitor_cell_must_say_why_it_is_empty(self):
        cap = capability("A", "MUST MATCH", 1.0, gsd=1.0)
        cap["competitors"]["cursor"] = {"score": None, "basis": "unknown"}
        code, _, err = run(board([cap]))
        self.assertEqual(code, 2)
        self.assertIn("not measured on this estate", err)

    def test_a_mandatory_name_that_matches_no_row_exits_2(self):
        doc = board([capability("A", "MUST MATCH", 1.0, gsd=1.0)],
                    mandatory=["Release/CI"])
        code, _, err = run(doc)
        self.assertEqual(code, 2)
        self.assertIn("Release/CI", err)


class TheLiveFile(unittest.TestCase):
    def setUp(self):
        with open(LIVE, encoding="utf-8") as fh:
            self.doc = json.load(fh)
        with open(BOARD, encoding="utf-8") as fh:
            self.board_ids = {row["id"] for row in json.load(fh)["rows"]}

    def test_it_loads_and_carries_the_twenty_six_capabilities(self):
        self.assertEqual(len(self.doc["capabilities"]), 26)
        roles = [c["role"] for c in self.doc["capabilities"]]
        self.assertEqual(sorted(set(roles)), ["DOMINATE", "MUST MATCH"])

    def test_every_cited_row_id_exists_on_the_board(self):
        cited = set()
        for cap in self.doc["capabilities"]:
            for row_id in cap["brother"].get("cites") or []:
                cited.add(row_id)
        self.assertTrue(cited, "the live file cites no roadmap row at all")
        missing = sorted(cited - self.board_ids)
        self.assertEqual(missing, [], "cited rows absent from the roadmap")

    def test_every_score_carries_a_citation_or_names_its_other_source(self):
        """A number with no traceable source is what this file exists against."""
        for cap in self.doc["capabilities"]:
            basis = cap["brother"]["basis"]
            has_row = bool(cap["brother"].get("cites"))
            names_other = ("PARITY-2026-08-29.json" in basis
                           or "part A" in basis or "part B" in basis)
            self.assertTrue(has_row or names_other,
                            "%s grants its score from nothing"
                            % cap["capability"])

    def test_every_competitor_is_measured_or_says_the_sentence(self):
        for cap in self.doc["capabilities"]:
            for key, cell in cap["competitors"].items():
                if cell.get("score") is None:
                    self.assertIn("not measured on this estate",
                                  cell.get("basis", "").lower(),
                                  "%s / %s" % (cap["capability"], key))

    def test_it_runs_and_prints_both_scores(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT], capture_output=True, text=True,
            timeout=120, check=False, cwd=ROOT)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertIn("Competitive Floor Score:", proc.stdout)
        self.assertIn("Differentiation Score:", proc.stdout)

    def test_the_mandatory_list_is_section_sixs_own_six(self):
        self.assertEqual(
            sorted(self.doc["mandatory_parity"]["capabilities"]),
            sorted(["Release/CI", "Crash/resume", "Resume days later",
                    "Worktree isolation", "Runtime/tool safety",
                    "Install/update/uninstall"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
