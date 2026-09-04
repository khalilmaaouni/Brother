#!/usr/bin/env python3
"""Adversarial fixtures for the Bitbucket Cloud sibling of `sbe pr verify`.
Run: python3 tools/test_sbe_bbprverify.py

Mirrors tools/test_sbe_prverify.py's fixture style (a canned fetch injected
into `evaluate`, never a real socket) for src/brothersbe/bbprverify.py, the
Bitbucket Cloud implementation. See that module's own docstring for the API
facts (endpoint, schema, doc URLs) these fixtures assume.

bbprverify.py is not wired into bin/sbe yet (that wiring is cli.py's `_cmd_pr`,
owned by another writer -- see that file's docstring), so unlike
test_sbe_prverify.py's CLI-level tests, the "no credential" and read-only-law
tests here drive the module directly (main()/evaluate()) rather than
subprocess through `bin/sbe`. The zero-network-attempts assertion is made by
swapping in a spy for module._real_fetch, the same technique
test_sbe_prverify.py's TestRepoShapeValidation uses.

ZERO NETWORK. No test here ever calls the real Bitbucket API.
"""
import ast
import contextlib
import io
import os
import sys
import tempfile
import unittest
import subprocess
import shutil

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
BBPRVERIFY_SOURCE = os.path.join(ROOT, "src", "brothersbe", "bbprverify.py")

CANARY_SECRET = "canary-app-password-not-real-456"

SHA_A = "a" * 40
SHA_B = "b" * 40


# ---------------------------------------------------------------------------
# Canned Bitbucket Cloud REST shapes, modeled on the fields the OpenAPI v3
# document (component schemas "pullrequest", "pullrequest_endpoint",
# "participant", "account", "user") actually defines. See bbprverify.py's
# docstring for the doc URLs these are checked against.
# ---------------------------------------------------------------------------

def pr_body(sha=SHA_A, state="OPEN", author_uuid="{author-uuid}",
           author_name="Author One", participants=None):
    return {
        "state": state,
        "author": {"type": "user", "uuid": author_uuid, "display_name": author_name},
        "source": {"commit": {"hash": sha}},
        "participants": participants or [],
    }


def participant(display_name, uuid=None, account_id=None, approved=True,
               role="REVIEWER", state="approved"):
    user = {"type": "user", "display_name": display_name}
    if uuid:
        user["uuid"] = uuid
    if account_id:
        user["account_id"] = account_id
    return {"user": user, "role": role, "approved": approved, "state": state}


class FakeFetch(object):
    """A canned (method, url, token) responder. This module hits exactly ONE
    endpoint (the pull request resource; Bitbucket embeds participants on it,
    unlike GitHub's separate /reviews call), so no substring routing is
    needed -- just a response queue, consumed in call order. A queue of one
    is reused for every call (the ordinary case: no race), a queue of two
    lets a scenario vary the second call (the force-push race). `self.calls`
    records every (method, url) pair, never the token."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, token):
        self.calls.append((method, url))
        if not self.responses:
            return (404, None, None)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@contextlib.contextmanager
def no_credential_env():
    """Removes every Bitbucket credential variable for the duration of the
    block, restoring whatever was there (including "unset") afterward."""
    keys = ("BITBUCKET_TOKEN", "BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD")
    saved = dict((k, os.environ.pop(k, None)) for k in keys)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def all_report_text(mod, report):
    """Rendered form plus a raw repr, so an assertion about where a phrase
    appears does not depend on which of the two carries it."""
    import json
    rendered = mod.render(report)
    dumped = json.dumps(report, default=str)
    return rendered + "\n" + dumped


class BbprverifyCase(unittest.TestCase):
    REPO = "myworkspace/demo"
    NUMBER = 7
    TOKEN = "Bearer test-token-present-1234567890"

    def setUp(self):
        if SRC not in sys.path:
            sys.path.insert(0, SRC)
        from brothersbe import bbprverify as mod
        self.mod = mod

    def evaluate(self, fetch, token=None):
        return self.mod.evaluate(self.REPO, self.NUMBER,
                                 self.TOKEN if token is None else token, fetch=fetch)

    def control(self, report, name):
        for c in report["controls"]:
            if c["name"] == name:
                return c
        self.fail("no control named %r in the report; controls present: %s"
                  % (name, [c["name"] for c in report["controls"]]))


# ---------------------------------------------------------------------------
# 1. No credential anywhere: NO-DATA everywhere, remedy present, nonzero
#    exit, zero network attempts.
# ---------------------------------------------------------------------------

class TestNoCredentialAnywhere(BbprverifyCase):
    def test_no_credential_is_no_data_everywhere_with_remedy_zero_network_nonzero_exit(self):
        spy_calls = []

        def spy(method, url, token):
            spy_calls.append((method, url))
            return (404, None, None)

        with no_credential_env():
            original = self.mod._real_fetch
            self.mod._real_fetch = spy
            out, err = io.StringIO(), io.StringIO()
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = out, err
            try:
                exit_code = self.mod.main(["verify", str(self.NUMBER), "--repo", self.REPO,
                                          "--json"])
            finally:
                sys.stdout, sys.stderr = old_out, old_err
                self.mod._real_fetch = original

        combined = out.getvalue() + err.getvalue()
        self.assertEqual(spy_calls, [], "no credential must mean zero network attempts: %r"
                         % spy_calls)
        self.assertNotEqual(exit_code, 0, "no credential must never exit 0: %r" % combined)
        import json as json_mod
        report = json_mod.loads(out.getvalue())
        self.assertEqual(report["final"], "NO-DATA", report)
        verdicts = set(c["verdict"] for c in report["controls"])
        self.assertEqual(verdicts, {"NO-DATA"},
                         "every control must read NO-DATA, never PASS or FAIL: %s" % verdicts)
        self.assertTrue(
            "BITBUCKET_TOKEN" in combined or "BITBUCKET_APP_PASSWORD" in combined,
            "the remedy must name at least one way to supply a credential: %r" % combined)


# ---------------------------------------------------------------------------
# 2. A non-Bitbucket origin remote: NO-DATA naming the host actually found,
#    checked before any fetch, only against the real transport.
# ---------------------------------------------------------------------------

class TestNonBitbucketOriginRemote(BbprverifyCase):
    def test_a_github_hosted_origin_remote_is_no_data_naming_github(self):
        cwd = tempfile.mkdtemp()
        try:
            # check=True on both: the fixture IS the test. A failed git init
            # leaves a directory that is not a repository, and the assertion
            # below then passes for the wrong reason, which is the shape this
            # repository keeps finding one level down.
            subprocess.run(["git", "init"], cwd=cwd, capture_output=True,
                           timeout=10, check=True)
            subprocess.run(["git", "remote", "add", "origin",
                            "https://github.com/octo/demo.git"],
                           cwd=cwd, capture_output=True, timeout=10, check=True)
            os.environ["BITBUCKET_TOKEN"] = "a-token-present-so-only-the-host-check-fires"
            try:
                report = self.mod.evaluate(self.REPO, self.NUMBER,
                                           "Bearer a-token-present-so-only-the-host-check-fires",
                                           fetch=None, cwd=cwd)
            finally:
                os.environ.pop("BITBUCKET_TOKEN", None)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        self.assertEqual(report["final"], "NO-DATA", report)
        self.assertIn("github.com", report["finalDetail"], report)


# ---------------------------------------------------------------------------
# 3. PR not found: NO-DATA, never FAIL (Bitbucket's 404 is ambiguous between
#    "does not exist" and "this credential cannot see it").
# ---------------------------------------------------------------------------

class TestPRNotFound(BbprverifyCase):
    def test_a_404_on_the_pull_request_is_no_data_never_fail(self):
        fetch = FakeFetch([(404, None, None)])
        report = self.evaluate(fetch)
        control = self.control(report, "PR EXISTS")
        self.assertEqual(control["verdict"], "NO-DATA", control)
        self.assertNotEqual(control["verdict"], "FAIL", control)


# ---------------------------------------------------------------------------
# 4. Independent approval: self alone, a genuine second person, and an
#    approval that cannot be attributed to a distinguishable identity.
# ---------------------------------------------------------------------------

class TestSelfApprovalOnly(BbprverifyCase):
    def test_the_author_approving_their_own_pr_is_not_independent(self):
        body = pr_body(sha=SHA_A, author_uuid="{same-uuid}", author_name="Same Person",
                       participants=[participant("Same Person", uuid="{same-uuid}")])
        fetch = FakeFetch([(200, body, None)])
        report = self.evaluate(fetch)
        control = self.control(report, "INDEPENDENT APPROVAL")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertNotEqual(control["verdict"], "PASS", control)


class TestGenuineSecondPersonApproval(BbprverifyCase):
    def test_a_second_persons_approval_is_pass(self):
        body = pr_body(sha=SHA_A, author_uuid="{author-uuid}", author_name="Author One",
                       participants=[participant("Reviewer Two", uuid="{reviewer-uuid}")])
        fetch = FakeFetch([(200, body, None)])
        report = self.evaluate(fetch)
        control = self.control(report, "INDEPENDENT APPROVAL")
        self.assertEqual(control["verdict"], "PASS", control)
        self.assertIn("Reviewer Two", control["detail"], control)


class TestUnresolvableApproverIdentity(BbprverifyCase):
    def test_an_approval_with_no_stable_identifier_is_no_data_not_pass(self):
        """The approver carries only a display_name -- no uuid, no
        account_id. Two people can share a display_name, so this must never
        be certified independent, and it must not be asserted a self-approval
        either: it is NO-DATA."""
        body = pr_body(sha=SHA_A, author_uuid="{author-uuid}", author_name="Author One",
                       participants=[participant("Mystery Reviewer")])
        fetch = FakeFetch([(200, body, None)])
        report = self.evaluate(fetch)
        control = self.control(report, "INDEPENDENT APPROVAL")
        self.assertEqual(control["verdict"], "NO-DATA", control)
        self.assertNotEqual(control["verdict"], "PASS", control)


# ---------------------------------------------------------------------------
# 5. APPROVAL IS CURRENT: Bitbucket's participant object carries no commit
#    reference at all (unlike GitHub's review.commit_id), so this control
#    must never assert PASS or FAIL -- only NO-DATA naming the gap.
# ---------------------------------------------------------------------------

class TestApprovalIsCurrentAlwaysNoData(BbprverifyCase):
    def test_an_existing_approval_still_reads_no_data_for_currency(self):
        body = pr_body(sha=SHA_A, author_uuid="{author-uuid}", author_name="Author One",
                       participants=[participant("Reviewer Two", uuid="{reviewer-uuid}")])
        fetch = FakeFetch([(200, body, None)])
        report = self.evaluate(fetch)
        control = self.control(report, "APPROVAL IS CURRENT")
        self.assertEqual(control["verdict"], "NO-DATA", control)


# ---------------------------------------------------------------------------
# 6. Force-push mid-run: the head moves between the first and last fetch.
# ---------------------------------------------------------------------------

class TestHeadRaceMidRun(BbprverifyCase):
    def test_a_head_that_changes_between_first_and_last_fetch_is_unverifiable(self):
        fetch = FakeFetch([
            (200, pr_body(sha=SHA_A, participants=[participant("Reviewer Two",
                                                               uuid="{r2}")]), None),
            (200, pr_body(sha=SHA_B, participants=[participant("Reviewer Two",
                                                               uuid="{r2}")]), None),
        ])
        report = self.evaluate(fetch)
        self.assertEqual(report["final"], "UNVERIFIABLE", report)
        text = all_report_text(self.mod, report).lower()
        self.assertIn("re-run", text, text)


# ---------------------------------------------------------------------------
# 7. The credential is never printed.
# ---------------------------------------------------------------------------

class TestCredentialNeverPrinted(BbprverifyCase):
    def test_the_canary_secret_never_appears_in_a_full_canned_run(self):
        body = pr_body(sha=SHA_A, participants=[participant("Reviewer Two", uuid="{r2}")])
        fetch = FakeFetch([(200, body, None)])
        report = self.mod.evaluate(self.REPO, self.NUMBER, "Bearer " + CANARY_SECRET,
                                   fetch=fetch, secret=CANARY_SECRET)
        combined = all_report_text(self.mod, report)
        self.assertNotIn(CANARY_SECRET, combined,
                         "the credential must never appear in any output byte")


# ---------------------------------------------------------------------------
# 8. Read-only law: source-level, no import needed, mirrors
#    test_sbe_prverify.py's TestReadOnlyLaw.
# ---------------------------------------------------------------------------

FORBIDDEN_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _offending_calls(tree):
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords or []:
            if kw.arg == "data" and not (isinstance(kw.value, ast.Constant)
                                        and kw.value.value is None):
                findings.append("line %d: a call passes data=... (a body), which a read-only "
                                "client never needs" % node.lineno)
            if kw.arg == "method" and isinstance(kw.value, ast.Constant) \
                    and kw.value.value in FORBIDDEN_METHODS:
                findings.append("line %d: a call passes method=%r"
                                % (node.lineno, kw.value.value))
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value in FORBIDDEN_METHODS:
                findings.append("line %d: a call passes the literal %r as a positional "
                                "argument" % (node.lineno, arg.value))
    return findings


class TestReadOnlyLaw(unittest.TestCase):
    def test_every_request_construction_is_get_no_mutating_method_anywhere(self):
        if not os.path.exists(BBPRVERIFY_SOURCE):
            self.fail("src/brothersbe/bbprverify.py does not exist yet.")
        with io.open(BBPRVERIFY_SOURCE, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=BBPRVERIFY_SOURCE)
        findings = _offending_calls(tree)
        self.assertEqual(findings, [],
                         "a read-only client must never construct a non-GET request:\n  "
                         + "\n  ".join(findings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
