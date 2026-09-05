"""Drive earn_first backwards as well as forwards.

The same argument test_floor_score.py makes about its own board applies
here one level up: a suite that only runs the shipped guides proves today's
guides, never the instrument. So every rule earn_first applies (a MATCH, a
BEHIND, a NO-DATA, an OR group, the self-healing insertion, idempotence, a
malformed block) gets its own fixture floor file and fixture document,
built fresh in a temp directory, never the real tree.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "earn_first.py")

COMPETITORS = ["gsd", "superpowers", "compound", "bmad", "claude_code",
               "codex", "cursor", "opencode"]
NO_DATA_CELL = {"score": None, "basis": "not measured on this estate"}


def cells(**measured):
    out = {key: dict(NO_DATA_CELL) for key in COMPETITORS}
    for key, score in measured.items():
        out[key] = {"score": score, "basis": "fixture round 1"}
    return out


def capability(name, role, brother, **measured):
    return {
        "capability": name,
        "role": role,
        "competitive_target": "fixture",
        "derivation": "fixture",
        "brother": {"score": brother, "basis": "fixture evidence",
                    "cites": ["E7"]},
        "competitors": cells(**measured),
    }


def floor_doc(capabilities):
    return {
        "the_floor_rule": "no material category more than 0.15 behind the "
                          "best current competitor",
        "mandatory_parity": {"sentence": "fixture", "capabilities": []},
        "competitors": {key: {"display": key} for key in COMPETITORS},
        "capabilities": capabilities,
    }


class TempTree(unittest.TestCase):
    """A scratch directory per test, never the real tree."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="earn-first-test-")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_floor(self, capabilities):
        path = os.path.join(self.dir, "FLOOR.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(floor_doc(capabilities), fh)
        return path

    def write_doc(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def run_tool(self, floor_path, doc_paths):
        cmd = [sys.executable, SCRIPT, "--source", floor_path, "--root", self.dir]
        for doc in doc_paths:
            cmd += ["--doc", doc]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=120, check=False, cwd=ROOT)
        return proc.returncode, proc.stdout, proc.stderr


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def gate(capability_name, floor, require=None, group=None):
    parts = ["capability=%s" % capability_name, "floor=%s" % floor]
    if require:
        parts.append("require=%s" % require)
    if group:
        parts.append("group=%s" % group)
    return "<!-- earn-first: %s -->\n" % ", ".join(parts)


class HandAuthoredGatesAreRespected(TempTree):
    """A document that already carries its own blocks is never rewritten,
    and the tool's own verdict follows exactly what those blocks say."""

    def test_a_document_where_every_gate_matches_is_earned(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 0.9, gsd=1.0),
        ])
        text = "# Guide\n\n" + gate("Release/CI", "gsd", require="parity")
        doc = self.write_doc("HAND.md", text)
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "HAND.md: EARNED")

    def test_exactly_at_the_floor_gap_is_a_match(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 0.85, gsd=1.0),
        ])
        doc = self.write_doc(
            "HAND.md", "# Guide\n\n" + gate("Release/CI", "gsd", "parity"))
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "HAND.md: EARNED")

    def test_one_point_past_the_floor_gap_is_not_earned(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 0.84, gsd=1.0),
        ])
        doc = self.write_doc(
            "HAND.md", "# Guide\n\n" + gate("Release/CI", "gsd", "parity"))
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.strip(),
            "HAND.md: NOT EARNED (Release/CI: 0.84 against gsd 1.00)")

    def test_a_dominate_gate_needs_brother_to_lead_not_merely_be_close(self):
        floor = self.write_floor([
            capability("Review depth", "DOMINATE", 0.85, compound=0.9),
        ])
        doc = self.write_doc(
            "HAND.md", "# Guide\n\n" + gate("Review depth", "compound", "lead"))
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("NOT EARNED", out)
        self.assertIn("Review depth: 0.85 against compound 0.90", out)

    def test_a_dominate_gate_at_a_tie_counts_as_leading(self):
        floor = self.write_floor([
            capability("Review depth", "DOMINATE", 0.9, compound=0.9),
        ])
        doc = self.write_doc(
            "HAND.md", "# Guide\n\n" + gate("Review depth", "compound", "lead"))
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "HAND.md: EARNED")

    def test_a_never_measured_competitor_is_no_data_not_a_pass(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 1.0),
        ])
        doc = self.write_doc(
            "HAND.md", "# Guide\n\n" + gate("Release/CI", "gsd", "parity"))
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.strip(),
            "HAND.md: NO-DATA (Release/CI never measured against gsd)")

    def test_hand_authored_blocks_are_never_overwritten(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 1.0, gsd=1.0),
        ])
        text = "# Guide\n\n" + gate("Release/CI", "gsd", "parity")
        doc = self.write_doc("MIGRATE-FROM-GSD.md", text)
        before = read_text(doc)
        code, _out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        after = read_text(doc)
        self.assertEqual(before, after)


class OrGroupsRepresentAtLeastOneOf(TempTree):
    """group= marks a set where one MATCH earns the whole set, per section
    21's own 'or', not an 'and' this tool invented."""

    def test_one_matching_gate_in_a_group_earns_it(self):
        floor = self.write_floor([
            capability("Falsifiable verification", "DOMINATE", 0.9, gsd=0.1),
            capability("Active memory", "DOMINATE", 0.1, gsd=0.9),
        ])
        text = ("# Guide\n\n"
                + gate("Falsifiable verification", "gsd", "lead", group="g")
                + gate("Active memory", "gsd", "lead", group="g"))
        doc = self.write_doc("HAND.md", text)
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "HAND.md: EARNED")

    def test_no_gate_in_a_group_matching_is_reported_as_not_earned(self):
        floor = self.write_floor([
            capability("Falsifiable verification", "DOMINATE", 0.1, gsd=0.9),
            capability("Active memory", "DOMINATE", 0.1, gsd=0.9),
        ])
        text = ("# Guide\n\n"
                + gate("Falsifiable verification", "gsd", "lead", group="g")
                + gate("Active memory", "gsd", "lead", group="g"))
        doc = self.write_doc("HAND.md", text)
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("NOT EARNED", out)

    def test_a_group_where_every_gate_is_no_data_reads_no_data(self):
        floor = self.write_floor([
            capability("Falsifiable verification", "DOMINATE", 0.9),
            capability("Active memory", "DOMINATE", 0.9),
        ])
        text = ("# Guide\n\n"
                + gate("Falsifiable verification", "gsd", "lead", group="g")
                + gate("Active memory", "gsd", "lead", group="g"))
        doc = self.write_doc("HAND.md", text)
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("NO-DATA", out)


class SelfHealingInsertion(TempTree):
    """A known document with no earn-first block yet gets the default gate
    set inserted once, at its own anchor line, and stays put after that."""

    def test_a_gsd_guide_with_no_block_gets_the_default_inserted(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 0.5, gsd=0.5),
        ])
        text = ("# Migrating from GSD to Brother\n\n"
                "Floor, quoted verbatim from docs/plan/"
                "SWITCHING-STRATEGY-2026-09-04.md section 21 (GSD): "
                "\"Allowed only when...\"\n\n"
                "More prose.\n")
        doc = self.write_doc("MIGRATE-FROM-GSD.md", text)
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("MIGRATE-FROM-GSD.md:", out)
        after = read_text(doc)
        self.assertIn("<!-- earn-first: capability=Release/CI, floor=gsd", after)
        self.assertIn("<!-- earn-first: capability=Crash/resume, floor=gsd",
                      after)

    def test_the_insertion_is_idempotent_on_a_second_run(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 0.5, gsd=0.5),
        ])
        text = ("# Migrating from GSD to Brother\n\n"
                "Floor, quoted verbatim from docs/plan/"
                "SWITCHING-STRATEGY-2026-09-04.md section 21 (GSD): "
                "\"Allowed only when...\"\n\n"
                "More prose.\n")
        doc = self.write_doc("MIGRATE-FROM-GSD.md", text)
        code, out1, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        after_first = read_text(doc)
        code, out2, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        after_second = read_text(doc)
        self.assertEqual(after_first, after_second)
        self.assertEqual(out1, out2)

    def test_an_unknown_document_name_with_no_block_stays_no_data(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 0.5, gsd=0.5),
        ])
        doc = self.write_doc("SOME-OTHER-GUIDE.md", "# Nothing to see\n")
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("NO-DATA (no earn-first gate declared)", out)


class MalformedInputNeverCrashesAndNeverPasses(TempTree):
    def test_a_block_missing_the_floor_key_is_no_data(self):
        floor = self.write_floor([capability("Release/CI", "MUST MATCH", 1.0)])
        doc = self.write_doc(
            "HAND.md",
            "# Guide\n\n<!-- earn-first: capability=Release/CI -->\n")
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("EARNED", out.replace("NOT EARNED", "").replace(
            "NO-DATA", ""))

    def test_an_unknown_capability_name_is_named_in_the_no_data_reason(self):
        floor = self.write_floor([capability("Release/CI", "MUST MATCH", 1.0)])
        doc = self.write_doc(
            "HAND.md", "# Guide\n\n" + gate("Nonexistent Capability", "gsd"))
        code, out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("Nonexistent Capability is not on the floor board", out)

    def test_a_missing_document_is_no_data_not_a_crash(self):
        floor = self.write_floor([capability("Release/CI", "MUST MATCH", 1.0)])
        missing = os.path.join(self.dir, "GHOST.md")
        code, out, err = self.run_tool(floor, [missing])
        self.assertEqual(code, 0, err)
        self.assertIn("NO-DATA (document does not exist)", out)

    def test_a_malformed_floor_file_is_reported_not_crashed(self):
        bad = os.path.join(self.dir, "BAD.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("not json")
        doc = self.write_doc("HAND.md", "# Guide\n")
        code, out, err = self.run_tool(bad, [doc])
        self.assertEqual(code, 0, err)
        self.assertIn("NO-DATA", out)


class ExitCodeIsAlwaysZero(TempTree):
    """earn_first reports; it never gates a build on its own exit code."""

    def test_a_behind_gate_still_exits_0(self):
        floor = self.write_floor([
            capability("Release/CI", "MUST MATCH", 0.1, gsd=1.0),
        ])
        doc = self.write_doc(
            "HAND.md", "# Guide\n\n" + gate("Release/CI", "gsd", "parity"))
        code, _out, err = self.run_tool(floor, [doc])
        self.assertEqual(code, 0, err)


class TheLiveGuidesCiteRealCapabilities(unittest.TestCase):
    """One test of the shipped documents themselves: every capability and
    competitor an earn-first block on the real guides names is one the live
    FLOOR-2026-09-05.json actually carries, so a typo in the hand-written
    default gate tables fails here rather than silently reading NO-DATA
    forever."""

    def test_default_gate_tables_name_real_capabilities_and_competitors(self):
        sys.path.insert(0, HERE)
        import earn_first as ef  # noqa: E402
        import floor_score  # noqa: E402
        doc, competitor_keys = floor_score.load(floor_score.SOURCE)
        cap_names = {c["capability"] for c in doc["capabilities"]}
        for table_name in ("GSD_GATES", "SUPERPOWERS_GATES", "COMPOUND_GATES",
                           "BMAD_GATES"):
            for g in getattr(ef, table_name):
                self.assertIn(g["capability"], cap_names,
                             "%s: %s" % (table_name, g))
                self.assertIn(g["floor"], competitor_keys,
                             "%s: %s" % (table_name, g))


if __name__ == "__main__":
    unittest.main()
