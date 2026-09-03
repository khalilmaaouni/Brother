#!/usr/bin/env python3
"""Unit tests for scripts/verify_advisor.py. No port is bound here."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_advisor as va  # noqa: E402


class TestVerdicts(unittest.TestCase):
    def test_exit_codes_map_to_verdict_words(self):
        self.assertEqual(va.verdict_for_exit(0), "PASS")
        self.assertEqual(va.verdict_for_exit(1), "FAIL")
        self.assertEqual(va.verdict_for_exit(2), "NO-DATA")
        self.assertEqual(va.verdict_for_exit(127), "FAIL")

    def test_run_check_follows_child_exit_code(self):
        checks = {"ok": ["sh", "-c", "echo fine; exit 0"],
                  "bad": ["sh", "-c", "echo broken; exit 1"],
                  "nodata": ["sh", "-c", "exit 2"]}
        self.assertEqual(va.run_check("ok", ".", checks)["verdict"], "PASS")
        bad = va.run_check("bad", ".", checks)
        self.assertEqual((bad["verdict"], bad["exit_code"]), ("FAIL", 1))
        self.assertIn("broken", bad["tail"])
        self.assertEqual(va.run_check("nodata", ".", checks)["verdict"], "NO-DATA")

    def test_missing_command_is_nodata_never_a_pass(self):
        checks = {"ghost": ["/no/such/binary/anywhere-xyz"]}
        res = va.run_check("ghost", ".", checks)
        self.assertEqual(res["verdict"], "NO-DATA")
        self.assertIsNone(res["exit_code"])

    def test_unknown_name_is_nodata_and_runs_nothing(self):
        res = va.run_check("not-configured", ".", {"ok": ["true"]})
        self.assertEqual(res["verdict"], "NO-DATA")


class TestEscaping(unittest.TestCase):
    def test_diff_html_renders_script_tags_inert(self):
        real = va.git_text
        va.git_text = lambda repo, args: '<script>alert("x")</script>'
        try:
            frag = va.render_diff_html(".")
        finally:
            va.git_text = real
        self.assertNotIn("<script>", frag)
        self.assertIn("&lt;script&gt;", frag)


class TestMailbox(unittest.TestCase):
    def test_question_file_shape_round_trips(self):
        with tempfile.TemporaryDirectory() as box:
            qid = va.write_question(box, "is this safe to commit?", ".")
            path = os.path.join(box, "q-%s.json" % qid)
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(data["question"], "is this safe to commit?")
            self.assertIn("diff_stat", data["context"])
            self.assertIn("last_check", data["context"])

    def test_answer_file_is_served_once_present(self):
        with tempfile.TemporaryDirectory() as box:
            self.assertEqual(va.read_answers(box), {})
            with open(os.path.join(box, "a-123-0001.json"), "w") as fh:
                json.dump({"answer": "looks good"}, fh)
            answers = va.read_answers(box)
            self.assertEqual(answers["123-0001"]["answer"], "looks good")

    def test_half_written_answer_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as box:
            with open(os.path.join(box, "a-9-0.json"), "w") as fh:
                fh.write('{"answer": "trunc')
            self.assertEqual(va.read_answers(box), {})

    def test_first_run_writes_mailbox_readme(self):
        with tempfile.TemporaryDirectory() as parent:
            box = os.path.join(parent, "mb")
            va.ensure_mailbox(box)
            self.assertTrue(os.path.exists(os.path.join(box, "README.md")))


class FakeLC:
    """Stands in for bm_vault_lifecycle: candidate state is decided by a
    literal 'promotion: candidate' line, same field the real contract reads."""

    @staticmethod
    def walk(vault):
        return sorted(os.path.join(vault, f) for f in os.listdir(vault)
                      if f.endswith(".md"))

    @staticmethod
    def read_promotion(text):
        state = "candidate" if "promotion: candidate" in text else "legacy"
        return state, {}, []


class TestListCandidates(unittest.TestCase):
    def test_no_vault_is_error(self):
        res = va.list_candidates(None, "/whatever")
        self.assertIn("error", res)

    def test_no_tools_root_is_error(self):
        with tempfile.TemporaryDirectory() as vault:
            res = va.list_candidates(vault, None)
            self.assertIn("error", res)

    def test_filters_to_candidate_state_and_reads_title(self):
        with tempfile.TemporaryDirectory() as vault:
            with open(os.path.join(vault, "a.md"), "w") as fh:
                fh.write("---\npromotion: candidate\n---\n\n# Note A\n")
            with open(os.path.join(vault, "b.md"), "w") as fh:
                fh.write("---\npromotion: legacy\n---\n\n# Note B\n")
            res = va.list_candidates(vault, "/unused", _lc=FakeLC)
            self.assertEqual(len(res["items"]), 1)
            self.assertEqual(res["items"][0]["path"], "a.md")
            self.assertEqual(res["items"][0]["title"], "Note A")


class TestCandidatesHtmlEscaping(unittest.TestCase):
    def test_title_and_path_are_escaped(self):
        fake = {"items": [{"path": "x/<script>.md",
                            "title": "<script>alert(1)</script>"}]}
        real = va.list_candidates
        va.list_candidates = lambda vault, tools_root: fake
        try:
            frag = va.render_candidates_html("v", "t")
        finally:
            va.list_candidates = real
        self.assertNotIn("<script>alert", frag)
        self.assertIn("&lt;script&gt;", frag)

    def test_error_is_escaped(self):
        real = va.list_candidates
        va.list_candidates = lambda vault, tools_root: {"error": "<bad>"}
        try:
            frag = va.render_candidates_html("v", "t")
        finally:
            va.list_candidates = real
        self.assertIn("&lt;bad&gt;", frag)

    def test_no_candidates_says_so_plainly(self):
        real = va.list_candidates
        va.list_candidates = lambda vault, tools_root: {"items": []}
        try:
            frag = va.render_candidates_html("v", "t")
        finally:
            va.list_candidates = real
        self.assertIn("No candidates waiting", frag)


class TestCandidateAction(unittest.TestCase):
    """These tests are the calibration for the token gate: comment out the
    secrets.compare_digest check in candidate_action and
    test_validate_without_token_is_refused_and_runs_nothing is the one that
    goes red."""

    def setUp(self):
        self._real_list = va.list_candidates
        va.list_candidates = lambda vault, tools_root: {
            "items": [{"path": "40-Failures/x.md", "title": "X"}]}

    def tearDown(self):
        va.list_candidates = self._real_list

    def test_validate_without_token_is_refused_and_runs_nothing(self):
        with mock.patch("verify_advisor.subprocess.run") as run:
            res = va.candidate_action(
                "validated", {"path": "40-Failures/x.md"},
                "the-real-token", "/vault", "/tools")
        self.assertIn("error", res)
        run.assert_not_called()

    def test_wrong_token_is_refused_and_runs_nothing(self):
        with mock.patch("verify_advisor.subprocess.run") as run:
            res = va.candidate_action(
                "validated", {"path": "40-Failures/x.md", "token": "nope"},
                "the-real-token", "/vault", "/tools")
        self.assertIn("error", res)
        run.assert_not_called()

    def test_unknown_path_is_refused_and_runs_nothing(self):
        with mock.patch("verify_advisor.subprocess.run") as run:
            res = va.candidate_action(
                "validated", {"path": "not/a/candidate.md", "token": "the-real-token"},
                "the-real-token", "/vault", "/tools")
        self.assertIn("error", res)
        run.assert_not_called()

    def test_validate_with_token_invokes_exact_promote_argv(self):
        with mock.patch("verify_advisor.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="promoted", stderr="")
            res = va.candidate_action(
                "validated", {"path": "40-Failures/x.md", "token": "the-real-token"},
                "the-real-token", "/vault", "/tools")
        self.assertTrue(res["ok"])
        cmd = run.call_args[0][0]
        self.assertEqual(cmd, [
            "python3", os.path.join("/tools", "tools", "bm_vault_promotions.py"),
            "promote", "--vault", "/vault", "--id", "40-Failures/x.md",
            "--to", "validated", "--by", "Khalil Maaouni, via Verify Advisor",
            "--apply"])

    def test_reject_with_token_invokes_reject_argv(self):
        with mock.patch("verify_advisor.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="rejected", stderr="")
            va.candidate_action(
                "rejected", {"path": "40-Failures/x.md", "token": "the-real-token"},
                "the-real-token", "/vault", "/tools")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--to") + 1], "rejected")

    def test_tool_failure_is_not_ok(self):
        with mock.patch("verify_advisor.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="REFUSED")
            res = va.candidate_action(
                "validated", {"path": "40-Failures/x.md", "token": "the-real-token"},
                "the-real-token", "/vault", "/tools")
        self.assertFalse(res["ok"])
        self.assertIn("REFUSED", res["tail"])


if __name__ == "__main__":
    unittest.main()
