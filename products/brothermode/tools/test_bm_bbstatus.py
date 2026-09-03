#!/usr/bin/env python3
"""Suite for tools/bm_bbstatus.py, the host-aware reporting arm of
scripts/local-gates.sh, and for the routing contract that script holds.

Standard library only. Run: python3 tools/test_bm_bbstatus.py

WHAT THIS SUITE DEFENDS, in the order the failures would hurt:

  1. Classification is right on the URL shapes both hosts actually accept,
     and everything unreadable is unknown, never a guess.
  2. A post with no credential, an unreadable URL, or a refusing API
     REPORTS its reason and never raises: the runner's contract is that a
     reporting failure on Bitbucket or an unknown host cannot change the
     gate's exit status, and a tool that raises would change it.
  3. No network request is even ATTEMPTED before the credential and URL
     checks pass. Proven with a recording opener, not assumed.
  4. The runner's GitHub arm is byte-for-byte the historical one, exit 2 on
     a failed POST included, and its Bitbucket and unknown arms contain no
     exit at all. Proven against scripts/local-gates.sh's own text, because
     that text is the control being trusted.
  5. The credential never reaches a returned message, even when the
     transport hands back text this module did not compose (a URLError
     reason). Proven by making the recording opener raise an error whose
     text CONTAINS the secret, then asserting the secret is gone and
     `<redacted>` stands in its place, for both credential shapes.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(HERE, "bm_bbstatus.py")
GATES = os.path.join(ROOT, "scripts", "local-gates.sh")

_spec = importlib.util.spec_from_file_location("bm_bbstatus", TOOL)
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


class RecordingOpener(object):
    """Stands in for urllib.request.urlopen: records the request it was
    given and answers with a canned status code, so a test can assert on
    the request without any network existing at all."""

    def __init__(self, status=201, error=None):
        self.status = status
        self.error = error
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error

        class _Response(object):
            status = self.status

            def getcode(self):
                return self.status
        return _Response()


class TestClassify(unittest.TestCase):

    def test_github_https_and_ssh_forms(self):
        for url in ("https://github.com/khalilmaaouni/BrotherModeUp.git",
                    "git@github.com:khalilmaaouni/BrotherModeUp.git",
                    "ssh://git@github.com/khalilmaaouni/BrotherModeUp.git",
                    "HTTPS://GITHUB.COM/owner/repo"):
            self.assertEqual(bb.classify(url), "github", url)

    def test_bitbucket_https_and_ssh_forms(self):
        for url in ("https://bitbucket.org/kmaaouni/brothermodeup.git",
                    "git@bitbucket.org:kmaaouni/brothermodeup.git",
                    "ssh://git@bitbucket.org/kmaaouni/brothermodeup.git",
                    "https://user@bitbucket.org/kmaaouni/repo"):
            self.assertEqual(bb.classify(url), "bitbucket", url)

    def test_everything_else_is_unknown_never_a_guess(self):
        for url in ("https://gitlab.com/owner/repo.git",
                    "git@codeberg.org:owner/repo.git",
                    "https://dev.azure.com/org/project/_git/repo",
                    "/home/user/a/local/path",
                    "not a url at all",
                    ""):
            self.assertEqual(bb.classify(url), "unknown", repr(url))

    def test_a_lookalike_host_is_not_promoted(self):
        """github.com.evil.example must not classify as github: the suffix
        match is anchored to the real registrable names, and this is the
        case that would misroute a verdict to a stranger's API."""
        for url in ("https://github.com.evil.example/owner/repo",
                    "git@bitbucket.org.evil.example:owner/repo.git",
                    "https://mygithub.com/owner/repo"):
            self.assertEqual(bb.classify(url), "unknown", url)

    def test_the_cli_prints_the_kind_and_exits_zero(self):
        for url, expected in (
                ("https://github.com/o/r.git", "github"),
                ("git@bitbucket.org:w/r.git", "bitbucket"),
                ("", "unknown")):
            r = subprocess.run(
                [sys.executable, TOOL, "classify", url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), expected)


class TestWorkspaceRepo(unittest.TestCase):

    def test_https_and_ssh_paths_yield_the_pair(self):
        self.assertEqual(
            bb.workspace_repo("https://bitbucket.org/kmaaouni/bmup.git"),
            ("kmaaouni", "bmup"))
        self.assertEqual(
            bb.workspace_repo("git@bitbucket.org:kmaaouni/bmup.git"),
            ("kmaaouni", "bmup"))
        self.assertEqual(
            bb.workspace_repo("ssh://git@bitbucket.org/ws/repo"),
            ("ws", "repo"))

    def test_a_path_with_no_pair_is_none(self):
        for url in ("https://bitbucket.org/onlyworkspace",
                    "https://bitbucket.org/", "", "garbage"):
            self.assertIsNone(bb.workspace_repo(url), repr(url))


class TestSlug(unittest.TestCase):
    """The address the runner's GitHub arm posts to. The runner captures
    this command's STDOUT directly into that address, so what it does NOT
    print on a refusal matters as much as what it prints on success."""

    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, TOOL, "slug"] + list(argv),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=60)

    def test_both_url_forms_give_owner_slash_repo(self):
        for url in ("https://github.com/khalilmaaouni/BrotherModeUp.git",
                    "git@github.com:khalilmaaouni/BrotherModeUp.git",
                    "ssh://git@github.com/khalilmaaouni/BrotherModeUp",
                    "https://github.com/khalilmaaouni/BrotherModeUp"):
            r = self._run(url)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(),
                             "khalilmaaouni/BrotherModeUp", url)

    def test_a_fork_resolves_to_the_fork_and_not_the_canonical_repo(self):
        """THE WHOLE POINT. With the old hardcoded target, a fork's battery
        posted its verdict onto the canonical repository's commits."""
        r = self._run("https://github.com/someone-else/BrotherModeUp.git")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "someone-else/BrotherModeUp")
        self.assertNotIn("khalilmaaouni", r.stdout)

    def test_a_url_it_cannot_split_prints_nothing_on_stdout(self):
        for url in ("https://github.com/onlyanowner", "https://github.com/",
                    "", "garbage"):
            r = self._run(url)
            self.assertEqual(r.returncode, 3, url)
            self.assertEqual(
                r.stdout, "",
                "a refusal printed %r on stdout, which the runner would "
                "have posted to" % r.stdout)
            self.assertIn("does not split into owner/repo", r.stderr)

    def test_the_wrong_argument_count_is_a_usage_error_not_a_refusal(self):
        for argv in ((), ("a", "b")):
            r = self._run(*argv)
            self.assertEqual(r.returncode, 2, argv)
            self.assertEqual(r.stdout, "")

    def test_it_is_the_same_parser_the_bitbucket_post_uses(self):
        """One parser, verified by agreement rather than by claiming it.
        A second parser for the GitHub side is the thing this avoids."""
        for url in ("https://github.com/o/r.git", "git@bitbucket.org:w/p",
                    "ssh://git@github.com/a/b"):
            pair = bb.workspace_repo(url)
            r = self._run(url)
            self.assertEqual(r.stdout.strip(), "%s/%s" % pair, url)


class TestWindowsNewlineTranslation(unittest.TestCase):
    """WINDOWS, reproduced rather than reasoned about.

    Python opens sys.stdout as a text stream with newline=None, and on
    Windows that translates every "\n" written into "\r\n". This suite
    cannot run on Windows, so it installs EXACTLY that policy on a stand-in
    stream and checks the bytes that come out. Everything stdout carries
    here is a value another program parses: scripts/local-gates.sh captures
    `classify` into HOST_KIND and `slug` into POST_TARGET with $(...), and
    command substitution strips trailing NEWLINES but never a carriage
    return, so a stray "\r" reaches the URL rather than being swallowed."""

    def _emit(self, call):
        """Run `call` with a Windows-policy stdout; give back raw bytes."""
        raw = io.BytesIO()
        win = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
        real = sys.stdout
        sys.stdout = win
        try:
            call()
            win.flush()
        finally:
            sys.stdout = real
        return raw.getvalue()

    def test_slug_emits_no_carriage_return(self):
        out = self._emit(
            lambda: bb.cmd_slug(["https://github.com/kay/BrotherModeUp.git"]))
        self.assertNotIn(
            b"\r", out,
            "slug emitted %r under Windows newline policy; the runner "
            "captures this straight into the repository it POSTs to" % out)
        self.assertEqual(out, b"kay/BrotherModeUp\n")

    def test_classify_emits_no_carriage_return(self):
        out = self._emit(
            lambda: bb.cmd_classify(["git@bitbucket.org:team/repo.git"]))
        self.assertNotIn(b"\r", out)
        self.assertEqual(out, b"bitbucket\n")

    def test_the_shell_capture_of_a_windows_slug_is_still_clean(self):
        """The failure this pins, end to end. $(...) removes trailing
        newlines only, so before the fix POST_TARGET held "owner/repo\r"
        and the arm built repos/owner/repo\r/statuses/<sha>. Verified in
        bash: a 27 character slug came back 28 characters long."""
        out = self._emit(
            lambda: bb.cmd_slug(["https://github.com/kay/BrotherModeUp.git"]))
        captured = out.decode("utf-8").rstrip("\n")
        self.assertEqual(captured, "kay/BrotherModeUp")
        self.assertNotIn("\r", captured)
        self.assertEqual("repos/%s/statuses/abc" % captured,
                         "repos/kay/BrotherModeUp/statuses/abc")

    def test_a_refusal_still_prints_nothing_at_all_on_windows(self):
        out = self._emit(lambda: bb.cmd_slug(["https://github.com/lonely"]))
        self.assertEqual(
            out, b"",
            "a refusal put %r on a Windows stdout; empty is the contract "
            "because the runner posts whatever it captures" % out)


class TestPostStatus(unittest.TestCase):

    URL = "https://bitbucket.org/kmaaouni/brothermodeup.git"
    SHA = "0123456789abcdef0123456789abcdef01234567"

    def test_no_credential_names_both_shapes_and_attempts_nothing(self):
        opener = RecordingOpener()
        ok, message = bb.post_status(
            self.URL, self.SHA, "success", "gate green", "local-gates",
            "", {}, opener)
        self.assertFalse(ok)
        self.assertIn("BITBUCKET_TOKEN", message)
        self.assertIn("BITBUCKET_APP_PASSWORD", message)
        self.assertEqual(opener.requests, [],
                         "a request was attempted with no credential")

    def test_an_unreadable_url_is_refused_before_any_request(self):
        opener = RecordingOpener()
        ok, message = bb.post_status(
            "https://bitbucket.org/nopair", self.SHA, "success", "d",
            "local-gates", "", {"BITBUCKET_TOKEN": "tok"}, opener)
        self.assertFalse(ok)
        self.assertIn("workspace/repository", message)
        self.assertEqual(opener.requests, [])

    def test_a_bad_state_is_refused_by_name(self):
        opener = RecordingOpener()
        ok, message = bb.post_status(
            self.URL, self.SHA, "greenish", "d", "local-gates", "",
            {"BITBUCKET_TOKEN": "tok"}, opener)
        self.assertFalse(ok)
        self.assertIn("greenish", message)
        self.assertEqual(opener.requests, [])

    def test_a_token_posts_bearer_to_the_commit_statuses_endpoint(self):
        opener = RecordingOpener(status=201)
        ok, message = bb.post_status(
            self.URL, self.SHA, "success", "247 tests, ALL GREEN",
            "local-gates", "", {"BITBUCKET_TOKEN": "sekrit"}, opener)
        self.assertTrue(ok, message)
        request, timeout = opener.requests[0]
        self.assertEqual(
            request.full_url,
            "https://api.bitbucket.org/2.0/repositories/kmaaouni/"
            "brothermodeup/commit/%s/statuses/build" % self.SHA)
        self.assertEqual(request.get_header("Authorization"),
                         "Bearer sekrit")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["key"], "local-gates")
        self.assertEqual(payload["state"], "SUCCESSFUL")
        self.assertEqual(payload["description"], "247 tests, ALL GREEN")
        self.assertTrue(payload["url"].startswith("https://bitbucket.org/"))
        self.assertEqual(timeout, bb.TIMEOUT_SECONDS)

    def test_username_and_app_password_post_basic(self):
        opener = RecordingOpener(status=200)
        ok, _message = bb.post_status(
            self.URL, self.SHA, "failure", "2 SUITE(S) FAILED",
            "local-gates", "", {"BITBUCKET_USERNAME": "kay",
                                "BITBUCKET_APP_PASSWORD": "pw"}, opener)
        self.assertTrue(ok)
        request, _timeout = opener.requests[0]
        auth = request.get_header("Authorization")
        self.assertTrue(auth and auth.startswith("Basic "), auth)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["state"], "FAILED")

    def test_an_http_refusal_is_a_message_not_an_exception(self):
        error = urllib.error.HTTPError(
            "https://api.bitbucket.org/x", 401, "Unauthorized", {}, None)
        opener = RecordingOpener(error=error)
        ok, message = bb.post_status(
            self.URL, self.SHA, "success", "d", "local-gates", "",
            {"BITBUCKET_TOKEN": "tok"}, opener)
        self.assertFalse(ok)
        self.assertIn("401", message)

    def test_a_dead_network_is_a_message_not_an_exception(self):
        error = urllib.error.URLError("no route to host")
        opener = RecordingOpener(error=error)
        ok, message = bb.post_status(
            self.URL, self.SHA, "success", "d", "local-gates", "",
            {"BITBUCKET_TOKEN": "tok"}, opener)
        self.assertFalse(ok)
        self.assertIn("no route to host", message)

    def test_a_bearer_token_leaked_into_an_error_comes_back_redacted(self):
        """Law 2. The opener is made to raise an error whose text CONTAINS
        the token, standing in for whatever a transport might one day echo
        back (a proxy diagnostic, a verbose URLError reason). The token
        must never survive into the message this function returns."""
        secret = "sekrit-token-should-never-print"
        error = urllib.error.URLError(
            "could not connect (sent Authorization: Bearer %s)" % secret)
        opener = RecordingOpener(error=error)
        ok, message = bb.post_status(
            self.URL, self.SHA, "success", "d", "local-gates", "",
            {"BITBUCKET_TOKEN": secret}, opener)
        self.assertFalse(ok)
        self.assertNotIn(secret, message)
        self.assertIn("<redacted>", message)

    def test_an_app_password_leaked_into_an_error_comes_back_redacted(self):
        """Same law, the Basic-auth shape: the app password is the sensitive
        half and must be scrubbed; the username is not sensitive and is not
        asserted either way here."""
        secret = "app-password-should-never-print"
        error = urllib.error.URLError(
            "could not connect (sent Authorization: Basic ...%s...)"
            % secret)
        opener = RecordingOpener(error=error)
        ok, message = bb.post_status(
            self.URL, self.SHA, "success", "d", "local-gates", "",
            {"BITBUCKET_USERNAME": "kay", "BITBUCKET_APP_PASSWORD": secret},
            opener)
        self.assertFalse(ok)
        self.assertNotIn(secret, message)
        self.assertIn("<redacted>", message)

    def test_the_cli_post_without_credentials_exits_nonzero_with_the_reason(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("BITBUCKET_")}
        r = subprocess.run(
            [sys.executable, TOOL, "post", "--url", self.URL, "--sha",
             self.SHA, "--state", "success", "--description", "d"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BITBUCKET_TOKEN", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestTheRunnersRoutingContract(unittest.TestCase):
    """scripts/local-gates.sh is the control this tool serves, so its text
    is what these tests read. The GitHub arm's nonzero return is load
    bearing for sessions on the canonical checkout and must survive every
    edit to it; the Bitbucket and unknown arms must never be able to change
    the gate's exit."""

    def setUp(self):
        self.text = _read(GATES)

    def test_the_script_parses(self):
        r = subprocess.run(["bash", "-n", GATES],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_runner_routes_on_this_tools_classification(self):
        self.assertIn("bm_bbstatus.py", self.text)
        self.assertIn("classify", self.text)
        self.assertIn('case "$HOST_KIND" in', self.text)

    def test_the_github_arm_keeps_exit_2_on_a_failed_post(self):
        self.assertIn('gh api -X POST "repos/$POST_TARGET/statuses/$SHA"',
                      self.text)
        self.assertIn('|| { echo "POST FAILED: the result above still '
                      'stands, it just was not reported."; exit 2; }',
                      self.text)

    def test_the_runner_holds_no_hardcoded_repository_name(self):
        """THE FORK BUG, pinned so it cannot come back. A literal target
        posts one tree's verdict onto another tree's commits the moment
        this script is copied or forked, and it read as correct for as long
        as it was only ever run on the canonical checkout."""
        self.assertNotIn(
            "khalilmaaouni/BrotherModeUp", self.text,
            "the runner names a repository literally again; the post "
            "target has to come from this checkout's own origin remote")

    def test_the_post_target_is_parsed_by_this_tool_not_a_second_parser(self):
        self.assertIn("bm_bbstatus.py slug", self.text)
        self.assertIn('POST_TARGET="$(python3 tools/bm_bbstatus.py slug',
                      self.text)

    def test_the_github_arm_refuses_a_target_it_could_not_parse(self):
        arm = self._arm("github", code_only=True)
        self.assertIn('if [ -z "$POST_TARGET" ]; then', arm)
        self.assertIn("REFUSED to post", arm)
        self.assertIn("exit 2", arm)
        self.assertNotIn(
            "gh api", arm.split('if [ -z "$POST_TARGET" ]; then')[0],
            "the arm reaches gh api before it has checked that it has a "
            "repository to post to")

    def _arm(self, label, code_only=False):
        """One arm of the routing case. code_only drops comment lines: the
        assertions below are about what the arm DOES, and a comment that
        explains the GitHub arm's exit 2 must not read as the Bitbucket arm
        containing one."""
        pattern = re.compile(
            r"^\s*%s\)\n(.*?)^\s*;;" % re.escape(label),
            re.DOTALL | re.MULTILINE)
        match = pattern.search(self.text)
        self.assertIsNotNone(
            match, "no %s) arm in scripts/local-gates.sh's routing case"
            % label)
        body = match.group(1)
        if not code_only:
            return body
        return "\n".join(line for line in body.splitlines()
                         if not line.lstrip().startswith("#"))

    def test_the_bitbucket_arm_reports_failure_without_changing_the_exit(self):
        arm = self._arm("bitbucket", code_only=True)
        self.assertIn("bm_bbstatus.py", arm)
        self.assertNotIn(
            "exit", arm,
            "the bitbucket arm runs an exit, so a reporting failure could "
            "change the gate's exit status, which is the exact contract "
            "this routing exists to keep")
        self.assertIn("still stands", arm)

    def test_the_unknown_arm_says_so_and_changes_nothing(self):
        arm = self._arm("*", code_only=True)
        self.assertNotIn("exit", arm)
        self.assertIn("not posted", arm)

    def test_the_final_exit_is_still_the_gate_verdict(self):
        self.assertTrue(
            self.text.rstrip().endswith('[ "$STATE" = "success" ]'),
            "the script no longer ends by exiting with the battery's own "
            "verdict")


if __name__ == "__main__":
    unittest.main(verbosity=1)
