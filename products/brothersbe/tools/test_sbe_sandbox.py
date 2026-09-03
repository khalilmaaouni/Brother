#!/usr/bin/env python3
"""Plan 12.4, Lane C4: doc-truth for docs/guides/00-sandbox.md.

Run: python3 tools/test_sbe_sandbox.py

The doc-truth idiom this project already uses (`evals/run_evals.py`'s
`dc7`, `evals/replay_guide05.py`, `evals/replay_book.py`): a page that claims
"every command was run and every block of output is what it printed" is only
true if something mechanical keeps checking it. This suite builds the exact
sandbox `tools/fixtures/sandbox/build_sandbox.py` builds, drives the exact
eight-step sequence `docs/guides/00-sandbox.md` walks a reader through
through the REAL engine (`bin/sbe` and the `tools/sbe_*.py` scripts, as real
subprocesses, real git, nothing mocked), and asserts that every verdict line
the guide quotes is a line the tools actually printed there.

Scope, stated once here rather than left implicit (the golden scenario
suite's own discipline, `tools/test_sbe_golden_scenario.py`): THREE kinds of
line are deliberately NOT held to a byte-exact match, because no sandbox
build can make them portable, and the guide says so at each one:

  1. `sbe evidence run`'s duration (a real clock reading) and the receipt id
     `sbe work finish` prints (derived from the receipt's own bytes,
     including that duration): checked as a fixed prefix and suffix around
     the variable middle, never the exact digits.
  2. The absolute worktree path `sbe work start` prints, and every absolute
     path this suite's OWN temp-dir build produces (`sbe review` and `sbe
     handover prepare` both print one): checked as a fixed prefix and
     suffix, never the path itself, because the guide's path
     (`~/sbe-sandbox/...`) and this suite's (a fresh `tempfile.mkdtemp()`)
     are never going to be the same string, and neither would a real
     reader's `~` expand to what either of us has.
  3. `sbe doctor`'s `tools`, `plugin-manifest`, `vault` and `private-names`
     lines: `tools` and `plugin-manifest` name wherever THIS install lives
     on disk, which is a different path (and, after a version bump, a
     different version string) on every machine and in every clone; `vault`
     and `private-names` read the CALLING machine's own environment
     variables and home directory, which this suite explicitly clears (see
     `_STRIP_ENV` and `_clean_env`) to get the same honest "not configured
     yet" reading the
     guide shows, but which a real reader's own machine may answer either
     way. Only `identity` is pinned exactly: it reads the SANDBOX repository's
     own local git config, which `build_sandbox.py` sets, so it reads the
     same on every machine. `sbe review`'s "CHECKS FED BY A VAULT OR
     REGISTRY" block (11 of its 12 lines, `citation-inventory` through
     `review-cadence`) is EXCLUDED from this suite entirely for the same
     reason: those checks are about the INSTALLING MACHINE and the
     INSTALLED SKILL'S OWN TREE, never about the say-hello change, and the
     guide says so in prose rather than pinning the counts.

Red-first, mirroring this project's own "no invented transcripts" discipline
in place of a source-code behavior change: `TestSandboxJourneyMatchesGuide`
below was first run against a copy of the guide with one line deliberately
wrong (a mismatched tier and a mismatched recommendation both proved a
mismatch failure, never an ERROR, before either line was corrected to what
the tools actually printed).
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
GUIDE = os.path.join(ROOT, "docs", "guides", "00-sandbox.md")
FIXTURE_DIR = os.path.join(HERE, "fixtures", "sandbox")
if FIXTURE_DIR not in sys.path:
    sys.path.insert(0, FIXTURE_DIR)
import build_sandbox as bsb  # noqa: E402  (path setup has to come first)


def _guide_text():
    with io.open(GUIDE, encoding="utf-8") as fh:
        return fh.read()


# Env vars that make a doctor/review run depend on the CALLING machine
# rather than on the sandbox this suite builds: cleared before every
# subprocess below, exactly the discipline docs/guides/00-sandbox.md's own
# transcript was captured under, and named in the module docstring above.
_STRIP_ENV = ("BROTHERSBE_VAULT", "BROTHERSBE_REGISTRIES",
             "BROTHERSBE_PRIVATE_NAMES", "SBE_CITATION_ROOT")


def _clean_env():
    env = dict(os.environ)
    for key in _STRIP_ENV:
        env.pop(key, None)
    return env


def run(argv, cwd, stdin=None):
    """One subprocess, three values (see build_sandbox.run_sbe's own
    docstring for why every runner helper in this project's fixtures
    returns three rather than a (verdict, evidence)-shaped pair)."""
    out = subprocess.run([sys.executable] + list(argv), cwd=cwd, input=stdin,
                         capture_output=True, text=True, env=_clean_env())
    return out.returncode, out.stdout + out.stderr, out.stderr


class TestSandboxJourneyMatchesGuide(unittest.TestCase):
    """Builds the sandbox once, drives docs/guides/00-sandbox.md's eight
    steps in order through the real engine, and holds the captured output
    for every test method below to assert against. One build, one journey,
    read many times -- the same fixture-per-class discipline `tools/
    test_sbe_golden_scenario.py` uses, because re-driving eight real
    subprocess steps (two of them opening a git worktree) once per
    assertion would make this suite slow for no honesty gained."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sbe-sandbox-test-")
        cls.repo = os.path.join(cls.tmp, "repo")
        cls.worktrees = os.path.join(cls.tmp, "worktrees")
        os.makedirs(cls.worktrees)
        cls.built = bsb.build(cls.repo, SBE)
        if cls.built["validate_code"] != 0:
            raise AssertionError("fixture setup wrote a plan `sbe plan` itself refuses; "
                                 "this is a fixture bug, not a doc-truth finding: %s"
                                 % cls.built["validate_text"])
        cls.dossier = cls.built["dossier"]
        cls.out = {}
        cls._drive()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _drive(cls):
        out = cls.out

        # Step 1: install health.
        code, text, _err = run([SBE, "doctor"], cwd=cls.repo)
        out["doctor_code"], out["doctor"] = code, text

        # Step 2: describe an outcome.
        code, text, _err = run([os.path.join(ROOT, "tools", "sbe_intake.py")],
                               cwd=cls.dossier, stdin="n\nn\ny\nn\nnone\n")
        out["intake_code"], out["intake"] = code, text

        # Step 3: see the risk level. Run from the REPO ROOT, not the
        # dossier, mirroring docs/guides/00-sandbox.md's own `cd
        # ~/sbe-sandbox/repo` before this command: the dossier directory
        # never sees the .brothersbe project-init folder sitting beside
        # `design/`, so a run from inside the dossier can never show it, and
        # the guide's own live-captured block names it by name.
        code, text, _err = run([os.path.join(ROOT, "tools", "sbe_design.py"),
                                "artifacts", "."], cwd=cls.repo)
        out["design_code"], out["design"] = code, text

        # Step 4: accept one decision, then write it down and commit it
        # alongside the intake answers, using build_sandbox's own pinned-
        # identity, pinned-date git() so the resulting hash is the same
        # every run, on every machine (see build_sandbox.git's docstring).
        code, text, _err = run(
            [os.path.join(ROOT, "tools", "sbe_decide.py"),
             os.path.join(ROOT, "tables", "architecture.json"), "shape",
             "deploying_teams=1", "consistency=strong", "ops_maturity=low",
             "failure_isolation=low"], cwd=cls.dossier)
        out["decide_code"], out["decide"] = code, text

        bsb.write(cls.repo, "design/say-hello/03-decision.md",
                 "# 03. Decision: architecture shape\n\n"
                 "Scored by tools/sbe_decide.py against tables/architecture.json, shape "
                 "table,\nfor a one-person project with low ops maturity.\n\n"
                 "Recommendation: modular monolith. Accepted: keep the greeting service "
                 "as one\nmodule in this repository, not a separate service.\n")
        bsb.git(cls.repo, "add", "-A")
        bsb.git(cls.repo, "commit", "-qm",
               "intake and decision: describe the change and accept its shape")
        out["decision_head"] = bsb.git(cls.repo, "rev-parse", "HEAD")

        # Step 5: start one task, then do the work.
        code, text, _err = run(
            [SBE, "work", "start", "T01", "--plan",
             os.path.join("design", "say-hello", "08-plan.json"),
             "--worktree-dir", cls.worktrees, "--agent", "you", "--cwd", "."],
            cwd=cls.repo)
        out["start_code"], out["start"] = code, text
        cls.worktree = os.path.join(cls.worktrees,
                                    "%s-sbe-T01" % os.path.basename(cls.repo))

        bsb.write(cls.worktree, bsb.SERVICE_PATH,
                 "def greet(name):\n    return \"Hello, %s!\" % name\n")
        bsb.write(cls.worktree, bsb.SQL_PATH,
                 "-- Log every greeting so we can see how many hellos this service has "
                 "said.\nCREATE TABLE greeting_log (\n    id INTEGER PRIMARY KEY,\n"
                 "    name TEXT NOT NULL,\n    greeted_at TEXT NOT NULL\n);\n")
        bsb.git(cls.worktree, "add", "-A")
        bsb.git(cls.worktree, "commit", "-qm",
               "hello: add the greeting service and its log table")

        # Step 6: run proof, then finish.
        receipt = os.path.join(cls.repo, ".sbe", "evidence", "T01-receipt.json")
        code, text, _err = run(
            [SBE, "evidence", "run", "--out", receipt, "--kind", "gate", "--cwd", ".",
             "--"] + bsb.VERIFY_ARGV, cwd=cls.worktree)
        out["evidence_code"], out["evidence"] = code, text

        code, text, _err = run([SBE, "work", "finish", "T01", "--cwd", "."], cwd=cls.repo)
        out["finish_code"], out["finish"] = code, text

        # Step 7: review.
        code, text, _err = run(
            [SBE, "review", "design/say-hello", "--write", "--reviewer", "you",
             "--reviewer-type", "human", "--result", "approved"], cwd=cls.repo)
        out["review_code"], out["review"] = code, text

        # Step 8: reach the prepared handover.
        code, text, _err = run(
            [SBE, "handover", "prepare", "design/say-hello", "--outgoing",
             "you@example.invalid", "--receiver", "teammate@example.invalid"],
            cwd=cls.repo)
        out["handover_code"], out["handover"] = code, text

    # -- small helpers ------------------------------------------------------

    def assertGuideAndLiveBothSay(self, exact_line, live_text):
        """The strictest form: one exact line, quoted verbatim in the guide
        AND printed verbatim by a live run."""
        self.assertIn(exact_line, _guide_text(),
                     "docs/guides/00-sandbox.md no longer quotes:\n  %r" % (exact_line,))
        self.assertIn(exact_line, live_text,
                     "the tools no longer print the line docs/guides/00-sandbox.md "
                     "quotes:\n  %r\ngot:\n%s" % (exact_line, live_text))

    def assertGuideAndLiveBothMatch(self, prefix, suffix, live_text):
        """For a line docs/guides/00-sandbox.md itself says varies (a
        duration, a receipt id, an absolute path): both copies must still
        carry the fixed prefix and suffix around whatever varies."""
        pattern = re.escape(prefix) + r".*" + re.escape(suffix)
        self.assertRegex(_guide_text(), pattern,
                         "docs/guides/00-sandbox.md no longer carries a line shaped like "
                         "%r ... %r" % (prefix, suffix))
        self.assertRegex(live_text, pattern,
                         "the tools no longer print a line shaped like %r ... %r\ngot:\n%s"
                         % (prefix, suffix, live_text))

    # -- step 1: install health ----------------------------------------------

    def test_step1_doctor_identity_line_is_the_sandboxs_own_and_exits_clean(self):
        self.assertEqual(self.out["doctor_code"], 0, self.out["doctor"])
        self.assertGuideAndLiveBothSay(
            "identity         PASS     git config reports name \"Sandbox Fixture\" and "
            "email \"sandbox@example.invalid\"", self.out["doctor"])
        # tools/plugin-manifest/vault/private-names are install- and
        # machine-specific (module docstring, point 3): only the check
        # NAMES and that a verdict word follows are checked, never the
        # detail text.
        for name in ("python", "tools", "plugin-manifest", "git", "vault",
                     "private-names"):
            pattern = re.compile(r"^%s\s+(PASS|FAIL|NO-DATA)" % re.escape(name), re.MULTILINE)
            self.assertRegex(self.out["doctor"], pattern, self.out["doctor"])

    # -- step 2: describe an outcome -----------------------------------------

    def test_step2_intake_writes_tier_t0_relative_to_the_dossier(self):
        self.assertEqual(self.out["intake_code"], 0, self.out["intake"])
        self.assertGuideAndLiveBothSay(
            "tier T0 (artifacts required: none) written to ./00-intake.json",
            self.out["intake"])
        self.assertGuideAndLiveBothSay(
            "To override this tier, edit that file and set all three fields: "
            "\"tier\" (the tier you are moving to), \"override\" (the same tier, "
            "declaring the move), and \"override_reason\" (at least 3 words and 12 "
            "characters). A move with any of the three missing or disagreeing FAILs "
            "the design check as an edit rather than an override.", self.out["intake"])

    # -- step 3: see the risk level -------------------------------------------

    def test_step3_design_artifacts_reads_tier_t0_requires_nothing(self):
        self.assertEqual(self.out["design_code"], 0, self.out["design"])
        # Pins the .brothersbe project-init folder by name: run from the
        # repo root (this suite's own cls.repo, matching the guide's `cd
        # ~/sbe-sandbox/repo`), find_dossiers sees TWO directories directly
        # under root (.brothersbe and design), one of which contributed no
        # dossier, and both the scope line and the dossier header say so.
        self.assertGuideAndLiveBothSay(
            "  scope      -        read 1 dossier under . (design/say-hello); 1 of "
            "2 director(y/ies) directly under . contributed no dossier "
            "(.brothersbe)", self.out["design"])
        self.assertGuideAndLiveBothSay(
            "  dossier: design/say-hello (under .)", self.out["design"])
        self.assertGuideAndLiveBothSay(
            "artifacts  NO-DATA  tier T0 requires no artifact, so this check opened "
            "none and there is nothing here it can vouch for; examined "
            "design/say-hello under . [severity: gate]", self.out["design"])

    # -- step 4: accept one decision -------------------------------------------

    def test_step4_decide_recommends_modular_monolith(self):
        self.assertEqual(self.out["decide_code"], 0, self.out["decide"])
        for line in (
            "Recommendation: modular monolith",
            "  - deploying_teams=1 favours modular monolith, monolith",
            "  - consistency=strong favours monolith, modular monolith",
            "  - ops_maturity=low favours monolith, modular monolith",
            "  - failure_isolation=low favours monolith, modular monolith",
            "What would flip this: Cross four independently deploying teams, or need "
            "one module to fail without the others while ops maturity is high, and "
            "revisit this decision.",
        ):
            self.assertGuideAndLiveBothSay(line, self.out["decide"])

    # -- step 5: start one task -------------------------------------------------

    def test_step5_work_start_opens_t01_at_the_seed_commit(self):
        self.assertEqual(self.out["start_code"], 0, self.out["start"])
        # The seed commit id is a DERIVED value: it moves whenever the sandbox
        # fixture's seeded content changes, which is why
        # tools/regen_sandbox_guide.py exists to repin the guide's copy of it.
        # Hardcoding it here made a THIRD copy that no tool owned, so a repin
        # could never make this suite green: the guide moved and this line did
        # not. It is asserted the way this class already asserts every other
        # value the guide says varies, by pinning the fixed prose on both sides
        # of it. Guide against live equality for the id itself is
        # `regen_sandbox_guide.py --check`, which is now a battery step.
        self.assertGuideAndLiveBothMatch(
            "sbe task open: T01 is open. you (writer) owns 2 path(s): "
            "src/hello/greeting.py, migrations/0001_greeting_log.sql. Base ",
            ". Close runs the diff postcondition against exactly this "
            "declaration.", self.out["start"])
        self.assertGuideAndLiveBothMatch(
            "sbe work start T01: branch sbe/say-hello/T01 at ",
            ", registry record open.", self.out["start"])
        for line in ("acceptance criteria:", "  - A visitor gets a friendly greeting "
                    "back", "verification commands:",
                    "  - python3 -c \"print('GREETING-OK')\"", "dossier sources:",
                    "  - 01-notes.md#the-change"):
            self.assertGuideAndLiveBothSay(line, self.out["start"])

    # -- step 6: run proof --------------------------------------------------

    def test_step6_evidence_run_and_finish_close_clean(self):
        self.assertEqual(self.out["evidence_code"], 0, self.out["evidence"])
        self.assertGuideAndLiveBothSay("GREETING-OK", self.out["evidence"])
        # The advisory REASON changed when the check registry landed: a
        # free-form command is now advisory because nothing outside the caller
        # says which check it is, which is a stronger statement than the older
        # "no CI run id" one and is printed whether or not a run id exists. The
        # guide was repasted from live output and this expectation follows it,
        # because a doc-truth suite that keeps the old sentence would fail the
        # page for telling the truth.
        self.assertGuideAndLiveBothSay(
            "sbe evidence run: FREE FORM run: no registered check, so this receipt "
            "is advisory and satisfies no required policy check", self.out["evidence"])
        self.assertGuideAndLiveBothMatch(
            "sbe evidence run: receipt written to ",
            ". Trust LOCAL-ADVISORY (this receipt was minted for a free-form "
            "command rather than a check registered in .sbe/checks.yml, so nothing "
            "outside the caller says which check it is. Free-form evidence is "
            "advisory whatever else is true of it). Command exited 0 in ",
            self.out["evidence"])
        # Same derived seed id as step 5, third copy, same reasoning.
        self.assertGuideAndLiveBothMatch(
            "s, over 2 covered file(s) from the diff ",
            "..HEAD. Declared check kind(s): gate. stdout and stderr are "
            "recorded as digests only. argv held 0 secret-shaped token(s) and "
            "was recorded verbatim.", self.out["evidence"])

        self.assertEqual(self.out["finish_code"], 0, self.out["finish"])
        for line in ("  IN-SCOPE   migrations/0001_greeting_log.sql",
                    "  IN-SCOPE   src/hello/greeting.py",
                    "sbe task close T01: PASS. 2 changed path(s), all inside the "
                    "declaration. Closed clean."):
            self.assertGuideAndLiveBothSay(line, self.out["finish"])
        self.assertGuideAndLiveBothMatch(
            "sbe work finish T01: closed clean with receipt ",
            "; the plan task is complete by the single-source rule.", self.out["finish"])

    # -- step 7: review -------------------------------------------------------

    def test_step7_review_hard_gates_all_no_data_and_record_written(self):
        self.assertEqual(self.out["review_code"], 0, self.out["review"])
        self.assertGuideAndLiveBothSay(
            "silent-failure-lints      NO-DATA  lint root design/say-hello holds no "
            "scannable source (.py .sql .swift .rb .js .ts .go), so nothing was "
            "opened; 4 file(s) under design/say-hello were not opened because this "
            "lint has no pattern that reads their kind (.json 2, .md 2); its "
            "patterns are written for .py .sql .swift .rb .js .ts .go, so this "
            "verdict covers those kinds and says nothing about the rest "
            "[severity: gate]", self.out["review"])
        for line in (
            "  numbers   NO-DATA  no numbers-manifest found; if this change presents "
            "no decision figure that is correct, else add one; no "
            "numbers-manifest.json read under design/say-hello; 0 of 0 "
            "director(y/ies) directly under design/say-hello contributed no "
            "numbers-manifest.json [severity: gate]",
            "  migration NO-DATA  no migration in this change, or no "
            "migration-receipt.json; no migration-receipt.json read under "
            "design/say-hello; 0 of 0 director(y/ies) directly under "
            "design/say-hello contributed no migration-receipt.json [severity: gate]",
            "  approval  NO-DATA  no APPROVAL file and no Approved-by trailer; if "
            "this change touches no money or partner path that is correct; no "
            "APPROVAL read under design/say-hello; 0 of 0 director(y/ies) directly "
            "under design/say-hello contributed no APPROVAL [severity: gate]",
            "  ran       NO-DATA  no ran-receipt.json; a SQL or pipeline change is "
            "not done until its check executed and left a receipt; no "
            "ran-receipt.json read under design/say-hello; 0 of 0 director(y/ies) "
            "directly under design/say-hello contributed no ran-receipt.json "
            "[severity: gate]",
            "sbe review: exit 0 means no control FAILED. It does not mean a control "
            "passed. Read the verdict lines above: NO-DATA examined nothing and "
            "WAIVED suppressed a finding, and neither one is a pass.",
        ):
            self.assertGuideAndLiveBothSay(line, self.out["review"])

        # The bound head is the decision commit from step 4: real, derived
        # here, never hand-copied, and it must be the SAME hash the guide
        # quotes (proving the guide's git-identity-and-date pinning still
        # produces the bytes it claims to).
        want_head = self.out["decision_head"][:12]
        self.assertGuideAndLiveBothSay(
            "sbe review: review record written, bound to head %s, 0 finding(s) and "
            "0 accepted risk(s):" % want_head, self.out["review"])

    # -- step 8: reach the prepared handover -----------------------------------

    def test_step8_handover_prepare_names_one_task_done(self):
        self.assertEqual(self.out["handover_code"], 0, self.out["handover"])
        want_head = self.out["decision_head"][:12]
        self.assertGuideAndLiveBothSay(
            "sbe handover prepare: written, bound to head %s: 1 done, 0 in flight, "
            "0 not started, 5 evidence item(s). From you@example.invalid to "
            "teammate@example.invalid. Ownership remains with you@example.invalid "
            "until teammate@example.invalid acknowledges." % want_head,
            self.out["handover"])


class TestSandboxBuildIsSmallerThanGoldenScenario(unittest.TestCase):
    """Pass criterion, stated in plan 12.4 Lane C4: the sandbox is smaller
    than the golden scenario builder it mirrors -- one task, not four, and
    no team-registry machinery. Checked directly against the plan this
    builder writes, not asserted by reading the source and trusting it."""

    def test_the_plan_has_exactly_one_task(self):
        tmp = tempfile.mkdtemp(prefix="sbe-sandbox-size-")
        try:
            repo = os.path.join(tmp, "repo")
            built = bsb.build(repo, SBE)
            self.assertEqual(built["validate_code"], 0, built["validate_text"])
            import json
            with io.open(built["plan_path"], encoding="utf-8") as fh:
                plan = json.load(fh)
            self.assertEqual(len(plan["tasks"]), 1, plan)
            self.assertEqual(plan["tasks"][0]["role"], "writer", plan)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_builder_writes_no_intake_or_adr_for_the_reader_to_find_prewritten(self):
        # docs/guides/00-sandbox.md's own claim (step 2 and step 4): intake
        # and the decision file are the READER's first two live actions,
        # not something the builder hands them pre-written.
        tmp = tempfile.mkdtemp(prefix="sbe-sandbox-size-")
        try:
            repo = os.path.join(tmp, "repo")
            built = bsb.build(repo, SBE)
            self.assertFalse(
                os.path.exists(os.path.join(built["dossier"], "00-intake.json")),
                "the builder must not pre-write 00-intake.json; docs/guides/"
                "00-sandbox.md step 2 has the reader do that live")
            self.assertFalse(
                os.path.exists(os.path.join(built["dossier"], "03-decision.md")),
                "the builder must not pre-write 03-decision.md; docs/guides/"
                "00-sandbox.md step 4 has the reader do that live")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
