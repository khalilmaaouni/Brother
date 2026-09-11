#!/usr/bin/env python3
"""The cutover pack's own checks, driven backwards as well as forwards.

A measuring instrument needs an adversary before its verdict is quoted. The
refresh gate is the load bearing piece of the cutover pack (it is what keeps a
snapshot honest after the estate moves), so every one of its three verdicts is
driven here, including the two that are not the happy path.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "refresh_cutover_state.py")
sys.path.insert(0, HERE)
import cutover_pack as CP  # noqa: E402
import refresh_cutover_state as RG  # noqa: E402


def run_gate(*args):
    p = subprocess.run([sys.executable, GATE] + list(args),
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout


class GateVerdicts(unittest.TestCase):
    def test_missing_plane_is_no_data_never_a_pass(self):
        rc, out = run_gate("--plane", "/nonexistent/plane.json")
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)

    def test_unreadable_plane_is_no_data(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            f.write("{ this is not json")
            bad = f.name
        try:
            rc, out = run_gate("--plane", bad)
            self.assertEqual(rc, 2, out)
            self.assertIn("NO-DATA", out)
        finally:
            os.unlink(bad)

    def test_plane_with_no_existing_repo_is_no_data(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            json.dump({"repos": {"/nonexistent/repo": {"head": "a" * 40}}}, f)
            plane = f.name
        try:
            rc, out = run_gate("--plane", plane)
            self.assertEqual(rc, 2, out)
        finally:
            os.unlink(plane)


class DriftDetection(unittest.TestCase):
    """diff_against is pure over its two inputs, so drift is driven directly
    without needing a live repository."""

    def _plane(self, **over):
        base = {
            "repos": {"/r": {"head": "a" * 40, "tags_newest": ["v1.0.0"]}},
            "pull_requests": {"open": [{"number": 1}, {"number": 2}]},
        }
        base.update(over)
        return base

    def test_identical_state_is_no_drift(self):
        p = self._plane()
        live = {"repos": {"/r": {"head": "a" * 40, "tags_newest": ["v1.0.0"]}},
                "pull_requests": {"open": [{"number": 1}, {"number": 2}]}}
        self.assertEqual(RG.diff_against(p, live), [])

    def test_moved_head_is_named(self):
        p = self._plane()
        live = {"repos": {"/r": {"head": "b" * 40, "tags_newest": ["v1.0.0"]}},
                "pull_requests": {"open": [{"number": 1}, {"number": 2}]}}
        drift = RG.diff_against(p, live)
        self.assertTrue(any("HEAD moved" in d for d in drift), drift)

    def test_new_tag_is_named(self):
        p = self._plane()
        live = {"repos": {"/r": {"head": "a" * 40,
                                 "tags_newest": ["v1.0.1", "v1.0.0"]}},
                "pull_requests": {"open": [{"number": 1}, {"number": 2}]}}
        drift = RG.diff_against(p, live)
        self.assertTrue(any("v1.0.1" in d for d in drift), drift)

    def test_closed_and_opened_pull_requests_are_both_named(self):
        p = self._plane()
        live = {"repos": {"/r": {"head": "a" * 40, "tags_newest": ["v1.0.0"]}},
                "pull_requests": {"open": [{"number": 2}, {"number": 3}]}}
        drift = RG.diff_against(p, live)
        joined = " ".join(drift)
        self.assertIn("1", joined)
        self.assertIn("3", joined)

    def test_unreadable_live_pull_requests_is_drift_not_silence(self):
        """An unreadable pull request list and an empty one look identical to
        a reader and mean opposite things, so the unreadable case must speak."""
        p = self._plane()
        live = {"repos": {"/r": {"head": "a" * 40, "tags_newest": ["v1.0.0"]}},
                "pull_requests": {"error": "NO-DATA: gh missing"}}
        drift = RG.diff_against(p, live)
        self.assertTrue(any("NO-DATA" in d for d in drift), drift)


class SelectionRule(unittest.TestCase):
    def test_absent_status_is_not_an_open_status(self):
        """The first build copied 61 of 61 decision records because a missing
        status was read as open. A selection rule that selects everything is
        not a selection rule."""
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "old-2020-01-01.json"), "w") as f:
            json.dump({"title": "no status field here"}, f)
        with open(os.path.join(d, "live-2020-01-01.json"), "w") as f:
            json.dump({"status": "open"}, f)
        picked, skipped = CP.select_decisions(d, "2099-12-31")
        names = [os.path.basename(p) for p in picked]
        self.assertIn("live-2020-01-01.json", names)
        self.assertNotIn("old-2020-01-01.json", names)
        self.assertEqual(skipped, 1)

    def test_today_dated_record_is_kept_whatever_its_status(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "x-2026-09-10.json"), "w") as f:
            json.dump({"status": "closed"}, f)
        picked, _ = CP.select_decisions(d, "2026-09-10")
        self.assertEqual(len(picked), 1)


class VaultPrivacyScreen(unittest.TestCase):
    """index_vault carries the privacy property of this whole pack, so it is
    driven backwards: a note whose FILENAME names a private term must not
    appear in the index, and the drop must be counted rather than silent.

    Written after the fact, and that is the finding: the screen shipped with no
    test, and the defect it fixes (a client term reaching a pack through a note
    title) was found by the ceremony enforcer, not by this suite."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.terms = os.path.join(self.vault, "_terms.txt")
        with open(self.terms, "w") as f:
            f.write("# one short term, one long term\nACME\nContosoCorp\n")

    def _note(self, relpath):
        full = os.path.join(self.vault, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write("# a note\n")
        return full

    def test_private_term_in_filename_is_excluded_and_counted(self):
        self._note("notes/ordinary-note.md")
        self._note("notes/the-ACME-migration.md")
        text, kept, dropped = CP.index_vault(self.vault, terms_path=self.terms)
        self.assertIsNotNone(text)
        self.assertEqual(kept, 1)
        self.assertEqual(dropped, 1)
        self.assertNotIn("ACME", text)
        self.assertIn("ordinary-note", text)

    def test_private_term_in_folder_name_is_excluded(self):
        self._note("ContosoCorp/a-note.md")
        text, kept, dropped = CP.index_vault(self.vault, terms_path=self.terms)
        self.assertEqual(kept, 0)
        self.assertEqual(dropped, 1)
        self.assertNotIn("ContosoCorp", text)

    def test_short_term_matches_only_as_a_whole_word(self):
        """A four character term must not swallow every word containing it,
        or the index would drop most of the vault and call that privacy."""
        self._note("notes/ACMEISH-not-the-client.md")
        text, kept, dropped = CP.index_vault(self.vault, terms_path=self.terms)
        self.assertEqual(kept, 1, "a short term matched inside a longer word")
        self.assertEqual(dropped, 0)

    def test_unreadable_terms_list_refuses_to_index_rather_than_pass(self):
        """No screen means no index. An unscreened index is the failure mode
        this function exists to prevent, so absence of terms is not clean."""
        text, kept, dropped = CP.index_vault(
            self.vault, terms_path="/nonexistent/terms.txt")
        self.assertIsNone(text)
        self.assertEqual(kept, 0)


class PrivateTermScanBeforeZip(unittest.TestCase):
    """cutover_pack.py is a cross-account handover artifact: decision and
    plan content is copied verbatim, including from arbitrary remote
    branches, and none of those copy paths ran the estate's own
    private-term scan before this fix. build() itself is faked here with a
    fixture pack, because the real build() walks live home directories
    (~/Documents/Kay Vault, ~/Documents/BrotherModeUp-handovers) that a
    test must never touch."""

    def setUp(self):
        self.out_dir = tempfile.mkdtemp()
        self.terms = os.path.join(self.out_dir, "_terms.txt")
        with open(self.terms, "w") as f:
            f.write("ACMESECRET\n")
        self.pack = os.path.join(self.out_dir,
                                 "2099-01-01-CUTOVER-1.0.13-pack")
        os.makedirs(self.pack)

    def _plant_decision(self, body):
        with open(os.path.join(self.pack, "decision-x.json"), "w") as f:
            f.write(body)

    def _run_main(self):
        meta = {"problems": [], "decisions_copied": 0,
                "decisions_from_branches": 0, "decisions_unmerged": [],
                "decisions_indexed_only": 0, "plans_copied": 0,
                "plans_indexed_only": 0,
                "counts": {"packs": 0, "plans": 0, "vault": 0,
                          "vault_excluded_private": 0}}
        buf = io.StringIO()
        with unittest.mock.patch.object(
                CP, "build", return_value=(self.pack, {}, meta)), \
             unittest.mock.patch.object(CP.HPS, "TERMS_FILE", self.terms), \
             contextlib.redirect_stdout(buf):
            rc = CP.main(["--repo", CP.ROOT, "--out-dir", self.out_dir])
        return rc, buf.getvalue()

    def test_private_term_in_planted_decision_refuses_before_zip(self):
        self._plant_decision(
            '{"note": "escalate to ACMESECRET before the cut"}')
        rc, out = self._run_main()
        self.assertEqual(rc, 1, out)
        self.assertFalse(os.path.isfile(self.pack + ".zip"),
                         "a hit must never reach the zip step")
        self.assertNotIn("ACMESECRET", out,
                         "the matched value must never be printed")
        self.assertIn("REFUSED", out)
        self.assertIn("decision-x.json", out)

    def test_clean_pack_scans_and_still_zips(self):
        self._plant_decision('{"note": "nothing private here"}')
        rc, out = self._run_main()
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(self.pack + ".zip"))
        self.assertIn("OK:", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
