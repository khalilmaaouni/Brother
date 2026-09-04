#!/usr/bin/env python3
"""Calibration for tools/bm_vault_labels.py, WBS row VB3-13: derived memory
cannot declassify.

Driven backwards throughout: every withhold has a matching serve on the same
fixture's cleared half, every tighten has a matching "never loosens" on the
same source, every violation has a matching clean pass.

No em or en dashes anywhere in this file.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")
sys.path.insert(0, HERE)
import bm_vault_labels as labels    # noqa: E402
import bm_vault_compose as compose  # noqa: E402
import bm_vault_export as export    # noqa: E402
import bm_vault_digest as digest    # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def note(id_, extra="", body="\n# body\n", type_="reference", status="standing"):
    lines = ["---", "id: %s" % id_, "type: %s" % type_, "status: %s" % status]
    if extra:
        lines.append(extra)
    lines.append("created: 2026-08-01")
    lines.append("---")
    return "\n".join(lines) + body


def frontmatter(fields, body="body\n"):
    lines = ["---"] + ["%s: %s" % (k, v) for k, v in fields] + ["---", "", body]
    return "\n".join(lines)


def write(vault, relpath, text):
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def owners_json(vault, domains):
    write(vault, os.path.join("99-System", "owners.json"),
          json.dumps({"domains": domains}))


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-labels-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        return write(self.vault, name, text)

    def _read(self, name):
        with open(os.path.join(self.vault, name), encoding="utf-8") as fh:
            return fh.read()


# --------------------------------------------------------------------------
# The vocabulary and comparator, pure functions, no filesystem.
# --------------------------------------------------------------------------

class VocabularyAndComparator(unittest.TestCase):
    def test_labels_ordered_least_to_most_restrictive(self):
        self.assertEqual(labels.LABELS, ("public", "internal", "restricted"))

    def test_most_restrictive_wins_either_argument_order(self):
        self.assertEqual(labels.most_restrictive("public", "restricted"), "restricted")
        self.assertEqual(labels.most_restrictive("restricted", "public"), "restricted")
        self.assertEqual(labels.most_restrictive("internal", "public"), "internal")

    def test_derive_of_empty_is_public(self):
        self.assertEqual(labels.derive([]), "public")

    def test_derive_picks_most_restrictive_across_many(self):
        self.assertEqual(labels.derive(["public", "internal", "public"]), "internal")
        self.assertEqual(labels.derive(["public", "restricted", "internal"]), "restricted")

    def test_unknown_label_fails_toward_the_most_restrictive_rank(self):
        self.assertEqual(labels.rank_of("not-a-real-label"), labels.rank_of("restricted"))

    def test_read_label_from_security_label_field(self):
        text = note("n-0000000000000001", extra="security_label: internal")
        self.assertEqual(labels.read_label(text), "internal")

    def test_read_label_falls_back_to_legacy_restricted_true(self):
        text = note("n-0000000000000002", extra="restricted: true")
        self.assertEqual(labels.read_label(text), "restricted")

    def test_read_label_defaults_to_public_with_neither_field(self):
        text = note("n-0000000000000003")
        self.assertEqual(labels.read_label(text), "public")

    def test_apply_label_keeps_legacy_restricted_field_in_sync(self):
        text = note("n-0000000000000004")
        restricted_text = labels.apply_label(text, "restricted")
        self.assertIn("security_label: restricted", restricted_text)
        self.assertIn("restricted: true", restricted_text)
        public_text = labels.apply_label(restricted_text, "public")
        self.assertIn("security_label: public", public_text)
        self.assertIn("restricted: false", public_text)


# --------------------------------------------------------------------------
# Deliverable 2: derivation at compose time (split/merge), most-restrictive-
# wins, and lineage back to sources readable straight off the note.
# --------------------------------------------------------------------------

class ComposeInheritsMostRestrictive(VaultFixture):
    def test_merge_of_restricted_and_public_derives_restricted(self):
        self._write("A.md", note("n-000000000000000a", extra="security_label: restricted",
                                  body="\n# A\n\nA's restricted content.\n"))
        self._write("B.md", note("n-000000000000000b",
                                  body="\n# B\n\nB's public content.\n"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = compose.main(["merge", "--vault", self.vault, "--from", "A",
                                  "--to", "B", "--apply"])
        self.assertEqual(code, 0, buf.getvalue())
        plan_output = buf.getvalue()
        self.inheritance_line = next(
            l for l in plan_output.splitlines() if l.strip().startswith("security label:"))
        self.assertEqual(self.inheritance_line.strip(),
                          "security label: restricted + public -> restricted")

        b_text = self._read("B.md")
        self.assertIn("security_label: restricted", b_text)
        self.assertIn("restricted: true", b_text)
        self.assertIn("derived_from_ids: n-000000000000000a,n-000000000000000b", b_text)
        self.assertIn("derived_from_labels: restricted,public", b_text)
        self.assertIn("derived_at:", b_text)
        # A itself is untouched (never edited to declassify): still restricted.
        a_text = self._read("A.md")
        self.assertEqual(labels.read_label(a_text), "restricted")

    def test_split_inherits_the_single_sources_label(self):
        self._write("Source.md", note("n-000000000000000c", extra="security_label: restricted",
                                       body="\n# Source\n\n## Sub\n\nSecret bit.\n"))
        code = compose.main(["split", "--vault", self.vault, "--note", "Source",
                              "--heading", "Sub", "--today", "2026-08-30", "--apply"])
        self.assertEqual(code, 0)
        new_text = self._read("sub.md")
        self.assertEqual(labels.read_label(new_text), "restricted")
        self.assertIn("derived_from_ids: n-000000000000000c", new_text)
        self.assertIn("derived_from_labels: restricted", new_text)


# --------------------------------------------------------------------------
# Deliverable 2, the other half: a hand-set weaker label is never silently
# honored -- check_derived_notes names it.
# --------------------------------------------------------------------------

class ViolationCheckNamesWeakerHandSetLabel(VaultFixture):
    def test_weaker_hand_set_label_is_a_named_violation(self):
        self._write("Derived.md", frontmatter([
            ("id", "n-000000000000000d"),
            ("derived_from_ids", "n-source-a,n-source-b"),
            ("derived_from_labels", "restricted,public"),
            ("security_label", "public"),   # hand-edited weaker than its own record
            ("restricted", "false"),
        ]))
        violations = labels.check_derived_notes(self.vault)
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v["relpath"], "Derived.md")
        self.assertEqual(v["declared"], "public")
        self.assertEqual(v["expected"], "restricted")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = labels.cmd_check(self.vault)
        self.assertEqual(code, 1)
        self.violation_line = next(
            l for l in buf.getvalue().splitlines() if l.startswith("VIOLATION:"))
        self.assertIn("Derived.md", self.violation_line)
        self.assertIn("'public'", self.violation_line)
        self.assertIn("'restricted'", self.violation_line)

    def test_a_correctly_labeled_derived_note_is_clean(self):
        self._write("Derived.md", frontmatter([
            ("id", "n-000000000000000e"),
            ("derived_from_ids", "n-source-a,n-source-b"),
            ("derived_from_labels", "restricted,public"),
            ("security_label", "restricted"),
            ("restricted", "true"),
        ]))
        self.assertEqual(labels.check_derived_notes(self.vault), [])
        code = labels.cmd_check(self.vault)
        self.assertEqual(code, 0)


# --------------------------------------------------------------------------
# Deliverable 3: recall enforcement composes with decide()/decide_dual(),
# never a parallel mechanism -- the real bm_vault.py recall path, driven.
# --------------------------------------------------------------------------

class RecallComposesWithExistingPolicy(unittest.TestCase):
    QUERY = ["recall", "--query", "kestrel merger figures", "--limit", "5", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-labels-recall-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.vault, "50-Private"))
        os.makedirs(os.path.join(cls.vault, "99-System"))

        public_text = note("n-00000000000000f1",
                            body="\n# Public\n\nThe kestrel merger figures, public summary.\n")
        restricted_text = labels.apply_label(
            note("n-00000000000000f2",
                 body="\n# Secret\n\nThe kestrel merger figures, unredacted per-line detail.\n"),
            "restricted")
        derived_text = labels.annotate_derivation(
            restricted_text, [("n-00000000000000f1", "public"),
                               ("n-00000000000000f0", "restricted")],
            "2026-08-30")
        with open(os.path.join(cls.vault, "kestrel-public.md"), "w") as f:
            f.write(public_text)
        with open(os.path.join(cls.vault, "50-Private", "kestrel-derived-secret.md"), "w") as f:
            f.write(derived_text)

        with open(os.path.join(cls.vault, "99-System", "access-policy.json"), "w") as f:
            json.dump({"default": "allow",
                       "rules": [{"identity": "public-only", "path": "50-Private/*",
                                  "action": "deny"}]}, f)

        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env.pop("BM_IDENTITY", None)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        idx = subprocess.run([sys.executable, TOOL, "index", "--vault", cls.vault],
                              env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert idx.returncode == 0, idx.stdout.decode() + idx.stderr.decode()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, identity):
        env = dict(self.env)
        env["BM_IDENTITY"] = identity
        p = subprocess.run([sys.executable, TOOL] + self.QUERY, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def test_public_only_identity_is_withheld_the_derived_restricted_claim(self):
        code, out = self._run("public-only")
        self.assertEqual(code, 0, out)
        self.assertNotIn("unredacted per-line detail", out)
        self.assertNotIn("kestrel-derived-secret", out)
        self.assertIn("withheld by access policy", out)
        self.withheld_line = next(l for l in out.splitlines() if "withheld" in l)

    def test_cleared_identity_is_served_the_same_derived_claim(self):
        code, out = self._run("cleared-analyst")
        self.assertEqual(code, 0, out)
        self.assertIn("kestrel derived secret", out)
        self.assertIn("kestrel-derived-secret.md", out)


# --------------------------------------------------------------------------
# Deliverable 4a: export row carries the label (reuses the sensitivity
# column VB8-04 already ships, no new column).
# --------------------------------------------------------------------------

class ExportRowCarriesTheLabel(VaultFixture):
    def setUp(self):
        super().setUp()
        restricted = labels.apply_label(
            note("n-0000000000000f10",
                 body="\nclaim: the derived restricted finding [evidence: path:x.md]\n"),
            "restricted")
        self._write("Derived.md", restricted)
        self._write("Public.md", note(
            "n-0000000000000f11",
            body="\nclaim: the public finding [evidence: path:y.md]\n"))

    def test_restricted_derived_row_carries_restricted_sensitivity(self):
        rows, _skipped, _ids, _unread, skipped_restricted = export.build_assertions(
            self.vault, include_restricted=True)
        self.assertEqual(skipped_restricted, 0)
        by_id = {r["note_id"]: r for r in rows}
        self.assertEqual(by_id["n-0000000000000f10"]["sensitivity"], "restricted")
        self.assertEqual(by_id["n-0000000000000f11"]["sensitivity"], "standard")

    def test_default_export_excludes_the_restricted_row(self):
        rows, _skipped, _ids, _unread, skipped_restricted = export.build_assertions(
            self.vault, include_restricted=False)
        self.assertEqual(skipped_restricted, 1)
        note_ids = {r["note_id"] for r in rows}
        self.assertNotIn("n-0000000000000f10", note_ids)
        self.assertIn("n-0000000000000f11", note_ids)


# --------------------------------------------------------------------------
# Deliverable 4b: the digest, a downstream surface, excludes a
# restricted-labeled claim from an unauthorized principal's page.
# --------------------------------------------------------------------------

class DigestExcludesForUnauthorizedPrincipal(unittest.TestCase):
    DATE = "2026-08-30"

    def _build(self, owner):
        vault = tempfile.mkdtemp(prefix="bm-vault-labels-digest-")
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        owners_json(vault, {"10-Projects/team": {"owner": owner, "steward": owner}})
        secret = labels.apply_label(frontmatter([
            ("id", "n-0000000000000f20"), ("type", "reference"), ("status", "open"),
            ("created", "2020-01-01"), ("promotion", "candidate"),
            ("provenance_actor", "human-writer"),
        ], body="a restricted derived finding\n"), "restricted")
        write(vault, "10-Projects/team/secret.md", secret)
        write(vault, "10-Projects/team/public.md", frontmatter([
            ("id", "n-0000000000000f21"), ("type", "reference"), ("status", "open"),
            ("created", "2020-01-01"), ("promotion", "candidate"),
            ("provenance_actor", "human-writer"),
        ], body="an ordinary public finding\n"))
        write(vault, os.path.join("99-System", "access-policy.json"), json.dumps(
            {"default": "allow",
             "rules": [{"identity": "blocked", "path": "10-Projects/team/secret.md",
                        "action": "deny"}]}))
        code, digest_paths, _immediate, messages = digest.build(vault, date=self.DATE)
        self.assertEqual(code, 0, messages)
        digest_path = os.path.join(vault, digest_paths[0])
        with open(digest_path, encoding="utf-8") as fh:
            text = fh.read()
        return text, messages

    def test_denied_identity_never_sees_the_restricted_claim(self):
        text, messages = self._build("blocked")
        self.assertNotIn("secret.md", text)
        self.assertIn("public.md", text)
        self.withheld_message = next(m for m in messages if m.startswith("WITHHELD"))
        self.assertRegex(self.withheld_message, r"^WITHHELD \d+ item\(s\)")

    def test_cleared_identity_still_sees_the_same_claim(self):
        text, messages = self._build("cleared")
        self.assertIn("secret.md", text)
        self.assertIn("public.md", text)
        self.assertFalse(any(m.startswith("WITHHELD") for m in messages), messages)


# --------------------------------------------------------------------------
# Deliverable 2, "human approval does not declassify": relabeling a SOURCE is
# its own recorded act, and propagates forward, tightening only, never
# loosening a note already derived from it.
# --------------------------------------------------------------------------

class RelabelAndPropagateTightenNeverLoosen(VaultFixture):
    def test_relabel_source_records_the_change_on_the_source_itself(self):
        text = note("n-0000000000000f30")
        new_text, old_label = labels.relabel_source(text, "restricted", "2026-08-31")
        self.assertEqual(old_label, "public")
        self.assertIn("security_label: restricted", new_text)
        self.assertIn("security_label_changed_from: public", new_text)
        self.assertIn("security_label_changed_at: 2026-08-31", new_text)

    def test_propagate_tightens_a_note_derived_from_the_relabeled_source(self):
        self._write("Derived.md", frontmatter([
            ("id", "n-0000000000000f31"),
            ("derived_from_ids", "n-source-a,n-source-b"),
            ("derived_from_labels", "public,public"),
            ("security_label", "public"), ("restricted", "false"),
        ]))
        touched = labels.propagate(self.vault, "n-source-a", "public", "restricted")
        self.assertEqual(touched, ["Derived.md"])
        text = self._read("Derived.md")
        self.assertEqual(labels.read_label(text), "restricted")
        self.assertIn("derived_from_labels: restricted,public", text)

    def test_propagate_never_loosens_when_a_source_is_relabeled_looser(self):
        self._write("Derived.md", frontmatter([
            ("id", "n-0000000000000f32"),
            ("derived_from_ids", "n-source-a,n-source-b"),
            ("derived_from_labels", "restricted,public"),
            ("security_label", "restricted"), ("restricted", "true"),
        ]))
        touched = labels.propagate(self.vault, "n-source-a", "restricted", "public")
        self.assertEqual(touched, [])
        text = self._read("Derived.md")
        self.assertEqual(labels.read_label(text), "restricted")
        self.assertIn("derived_from_labels: restricted,public", text)


# --------------------------------------------------------------------------
# Deliverable: NO-DATA where the stores this module reads are absent.
# --------------------------------------------------------------------------

class NoDataWhereStoresAreAbsent(unittest.TestCase):
    def test_check_of_an_unreadable_vault_is_no_data(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = labels.cmd_check("/nowhere/at/all/vb3-13")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", buf.getvalue())

    def test_digest_build_fails_closed_on_a_broken_policy_never_open(self):
        vault = tempfile.mkdtemp(prefix="bm-vault-labels-digest-nodata-")
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        owners_json(vault, {})
        write(vault, os.path.join("99-System", "access-policy.json"), "{not json")
        code, digest_paths, immediate_path, messages = digest.build(vault, date="2026-08-30")
        self.assertEqual(code, 2, messages)
        self.assertEqual(digest_paths, [])
        self.assertIsNone(immediate_path)
        self.assertTrue(any("NO-DATA" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
