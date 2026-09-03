#!/usr/bin/env python3
"""Tests for the interchange contract (VB3-16): schemas/interchange/*.schema.json
and tools/bm_vault_interchange.py.

Driven backwards throughout: every PASS case has a doctored sibling that must be
refused, by name, and the evolution/star claims are checked both ways (a schema
change really does migrate forward and the star projection really does drop what
its own header says it drops).

Run: python3 tools/test_bm_vault_interchange.py      (unittest output, exit 0 or 1)
"""
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_interchange as ic     # noqa: E402
import bm_vault_assertions as A       # noqa: E402
import bm_vault_events as ev          # noqa: E402
import bm_vault_export as ex          # noqa: E402
import bm_vault_ids as ids_mod        # noqa: E402

TOOL = os.path.join(HERE, "bm_vault_interchange.py")


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.returncode, p.stdout, p.stderr


def make_vault(tmp, notes):
    """Mirrors tools/test_bm_vault_export.py's own helper of the same name:
    notes: [(filename, frontmatter_extra_lines, body_lines)]."""
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault, exist_ok=True)
    ids = []
    for name, extra, body in notes:
        nid = ids_mod.mint(set(ids))
        ids.append(nid)
        fm = "id: %s\n" % nid + "".join(l + "\n" for l in extra)
        text = "---\n%s---\n\n%s\n" % (fm, "\n".join(body))
        with open(os.path.join(vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    return vault, ids


class TestAssertionSchemaValidatesRealFixture(unittest.TestCase):
    """A real assertion, minted through bm_vault_assertions.py's own
    cmd_mint_assertion, must validate clean against assertion.schema.json."""

    def test_real_minted_assertion_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = Args(vault=tmp, subject="n-" + "a" * 16, predicate="headcount",
                       value="42", authority="derived", lifecycle="candidate",
                       source="doc:x", valid_from=None, valid_to=None,
                       observed_at=None, ingested_at=None, verified_at=None,
                       supersedes=None)
            self.assertEqual(A.cmd_mint_assertion(args), 0)
            path = A.assertions_path(tmp)
            rc, out, err = run(["validate", "--kind", "assertion", path])
            self.assertEqual(rc, 0, err)
            self.assertIn("PASS kind=assertion", out)
            self.assertIn("rows=1", out)

    def test_missing_required_field_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "assertions.jsonl")
            record = {"id": "as-" + "0" * 16, "subject": "n-" + "0" * 16,
                     "predicate": "p", "authority": "derived",
                     "lifecycle": "candidate", "source_locator": "doc:x",
                     "recorded_at": "2026-08-30"}  # "value" missing on purpose
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            rc, out, err = run(["validate", "--kind", "assertion", path])
            self.assertEqual(rc, 1)
            self.assertIn("missing required field 'value'", err)

    def test_wrong_enum_authority_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "assertions.jsonl")
            record = {"id": "as-" + "0" * 16, "subject": "n-" + "0" * 16,
                     "predicate": "p", "value": "v", "authority": "made_up_level",
                     "lifecycle": "candidate", "source_locator": "doc:x",
                     "recorded_at": "2026-08-30"}
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            rc, out, err = run(["validate", "--kind", "assertion", path])
            self.assertEqual(rc, 1)
            self.assertIn("not in enum", err)
            self.assertIn("made_up_level", err)


class TestEventSchemaValidatesRealFixtures(unittest.TestCase):
    """Every one of the five kinds, run through bm_vault_events.py's own
    _validate first (proving the fixture is real, not merely plausible),
    then through this contract's schema."""

    def _both_accept(self, record):
        ev._validate(dict(record), "fixture")   # raises FoldError if not real
        schema = ic.load_schema("event.schema.json")
        problems = ic.validate_record(record, schema)
        self.assertEqual(problems, [], "schema refused a record bm_vault_events "
                                       "itself accepts: %s" % problems)

    def test_upsert(self):
        self._both_accept({"event_key": "k1", "kind": "upsert", "ref": "note-1",
                          "occurred_at": "2026-01-01T00:00:00Z",
                          "recorded_at": "2026-01-01T00:00:00Z"})

    def test_correct(self):
        self._both_accept({"event_key": "k2", "kind": "correct", "ref": "note-1",
                          "corrects": "k1", "occurred_at": "2026-01-02",
                          "recorded_at": "2026-01-02"})

    def test_tombstone(self):
        self._both_accept({"event_key": "k3", "kind": "tombstone", "ref": "note-1",
                          "occurred_at": "2026-01-03T00:00:00Z",
                          "recorded_at": "2026-01-03T00:00:00Z"})

    def test_merged_into_through_the_real_identity_writer(self):
        """The exact shape tools/bm_vault_identity.py's cmd_merge mints:
        occurred_at is a bare date, recorded_at is a timezone-offset
        isoformat() string ('+00:00'), never 'Z'. This caught a schema bug
        during authoring (a Z-only pattern refused this real record) and
        stays as the regression test for it."""
        effective = "2026-08-30"
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds")
        self.assertTrue(recorded_at.endswith("+00:00"),
                        "this test's own premise (isoformat uses +00:00, not Z) "
                        "no longer holds on this Python; re-check the schema "
                        "pattern against whatever it does emit")
        self._both_accept({
            "event_key": "merge:a:b:%s" % effective, "kind": "merged_into",
            "ref": "n-" + "1" * 16, "into": "n-" + "2" * 16,
            "rule_version": "v1", "effective": effective,
            "occurred_at": effective, "recorded_at": recorded_at,
        })

    def test_unmerged(self):
        self._both_accept({"event_key": "unmerge:a:b:2026-08-30", "kind": "unmerged",
                          "ref": "n-" + "1" * 16, "into": "n-" + "2" * 16,
                          "effective": "2026-08-30", "occurred_at": "2026-08-30",
                          "recorded_at": "2026-08-30"})

    def test_payload_bearing_field_refused_by_name(self):
        """A field that reads as prose (a note body smuggled into `ref`)
        must fail the schema's length/pattern bound, whatever field it
        rides in on -- the payload-free rule, expressed structurally."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            record = {"event_key": "k1", "kind": "upsert",
                     "ref": "this looks like a whole sentence of note body "
                            "text riding along inside the ref field, which "
                            "the payload shape check must catch by length " * 3,
                     "occurred_at": "2026-01-01T00:00:00Z",
                     "recorded_at": "2026-01-01T00:00:00Z"}
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            rc, out, err = run(["validate", "--kind", "event", path])
            self.assertEqual(rc, 1)
            self.assertIn("ref", err)
            self.assertIn("maxLength", err)

    def test_unknown_field_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            record = {"event_key": "k1", "kind": "upsert", "ref": "note-1",
                     "occurred_at": "2026-01-01T00:00:00Z",
                     "recorded_at": "2026-01-01T00:00:00Z", "note_body": "hello"}
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            rc, out, err = run(["validate", "--kind", "event", path])
            self.assertEqual(rc, 1)
            self.assertIn("unknown field", err)
            self.assertIn("note_body", err)


class TestExportRowSchemasValidateRealFixtures(unittest.TestCase):
    """Real bundle rows, produced by bm_vault_export.py's own cmd_bundle
    against a real fixture vault -- the exact table VB8-04 ships."""

    def test_real_assertions_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = make_vault(tmp, [
                ("a.md", ["authority: source_of_record", "promotion: validated",
                          "promoted_by: khalil", "promoted_at: 2026-08-01",
                          "valid_from: 2026-01-01"],
                 ["claim: the sky is blue [evidence: repo:abcdef1]"]),
            ])
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=[],
                                    include_restricted=False, events=None))
            self.assertEqual(rc, 0)
            rc, stdout, err = run(["validate", "--kind", "export_assertion",
                                  os.path.join(out, "assertions.jsonl")])
            self.assertEqual(rc, 0, err)
            self.assertIn("PASS kind=export_assertion", stdout)

    def test_real_events_row_live_and_tombstoned(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [
                ("a.md", [], ["claim: x [evidence: repo:1234567]"]),
                ("b.md", [], ["claim: y [evidence: repo:1234567]"]),
            ])
            events_path = os.path.join(tmp, "events.jsonl")
            with open(events_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"event_key": "k1", "kind": "upsert", "ref": ids[0],
                                    "occurred_at": "2026-01-01T00:00:00Z",
                                    "recorded_at": "2026-01-01T00:00:00Z"}) + "\n")
                fh.write(json.dumps({"event_key": "k2", "kind": "upsert", "ref": ids[1],
                                    "occurred_at": "2026-01-01T00:00:00Z",
                                    "recorded_at": "2026-01-01T00:00:00Z"}) + "\n")
                fh.write(json.dumps({"event_key": "k3", "kind": "tombstone", "ref": ids[1],
                                    "occurred_at": "2026-01-02T00:00:00Z",
                                    "recorded_at": "2026-01-02T00:00:00Z"}) + "\n")
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=[],
                                    include_restricted=False, events=events_path))
            self.assertEqual(rc, 0)
            with open(os.path.join(out, "events.jsonl"), encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            statuses = {r["status"] for r in rows}
            self.assertEqual(statuses, {"live", "tombstoned"},
                             "fixture must exercise both export-event row shapes")
            rc, stdout, err = run(["validate", "--kind", "export_event",
                                  os.path.join(out, "events.jsonl")])
            self.assertEqual(rc, 0, err)
            self.assertIn("PASS kind=export_event", stdout)
            self.assertIn("rows=2", stdout)


class TestFieldRegistry(unittest.TestCase):
    """Every shipped x-field-id is unique across all five schema files and
    matches schemas/interchange/field_registry.json's own ledger."""

    def test_registry_clean_via_cli(self):
        rc, out, err = run(["check-registry"])
        self.assertEqual(rc, 0, err)
        self.assertIn("REGISTRY CLEAN", out)

    def test_every_shipped_id_matches_the_registry(self):
        registry = ic.load_field_registry()
        registered = {f["name"]: f["id"] for f in registry["fields"]}
        for (schema_file, field_name), field_id in ic.iter_schema_field_ids().items():
            self.assertEqual(registered.get(field_name), field_id,
                             "%s field %r carries id %s, registry disagrees"
                             % (schema_file, field_name, field_id))

    def test_no_bare_integer_id_names_two_different_fields(self):
        by_id = {}
        for (_schema_file, field_name), field_id in ic.iter_schema_field_ids().items():
            if field_id in by_id:
                self.assertEqual(by_id[field_id], field_name,
                                 "x-field-id %s is shared by %r and %r" %
                                 (field_id, by_id[field_id], field_name))
            by_id[field_id] = field_name
        self.assertTrue(by_id, "the scanner found no fields; it is broken")

    def test_synthetic_id_reuse_is_detected_by_evolve_check(self):
        """A registry test needs an adversarial case too: mint a fresh V2
        that keeps a field's NAME but reassigns its id to a different
        field entirely (the exact "never reused" violation the row
        forbids). This never touches a real shipped schema file."""
        v1 = {"type": "object", "additionalProperties": False,
             "required": ["a"],
             "properties": {"a": {"type": "string", "x-field-id": 1},
                            "b": {"type": "string", "x-field-id": 2}}}
        v2_reused = {"type": "object", "additionalProperties": False,
                    "required": ["a"],
                    "properties": {"a": {"type": "string", "x-field-id": 2},
                                  "b": {"type": "string", "x-field-id": 2}}}
        ok, message = ic.evolve_check(v1, v2_reused)
        self.assertFalse(ok)
        self.assertIn("x-field-id 2", message)


class TestEvolve(unittest.TestCase):
    """Additive migrates forward; removal and type change are refused by
    name; running the same check with source/target swapped is how this
    module answers 'does it migrate back, or is it refused honestly'."""

    V1 = {"type": "object", "additionalProperties": False,
         "required": ["a"],
         "properties": {"a": {"type": "string", "x-field-id": 1}}}

    def test_additive_optional_field_is_forward_compatible(self):
        v2 = {"type": "object", "additionalProperties": False,
             "required": ["a"],
             "properties": {"a": {"type": "string", "x-field-id": 1},
                            "b": {"type": "string", "x-field-id": 2}}}
        ok, message = ic.evolve_check(self.V1, v2)
        self.assertTrue(ok, message)

    def test_additive_required_field_with_default_is_forward_compatible(self):
        v2 = {"type": "object", "additionalProperties": False,
             "required": ["a", "b"],
             "properties": {"a": {"type": "string", "x-field-id": 1},
                            "b": {"type": "string", "x-field-id": 2, "default": ""}}}
        ok, message = ic.evolve_check(self.V1, v2)
        self.assertTrue(ok, message)

    def test_additive_required_field_with_no_default_is_refused(self):
        v2 = {"type": "object", "additionalProperties": False,
             "required": ["a", "b"],
             "properties": {"a": {"type": "string", "x-field-id": 1},
                            "b": {"type": "string", "x-field-id": 2}}}
        ok, message = ic.evolve_check(self.V1, v2)
        self.assertFalse(ok)
        self.assertIn("ADDED required field 'b'", message)

    def test_removal_is_refused_by_name(self):
        v2 = {"type": "object", "additionalProperties": False,
             "required": [], "properties": {}}
        ok, message = ic.evolve_check(self.V1, v2)
        self.assertFalse(ok)
        self.assertIn("REMOVED field 'a'", message)

    def test_type_change_is_refused_by_name(self):
        v2 = {"type": "object", "additionalProperties": False,
             "required": ["a"],
             "properties": {"a": {"type": "integer", "x-field-id": 1}}}
        ok, message = ic.evolve_check(self.V1, v2)
        self.assertFalse(ok)
        self.assertIn("TYPE CHANGE on field 'a'", message)

    def test_forward_then_backward_via_cli_one_ok_one_refused_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            v1_path = os.path.join(tmp, "v1.json")
            v2_path = os.path.join(tmp, "v2.json")
            v2 = {"type": "object", "additionalProperties": False,
                 "required": ["a"],
                 "properties": {"a": {"type": "string", "x-field-id": 1},
                                "b": {"type": "string", "x-field-id": 2}}}
            with open(v1_path, "w", encoding="utf-8") as fh:
                json.dump(self.V1, fh)
            with open(v2_path, "w", encoding="utf-8") as fh:
                json.dump(v2, fh)

            rc_fwd, out_fwd, err_fwd = run(["evolve", "--from", v1_path, "--to", v2_path])
            self.assertEqual(rc_fwd, 0, err_fwd)
            self.assertIn("FORWARD-COMPATIBLE", out_fwd)

            rc_back, out_back, err_back = run(["evolve", "--from", v2_path, "--to", v1_path])
            self.assertEqual(rc_back, 1)
            self.assertIn("REFUSED", err_back)
            self.assertIn("REMOVED field 'b'", err_back)

    def test_missing_schema_file_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            v1_path = os.path.join(tmp, "v1.json")
            with open(v1_path, "w", encoding="utf-8") as fh:
                json.dump(self.V1, fh)
            rc, out, err = run(["evolve", "--from", v1_path,
                              "--to", os.path.join(tmp, "does-not-exist.json")])
            self.assertEqual(rc, 2)
            self.assertIn("NO-DATA", err)


class TestStarProjection(unittest.TestCase):
    """LOSSY, and it must actually drop what its own header claims: the
    losing row's distinct value and distinct valid_from/valid_to window
    never appear anywhere in the star output."""

    def _fixture_rows(self):
        return [
            {"id": "as-" + "1" * 16, "subject": "n-" + "s" * 16, "predicate": "hq_city",
            "value": "Tokyo", "authority": "source_of_record", "lifecycle": "canonical",
            "valid_from": "2026-01-01", "valid_to": "2026-12-31"},
            {"id": "as-" + "2" * 16, "subject": "n-" + "s" * 16, "predicate": "hq_city",
            "value": "Osaka", "authority": "derived", "lifecycle": "candidate",
            "valid_from": "2020-01-01", "valid_to": "2025-12-31"},
            {"id": "as-" + "3" * 16, "subject": "n-" + "t" * 16, "predicate": "headcount",
            "value": "100", "authority": "casual", "lifecycle": "candidate",
            "valid_from": "2026-06-01", "valid_to": ""},
        ]

    def test_build_star_drops_the_losing_row(self):
        rows = self._fixture_rows()
        star_rows, dropped_evidence, dropped_contradictions = ic.build_star(rows)
        self.assertEqual(len(star_rows), 2)
        self.assertEqual(dropped_evidence, 1)
        self.assertEqual(dropped_contradictions, 1)
        hq = next(r for r in star_rows if r["predicate"] == "hq_city")
        self.assertEqual(hq["value"], "Tokyo")
        self.assertEqual(hq["valid_from"], "2026-01-01")
        all_values = {r["value"] for r in star_rows}
        self.assertNotIn("Osaka", all_values,
                         "the star claims to drop the losing value; it must not "
                         "leak through anywhere in the output")
        all_windows = {(r["valid_from"], r["valid_to"]) for r in star_rows}
        self.assertNotIn(("2020-01-01", "2025-12-31"), all_windows,
                         "the losing row's own bi-temporal window must be gone, "
                         "not merged into the winner's")

    def test_cli_header_names_all_three_lossy_axes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "assertions.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for row in self._fixture_rows():
                    fh.write(json.dumps(row) + "\n")
            rc, out, err = run(["star", "--assertions", path])
            self.assertEqual(rc, 0, err)
            header = out.splitlines()[0]
            self.assertIn("LOSSY", header)
            self.assertIn("bi-temporal", header)
            self.assertIn("many-to-many evidence", header)
            self.assertIn("contradiction edge", header)
            body_rows = [json.loads(l) for l in out.splitlines()[1:] if l.strip()]
            self.assertEqual(len(body_rows), 2)

    def test_no_data_for_missing_assertions_file(self):
        rc, out, err = run(["star", "--assertions", "/no/such/file.jsonl"])
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", err)


class TestNoData(unittest.TestCase):
    def test_unknown_kind_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            open(path, "w").close()
            rc, out, err = run(["validate", "--kind", "not-a-real-kind", path])
            self.assertEqual(rc, 2)

    def test_missing_file_is_no_data(self):
        rc, out, err = run(["validate", "--kind", "assertion", "/no/such/file.jsonl"])
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", err)


if __name__ == "__main__":
    unittest.main()
