#!/usr/bin/env python3
"""Adversarial fixtures for `sbe protections verify`.
Run: python3 tools/test_sbe_protections.py

Spec of record: BrotherSBE_RELEASE_BLOCKERS_FOR_FABLE.md item 5. These fixtures
assert exactly what 5.1 and 5.3 say, at the founder-scoped solo level recorded
in the brief for BR-1015: the CODEOWNERS file and the verifier are in scope,
configuring GitHub itself and editing workflows are human steps and are not.

ZERO NETWORK. No test here reaches GitHub. The control-logic tests inject
FakeGh, defined below, which answers from an in-memory table and asserts
GET-only itself; the CLI tests run with no token and an empty PATH, which is
the no-access path, where the module makes zero requests by construction.

CALIBRATION. Five defects were reinjected into src/brothersbe/protections.py,
one at a time, each fixture run against the broken file and then against the
restored one. The verbatim assertion each defect produced:

  1. `_boolean_rule_fact` no longer testing key membership first, so an ABSENT
     key read as False. Red in test_a_missing_boolean_key_is_no_data_not_fail:
     "AssertionError: 'FAIL' != 'NO-DATA'".
  2. The no-access path emitting no controls at all. Red in
     test_no_access_is_no_data_and_never_pass:
     "AssertionError: 'NO-SUCH-CONTROL' != 'NO-DATA'".
  3. `_bypass_actors` ignoring its unreadable list. Red in
     test_unreadable_ruleset_detail_is_no_data_not_an_empty_list:
     "AssertionError: 'PASS' != 'NO-DATA'".
  4. CODEOWNERS coverage matched by prefix instead of exact pattern, so a file
     owning "/tools/" was read as owning "/tools/sbe_gate.py". Red in
     test_a_broader_pattern_does_not_cover_a_named_path: the detail then read
     "covers 5 of the 11 control plane paths ... uncovered: /hooks/,
     /.claude-plugin/, ..." with /tools/sbe_gate.py silently counted as owned.
  5. `--strict` ignored in `_bypass_actors`. Red in
     test_strict_refuses_any_bypass_actor: the detail fell back to the default
     mode sentence and "5.2.10" was not in it.
Each of the five exited 1 with the defect in place and 0 with it restored. The
whole file passing against the shipped module is the green half; that list is
the red half, and it is why each assertion is worth its line.
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
PROTECTIONS_SOURCE = os.path.join(SRC, "brothersbe", "protections.py")

# Synthetic, and deliberately not shaped like tools/sbe_telemetry.py's
# github-token pattern: this canary proves protections.py itself never echoes a
# token, rather than proving some unrelated redaction helper caught it.
CANARY_TOKEN = "ghp-canary-not-real-456"

REPO = "khalilmaaouni/BrotherSBE"
BRANCH = "main"
RULESET_ID = 42

# The eleven control plane paths item 5.1 names. Kept here as data so a fixture
# can build a CODEOWNERS file that covers all of them, or all but one.
WANTED_PATHS = (
    "/.github/workflows/",
    "/.github/actions/sbe-consumer/",
    "/hooks/",
    "/.claude-plugin/",
    "/.sbe/policy.yml",
    "/.sbe/checks.yml",
    "/src/brothersbe/evidence.py",
    "/src/brothersbe/policy.py",
    "/tools/sbe_*hook*.py",
    "/tools/sbe_*guard*.py",
    "/tools/sbe_gate.py",
)


# ---------------------------------------------------------------------------
# Canned gh api shapes. Modeled on the branch-rules list and the ruleset detail
# the verifier reads; each payload carries only the fields a control uses.
# ---------------------------------------------------------------------------

def pull_request_rule(code_owner=True, dismiss_stale=True, last_push=True,
                      parameters=None):
    rule = {"type": "pull_request", "ruleset_id": RULESET_ID,
            "ruleset_source": REPO}
    if parameters is None:
        parameters = {"required_approving_review_count": 1,
                      "require_code_owner_review": code_owner,
                      "dismiss_stale_reviews_on_push": dismiss_stale,
                      "require_last_push_approval": last_push,
                      "required_review_thread_resolution": True}
    rule["parameters"] = parameters
    return rule


def status_checks_rule(contexts=("brothersbe-gates / linux-py39",
                                 "brothersbe-gates / macos-py39"),
                       parameters=None):
    rule = {"type": "required_status_checks", "ruleset_id": RULESET_ID,
            "ruleset_source": REPO}
    if parameters is None:
        parameters = {"strict_required_status_checks_policy": True,
                      "required_status_checks": [{"context": c} for c in contexts]}
    rule["parameters"] = parameters
    return rule


def simple_rule(kind):
    return {"type": kind, "ruleset_id": RULESET_ID, "ruleset_source": REPO,
            "parameters": {}}


def good_rules():
    """The branch-rules list a fully configured main branch returns."""
    rules = [pull_request_rule(), status_checks_rule(),
             simple_rule("non_fast_forward"), simple_rule("deletion")]
    return rules


def ruleset_detail(actors=()):
    return {"id": RULESET_ID, "name": "main protection", "target": "branch",
            "enforcement": "active", "bypass_actors": list(actors)}


def bypass_actor(actor_id=5, actor_type="RepositoryRole", mode="always"):
    return {"actor_id": actor_id, "actor_type": actor_type, "bypass_mode": mode}


class FakeGh(object):
    """A canned (method, path, token) router.

    Routes match by SUBSTRING CONTAINMENT against the path the module built,
    longest needle first, so a test can can "/rulesets/42" and "/rules/branches"
    in either order without one shadowing the other. Every call is recorded, and
    a method other than GET fails the test on the spot: the read-only law is not
    something a canned transport gets to be lenient about.
    """

    def __init__(self, routes, case):
        self.routes = dict(routes)
        self.case = case
        self.calls = []

    def __call__(self, method, path, token):
        self.calls.append(path)
        self.case.assertEqual(method, "GET",
                              "this client is read-only; it built a %s request for %s"
                              % (method, path))
        for needle in sorted(self.routes, key=len, reverse=True):
            if needle in path:
                answer = self.routes[needle]
                return answer[0], answer[1], answer[2]
        self.case.fail("no canned route matched %r; canned: %s"
                       % (path, sorted(self.routes)))


def ok(body):
    """A canned 200 with a parsed body."""
    answer = (200, body, None)
    return answer


def http(status):
    """A canned HTTP error status with no body."""
    answer = (status, None, None)
    return answer


def silent(reason="the machine is offline"):
    """A canned no-answer-at-all."""
    answer = (None, None, reason)
    return answer


def full_routes(actors=()):
    routes = {"/rules/branches": ok(good_rules()),
              "/rulesets/%d" % RULESET_ID: ok(ruleset_detail(actors))}
    return routes


def codeowners_text(skip=(), owner="@khalilmaaouni", ownerless=()):
    lines = ["# a synthetic CODEOWNERS for the fixtures"]
    for path in WANTED_PATHS:
        if path in skip:
            continue
        if path in ownerless:
            lines.append(path)
        else:
            lines.append("%s %s" % (path, owner))
    return "\n".join(lines) + "\n"


def verdict_of(report, name):
    """The verdict recorded for one control name, or a sentence saying it is
    absent. One value, never a pair: this is a fixture helper, not a verdict
    source, and evals/test_no_data_class.py reads two-value returns in tools/
    as possible verdict pairs."""
    for control in report["controls"]:
        if control["name"] == name:
            return control["verdict"]
    return "NO-SUCH-CONTROL"


def detail_of(report, name):
    for control in report["controls"]:
        if control["name"] == name:
            return control["detail"]
    return ""


NETWORK_FACTS = ("RULESET EXISTS", "REQUIRED CHECKS", "CODEOWNERS REVIEW",
                 "STALE REVIEWS DISMISSED", "LAST PUSH APPROVAL",
                 "FORCE PUSH DISABLED", "BYPASS ACTORS")


class ProtectionsCase(unittest.TestCase):
    """Imports the module once, in setUp, so a missing module ERRORs one time in
    one place rather than as a scattered AttributeError per test."""

    def setUp(self):
        if SRC not in sys.path:
            sys.path.insert(0, SRC)
        from brothersbe import protections
        self.mod = protections
        self.tmp = tempfile.mkdtemp(prefix="sbe-protections-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_codeowners(self, text, rel=".github/CODEOWNERS"):
        path = os.path.join(self.tmp, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def evaluate(self, **kwargs):
        kwargs.setdefault("token", CANARY_TOKEN)
        kwargs.setdefault("root", self.tmp)
        return self.mod.evaluate(REPO, BRANCH, kwargs.pop("token"), **kwargs)


# ---------------------------------------------------------------------------
# The local leg: CODEOWNERS, no network needed
# ---------------------------------------------------------------------------

class TestCodeownersLeg(ProtectionsCase):
    def test_an_owner_inside_a_comment_is_not_an_owner(self):
        """GitHub treats '#' as start-of-comment anywhere on a line, so a
        pattern whose only owner sits behind a '#' has NO owner. Calibrated
        against the defect a hostile refuter found here: before the parser cut
        comments, '/hooks/ # @owner' read as real coverage, which is a control
        reporting protection that a human had commented out."""
        from brothersbe.protections import parse_codeowners
        entries, unparsed = parse_codeowners(
            "# /a/ @fully-commented-out\n"
            "/hooks/ # @owner-inside-a-comment\n"
            "/real/ @genuine-owner # a trailing note is only a note\n")
        patterns = [pattern for pattern, _owners in entries]
        self.assertNotIn("/hooks/", patterns,
                         "a pattern whose owner is inside a comment was counted as owned")
        self.assertNotIn("/a/", patterns,
                         "a fully commented line was counted as an entry")
        self.assertEqual([("/real/", ["@genuine-owner"])], entries,
                         "a trailing comment must not remove a real owner")
        self.assertTrue(any("/hooks/" in line for line in unparsed),
                        "the ignored pattern has to be named, never dropped in silence")

    def test_full_coverage_passes_both_local_controls(self):
        self.write_codeowners(codeowners_text())
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertEqual(verdict_of(report, "CODEOWNERS FILE"), "PASS")
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "PASS")

    def test_a_pass_still_says_coverage_is_not_independence(self):
        self.write_codeowners(codeowners_text())
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertIn("coverage is not independence",
                      detail_of(report, "CODEOWNERS COVERAGE"))

    def test_no_codeowners_file_at_all_is_fail_not_no_data(self):
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertEqual(verdict_of(report, "CODEOWNERS FILE"), "FAIL")
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "FAIL")
        self.assertEqual(report["final"], "FAIL")

    def test_one_uncovered_control_plane_path_fails_and_is_named(self):
        self.write_codeowners(codeowners_text(skip=("/hooks/",)))
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "FAIL")
        self.assertIn("/hooks/", detail_of(report, "CODEOWNERS COVERAGE"))

    def test_a_pattern_with_no_owner_does_not_count_as_coverage(self):
        self.write_codeowners(codeowners_text(ownerless=("/tools/sbe_gate.py",)))
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "FAIL")
        self.assertIn("/tools/sbe_gate.py", detail_of(report, "CODEOWNERS COVERAGE"))

    def test_a_broader_pattern_does_not_cover_a_named_path(self):
        self.write_codeowners("/tools/ @khalilmaaouni\n/.github/ @khalilmaaouni\n")
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "FAIL")
        self.assertIn("/tools/sbe_gate.py", detail_of(report, "CODEOWNERS COVERAGE"))

    def test_an_empty_codeowners_file_is_fail(self):
        self.write_codeowners("# nothing but a comment\n")
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertEqual(verdict_of(report, "CODEOWNERS FILE"), "FAIL")

    def test_a_root_that_is_not_a_directory_is_no_data_never_pass(self):
        report = self.evaluate(fetch=FakeGh(full_routes(), self),
                               root=os.path.join(self.tmp, "absent"))
        self.assertEqual(verdict_of(report, "CODEOWNERS FILE"), "NO-DATA")
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "NO-DATA")

    def test_the_shipped_codeowners_file_covers_every_control_plane_path(self):
        """The done-check on the deliverable itself, not on a fixture."""
        report = self.evaluate(fetch=FakeGh(full_routes(), self), root=ROOT)
        self.assertEqual(verdict_of(report, "CODEOWNERS FILE"), "PASS",
                         detail_of(report, "CODEOWNERS FILE"))
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "PASS",
                         detail_of(report, "CODEOWNERS COVERAGE"))

    def test_the_shipped_codeowners_file_states_the_solo_limit(self):
        path = os.path.join(ROOT, ".github", "CODEOWNERS")
        with io.open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("ONE human", text)
        self.assertIn("CANNOT stop the", text)
        self.assertIn("@khalilmaaouni", text)

    def test_a_utf8_codeowners_still_reads_on_a_non_utf8_code_page(self):
        """Same class as versionbump's TestNonUtf8CodePage: the reader opened
        CODEOWNERS with no encoding= argument, so it read in the process
        locale encoding. Under a strict locale (C here) a UTF-8 byte raised
        UnicodeDecodeError, which the except clause turned into NO-DATA over
        a file that exists and is perfectly readable; permissive pages (1252,
        and 932 for these bytes) would instead silently mis-decode, which the
        explicit utf-8 read also ends. The C locale reproduces the raising
        mechanism; Windows itself stays UNVERIFIED until a real machine runs
        docs/WINDOWS-CHECK.md. Calibrated against the unfixed line: this test
        then fails with "AssertionError: 'NO-DATA' != 'PASS'"."""
        self.write_codeowners(codeowners_text() +
                              "# maintained with Björn Kjær\n")
        probe = (
            "import json, sys\n"
            "from brothersbe import protections\n"
            "controls = []\n"
            "def control(name, verdict, detail):\n"
            "    controls.append([name, verdict])\n"
            "protections._codeowners_controls(sys.argv[1], control)\n"
            "sys.stdout.write(json.dumps(controls))\n"
        )
        env = dict(os.environ)
        env.update(LC_ALL="C", LANG="C", LANGUAGE="",
                   PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONPATH=SRC)
        out = subprocess.run([sys.executable, "-c", probe, self.tmp],
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        verdicts = dict(json.loads(out.stdout))
        self.assertEqual(verdicts.get("CODEOWNERS FILE"), "PASS",
                         "a UTF-8 CODEOWNERS must read on a non-UTF-8 code "
                         "page; got %r" % (verdicts,))
        self.assertEqual(verdicts.get("CODEOWNERS COVERAGE"), "PASS", verdicts)


# ---------------------------------------------------------------------------
# The no-access path
# ---------------------------------------------------------------------------

class TestNoAccessPath(ProtectionsCase):
    def test_no_access_is_no_data_and_never_pass(self):
        self.write_codeowners(codeowners_text())
        fake = FakeGh({}, self)
        report = self.evaluate(token=None, fetch=fake)
        for name in NETWORK_FACTS:
            self.assertEqual(verdict_of(report, name), "NO-DATA",
                             "%s answered %s with no GitHub access"
                             % (name, verdict_of(report, name)))
        self.assertEqual(report["final"], "NO-DATA")

    def test_no_access_makes_zero_requests(self):
        self.write_codeowners(codeowners_text())
        fake = FakeGh({}, self)
        self.evaluate(token=None, fetch=fake)
        self.assertEqual(fake.calls, [])

    def test_every_no_data_carries_the_remedy(self):
        report = self.evaluate(token=None, fetch=FakeGh({}, self))
        for name in NETWORK_FACTS:
            self.assertIn("gh auth login", detail_of(report, name))

    def test_the_report_says_the_release_checklist_treats_no_data_as_blocking(self):
        report = self.evaluate(token=None, fetch=FakeGh({}, self))
        rendered = self.mod.render(report)
        self.assertIn("BLOCKING", rendered)
        self.assertIn("exits nonzero", rendered)

    def test_the_local_leg_still_produces_findings_without_access(self):
        self.write_codeowners(codeowners_text())
        report = self.evaluate(token=None, fetch=FakeGh({}, self))
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "PASS")


# ---------------------------------------------------------------------------
# The seven ruleset facts of 5.3
# ---------------------------------------------------------------------------

class TestSevenRulesetFacts(ProtectionsCase):
    def setUp(self):
        ProtectionsCase.setUp(self)
        self.write_codeowners(codeowners_text())

    def test_a_fully_configured_branch_passes_every_fact(self):
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        for name in NETWORK_FACTS:
            self.assertEqual(verdict_of(report, name), "PASS",
                             "%s: %s" % (name, detail_of(report, name)))
        self.assertEqual(report["final"], "PASS")

    def test_no_ruleset_at_all_fails_every_network_fact(self):
        report = self.evaluate(fetch=FakeGh({"/rules/branches": ok([])}, self))
        for name in NETWORK_FACTS:
            self.assertEqual(verdict_of(report, name), "FAIL",
                             "%s: %s" % (name, detail_of(report, name)))

    def test_a_missing_status_checks_rule_fails(self):
        rules = [pull_request_rule(), simple_rule("non_fast_forward")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        self.assertEqual(verdict_of(report, "REQUIRED CHECKS"), "FAIL")

    def test_a_status_checks_rule_naming_zero_contexts_fails(self):
        rules = [pull_request_rule(), status_checks_rule(contexts=()),
                 simple_rule("non_fast_forward")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        self.assertEqual(verdict_of(report, "REQUIRED CHECKS"), "FAIL")

    def test_a_missing_contexts_parameter_is_no_data_not_pass(self):
        rules = [pull_request_rule(),
                 status_checks_rule(parameters={"strict_required_status_checks_policy": True}),
                 simple_rule("non_fast_forward")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        self.assertEqual(verdict_of(report, "REQUIRED CHECKS"), "NO-DATA")

    def test_code_owner_review_not_required_fails(self):
        rules = [pull_request_rule(code_owner=False), status_checks_rule(),
                 simple_rule("non_fast_forward")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        self.assertEqual(verdict_of(report, "CODEOWNERS REVIEW"), "FAIL")
        self.assertEqual(report["final"], "FAIL")

    def test_stale_reviews_not_dismissed_fails(self):
        rules = [pull_request_rule(dismiss_stale=False), status_checks_rule(),
                 simple_rule("non_fast_forward")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        self.assertEqual(verdict_of(report, "STALE REVIEWS DISMISSED"), "FAIL")

    def test_last_push_approval_not_required_fails(self):
        rules = [pull_request_rule(last_push=False), status_checks_rule(),
                 simple_rule("non_fast_forward")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        self.assertEqual(verdict_of(report, "LAST PUSH APPROVAL"), "FAIL")

    def test_a_missing_boolean_key_is_no_data_not_fail(self):
        """A key GitHub did not send is a fact this run could not read. It is
        NO-DATA, never PASS, and never a FAIL invented out of an absent key."""
        rules = [pull_request_rule(parameters={"required_approving_review_count": 1}),
                 status_checks_rule(), simple_rule("non_fast_forward")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        for name in ("CODEOWNERS REVIEW", "STALE REVIEWS DISMISSED",
                     "LAST PUSH APPROVAL"):
            self.assertEqual(verdict_of(report, name), "NO-DATA",
                             "%s: %s" % (name, detail_of(report, name)))

    def test_no_pull_request_rule_fails_all_three_review_facts(self):
        rules = [status_checks_rule(), simple_rule("non_fast_forward"),
                 simple_rule("deletion")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        for name in ("CODEOWNERS REVIEW", "STALE REVIEWS DISMISSED",
                     "LAST PUSH APPROVAL"):
            self.assertEqual(verdict_of(report, name), "FAIL")

    def test_force_push_allowed_fails(self):
        rules = [pull_request_rule(), status_checks_rule(), simple_rule("deletion")]
        report = self.evaluate(fetch=FakeGh(
            {"/rules/branches": ok(rules),
             "/rulesets/%d" % RULESET_ID: ok(ruleset_detail())}, self))
        self.assertEqual(verdict_of(report, "FORCE PUSH DISABLED"), "FAIL")


class TestBypassActors(ProtectionsCase):
    def setUp(self):
        ProtectionsCase.setUp(self)
        self.write_codeowners(codeowners_text())

    def test_no_bypass_actor_passes(self):
        report = self.evaluate(fetch=FakeGh(full_routes(), self))
        self.assertEqual(verdict_of(report, "BYPASS ACTORS"), "PASS")

    def test_strict_refuses_any_bypass_actor(self):
        routes = full_routes(actors=[bypass_actor()])
        report = self.evaluate(fetch=FakeGh(routes, self), strict=True)
        self.assertEqual(verdict_of(report, "BYPASS ACTORS"), "FAIL")
        self.assertIn("5.2.10", detail_of(report, "BYPASS ACTORS"))

    def test_an_undeclared_bypass_actor_fails_in_default_mode(self):
        routes = full_routes(actors=[bypass_actor()])
        report = self.evaluate(fetch=FakeGh(routes, self))
        self.assertEqual(verdict_of(report, "BYPASS ACTORS"), "FAIL")

    def test_an_explicitly_declared_bypass_actor_passes_in_default_mode(self):
        routes = full_routes(actors=[bypass_actor()])
        report = self.evaluate(fetch=FakeGh(routes, self),
                               allowed_bypass_actors=["RepositoryRole:5(always)"])
        self.assertEqual(verdict_of(report, "BYPASS ACTORS"), "PASS",
                         detail_of(report, "BYPASS ACTORS"))

    def test_unreadable_ruleset_detail_is_no_data_not_an_empty_list(self):
        routes = {"/rules/branches": ok(good_rules()),
                  "/rulesets/%d" % RULESET_ID: http(403)}
        report = self.evaluate(fetch=FakeGh(routes, self))
        self.assertEqual(verdict_of(report, "BYPASS ACTORS"), "NO-DATA")

    def test_a_ruleset_detail_without_the_key_is_no_data(self):
        routes = {"/rules/branches": ok(good_rules()),
                  "/rulesets/%d" % RULESET_ID: ok({"id": RULESET_ID, "name": "main"})}
        report = self.evaluate(fetch=FakeGh(routes, self))
        self.assertEqual(verdict_of(report, "BYPASS ACTORS"), "NO-DATA")


class TestTransportFailures(ProtectionsCase):
    def setUp(self):
        ProtectionsCase.setUp(self)
        self.write_codeowners(codeowners_text())

    def test_permission_denied_is_unverifiable_never_pass(self):
        report = self.evaluate(fetch=FakeGh({"/rules/branches": http(403)}, self))
        for name in NETWORK_FACTS:
            self.assertEqual(verdict_of(report, name), "UNVERIFIABLE")
        self.assertEqual(report["final"], "UNVERIFIABLE")

    def test_a_404_is_unverifiable_not_an_unprotected_branch(self):
        report = self.evaluate(fetch=FakeGh({"/rules/branches": http(404)}, self))
        self.assertEqual(verdict_of(report, "RULESET EXISTS"), "UNVERIFIABLE")

    def test_silence_is_no_data(self):
        report = self.evaluate(fetch=FakeGh({"/rules/branches": silent()}, self))
        for name in NETWORK_FACTS:
            self.assertEqual(verdict_of(report, name), "NO-DATA")
        self.assertEqual(report["final"], "NO-DATA")

    def test_the_token_never_reaches_an_output_byte(self):
        routes = {"/rules/branches": silent("connection refused for " + CANARY_TOKEN)}
        report = self.evaluate(fetch=FakeGh(routes, self))
        rendered = self.mod.render(report) + json.dumps(report)
        self.assertNotIn(CANARY_TOKEN, rendered)
        self.assertIn("[REDACTED:token]", rendered)


# ---------------------------------------------------------------------------
# Source-level laws
# ---------------------------------------------------------------------------

FORBIDDEN_METHODS = ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
FORBIDDEN_GH_FLAGS = ("-X", "--method", "--input", "-f", "--field", "--raw-field")


def _offending_constants(tree):
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if text in FORBIDDEN_METHODS and text != "GET":
                findings.append("line %d: the literal %r appears" % (node.lineno, text))
            if text in FORBIDDEN_GH_FLAGS:
                findings.append("line %d: the gh mutating flag %r appears"
                                % (node.lineno, text))
    return findings


class TestReadOnlyLaw(unittest.TestCase):
    def test_no_mutating_method_or_gh_flag_anywhere_in_the_module(self):
        self.assertTrue(os.path.exists(PROTECTIONS_SOURCE),
                        "src/brothersbe/protections.py does not exist, so the read-only "
                        "law has nothing to check")
        with io.open(PROTECTIONS_SOURCE, encoding="utf-8") as handle:
            source = handle.read()
        findings = _offending_constants(ast.parse(source, filename=PROTECTIONS_SOURCE))
        self.assertEqual(findings, [],
                         "a read-only client must never construct a mutating request:\n  "
                         + "\n  ".join(findings))


# ---------------------------------------------------------------------------
# The CLI surface
# ---------------------------------------------------------------------------

class TestCliSurface(unittest.TestCase):
    def run_sbe(self, argv, env_extra=None):
        env = dict(os.environ)
        # No token anywhere, and no gh on PATH: this is the no-access path, and
        # it is the one CLI shape that opens nothing.
        env["GITHUB_TOKEN"] = ""
        env["GH_TOKEN"] = ""
        env["PATH"] = tempfile.gettempdir()
        env.update(env_extra or {})
        out = subprocess.run([sys.executable, SBE, "protections"] + list(argv),
                             capture_output=True, text=True, cwd=ROOT, env=env)
        return out

    def test_help_exits_zero_with_usage(self):
        for argv in (("-h",), ("--help",), ("verify", "-h"), ("verify", "--help")):
            out = self.run_sbe(argv)
            self.assertEqual(out.returncode, 0,
                             "sbe protections %s exited %d: %s"
                             % (" ".join(argv), out.returncode, out.stdout + out.stderr))
            self.assertIn("usage", (out.stdout + out.stderr).lower())

    def test_a_bad_flag_exits_two(self):
        out = self.run_sbe(("verify", "--repository", REPO, "--bogus"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_a_malformed_repository_exits_two(self):
        out = self.run_sbe(("verify", "--repository", "../evil", "--branch", BRANCH))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_a_malformed_branch_exits_two(self):
        out = self.run_sbe(("verify", "--repository", REPO, "--branch", "--strictly"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_the_briefed_invocation_runs_and_reports_no_data(self):
        out = self.run_sbe(("verify", "--repository", REPO, "--branch", BRANCH,
                            "--strict"))
        self.assertIn("NO-DATA", out.stdout)
        self.assertIn("BLOCKING", out.stdout)
        self.assertNotIn("Traceback", out.stderr)

    def test_no_access_exits_nonzero(self):
        out = self.run_sbe(("verify", "--repository", REPO, "--branch", BRANCH,
                            "--strict"))
        self.assertNotEqual(out.returncode, 0,
                            "a run that could not look must not exit 0: %s" % out.stdout)

    def test_the_local_leg_still_runs_from_the_cli(self):
        out = self.run_sbe(("verify", "--repository", REPO, "--branch", BRANCH,
                            "--cwd", ROOT, "--json"))
        report = json.loads(out.stdout)
        names = [c["name"] for c in report["controls"]]
        self.assertIn("CODEOWNERS COVERAGE", names)
        self.assertEqual(verdict_of(report, "CODEOWNERS COVERAGE"), "PASS",
                         detail_of(report, "CODEOWNERS COVERAGE"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
