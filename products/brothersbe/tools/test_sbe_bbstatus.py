#!/usr/bin/env python3
"""Tests for src/brothersbe/bbstatus.py, the Bitbucket build-status client.

Every test injects its own `post`, so nothing here touches the network. The
one path that MUST attempt no request at all (no credential) is asserted by
counting attempts against a sentinel that records them, rather than by
trusting the code's own claim.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import bbstatus  # noqa: E402


class _Recorder(object):
    """A stand-in for the network that records what it was asked to do."""

    def __init__(self, status=201, error=None):
        self.calls = []
        self.status = status
        self.error = error

    def __call__(self, url, header, payload):
        self.calls.append({"url": url, "header": header, "payload": payload})
        return self.status, self.error


class _Env(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("BITBUCKET_TOKEN", "BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class CredentialAbsent(_Env):
    def test_no_credential_is_no_data_with_a_remedy_and_zero_attempts(self):
        rec = _Recorder()
        verdict, sentence = bbstatus.post_status("w/r", "a" * 40, "success", post=rec)
        self.assertEqual("NO-DATA", verdict)
        self.assertEqual([], rec.calls,
                         "a run with no credential must make ZERO network attempts, "
                         "and this one made %d" % len(rec.calls))
        self.assertIn("BITBUCKET_TOKEN", sentence, "a NO-DATA must name its remedy")

    def test_a_username_without_its_password_is_not_a_credential(self):
        os.environ["BITBUCKET_USERNAME"] = "someone"
        rec = _Recorder()
        verdict, _ = bbstatus.post_status("w/r", "a" * 40, "success", post=rec)
        self.assertEqual("NO-DATA", verdict)
        self.assertEqual([], rec.calls)


class Posting(_Env):
    def setUp(self):
        _Env.setUp(self)
        os.environ["BITBUCKET_TOKEN"] = "tkn-do-not-leak"

    def test_a_successful_post_reports_posted_and_names_the_source(self):
        rec = _Recorder(status=201)
        verdict, sentence = bbstatus.post_status("kmaaouni/testbucket", "b" * 40,
                                                 "success", "52/52 gates", post=rec)
        self.assertEqual("POSTED", verdict)
        self.assertEqual(1, len(rec.calls))
        self.assertIn("BITBUCKET_TOKEN", sentence)

    def test_github_state_words_are_translated_not_passed_through(self):
        """Bitbucket rejects GitHub's vocabulary, so the translation is the
        difference between a reported result and a silent 400."""
        for given, expected in (("success", "SUCCESSFUL"), ("failure", "FAILED"),
                                ("error", "FAILED"), ("pending", "INPROGRESS")):
            rec = _Recorder(status=200)
            bbstatus.post_status("w/r", "c" * 40, given, post=rec)
            self.assertEqual(expected, rec.calls[0]["payload"]["state"],
                             "%r must be sent as %r" % (given, expected))

    def test_an_unknown_state_word_is_never_guessed_as_success(self):
        rec = _Recorder(status=200)
        bbstatus.post_status("w/r", "d" * 40, "banana", post=rec)
        self.assertEqual("STOPPED", rec.calls[0]["payload"]["state"],
                         "an unrecognised state must not become SUCCESSFUL")

    def test_the_endpoint_is_the_commit_build_status_resource(self):
        rec = _Recorder(status=201)
        bbstatus.post_status("kmaaouni/testbucket", "e" * 40, "success", post=rec)
        self.assertEqual(
            "https://api.bitbucket.org/2.0/repositories/kmaaouni/testbucket/commit/"
            + "e" * 40 + "/statuses/build",
            rec.calls[0]["url"])

    def test_the_credential_never_appears_in_the_returned_sentence(self):
        rec = _Recorder(status=None, error="connection reset by tkn-do-not-leak")
        _verdict, sentence = bbstatus.post_status("w/r", "f" * 40, "success", post=rec)
        self.assertNotIn("tkn-do-not-leak", sentence,
                         "the secret reached the caller's output")
        self.assertIn("<redacted>", sentence)


class Refusals(_Env):
    def setUp(self):
        _Env.setUp(self)
        os.environ["BITBUCKET_TOKEN"] = "tkn"

    def test_a_rejected_credential_says_what_the_token_needs(self):
        for code in (401, 403):
            verdict, sentence = bbstatus.post_status(
                "w/r", "a" * 40, "success", post=_Recorder(status=code))
            self.assertEqual("NO-DATA", verdict)
            self.assertIn("write access", sentence)

    def test_a_404_names_both_explanations_rather_than_choosing_one(self):
        verdict, sentence = bbstatus.post_status(
            "w/r", "a" * 40, "success", post=_Recorder(status=404))
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("different facts", sentence)

    def test_an_unreachable_network_is_no_data_not_a_crash(self):
        verdict, sentence = bbstatus.post_status(
            "w/r", "a" * 40, "success", post=_Recorder(status=None, error="no route"))
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("nothing was posted", sentence)

    def test_a_missing_repository_or_sha_is_refused_before_any_attempt(self):
        for repo, sha in (("", "a" * 40), ("noslash", "a" * 40), ("w/r", "")):
            rec = _Recorder()
            verdict, _ = bbstatus.post_status(repo, sha, "success", post=rec)
            self.assertEqual("NO-DATA", verdict)
            self.assertEqual([], rec.calls)


class ExitCodes(_Env):
    def test_main_exits_nonzero_when_nothing_was_posted(self):
        self.assertEqual(1, bbstatus.main(["w/r", "a" * 40, "success"]))

    def test_main_refuses_usage_without_enough_arguments(self):
        self.assertEqual(2, bbstatus.main(["only-one"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
