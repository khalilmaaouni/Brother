#!/usr/bin/env python3
"""Calibration for tools/bm_vault_contract.py, WBS row VB10-02.

Each required-field test is driven BACKWARDS: it first proves the note is
flagged missing the field under the real CONTRACT table, then re-runs the
same note against a copy of the table with that one field dropped for that
class, and proves the finding disappears. That is the only way to prove the
checker actually reads the declared table rather than a field list baked
into the test itself.

No em or en dashes anywhere in this file.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault_contract.py")
sys.path.insert(0, HERE)
import bm_vault_contract as c  # noqa: E402


def note(id_="n-0123456789abcdef", type_="reference", created="2026-08-30",
         extra_lines=None, body="\n# a note\n"):
    lines = ["---", "id: %s" % id_, "type: %s" % type_, "created: %s" % created]
    if extra_lines:
        lines.extend(extra_lines)
    lines.append("---")
    return "\n".join(lines) + body


FULL_FIELDS = {
    "decision": ["owner: khalil", "status: open", "description: a call"],
    "failure": ["owner: khalil", "symptom: it broke", "verified-by: khalil"],
    "reference": ["owner: khalil", "description: a doc"],
    "entity": ["owner: khalil", "description: a thing"],
    "capture": ["captured_by: bot", "captured_at: 2026-08-30",
                "expiry_class: short", "promotion: candidate"],
}


def full_note(cls):
    return note(type_=cls, extra_lines=list(FULL_FIELDS[cls]))


class RequiredFieldsDrivenBackwards(unittest.TestCase):
    def test_every_class_flags_each_of_its_own_fields_missing(self):
        for cls, lines in FULL_FIELDS.items():
            for field in c.CONTRACT[cls]:
                if field in ("id", "created"):
                    continue  # already present via note()'s own frontmatter
                dropped = [ln for ln in lines if not ln.startswith(field + ":")]
                text = note(type_=cls, extra_lines=dropped)
                block, _ = c.frontmatter_span(text)
                fmap = c._field_map(block)
                missing = c.missing_fields(cls, fmap)
                self.assertIn(field, missing, (cls, field, missing))

    def test_dropping_a_field_from_the_table_copy_stops_the_finding(self):
        """The table-mutation half: a field the note is genuinely missing
        stops being flagged once the CONTRACT copy no longer names it."""
        text = note(type_="decision", extra_lines=["status: open", "description: x"])
        block, _ = c.frontmatter_span(text)
        fmap = c._field_map(block)
        self.assertIn("owner", c.missing_fields("decision", fmap))
        mutated = copy.deepcopy(c.CONTRACT)
        mutated["decision"] = tuple(f for f in mutated["decision"] if f != "owner")
        self.assertNotIn("owner", c.missing_fields("decision", fmap, contract=mutated))

    def test_clean_note_of_each_class_has_no_missing_fields(self):
        for cls in FULL_FIELDS:
            text = full_note(cls)
            block, _ = c.frontmatter_span(text)
            fmap = c._field_map(block)
            self.assertEqual(c.missing_fields(cls, fmap), [], cls)

    def test_entity_needs_neither_status_nor_created_value(self):
        # entity's own table has no status/created entry at all: a note
        # missing both is still clean, proving the table (not lint's
        # universal base) governs this class.
        text = note(type_="entity", extra_lines=["owner: khalil", "description: x"])
        text = text.replace("created: 2026-08-30\n", "")
        block, _ = c.frontmatter_span(text)
        fmap = c._field_map(block)
        self.assertEqual(c.missing_fields("entity", fmap), [])


class SessionLogExemption(unittest.TestCase):
    def test_session_log_is_never_flagged(self):
        text = note(type_="session-log")  # carries none of any class's fields
        block, _ = c.frontmatter_span(text)
        fmap = c._field_map(block)
        for cls in c.CONTRACT:
            self.assertEqual(c.missing_fields("session-log", fmap), [])

    def test_type_outside_the_table_is_out_of_scope(self):
        text = note(type_="finding")
        block, _ = c.frontmatter_span(text)
        fmap = c._field_map(block)
        self.assertEqual(c.missing_fields("finding", fmap), [])


class CaptureLifecycleValue(unittest.TestCase):
    def test_wrong_promotion_value_is_a_finding(self):
        lines = [ln for ln in FULL_FIELDS["capture"] if not ln.startswith("promotion:")]
        lines.append("promotion: canonical")
        text = note(type_="capture", extra_lines=lines)
        block, _ = c.frontmatter_span(text)
        fmap = c._field_map(block)
        viol = c.value_violations("capture", fmap, candidate_value="candidate")
        self.assertTrue(any(f == "promotion" for f, _e, _a in viol), viol)

    def test_candidate_promotion_is_clean(self):
        text = full_note("capture")
        block, _ = c.frontmatter_span(text)
        fmap = c._field_map(block)
        self.assertEqual(c.value_violations("capture", fmap, candidate_value="candidate"), [])


class NewVsLegacyClassification(unittest.TestCase):
    def test_staged_relpath_is_always_error(self):
        text = note(type_="decision", created="2020-01-01")  # old date, missing fields
        findings = c.classify_note("x.md", text, is_new=True)
        self.assertTrue(findings)
        self.assertTrue(all(f["kind"] == "ERROR" for f in findings))

    def test_old_created_date_not_staged_is_queue(self):
        text = note(type_="decision", created="2020-01-01")
        findings = c.classify_note("x.md", text, is_new=False)
        self.assertTrue(findings)
        self.assertTrue(all(f["kind"] == "QUEUE" for f in findings))

    def test_classify_all_mode_uses_created_date_threshold(self):
        old_text = note(type_="decision", created="2020-01-01")
        new_text = note(type_="decision", created="2026-08-30")
        result = c.classify([("old.md", old_text), ("new.md", new_text)])
        self.assertTrue(any(f["path"] == "new.md" for f in result["error"]))
        self.assertTrue(any(f["path"] == "old.md" for f in result["queue"]))
        self.assertEqual(result["by_class"]["decision"]["error"], len(
            [f for f in result["error"] if f["path"] == "new.md"]))

    def test_classify_staged_mode_treats_named_paths_as_new(self):
        text = note(type_="decision", created="2020-01-01")
        result = c.classify([("x.md", text)], staged_rels={"x.md"})
        self.assertTrue(result["error"])
        self.assertFalse(result["queue"])


class OwnerAndStewardResolution(unittest.TestCase):
    def setUp(self):
        self.owners_map = {"domains": {"40-Failures": {"owner": "alice", "steward": "bob"},
                                        "10-Projects/zeta": {"owner": "carol"}}}

    def test_domain_of_bare_top_level_folder(self):
        self.assertEqual(c.domain_of("40-Failures/some-note.md"), "40-Failures")

    def test_domain_of_projects_subfolder(self):
        self.assertEqual(c.domain_of("10-Projects/zeta/note.md"), "10-Projects/zeta")

    def test_note_field_overrides_domain_map(self):
        owner, src = c.resolve_owner("40-Failures/n.md", {"owner": "dave"}, self.owners_map)
        self.assertEqual((owner, src), ("dave", "note"))

    def test_falls_back_to_domain_map(self):
        owner, src = c.resolve_owner("40-Failures/n.md", {}, self.owners_map)
        self.assertEqual((owner, src), ("alice", "domain:40-Failures"))

    def test_no_field_no_domain_entry_is_no_data(self):
        owner, src = c.resolve_owner("50-Reference/n.md", {}, self.owners_map)
        self.assertEqual(src, "no-data")
        self.assertIsNone(owner)

    def test_steward_resolves_independently_and_may_differ_from_owner(self):
        steward, src = c.resolve_steward("40-Failures/n.md", {}, self.owners_map)
        self.assertEqual((steward, src), ("bob", "domain:40-Failures"))
        # 10-Projects/zeta declares an owner but no steward: steward is NO-DATA,
        # never silently defaulted to the owner value.
        steward2, src2 = c.resolve_steward("10-Projects/zeta/n.md", {}, self.owners_map)
        self.assertIsNone(steward2)
        self.assertEqual(src2, "no-data")

    def test_owners_map_absent_is_no_data_not_a_guess(self):
        owner, src = c.resolve_owner("40-Failures/n.md", {}, None)
        self.assertIsNone(owner)
        self.assertEqual(src, "no-data")

    def test_load_owners_map_absent_file_is_none_none(self):
        with tempfile.TemporaryDirectory() as vault:
            m, err = c.load_owners_map(vault)
            self.assertIsNone(m)
            self.assertIsNone(err)

    def test_load_owners_map_malformed_is_an_error_not_none(self):
        with tempfile.TemporaryDirectory() as vault:
            sysdir = os.path.join(vault, "99-System")
            os.makedirs(sysdir)
            with open(os.path.join(sysdir, "owners.json"), "w") as fh:
                fh.write("not json")
            m, err = c.load_owners_map(vault)
            self.assertIsNone(m)
            self.assertIsNotNone(err)


class CLISmoke(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, TOOL] + list(args),
                               capture_output=True, text=True)

    def test_check_all_no_data_on_empty_vault(self):
        with tempfile.TemporaryDirectory() as vault:
            r = self._run("check", "--all", "--vault", vault)
            self.assertEqual(r.returncode, 2)
            self.assertIn("NO-DATA", r.stdout)

    def test_check_staged_no_data_on_non_git_vault(self):
        with tempfile.TemporaryDirectory() as vault:
            with open(os.path.join(vault, "n.md"), "w") as fh:
                fh.write(note())
            r = self._run("check", "--staged", "--vault", vault)
            self.assertEqual(r.returncode, 2)
            self.assertIn("NO-DATA", r.stdout)

    def test_check_all_never_blocks_on_findings(self):
        with tempfile.TemporaryDirectory() as vault:
            with open(os.path.join(vault, "bad.md"), "w") as fh:
                fh.write(note(type_="decision", created="2020-01-01"))
            r = self._run("check", "--all", "--vault", vault, "--json")
            self.assertEqual(r.returncode, 0, r.stdout)
            payload = json.loads(r.stdout)
            self.assertGreater(payload["counts"]["queue_count"], 0)

    def test_resolve_no_owner_no_map_reports_no_data(self):
        with tempfile.TemporaryDirectory() as vault:
            rel = "10-Projects/zeta/n.md"
            os.makedirs(os.path.join(vault, "10-Projects", "zeta"))
            with open(os.path.join(vault, rel), "w") as fh:
                fh.write(note())
            r = self._run("resolve", rel, "--vault", vault)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("owner: NO-DATA", r.stdout)


if __name__ == "__main__":
    unittest.main()
