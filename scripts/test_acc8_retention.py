#!/usr/bin/env python3
"""ACC8 retention: fixture only suite for rulebook_split.py and
donecheck_acc8_retention.py. Every rule here is invented; the suite runs
green under an empty HOME and publishes nothing.

Run: python3 -B scripts/test_acc8_retention.py
Python 3.9 compatible, standard library only. No em or en dashes in this file.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import donecheck_acc8_retention as dc  # noqa: E402
import rulebook_inventory as ri  # noqa: E402
import rulebook_split as rs  # noqa: E402

ENFORCER = "scripts/fixture_enforcer.py"
SOURCE = "\n".join([
    "# Rules",
    "",
    "## Core",
    "- Measure before claiming a number.",
    "- Never end a turn on a broken build.",
    "",
    "## Big law (with a long story)",
    "- THE RULE: name the command that established any claim about state.",
    "- THE INCIDENT: a long story about a timestamp nobody checked.",
    "- ENFORCEMENT: PARTLY ENFORCED. Built: python3 " + ENFORCER + " selftest. NOT built: a close time gate.",
    "- ENFORCEMENT: UNENFORCED by hook, stated discipline.",
    "",
    "## Fully enforced law",
    "- ENFORCEMENT: ENFORCED, a long story of why. Prove: `python3 " + ENFORCER + " --selftest` prints OK.",
    "",
    "## Retired",
    "- Superseded by Big law above. Surviving idea: keep the work as a bundle, never a question.",
    "- Measure before claiming a number.",
    "",
]) + "\n"
KEEP = [4, 5, 8]


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="acc8-")
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(self.repo, "scripts"))
        with open(os.path.join(self.repo, ENFORCER), "w") as handle:
            handle.write("\n")
        os.makedirs(self.home)
        self.note = os.path.join(self.root, "vault", "moved.md")
        self.draft, self.note_text, self.rows = rs.split(SOURCE, KEEP, self.note, self.home, self.repo, {7: "read before any big law work"})
        os.makedirs(os.path.dirname(self.note))
        with open(self.note, "w", encoding="utf-8") as handle:
            handle.write(self.note_text)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def doc(self, rows=None):
        return rs.map_document(SOURCE, self.rows if rows is None else rows)

    def test_every_unit_has_a_row_and_a_destination(self):
        self.assertEqual(len(self.rows), len(ri.rule_units(SOURCE)))
        self.assertTrue(all(r["destination"] for r in self.rows))
        hows = [r["how"] for r in self.rows]
        self.assertIn("duplicate of line 4", hows)
        self.assertIn("pointer with proving sentences", hows)
        self.assertIn("surviving sentences kept", hows)
        self.assertEqual(hows.count("moved verbatim"), 3)

    def test_draft_shape(self):
        self.assertIn(self.note, self.draft)
        self.assertIn("- MOVED (read before any big law work): 3 bullet(s)", self.draft)
        self.assertIn("- THE RULE: name the command", self.draft)
        self.assertNotIn("THE INCIDENT", self.draft)
        self.assertIn("## Big law (with a long story)\n- THE INCIDENT", self.note_text)
        self.assertIn("PARTLY ENFORCED", self.note_text)
        self.assertIn("- Surviving idea: keep the work as a bundle, never a question.", self.draft)
        self.assertIn("Prove: `python3 " + ENFORCER + " --selftest` prints OK.", self.draft)

    def test_check_passes_on_the_split(self):
        self.assertEqual(dc.check(SOURCE, self.draft, self.doc(), 10000, self.home, self.repo), [])

    def test_check_fails_on_no_destination(self):
        rows = json.loads(json.dumps(self.rows))
        rows[0]["destination"] = None
        self.assertIn("line 4: no destination", dc.check(SOURCE, self.draft, self.doc(rows), 10000, self.home, self.repo))

    def test_check_fails_on_missing_row(self):
        failures = dc.check(SOURCE, self.draft, self.doc(self.rows[1:]), 10000, self.home, self.repo)
        self.assertIn("line 4: no row in the map", failures)

    def test_check_refuses_a_pointer_for_a_partly_enforced_unit(self):
        draft = self.draft.replace("- MOVED (", "- ENFORCEMENT: ENFORCED by " + ENFORCER + ".\n- MOVED (", 1)
        rows = json.loads(json.dumps(self.rows))
        row = [r for r in rows if r["status"] == "PARTLY ENFORCED"][0]
        row["destination"], row["dest_text"] = "draft", "- ENFORCEMENT: ENFORCED by " + ENFORCER + ".\n"
        failures = dc.check(SOURCE, draft, self.doc(rows), 10000, self.home, self.repo)
        self.assertIn("line 10: map text is not the source unit or an allowed transformation of it", failures)

    def test_check_fails_on_status_change_inside_an_allowed_pointer(self):
        bad = "- ENFORCEMENT: UNENFORCED, pointer mutated.\n"
        rows = json.loads(json.dumps(self.rows))
        row = [r for r in rows if r["how"] == "pointer with proving sentences"][0]
        row["dest_text"] = bad
        with mock.patch.object(dc.rd, "pointer_line", return_value=bad.rstrip("\n")):
            failures = dc.check(SOURCE, self.draft + bad, self.doc(rows), 10000, self.home, self.repo)
        self.assertIn("line 14: enforcement status changed ENFORCED to UNENFORCED", failures)

    def test_check_refuses_the_reviewers_counterexample(self):
        """A row with the real line and sha256 but pointing at unrelated draft
        text must fail: the destination has to preserve the SOURCE obligation."""
        source = "- Never publish private records.\n"
        draft = "- Say hello.\n"
        unit = ri.rule_units(source)[0]
        row = {"line": 1, "sha256": unit["sha256"], "destination": "draft", "dest_text": draft, "heading": ""}
        failures = dc.check(source, draft, {"rows": [row]}, 10000, self.home, self.repo)
        self.assertEqual(failures, ["line 1: map text is not the source unit or an allowed transformation of it"])

    def test_check_refuses_duplicate_rows(self):
        rows = self.rows + [json.loads(json.dumps(self.rows[0]))]
        self.assertIn("line 4: 2 rows for one unit", dc.check(SOURCE, self.draft, self.doc(rows), 10000, self.home, self.repo))

    def test_check_refuses_extra_rows(self):
        extra = dict(self.rows[0], line=99, sha256="0" * 64)
        failures = dc.check(SOURCE, self.draft, self.doc(self.rows + [extra]), 10000, self.home, self.repo)
        self.assertIn("line 99: row names a unit the source does not have", failures)

    def test_malformed_rows_are_no_data(self):
        for rows in ("not a list", [{"line": 4}], [{"line": "4", "sha256": "x", "destination": "draft", "dest_text": "x", "heading": ""}]):
            with self.assertRaises(ValueError):
                dc.check(SOURCE, self.draft, {"rows": rows}, 10000, self.home, self.repo)
        doc = self.doc()
        doc["rows"] = [{"line": 4}]
        code, out = self._main(SOURCE, self.draft, doc)
        self.assertEqual((code, out[:8]), (2, "NO-DATA:"))

    def test_check_fails_on_text_not_at_destination(self):
        draft = self.draft.replace("- Never end a turn on a broken build.\n", "")
        self.assertIn("line 5: text not found at draft", dc.check(SOURCE, draft, self.doc(), 10000, self.home, self.repo))

    def test_check_fails_when_draft_names_no_pointer(self):
        draft = self.draft.replace(self.note, "/nowhere/else.md")
        failures = dc.check(SOURCE, draft, self.doc(), 10000, self.home, self.repo)
        self.assertTrue(any(f.endswith("draft names no pointer to " + self.note) for f in failures), failures)

    def test_check_fails_when_heading_absent_from_note(self):
        with open(self.note, "w", encoding="utf-8") as handle:
            handle.write(self.note_text.replace("## Big law (with a long story)", "## Other"))
        failures = dc.check(SOURCE, self.draft, self.doc(), 10000, self.home, self.repo)
        self.assertTrue(any(f.endswith("heading absent from " + self.note) for f in failures), failures)

    def test_check_fails_when_destination_unreadable(self):
        os.remove(self.note)
        failures = dc.check(SOURCE, self.draft, self.doc(), 10000, self.home, self.repo)
        self.assertIn("line 9: destination unreadable: " + self.note, failures)

    def test_check_fails_at_the_byte_limit(self):
        size = len(self.draft.encode("utf-8"))
        self.assertEqual(dc.check(SOURCE, self.draft, self.doc(), size + 1, self.home, self.repo), [])
        self.assertIn("draft is %d bytes, limit %d" % (size, size), dc.check(SOURCE, self.draft, self.doc(), size, self.home, self.repo))

    def _main(self, source, draft, doc):
        paths = {}
        for name, content in (("source.md", source), ("draft.md", draft)):
            paths[name] = os.path.join(self.root, name)
            with open(paths[name], "wb") as handle:
                handle.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        paths["map"] = os.path.join(self.root, "map.json")
        with open(paths["map"], "w", encoding="utf-8") as handle:
            handle.write(doc if isinstance(doc, str) else json.dumps(doc))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dc.main(["--source", paths["source.md"], "--draft", paths["draft.md"], "--map", paths["map"], "--limit", "10000", "--home", self.home, "--repo", self.repo])
        return code, out.getvalue()

    def test_main_three_verdicts(self):
        code, out = self._main(SOURCE, self.draft, self.doc())
        self.assertEqual((code, out[:5]), (0, "PASS:"))
        code, out = self._main(SOURCE, self.draft.replace("Surviving idea", "Nothing"), self.doc())
        self.assertEqual((code, out[:5]), (1, "FAIL:"))
        code, out = self._main(SOURCE, b"\xff\xfe not text", self.doc())
        self.assertEqual((code, out[:8]), (2, "NO-DATA:"))
        code, out = self._main(SOURCE + "- A new rule.\n", self.draft, self.doc())
        self.assertEqual((code, out[:8]), (2, "NO-DATA:"), "map built from another source is NO-DATA")
        wrong = json.dumps({"schema": "other", "source_sha256": ri._sha256(SOURCE.encode("utf-8")), "rows": []})
        code, out = self._main(SOURCE, self.draft, wrong)
        self.assertEqual((code, out[:8]), (2, "NO-DATA:"), "a wrong schema is NO-DATA, never a FAIL over its rows")

    def test_locate_finds_the_old_pointer_and_reports_its_status(self):
        old = self.draft.replace(
            "- ENFORCEMENT: UNENFORCED by hook, stated discipline.",
            "- ENFORCEMENT: UNENFORCED by hook, stated discipline.\n- ENFORCEMENT: ENFORCED by " + ENFORCER + " (text retired from this file).")
        rows = rs.locate(SOURCE, old, None, self.home, self.repo)
        row = [r for r in rows if r["line"] == 10][0]
        self.assertEqual(row["destination"], "draft")
        self.assertIn("line 10: map text is not the source unit or an allowed transformation of it",
                      dc.check(SOURCE, old, rs.map_document(SOURCE, rows), 10000, self.home, self.repo))

    def test_map_carries_no_dash_and_round_trips(self):
        doc = self.doc()
        text = json.dumps(doc) + rs.map_markdown(doc)
        for code in (0x2013, 0x2014):
            self.assertNotIn(chr(code), text)
        self.assertEqual(doc["source_sha256"], ri._sha256(SOURCE.encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
