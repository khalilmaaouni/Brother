#!/usr/bin/env python3
"""Adversarial fixtures for `sbe pr verify`. Run: python3 tools/test_sbe_prverify.py

Spec of record: docs/specs/2026-07-30-sbe-pr-verify.md. These fixtures assert
exactly what that document says, nothing else: a disagreement between this
file and the eventual src/brothersbe/prverify.py is a defect in one of them,
never a negotiation settled here.

AT THE TIME THIS FILE WAS WRITTEN, `bin/sbe` has no `pr` subcommand at all (it
is not in cli.py's COMMANDS table) and src/brothersbe/prverify.py does not
exist. Two different RED shapes follow from that, and both are deliberate:

  - Tests that drive `bin/sbe pr verify` as a subprocess (argument and
    no-token behavior) fail as ordinary assertion FAILUREs: argparse's
    "invalid choice" usage error is real output, so `assertIn("NO-DATA", ...)`
    and friends simply do not find it yet. No import, no crash, exactly the
    same style tools/test_sbe_work.py used for `sbe work` before it existed.
  - Tests that drive src/brothersbe/prverify.py DIRECTLY (the canned-response
    control logic the brief requires, because the module takes an injectable
    fetch and this suite must never open a socket) cannot reach that
    checkpoint at all without importing the module first. Every one of those
    classes imports it in setUp(), so every test in them ERRORs today at
    setUp with a clean ImportError, one time, in one place, rather than a
    scattered AttributeError per test. That ERROR is entirely attributable to
    the missing module, never to a bug in this file: nothing below this point
    in any such class ever runs, so nothing below it can be a fixture bug.

ZERO NETWORK. No test here ever calls the real GitHub API. The CLI tests only
exercise argument parsing and the no-token path (no fetch ever happens before
a token is even sought); the control-logic tests inject FakeFetch, defined
below, which answers from an in-memory table and asserts GET-only itself.

ASSUMED CONTRACT. The spec pins the CLI surface, the control names, the four
verdicts and the security rules; it deliberately leaves the Python-level
calling convention to whoever writes the fixtures first, so here it is,
written down once rather than guessed at twice:

  brothersbe.prverify.evaluate(repo, number, token, fetch=None, cwd=None,
                               head=None, policy=None) -> a report dict:
    {"tool": "sbe pr verify", "repository": repo, "number": number,
     "headSha": <the head sha last observed, or None>,
     "controls": [{"name": <CONTROL NAME>, "verdict": "PASS"|"FAIL"|
                   "NO-DATA"|"UNVERIFIABLE", "detail": <str>}, ...],
     "final": one of the four verdicts}
    Control names are the exact spec strings: "PR EXISTS", "PR HEAD",
    "AUTHOR KNOWN", "INDEPENDENT APPROVAL", "BOT APPROVAL", "REVIEW THREADS",
    "CODEOWNERS", "REQUIRED CHECKS", and "EVIDENCE FRESHNESS" only when `cwd`
    names a local evidence store. `fetch=None` means "use the real network
    transport"; a fixture always passes a FakeFetch instead.

  brothersbe.prverify.render(report) -> str, the human-readable form. Its
  header carries repository, PR number and head sha; its last line names the
  FINAL verdict.

  fetch(method, url, token) -> (status, body, error). Three values, never
  two: a two-value return here would read as a possible (verdict, evidence)
  pair to evals/test_no_data_class.py, the honesty meta-test, and this is not
  a verdict source either. `method` is always the literal "GET" (the
  read-only law TestReadOnlyLaw enforces at the source level, below).
  `status` is the HTTP status code, or None when the network was
  unreachable. `body` is the parsed JSON (dict or list), or None. `error` is
  a short string naming what went wrong, set only when `status` is None. The
  module is expected to call the PR resource endpoint (a URL containing
  "/pulls/<number>") FIRST, to learn the head sha everything else is
  evaluated against, and LAST, to detect a force-push mid-run (the spec's own
  words: "the head sha is fetched last and compared to the sha each control
  was evaluated against"). The other endpoints this suite cans: a URL
  containing "/pulls/<number>/reviews", one containing "/check-runs", and one
  containing "CODEOWNERS".

  These are decisions this loop had to make to hand the next one a concrete
  target, not a re-reading of the spec. A disagreement between this contract
  and the implementation is a defect in the implementation to fix, never a
  reason to loosen a fixture.
"""
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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
SBE = os.path.join(ROOT, "bin", "sbe")
SRC = os.path.join(ROOT, "src")
PRVERIFY_SOURCE = os.path.join(ROOT, "src", "brothersbe", "prverify.py")

# Synthetic and deliberately NOT shaped like tools/sbe_telemetry.py's
# SECRET_PATTERNS github-token regex (which needs an underscore after the
# ghp/gho/ghr/ghs/ghu prefix): this canary proves prverify.py itself never
# echoes a token, rather than proving the unrelated argv-redaction helper in
# src/brothersbe/evidence.py caught it. The two controls are not the same
# claim and this file tests only the first one.
CANARY_TOKEN = "ghp-canary-not-real-123"

SHA_A = "a" * 40
SHA_B = "b" * 40


# ---------------------------------------------------------------------------
# Canned GitHub REST shapes, modeled on the real field names the spec names:
# pulls/<n> (head.sha, user.login, state), reviews (user.login, user.type,
# state, commit_id, submitted_at), check-runs for a sha. Each payload carries
# only the fields a control actually reads.
# ---------------------------------------------------------------------------

def pr_body(number=7, sha=SHA_A, author="author-user", author_type="User", state="open",
           base_ref="main"):
    return {"number": number, "state": state, "head": {"sha": sha},
            "user": {"login": author, "type": author_type},
            "base": {"ref": base_ref}}


def review(login, state, sha=SHA_A, submitted_at="2026-07-30T12:00:00Z",
           user_type="User", rid=1):
    return {"id": rid, "user": {"login": login, "type": user_type}, "state": state,
            "commit_id": sha, "submitted_at": submitted_at}


def check_runs(conclusion="success", name="ci/build"):
    return {"check_runs": [{"name": name, "status": "completed", "conclusion": conclusion}]}


def protection(required_contexts=None, require_code_owner=False):
    """A canned branches/<base>/protection body: only the two fields F6+F7
    actually read, required_status_checks.contexts and required_pull_request_
    reviews.require_code_owner_reviews."""
    return {"required_status_checks": {"contexts": list(required_contexts or [])},
            "required_pull_request_reviews":
                {"require_code_owner_reviews": bool(require_code_owner)}}


class FakeFetch(object):
    """A canned (method, url, token) router. Routes match by SUBSTRING
    CONTAINMENT against the url the module built, longest needle first, so a
    test can can "/pulls/7/reviews" and "/pulls/7" in either order without one
    swallowing the other. `sequences` holds per-needle response LISTS,
    consumed in call order, for an endpoint the module is expected to poll
    more than once (the PR resource, to catch a force-push mid-run: see the
    contract above). `self.calls` records every (method, url) pair this fake
    actually saw -- never the token -- so a test can assert GET-only without
    ever holding the token anywhere a print or an assertion message could
    surface it.
    """

    def __init__(self, routes=None, sequences=None, default=(404, None, None)):
        self.routes = dict(routes or {})
        self.sequences = dict((k, list(v)) for k, v in (sequences or {}).items())
        self.default = default
        self.calls = []

    def __call__(self, method, url, token):
        self.calls.append((method, url))
        # Sequence and route needles are ranked TOGETHER by length, longest
        # first, not checked in two separate passes: a short needle like
        # "/pulls/7" (a sequence, so a force-push scenario can vary it call
        # to call) is a substring of the longer "/pulls/7/reviews" (a plain
        # route), and checking sequences to exhaustion before routes would
        # let the short needle swallow every reviews call too.
        tagged = ([(needle, "seq") for needle in self.sequences]
                 + [(needle, "route") for needle in self.routes])
        for needle, kind in sorted(tagged, key=lambda t: len(t[0]), reverse=True):
            if needle not in url:
                continue
            if kind == "seq":
                seq = self.sequences[needle]
                if seq:
                    return seq.pop(0)
                continue
            return self.routes[needle]
        return self.default


def baseline_routes():
    """Sane defaults a scenario can override: one successful check run, and
    branch protection that requires no status checks and no code owner
    review (so REQUIRED CHECKS reads PASS "nothing required" and CODEOWNERS
    reads NO-DATA "no applicable rule" unless a scenario overrides
    "/protection" to test F6+F7 directly). Every scenario below replaces
    only the routes it is actually testing."""
    return {"/check-runs": (200, check_runs("success"), None),
            "/protection": (200, protection(), None)}


def all_report_text(mod, report):
    """Every string this report could show a human, concatenated: the
    rendered form plus a raw JSON dump, so a scenario's assertion about where
    a phrase or a token appears does not depend on which of the two the
    implementation happens to put it in."""
    rendered = mod.render(report)
    dumped = json.dumps(report, default=str)
    return rendered + "\n" + dumped


def clean_no_token_env(home):
    """A copy of the environment with GITHUB_TOKEN and GH_TOKEN unset, gh
    stripped out of PATH entirely (an absent binary rather than a logged-out
    one: both are absence, but this does not depend on this machine's login
    state), and HOME repointed at an empty directory so no gh config or git
    credential helper is reachable either."""
    env = dict(os.environ)
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    env.pop("GH_CONFIG_DIR", None)
    kept = []
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        if os.path.exists(os.path.join(part, "gh")):
            continue
        kept.append(part)
    env["PATH"] = os.pathsep.join(kept)
    env["HOME"] = home
    return env


# ---------------------------------------------------------------------------
# 1. No token anywhere: CLI-driven, never imports the module.
# ---------------------------------------------------------------------------

class TestNoTokenAnywhere(unittest.TestCase):
    """Env scrubbed of both token variables, gh absent from PATH, HOME
    repointed at an empty directory so gh has no config to fall back on
    either: every network control must read NO-DATA with its remedy, the
    FINAL verdict must be NO-DATA, the exit must be nonzero, and PASS/FAIL
    must never appear."""

    def test_no_token_no_gh_is_no_data_everywhere_with_remedy_and_nonzero_exit(self):
        home = tempfile.mkdtemp()
        cwd = tempfile.mkdtemp()
        try:
            env = clean_no_token_env(home)
            proc = subprocess.run(
                [sys.executable, SBE, "pr", "verify", "7", "--repo", "octo/demo",
                 "--cwd", cwd], capture_output=True, text=True, env=env, timeout=30)
            combined = proc.stdout + proc.stderr
            self.assertNotEqual(proc.returncode, 0,
                                "no credentials anywhere must never exit 0: %r" % combined)
            self.assertIn("NO-DATA", combined,
                         "every network-dependent control must read NO-DATA when no token "
                         "exists anywhere: %r" % combined)
            self.assertIn("FINAL", combined, combined)
            self.assertIn("NO-DATA", combined.rsplit("FINAL", 1)[-1],
                         "the FINAL line itself must read NO-DATA, not just some control "
                         "above it: %r" % combined)
            self.assertTrue(
                "GITHUB_TOKEN" in combined or "gh auth login" in combined,
                "the one-line remedy must name at least one of the two ways to supply a "
                "token: %r" % combined)
            self.assertNotIn("PASS", combined,
                             "absent credentials must never be reported as a pass: %r"
                             % combined)
            self.assertNotIn("FAIL", combined,
                             "absent credentials must never be reported as a fail either: "
                             "absence of evidence is not evidence of absence: %r" % combined)
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(cwd, ignore_errors=True)


# ---------------------------------------------------------------------------
# Canned-response control logic: every class below drives
# src/brothersbe/prverify.py directly. setUp() imports it; until it exists,
# every test in these classes ERRORs there, once, cleanly. See the module
# docstring above for why that is the correct RED shape here.
# ---------------------------------------------------------------------------

class PrverifyCase(unittest.TestCase):
    REPO = "octo/demo"
    NUMBER = 7
    TOKEN = "test-token-present-1234567890"

    def setUp(self):
        if SRC not in sys.path:
            sys.path.insert(0, SRC)
        from brothersbe import prverify as mod
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


class TestStaleApproval(PrverifyCase):
    def test_an_approval_bound_to_a_commit_before_the_head_is_a_fail_naming_both(self):
        """The reviewer approved SHA_A; the PR has since moved to SHA_B. The
        approval predates the current head and must FAIL, naming both."""
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [review("reviewer-two", "APPROVED",
                                                            sha=SHA_A)], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_B), None),
                                    (200, pr_body(sha=SHA_B), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "INDEPENDENT APPROVAL")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertIn(SHA_A, control["detail"], control)
        self.assertIn(SHA_B, control["detail"], control)


class TestSelfApproval(PrverifyCase):
    def test_the_author_approving_their_own_pr_is_a_fail(self):
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [review("same-user", "APPROVED",
                                                            sha=SHA_A)], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A, author="same-user"), None),
                                    (200, pr_body(sha=SHA_A, author="same-user"), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "INDEPENDENT APPROVAL")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertIn("same-user", control["detail"], control)


class TestDismissedReview(PrverifyCase):
    def test_a_dismissed_review_counts_neither_as_approval_nor_as_a_block(self):
        """The only review on the PR is DISMISSED. It must not read as the
        independent approval this PR needs, and it must not read as an
        unresolved CHANGES_REQUESTED block either."""
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [review("carol", "DISMISSED",
                                                            sha=SHA_A)], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        approval = self.control(report, "INDEPENDENT APPROVAL")
        threads = self.control(report, "REVIEW THREADS")
        self.assertNotEqual(approval["verdict"], "PASS",
                           "a dismissed review must not count as the approval: %s" % approval)
        self.assertEqual(threads["verdict"], "PASS",
                        "a dismissed review must not count as an unresolved "
                        "CHANGES_REQUESTED block: %s" % threads)


class TestUnresolvedChangesRequested(PrverifyCase):
    def test_the_latest_review_per_reviewer_being_changes_requested_fails_threads(self):
        """carol approved first, then asked for changes. Latest-per-reviewer
        is CHANGES_REQUESTED, and it is still live: REVIEW THREADS must FAIL."""
        reviews = [review("carol", "APPROVED", sha=SHA_A,
                          submitted_at="2026-07-30T10:00:00Z", rid=1),
                  review("carol", "CHANGES_REQUESTED", sha=SHA_A,
                          submitted_at="2026-07-30T11:00:00Z", rid=2)]
        fetch = FakeFetch(
            routes=dict(baseline_routes(), **{"/pulls/7/reviews": (200, reviews, None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "REVIEW THREADS")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertIn("carol", control["detail"], control)


class TestBotOnlyApproval(PrverifyCase):
    def test_an_approval_from_a_bot_account_alone_fails_under_default_policy(self):
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [review("sbe-bot", "APPROVED", sha=SHA_A,
                                                            user_type="Bot")], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "BOT APPROVAL")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertNotEqual(report["final"], "PASS", report)


class TestMissingCodeowners(PrverifyCase):
    def test_no_applicable_codeowners_rule_is_no_data_never_pass(self):
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "CODEOWNERS")
        self.assertEqual(control["verdict"], "NO-DATA", control)


class TestFailedRequiredCheck(PrverifyCase):
    def test_a_failed_required_check_on_the_head_sha_fails_required_checks(self):
        fetch = FakeFetch(
            routes={"/check-runs": (200, check_runs("failure", name="ci/unit"), None),
                   "/protection": (200, protection(required_contexts=["ci/unit"]), None),
                   "/pulls/7/reviews": (200, [], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "REQUIRED CHECKS")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertIn("ci/unit", control["detail"], control)


class TestRequiredCheckMissing(PrverifyCase):
    def test_a_required_context_absent_from_the_check_runs_fails_naming_it(self):
        """Branch protection requires "ci/lint", but no check run by that
        name exists at all on the head sha (only an unrelated one ran)."""
        fetch = FakeFetch(
            routes={"/check-runs": (200, check_runs("success", name="ci/build"), None),
                   "/protection": (200, protection(required_contexts=["ci/lint"]), None),
                   "/pulls/7/reviews": (200, [], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "REQUIRED CHECKS")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertIn("ci/lint", control["detail"], control)


class TestRequiredChecksEmptyIsPass(PrverifyCase):
    def test_an_empty_required_contexts_list_is_pass_with_nothing_required_prose(self):
        fetch = FakeFetch(
            routes=dict(baseline_routes(), **{"/pulls/7/reviews": (200, [], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "REQUIRED CHECKS")
        self.assertEqual(control["verdict"], "PASS", control)
        self.assertIn("nothing required", control["detail"], control)


class TestForbiddenWithToken(PrverifyCase):
    def test_403_with_a_token_present_is_unverifiable_never_pass(self):
        fetch = FakeFetch(
            routes={"/check-runs": (403, None, None),
                   "/protection": (200, protection(required_contexts=["ci/build"]), None),
                   "/pulls/7/reviews": (200, [], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch, token="a-token-that-lacks-permission")
        control = self.control(report, "REQUIRED CHECKS")
        self.assertEqual(control["verdict"], "UNVERIFIABLE", control)
        self.assertNotEqual(report["final"], "PASS", report)


class TestBranchProtectionForbidden(PrverifyCase):
    def test_403_on_the_protection_endpoint_makes_both_controls_unverifiable_naming_it(self):
        """The protection endpoint itself is forbidden to this token. Neither
        REQUIRED CHECKS nor CODEOWNERS may fall back to a guess from the
        check-runs scan or a raw CODEOWNERS file: both must read UNVERIFIABLE
        and name the protection endpoint."""
        fetch = FakeFetch(
            routes={"/check-runs": (200, check_runs("success"), None),
                   "/protection": (403, None, None),
                   "/pulls/7/reviews": (200, [review("carol", "APPROVED", sha=SHA_A)], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        required = self.control(report, "REQUIRED CHECKS")
        codeowners = self.control(report, "CODEOWNERS")
        self.assertEqual(required["verdict"], "UNVERIFIABLE", required)
        self.assertEqual(codeowners["verdict"], "UNVERIFIABLE", codeowners)
        self.assertIn("protection", required["detail"].lower(), required)
        self.assertIn("protection", codeowners["detail"].lower(), codeowners)
        self.assertNotEqual(report["final"], "PASS", report)

    def test_404_on_the_protection_endpoint_makes_both_controls_unverifiable(self):
        fetch = FakeFetch(
            routes={"/check-runs": (200, check_runs("success"), None),
                   "/protection": (404, None, None),
                   "/pulls/7/reviews": (200, [], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        required = self.control(report, "REQUIRED CHECKS")
        codeowners = self.control(report, "CODEOWNERS")
        self.assertEqual(required["verdict"], "UNVERIFIABLE", required)
        self.assertEqual(codeowners["verdict"], "UNVERIFIABLE", codeowners)


class TestCodeownersRequiredWithApproval(PrverifyCase):
    def test_required_code_owner_review_satisfied_by_a_live_approval_is_pass(self):
        fetch = FakeFetch(
            routes={"/check-runs": (200, check_runs("success"), None),
                   "/protection": (200, protection(require_code_owner=True), None),
                   "/pulls/7/reviews": (200, [review("carol", "APPROVED", sha=SHA_A)], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "CODEOWNERS")
        self.assertEqual(control["verdict"], "PASS", control)
        self.assertIn("carol", control["detail"], control)


class TestCodeownersRequiredWithoutApproval(PrverifyCase):
    def test_required_code_owner_review_with_no_live_approval_fails(self):
        fetch = FakeFetch(
            routes={"/check-runs": (200, check_runs("success"), None),
                   "/protection": (200, protection(require_code_owner=True), None),
                   "/pulls/7/reviews": (200, [], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "CODEOWNERS")
        self.assertEqual(control["verdict"], "FAIL", control)
        self.assertNotEqual(report["final"], "PASS", report)


class TestCodeownersRequiredButReviewsUnreadable(PrverifyCase):
    def test_required_code_owner_review_with_unreadable_reviews_is_unverifiable(self):
        """Branch protection is readable and requires a code owner review,
        but the reviews endpoint itself is forbidden: CODEOWNERS cannot tell
        whether the requirement is satisfied, so it must not guess PASS or
        FAIL either way."""
        fetch = FakeFetch(
            routes={"/check-runs": (200, check_runs("success"), None),
                   "/protection": (200, protection(require_code_owner=True), None),
                   "/pulls/7/reviews": (403, None, None)},
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        control = self.control(report, "CODEOWNERS")
        self.assertEqual(control["verdict"], "UNVERIFIABLE", control)


class TestRepoShapeValidation(PrverifyCase):
    """M2: --repo must be owner/name, each side [A-Za-z0-9_.-]+, checked
    before token discovery and before any fetch. A path-traversal payload
    like foo/bar/../../x carries extra slashes the shape regex rejects."""

    def test_path_traversal_repo_is_refused_before_any_fetch(self):
        calls = []

        def spy(method, url, token):
            calls.append((method, url))
            return (404, None, None)

        original = self.mod._real_fetch
        self.mod._real_fetch = spy
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            exit_code = self.mod.main(["verify", "7", "--repo", "foo/bar/../../x"])
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            self.mod._real_fetch = original
        self.assertEqual(exit_code, 2, (exit_code, out.getvalue(), err.getvalue()))
        self.assertEqual(calls, [], "a malformed --repo must be refused before any "
                                    "fetch is ever made: %r" % calls)
        self.assertIn("owner/name", err.getvalue(), err.getvalue())

    def test_valid_repo_shape_accepts_owner_name_and_rejects_traversal(self):
        self.assertTrue(self.mod.valid_repo_shape("octo/demo"))
        self.assertFalse(self.mod.valid_repo_shape("foo/bar/../../x"))
        self.assertFalse(self.mod.valid_repo_shape("/etc/passwd"))
        self.assertFalse(self.mod.valid_repo_shape(""))
        self.assertFalse(self.mod.valid_repo_shape(None))

    def test_a_bare_dot_or_dot_dot_segment_is_refused_on_either_side(self):
        """REPO_SHAPE_RE's character class allows periods (real names carry
        them, e.g. octocat.github.io), so it matches "../repo" and
        "owner/.." on shape alone. A segment equal to exactly "." or ".."
        is a path-traversal token, not a name, and must be refused."""
        for repo in ("../repo", "owner/..", "./repo", "owner/."):
            self.assertFalse(self.mod.valid_repo_shape(repo),
                             "%r must be refused: a bare . or .. segment is a "
                             "path-traversal token, not a repository name" % repo)

    def test_a_bare_dot_or_dot_dot_repo_is_refused_before_any_fetch(self):
        for repo in ("../repo", "owner/..", "./repo", "owner/."):
            calls = []

            def spy(method, url, token, calls=calls):
                calls.append((method, url))
                return (404, None, None)

            original = self.mod._real_fetch
            self.mod._real_fetch = spy
            out, err = io.StringIO(), io.StringIO()
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = out, err
            try:
                exit_code = self.mod.main(["verify", "7", "--repo", repo])
            finally:
                sys.stdout, sys.stderr = old_out, old_err
                self.mod._real_fetch = original
            self.assertEqual(exit_code, 2,
                             (repo, exit_code, out.getvalue(), err.getvalue()))
            self.assertEqual(calls, [], "%r must be refused before any fetch is ever "
                                        "made: %r" % (repo, calls))


class TestHostileBaseRefShape(PrverifyCase):
    """base_ref is API-sourced (the pull request's base.ref), not
    user-supplied, but it still reaches a URL path segment: the
    branch-protection fetch. A shape containing "/" must never introduce an
    extra path segment into that URL; urlencoding with safe="" is what
    stops it."""

    def test_a_hostile_base_ref_is_percent_encoded_never_a_raw_path_segment(self):
        hostile_ref = "main/../../orgs/evil-org/repos"
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A, base_ref=hostile_ref), None),
                                    (200, pr_body(sha=SHA_A, base_ref=hostile_ref), None)]})
        self.evaluate(fetch)
        protection_calls = [url for method, url in fetch.calls if "/branches/" in url]
        self.assertTrue(protection_calls, fetch.calls)
        for url in protection_calls:
            self.assertNotIn("/branches/main/../../orgs/evil-org/repos/protection",
                             url, url)
            self.assertIn("%2F", url,
                         "a base_ref containing / must be percent-encoded, never left "
                         "as a raw path separator that could add path segments: %r" % url)


class TestHostileHeadShaShape(PrverifyCase):
    """first_sha is API-sourced (the pull request's head.sha), not
    user-supplied, but it still reaches a URL path segment: the check-runs
    fetch. A value that does not look like a commit sha must never be
    composed into that URL at all."""

    def test_a_hostile_head_sha_never_reaches_the_check_runs_url(self):
        hostile_sha = "deadbeef/../../evil"
        fetch = FakeFetch(
            routes={"/protection": (200, protection(required_contexts=["ci/build"]), None),
                   "/pulls/7/reviews": (200, [], None)},
            sequences={"/pulls/7": [(200, pr_body(sha=hostile_sha), None),
                                    (200, pr_body(sha=hostile_sha), None)]})
        report = self.evaluate(fetch)
        check_run_calls = [url for method, url in fetch.calls if "/check-runs" in url]
        self.assertEqual(check_run_calls, [],
                         "a head sha that does not look like a commit sha must never "
                         "be composed into the check-runs URL: %r" % check_run_calls)
        control = self.control(report, "REQUIRED CHECKS")
        self.assertEqual(control["verdict"], "UNVERIFIABLE", control)
        self.assertIn("does not look like a commit sha", control["detail"], control)

    def test_valid_sha_shape_accepts_hex_and_rejects_hostile_shapes(self):
        self.assertTrue(self.mod.valid_sha_shape("a" * 40))
        self.assertTrue(self.mod.valid_sha_shape("a" * 7))
        self.assertFalse(self.mod.valid_sha_shape("a" * 6), "shorter than 7 hex chars")
        self.assertFalse(self.mod.valid_sha_shape("a" * 41), "longer than 40 hex chars")
        self.assertFalse(self.mod.valid_sha_shape("deadbeef/../../evil"))
        self.assertFalse(self.mod.valid_sha_shape("DEADBEEF" + "a" * 32), "uppercase hex")
        self.assertFalse(self.mod.valid_sha_shape(""))
        self.assertFalse(self.mod.valid_sha_shape(None))


class TestForcePushMidRun(PrverifyCase):
    def test_a_force_pushed_head_between_first_and_last_fetch_is_unverifiable(self):
        """The PR resource is fetched twice: once to learn the head this run
        evaluates against, once more at the end to catch a race. Here the
        second fetch returns a different sha than the first."""
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [review("carol", "APPROVED",
                                                            sha=SHA_A)], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_B), None)]})
        report = self.evaluate(fetch)
        self.assertEqual(report["final"], "UNVERIFIABLE", report)
        text = all_report_text(self.mod, report).lower()
        self.assertIn("re-run", text,
                     "a race between the first and last head-sha fetch must be reported "
                     "with re-run prose, per the spec's own words: %s" % text)


class TestReportBinding(PrverifyCase):
    def test_repository_pr_number_and_head_sha_are_in_the_report_header(self):
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        report = self.evaluate(fetch)
        self.assertEqual(report["repository"], self.REPO, report)
        self.assertEqual(report["number"], self.NUMBER, report)
        self.assertEqual(report["headSha"], SHA_A, report)
        header = self.mod.render(report)
        self.assertIn(self.REPO, header, header)
        self.assertIn(str(self.NUMBER), header, header)
        self.assertTrue(SHA_A in header or SHA_A[:12] in header, header)


class TestTokenNeverPrinted(PrverifyCase):
    """The canary assertion, across a full canned run (module-direct, the
    token reaching only `evaluate`'s own argument, never an environment
    variable a child process could inherit) and a no-token CLI run (the
    token set as GITHUB_TOKEN, which today's `bin/sbe pr verify` usage error
    trivially never echoes; the assertion stays meaningful once the command
    exists, because nothing here weakens it for that day)."""

    def test_the_canary_token_never_appears_in_a_full_canned_run(self):
        fetch = FakeFetch(
            routes=dict(baseline_routes(),
                       **{"/pulls/7/reviews": (200, [review("carol", "APPROVED",
                                                            sha=SHA_A)], None)}),
            sequences={"/pulls/7": [(200, pr_body(sha=SHA_A), None),
                                    (200, pr_body(sha=SHA_A), None)]})
        captured_out, captured_err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = captured_out, captured_err
        try:
            report = self.mod.evaluate(self.REPO, self.NUMBER, CANARY_TOKEN, fetch=fetch)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        combined = (captured_out.getvalue() + captured_err.getvalue()
                   + all_report_text(self.mod, report))
        self.assertNotIn(CANARY_TOKEN, combined,
                         "the token must never appear in any output byte of any run")

    def test_the_canary_token_never_appears_in_a_no_token_cli_run(self):
        home = tempfile.mkdtemp()
        cwd = tempfile.mkdtemp()
        try:
            env = clean_no_token_env(home)
            env["GITHUB_TOKEN"] = CANARY_TOKEN
            proc = subprocess.run(
                [sys.executable, SBE, "pr", "verify", "7", "--repo", "octo/demo",
                 "--cwd", cwd], capture_output=True, text=True, env=env, timeout=30)
            combined = proc.stdout + proc.stderr
            self.assertNotIn(CANARY_TOKEN, combined,
                             "the token must never appear in any output byte of any run")
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(cwd, ignore_errors=True)


# ---------------------------------------------------------------------------
# 11. Read-only law: source-level, no import, so this is a clean FAILURE
# (not an ERROR) until the file exists, and stays a real check afterward.
# ---------------------------------------------------------------------------

FORBIDDEN_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _offending_calls(tree):
    """Every finding: a Call passing a non-GET method literal, or a `data=`
    keyword with a non-None value, anywhere in the module. A list, not a
    2-tuple: this file lives under tools/, where evals/test_no_data_class.py
    treats any 2-tuple return it cannot prove is never PASS as a candidate
    verdict source, and a plain list of strings carries no such ambiguity.
    """
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
        if not os.path.exists(PRVERIFY_SOURCE):
            self.fail("src/brothersbe/prverify.py does not exist yet, so the read-only law "
                     "has nothing to check. This is the RED state docs/specs/"
                     "2026-07-30-sbe-pr-verify.md describes; the check itself is real and "
                     "will run the moment the file exists.")
        with io.open(PRVERIFY_SOURCE, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=PRVERIFY_SOURCE)
        findings = _offending_calls(tree)
        self.assertEqual(findings, [],
                         "a read-only client must never construct a non-GET request:\n  "
                         + "\n  ".join(findings))


class TestHelpMeansHelp(unittest.TestCase):
    """`sbe pr verify -h` printed the right usage and exited 2 (the module
    folded argparse's SystemExit(0) for help into exit_usage), and `sbe pr -h`
    was refused as a missing 'verify' positional. Help is not an error, and a
    help request opens no network connection. Calibrated by reinjecting the
    bare `except SystemExit: return exit_usage` and the missing leading-help
    branch: each fixture failed on a returncode of 2 where 0 was asserted,
    before the fix was restored, the restore verified against the
    pre-recorded `git hash-object` of the fixed files."""

    def run_sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE, "pr"] + list(argv),
                             capture_output=True, text=True,
                             cwd=tempfile.gettempdir())
        return out.returncode, out.stdout, out.stderr

    def test_help_exits_0_with_usage(self):
        for argv in (("-h",), ("--help",), ("verify", "-h"), ("verify", "--help")):
            code, stdout, stderr = self.run_sbe(*argv)
            self.assertEqual(code, 0, "sbe pr %s exited %d: %s"
                             % (" ".join(argv), code, stdout + stderr))
            self.assertIn("usage", (stdout + stderr).lower(),
                          "sbe pr %s printed no usage: %r"
                          % (" ".join(argv), stdout + stderr))

    def test_a_genuinely_bad_flag_still_exits_2(self):
        for argv in (("--bogus",), ("verify", "12", "--repo", "octo/repo", "--bogus")):
            code, stdout, stderr = self.run_sbe(*argv)
            self.assertEqual(code, 2, "sbe pr %s exited %d, not the usage error 2: %s"
                             % (" ".join(argv), code, stdout + stderr))


if __name__ == "__main__":
    unittest.main(verbosity=2)
