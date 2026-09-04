#!/usr/bin/env python3
"""Calibration for tools/bm_vault_digest.py, WBS row VB11-04: per-person digests
built on bm_vault_route's own routing lanes, the pending-work Bases view, and the
immediate-class bypass for revocations and security findings.

Fixture, two principals:

  alice (domain 10-Projects/alice): a doctor-lint finding on a.md (missing
  "status", human-authored via provenance_actor), a governance queue entry
  classed "revocation" against the SAME a.md (must bypass to the immediate
  notice, never alice's daily digest), and a human-submitted candidate note
  c.md (promotion: candidate, provenance_actor set, no drafting model: a
  pending item with a human drafter, no MODEL: label).

  bob (domain 00-Inbox, where every bm_vault_enrich draft lands): one real
  machine-drafted candidate filed through enrich.file_draft (never a hand
  built imitation of its frontmatter), model="test-model-x": a pending item
  with a MODEL: label and a real diff size.

Driven backwards throughout: every positive assertion (label present, item
routed, item bypassed) has a matching negative one on the fixture's other half
(label absent, item not routed elsewhere, non-urgent item never reaching the
immediate file).

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_digest as digest    # noqa: E402
import bm_vault_enrich as enrich    # noqa: E402

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


def write(vault, relpath, text):
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def owners_json(vault, domains):
    write(vault, os.path.join("99-System", "owners.json"),
          json.dumps({"domains": domains}))


def frontmatter(fields, body="body\n"):
    lines = ["---"] + ["%s: %s" % (k, v) for k, v in fields] + ["---", "", body]
    return "\n".join(lines)


class TwoPeopleFixture(unittest.TestCase):
    DATE = "2026-08-30"

    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="bm-digest-")
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        owners_json(self.vault, {
            "10-Projects/alice": {"owner": "alice", "steward": "alice"},
            "00-Inbox": {"owner": "bob", "steward": "bob"},
            # The governance queue file itself (99-System/telemetry/...) is a
            # real note census/doctor also scan, and it declares no id/owner/
            # created/description of its own; routed to alice so this fixture
            # exercises exactly two principals rather than growing a third
            # "unassigned" bucket from the queue's own incidental findings.
            "99-System": {"owner": "alice", "steward": "alice"},
        })
        # alice, doctor only: BASE_REQUIRED (id, type, status, created) missing
        # "status". Fully compliant with the census contract (owner, description,
        # created present) so census does not also fire, keeping this the one
        # finding this note produces. Carries provenance_actor so its card's
        # drafter identity resolves to a human, never MODEL:.
        write(self.vault, "10-Projects/alice/a.md", frontmatter([
            ("id", "n-0000000000000001"), ("type", "reference"),
            ("created", "2020-01-01"), ("owner", "alice"), ("description", "x"),
            ("provenance_actor", "human-writer-1"),
        ]))
        # The governance review queue, in append_queue()'s own documented
        # format, one WARN line classed "revocation" against a.md: the
        # immediate bypass under test.
        write(self.vault, os.path.join("99-System", "telemetry", "vault-review-queue.md"),
              "---\nstatus: standing\ntype: reference\n---\n\n"
              "2026-08-30T00:00:00Z WARN revocation 10-Projects/alice/a.md "
              "revoked credential detail\n")
        # alice, a human-submitted pending candidate: promotion: candidate,
        # provenance_actor set, no drafting_model anywhere -- the "human
        # drafter, no MODEL: label" half of the reversed assertion.
        write(self.vault, "10-Projects/alice/c.md", frontmatter([
            ("id", "n-0000000000000002"), ("type", "reference"),
            ("status", "open"), ("created", "2020-01-01"),
            ("promotion", "candidate"), ("provenance_actor", "human-writer-2"),
        ], body="a human draft awaiting steward review\nwith a second line\n"))
        # bob's target note for the real enrichment draft below: fully
        # compliant with both census and doctor so it produces no finding of
        # its own, keeping bob's digest to exactly the draft under test.
        write(self.vault, "10-Projects/alice/target.md", frontmatter([
            ("id", "n-0000000000000003"), ("type", "reference"),
            ("status", "open"), ("created", "2020-01-01"),
            ("owner", "alice"), ("description", "x"),
        ]))
        # bob, the real machine-drafted candidate: filed through the actual
        # sibling tool (bm_vault_enrich.file_draft), never a hand-imitated
        # frontmatter block, so this fixture proves against the real writer.
        # It lands under 00-Inbox/, which owners.json routes to bob.
        ok, msg, rel = enrich.file_draft(
            vault=self.vault, note_ident="10-Projects/alice/target.md",
            field="description", value="a machine-drafted description\nsecond line",
            model="test-model-x")
        self.assertTrue(ok, msg)
        self.draft_rel = rel

    def _digest_files(self):
        d = os.path.join(self.vault, "99-System", "digests")
        if not os.path.isdir(d):
            return []
        return sorted(os.listdir(d))

    def _read(self, rel):
        with open(os.path.join(self.vault, rel), encoding="utf-8") as fh:
            return fh.read()


class ExactlyTwoDigestsWithCardFields(TwoPeopleFixture):
    def test_two_people_two_digests(self):
        code, digest_paths, immediate_path, messages = digest.build(
            self.vault, date=self.DATE)
        self.assertEqual(code, 0, messages)
        files = self._digest_files()
        non_immediate = [f for f in files if not f.endswith("-immediate.md")]
        self.assertEqual(len(non_immediate), 2, files)
        self.assertIn("%s-alice.md" % self.DATE, non_immediate)
        self.assertIn("%s-bob.md" % self.DATE, non_immediate)
        self.assertEqual(len(digest_paths), 2, digest_paths)
        self.assertIsNotNone(immediate_path)

        alice_text = self._read("99-System/digests/%s-alice.md" % self.DATE)
        bob_text = self._read("99-System/digests/%s-bob.md" % self.DATE)

        # Every card field present, on both digests.
        for text in (alice_text, bob_text):
            self.assertIn("drafter:", text)
            self.assertIn("evidence:", text)
            self.assertIn("diff size:", text)
            self.assertIn("steward review:", text)

        # alice's pending human draft: diff size computed, human drafter.
        self.assertIn("drafter: human-writer-2", alice_text)
        self.assertIn("candidate awaiting steward review", alice_text)
        # 2 lines, len("a human draft awaiting steward review\nwith a second line") chars
        value = "a human draft awaiting steward review\nwith a second line"
        self.assertIn("diff size: +2 line(s), %d char(s)" % len(value), alice_text)

        # alice's doctor finding: human drafter from provenance_actor, never a model.
        self.assertIn("drafter: human-writer-1", alice_text)
        self.assertNotIn("MODEL:", alice_text)

        # bob's machine draft: MODEL: label at the card's start, real diff size.
        self.assertIn("MODEL: test-model-x", bob_text)
        value2 = "a machine-drafted description\nsecond line"
        self.assertIn("diff size: +2 line(s), %d char(s)" % len(value2), bob_text)
        # its card block starts with the MODEL: line (not buried mid-card).
        lines = bob_text.splitlines()
        model_lines = [i for i, ln in enumerate(lines) if ln.strip() == "MODEL: test-model-x"]
        self.assertTrue(model_lines)
        self.assertEqual(lines[model_lines[0] + 1].strip(), "drafter: test-model-x")


class ModelLabelBothDirections(TwoPeopleFixture):
    """Direct doctored-render check, in addition to the two-fixture check
    above: a MODEL: line appears only when drafter_kind is machine, and is
    absent for the exact same rendering path when it is not."""

    def test_render_card_labels_only_machine_drafts(self):
        model_finding = {"source": "pending", "path": self.draft_rel,
                          "detail": "candidate awaiting steward review"}
        human_finding = {"source": "pending", "path": "10-Projects/alice/c.md",
                          "detail": "candidate awaiting steward review"}
        model_card = "\n".join(digest._render_card(self.vault, model_finding))
        human_card = "\n".join(digest._render_card(self.vault, human_finding))
        self.assertTrue(model_card.startswith("MODEL: test-model-x"))
        self.assertFalse(human_card.startswith("MODEL:"))
        self.assertNotIn("MODEL:", human_card)

        # Doctor the label logic: strip it exactly the way _render_card would
        # if the is_model branch were deleted, and confirm the fixture is
        # sensitive enough that the doctored render loses the assertion.
        doctored = "\n".join(l for l in digest._render_card(self.vault, model_finding)
                              if not l.startswith("MODEL:"))
        self.assertNotIn("MODEL:", doctored)
        self.assertNotEqual(doctored, model_card)


class ImmediateBypass(TwoPeopleFixture):
    def test_urgent_bypasses_daily_digest(self):
        code, _digests, immediate_path, _messages = digest.build(self.vault, date=self.DATE)
        self.assertEqual(code, 0)
        self.assertEqual(immediate_path, "99-System/digests/%s-immediate.md" % self.DATE)
        immediate_text = self._read(immediate_path)
        self.assertIn("class=revocation", immediate_text)
        self.assertIn("10-Projects/alice/a.md", immediate_text)
        self.assertIn("revoked credential detail", immediate_text)

        alice_text = self._read("99-System/digests/%s-alice.md" % self.DATE)
        # The revocation finding never reaches alice's daily digest.
        self.assertNotIn("revoked credential detail", alice_text)
        self.assertNotIn("revocation", alice_text)

    def test_non_urgent_never_reaches_immediate_file(self):
        code, _digests, immediate_path, _messages = digest.build(self.vault, date=self.DATE)
        self.assertEqual(code, 0)
        immediate_text = self._read(immediate_path)
        # alice's doctor finding and pending human draft are not urgent.
        self.assertNotIn("BASE_REQUIRED", immediate_text)
        self.assertNotIn("human-writer-2", immediate_text)
        self.assertNotIn("candidate awaiting steward review", immediate_text)


class NoDataProducesNoFiles(unittest.TestCase):
    def test_empty_vault_is_no_data_never_an_empty_page(self):
        vault = tempfile.mkdtemp(prefix="bm-digest-empty-")
        try:
            owners_json(vault, {})
            code, digest_paths, immediate_path, messages = digest.build(
                vault, date="2026-08-30")
            self.assertEqual(code, 0)
            self.assertEqual(digest_paths, [])
            self.assertIsNone(immediate_path)
            self.assertTrue(any("NO-DATA" in m and "2026-08-30" in m for m in messages),
                             messages)
            digest_dir = os.path.join(vault, "99-System", "digests")
            self.assertFalse(os.path.isdir(digest_dir) and os.listdir(digest_dir))
        finally:
            shutil.rmtree(vault, ignore_errors=True)


class PendingWorkBaseSyntax(unittest.TestCase):
    BASE_PATH = os.path.join(HERE, "..", "vault-template", "99-System", "views",
                              "pending-work.base")

    def test_shipped_base_file_parses(self):
        with open(self.BASE_PATH, encoding="utf-8") as fh:
            text = fh.read()
        ok, problems = digest.check_base_syntax(text)
        self.assertTrue(ok, problems)
        self.assertIn("promotion", text)
        self.assertIn('promotion == "candidate"', text)

    def test_broken_shapes_fail_the_same_check(self):
        ok, problems = digest.check_base_syntax("filters:\n  and:\n    - x\n")
        self.assertFalse(ok)
        self.assertTrue(problems)

        ok, problems = digest.check_base_syntax(
            "views:\n  - type: table\n    order:\n      - file.name\n")
        self.assertFalse(ok)
        self.assertTrue(any("name" in p for p in problems))

        ok, problems = digest.check_base_syntax(
            "filters:\n  - x\nviews:\n  - type: table\n    name: X\n")
        self.assertFalse(ok)
        self.assertTrue(any("and" in p or "or" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
