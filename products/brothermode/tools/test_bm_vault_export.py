#!/usr/bin/env python3
"""Calibration for tools/bm_vault_export.py, WBS row VB8-04, the secure
clearing house.

MEASURED ON THIS MACHINE: `age` is absent from PATH (command -v exits 1).
Every encrypted-mode test therefore runs through bm_vault_exchange's own
injected seam (age_encrypt/age_decrypt replaced in process with a reversible
fake), the same substitution test_bm_vault_exchange.py already uses. The
age-absent test below makes no such substitution, so it proves the real
NO-DATA path on a machine that genuinely lacks the binary.

Row's own done_check, verbatim: a fixture vault exports a bundle whose
manifest verifies (and refuses after one flipped byte, driven backwards); the
tables carry every governance column the contract names, pinned by a schema
test; an encrypted bundle contains no plaintext marker string anywhere in the
relay tree; a tampered encrypted bundle refuses on import-side verify; age
absent is NO-DATA naming the install command.

No em or en dashes anywhere in this file.
"""
import base64
import contextlib
import glob
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_export as ex  # noqa: E402
import bm_vault_exchange as exch  # noqa: E402
import bm_vault_ids as ids_mod  # noqa: E402

AGE_PRESENT = bool(shutil.which("age"))
MARKER = "ZQCLEARINGMARKER-" + uuid.uuid4().hex[:8]


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_vault(tmp, notes):
    """notes: [(filename, frontmatter_extra_lines, body_lines)]. Each note
    gets a minted stable id; frontmatter_extra_lines are inserted verbatim
    between the id line and the closing ---."""
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


def fake_encrypt(recipients, data, out_path):
    with open(out_path, "wb") as fh:
        fh.write(b"FAKEAGE1" + base64.b64encode(data))
    return True, None


def fake_decrypt(identity_path, in_path, out_path):
    with open(in_path, "rb") as fh:
        raw = fh.read()
    if not raw.startswith(b"FAKEAGE1"):
        return False, "fake decrypt: not a fake-age payload"
    with open(out_path, "wb") as fh:
        fh.write(base64.b64decode(raw[8:]))
    return True, None


class FakeAgeMixin:
    def setUp(self):
        self._real_encrypt = exch.age_encrypt
        self._real_decrypt = exch.age_decrypt
        exch.age_encrypt = fake_encrypt
        exch.age_decrypt = fake_decrypt

    def tearDown(self):
        exch.age_encrypt = self._real_encrypt
        exch.age_decrypt = self._real_decrypt


class TestSchemaColumns(unittest.TestCase):
    """The governance columns the contract names: tenant, note_id (ids),
    the five temporal fields, authority, lifecycle, sensitivity, and
    evidence_locator (locators) must all be present on every assertion row."""

    def test_all_governance_columns_present(self):
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
            with open(os.path.join(out, "assertions.jsonl"), encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            for col in ("note_id", "tenant", "valid_from", "valid_to", "observed_at",
                        "ingested_at", "verified_at", "authority", "lifecycle",
                        "sensitivity", "evidence_locator", "content_hash"):
                self.assertIn(col, row, "missing governance column %r" % col)
            self.assertEqual(row["authority"], "source_of_record")
            self.assertEqual(row["lifecycle"], "validated")
            self.assertEqual(row["valid_from"], "2026-01-01")


class TestRestriction(unittest.TestCase):
    """MAJOR fix, driven backwards: restricted notes are EXCLUDED BY
    DEFAULT (claim_text is verbatim note prose), --include-restricted opts
    them back in, and the manifest marks which happened so no downstream
    reader can miss it."""

    RESTRICTED_SENTENCE = "the restricted sentence must never ship by default"

    def _make_fixture(self, tmp):
        return make_vault(tmp, [
            ("open.md", [], ["claim: open fact [evidence: repo:1234567]"]),
            ("secret.md", ["restricted: true"],
             ["claim: %s [evidence: repo:7654321]" % self.RESTRICTED_SENTENCE]),
        ])

    def test_default_excludes_restricted_and_prints_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = self._make_fixture(tmp)
            out = os.path.join(tmp, "out")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=[],
                                         include_restricted=False, events=None))
            self.assertEqual(rc, 0)
            with open(os.path.join(out, "assertions.jsonl"), encoding="utf-8") as fh:
                blob = fh.read()
            self.assertNotIn(self.RESTRICTED_SENTENCE, blob,
                              "restricted sentence leaked into the default bundle")
            rows = [json.loads(l) for l in blob.splitlines() if l.strip()]
            claims = {r["claim_text"] for r in rows}
            self.assertIn("open fact", claims)
            # Never a silent omission: the excluded count is printed.
            self.assertIn("1 restricted note(s) excluded", stderr.getvalue())
            with open(os.path.join(out, "MANIFEST.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
            self.assertEqual(manifest["restricted_included"], False)

    def test_include_restricted_flag_includes_and_marks_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = self._make_fixture(tmp)
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=[],
                                     include_restricted=True, events=None))
            self.assertEqual(rc, 0)
            with open(os.path.join(out, "assertions.jsonl"), encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            sens = {r["claim_text"]: r["sensitivity"] for r in rows}
            self.assertEqual(sens["open fact"], "standard")
            self.assertEqual(sens[self.RESTRICTED_SENTENCE], "restricted")
            with open(os.path.join(out, "MANIFEST.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
            self.assertEqual(manifest["restricted_included"], True)
            # Driven backwards: flip the manifest's own restricted_included
            # flag after the fact -- caught like any other tampered field.
            manifest["restricted_included"] = False
            manifest_path = os.path.join(out, "MANIFEST.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            self.assertEqual(ex.cmd_verify(Args(bundle=out)), 1)

    def test_old_exclude_restricted_flag_is_gone(self):
        # REMOVED, not kept as a no-op alias: nothing else in this repo
        # referenced it (grepped before removal), so there is no caller to
        # keep quiet for.
        with self.assertRaises(SystemExit):
            ex._build_parser().parse_args(
                ["bundle", "--vault", "x", "--out", "y", "--exclude-restricted"])


class TestManifestVerify(unittest.TestCase):
    def test_verify_clean_then_refuses_flipped_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = make_vault(tmp, [
                ("a.md", [], ["claim: x [evidence: repo:1234567]"]),
            ])
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=[],
                                     include_restricted=False, events=None))
            self.assertEqual(rc, 0)
            self.assertEqual(ex.cmd_verify(Args(bundle=out)), 0)

            # Driven backwards: flip one byte in the assertions table.
            path = os.path.join(out, "assertions.jsonl")
            with open(path, "rb") as fh:
                data = bytearray(fh.read())
            data[0] ^= 0xFF
            with open(path, "wb") as fh:
                fh.write(data)
            self.assertEqual(ex.cmd_verify(Args(bundle=out)), 1)

    def test_missing_manifest_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ex.cmd_verify(Args(bundle=tmp)), 2)


class TestEncrypted(FakeAgeMixin, unittest.TestCase):
    def test_no_plaintext_marker_in_relay_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = make_vault(tmp, [
                ("a.md", [], ["claim: %s [evidence: repo:1234567]" % MARKER]),
            ])
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=["age1fakerecipient"],
                                     include_restricted=False, events=None))
            self.assertEqual(rc, 0)
            # No plaintext table file lands in encrypted mode.
            self.assertFalse(os.path.isfile(os.path.join(out, "assertions.jsonl")))
            for fn in os.listdir(out):
                with open(os.path.join(out, fn), "rb") as fh:
                    blob = fh.read()
                self.assertNotIn(MARKER.encode("utf-8"), blob,
                                  "plaintext marker leaked into %s" % fn)
            self.assertEqual(ex.cmd_verify(Args(bundle=out)), 0)

    def test_tampered_ciphertext_refuses_on_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = make_vault(tmp, [
                ("a.md", [], ["claim: x [evidence: repo:1234567]"]),
            ])
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=["age1fakerecipient"],
                                     include_restricted=False, events=None))
            self.assertEqual(rc, 0)
            cipher = glob.glob(os.path.join(out, "*.age"))[0]
            with open(cipher, "rb") as fh:
                data = bytearray(fh.read())
            data[-1] ^= 0xFF
            with open(cipher, "wb") as fh:
                fh.write(data)
            self.assertEqual(ex.cmd_verify(Args(bundle=out)), 1)


class TestAgeAbsentIsNoData(unittest.TestCase):
    @unittest.skipIf(AGE_PRESENT, "age is on PATH on this machine; cannot prove the NO-DATA path")
    def test_recipient_without_age_binary_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = make_vault(tmp, [
                ("a.md", [], ["claim: x [evidence: repo:1234567]"]),
            ])
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=["age1realrecipient"],
                                     include_restricted=False, events=None))
            self.assertEqual(rc, 2)


class TestEventsTable(unittest.TestCase):
    def test_events_reused_and_filtered_to_selected_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [
                ("a.md", [], ["claim: x [evidence: repo:1234567]"]),
                ("b.md", [], ["claim: y [evidence: repo:1234567]"]),
            ])
            events_path = os.path.join(tmp, "events.jsonl")
            other_ref = "some-other-ref-not-selected"
            with open(events_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"event_key": "k1", "kind": "upsert", "ref": ids[0],
                                      "occurred_at": "2026-01-01T00:00:00Z",
                                      "recorded_at": "2026-01-01T00:00:00Z"}) + "\n")
                fh.write(json.dumps({"event_key": "k2", "kind": "upsert", "ref": other_ref,
                                      "occurred_at": "2026-01-01T00:00:00Z",
                                      "recorded_at": "2026-01-01T00:00:00Z"}) + "\n")
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=[],
                                     include_restricted=False, events=events_path))
            self.assertEqual(rc, 0)
            with open(os.path.join(out, "events.jsonl"), encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            refs = {r["ref"] for r in rows}
            self.assertIn(ids[0], refs)
            self.assertNotIn(other_ref, refs)

    def test_no_event_log_is_empty_table_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ids = make_vault(tmp, [
                ("a.md", [], ["claim: x [evidence: repo:1234567]"]),
            ])
            out = os.path.join(tmp, "out")
            rc = ex.cmd_bundle(Args(vault=vault, out=out, recipient=[],
                                     include_restricted=False, events=None))
            self.assertEqual(rc, 0)
            with open(os.path.join(out, "events.jsonl"), encoding="utf-8") as fh:
                content = fh.read()
            self.assertEqual(content, "")


if __name__ == "__main__":
    unittest.main()
