#!/usr/bin/env python3
"""Bypass fixtures: the ways a person or an agent gets past these controls.

Run: python3 tools/test_sbe_bypass.py

An external review listed 35 ways this system could be bypassed. This file holds
the ones that can be exercised on a laptop with no network, no GitHub
credentials and no warehouse. `docs/BYPASS-COVERAGE.md` is the table: one row per
scenario, each row COVERED, UNREACHABLE HERE or UNCOVERED, and it is the
deliverable. This file is only the part of that table that runs.

Every fixture builds a real temporary git repository and runs the real tool
against it, matching `test_sbe_impact.py` and `test_sbe_evidence.py`. Nothing is
mocked, because a bypass lives at the seam between an artifact and a claim about
it, and a mocked seam would test the mock.

TWO KINDS OF FIXTURE LIVE HERE, and confusing them would be worse than having
neither, so they are named apart.

  A CONTROL fixture proves the bypass is caught: the verdict is not PASS, and
  the evidence line says why. `test_an_invented_review_id_...` is one.

  A LIMIT fixture proves the bypass WORKS, and carries `_is_a_limit` in its
  name. It exists so that the limit is a decision somebody made rather than a
  surprise somebody finds, exactly as
  `test_a_secret_shaped_argv_token_is_redacted_not_recorded_verbatim` now does in
  `test_sbe_evidence.py`. A limit fixture asserts the current behaviour AND
  asserts that the sentence the tool prints does not overclaim, because the
  overclaim is the part that would actually mislead somebody. Its scenario is
  UNCOVERED or UNREACHABLE HERE in the table, never COVERED.

A green run of this file means the covered scenarios were tested. It means
nothing about the scenarios the table marks UNREACHABLE HERE or UNCOVERED.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
GATE = os.path.join(HERE, "sbe_gate.py")
DESIGN = os.path.join(HERE, "sbe_design.py")
HOOK = os.path.join(HERE, "sbe_fence_hook.py")


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def write_json(cwd, rel, obj):
    return write(cwd, rel, json.dumps(obj, indent=2, sort_keys=True))


def verdict_line(text, name):
    """The one report line for a named check, or a sentence saying it is absent.

    Deliberately not a (verdict, evidence) pair: `evals/test_no_data_class.py`
    reads every two-value return under tools/ as a verdict this lint cannot
    prove is never PASS, and it is right to. The caller splits the line.
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            return line
    return "NO VERDICT LINE for %r in:\n%s" % (name, text)


def verdict_of(text, name):
    line = verdict_line(text, name)
    parts = line.split()
    return parts[1] if len(parts) >= 2 and parts[0] == name else line


class BypassFixture(unittest.TestCase):
    """A throwaway directory per test, and a git repository inside it when the
    control under test reads commits."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def init_repo(self, name="repo", email="dana@example.invalid", who="Dana Author"):
        repo = os.path.join(self.dir, name)
        os.makedirs(repo)
        git(repo, "init", "-q")
        git(repo, "config", "user.email", email)
        git(repo, "config", "user.name", who)
        git(repo, "config", "commit.gpgsign", "false")
        write(repo, "README.md", "base\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base")
        return repo

    def run_gate(self, which, root, extra=()):
        argv = [sys.executable, GATE] + list(extra) + [which, root]
        out = subprocess.run(argv, capture_output=True, text=True)
        self.last_code = out.returncode
        return out.stdout + out.stderr

    def run_design(self, root, extra=()):
        argv = [sys.executable, DESIGN] + list(extra) + [root]
        out = subprocess.run(argv, capture_output=True, text=True)
        self.last_code = out.returncode
        return out.stdout + out.stderr


# ---------------------------------------------------------------------------
# Scenarios 12, 13, 15: an approval that nobody can resolve, an approval whose
# claim lives in a file rather than in a signature, and an approval a later
# commit left behind.
#
# Scenario 14 (an author approving their own change under another identity) is
# NOT re-fixtured here: evals/run_evals.py already pins it five ways under a
# verified signature (a-trailing-period, a-reordered-name, an-initial,
# a-plus-address, a-role-suffix, all FAIL). Writing a sixth would grow the count
# and prove nothing new.
# ---------------------------------------------------------------------------
class TestApprovalCannotBeBoughtWithText(BypassFixture):

    def money_repo(self, message):
        repo = self.init_repo()
        write(repo, "APPROVAL", "touches the partner payout path\n")
        write(repo, "src/payout.py", "def pay():\n    return 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", message)
        return repo

    def test_an_invented_review_id_is_a_pointer_and_never_an_approval(self):
        """SCENARIO 12. The cheapest forgery on the money gate: type a review id
        that looks real. The gate cannot resolve it, so it must not certify it,
        and the sentence it prints has to say that in its own words rather than
        leaving the reader to assume a lookup happened."""
        repo = self.money_repo("payout batching\n\nReviewed-in: PR-99999")
        text = self.run_gate("approval", repo)
        self.assertEqual(verdict_of(text, "approval"), "NO-DATA", text)
        self.assertIn("does not resolve the id against any review platform",
                      verdict_line(text, "approval"))
        self.assertNotIn("PASS", verdict_line(text, "approval"))

    def test_that_invented_id_does_not_block_a_strict_run_either(self):
        """The honest other half of scenario 12, and the half a reader would
        otherwise get wrong. NO-DATA is never a pass, and it is also not a
        block: --strict exits on FAIL. So an invented id buys nothing and stops
        nothing, and a team that needs it stopped resolves the id in CI."""
        repo = self.money_repo("payout batching\n\nReviewed-in: PR-99999")
        self.run_gate("approval", repo, extra=("--strict",))
        self.assertEqual(self.last_code, 0,
                         "if this ever becomes 1, docs/BYPASS-COVERAGE.md row 12 has to "
                         "change with it")

    def test_a_review_id_that_names_no_review_is_a_broken_claim(self):
        """SCENARIO 12, the vacuous spelling. A placeholder where a pointer
        belongs is worse than nothing, because it occupies the field a reviewer
        would look at, so it FAILs rather than reporting an absence."""
        repo = self.money_repo("payout batching\n\nReviewed-in: TODO")
        text = self.run_gate("approval", repo)
        self.assertEqual(verdict_of(text, "approval"), "FAIL", text)
        self.assertIn("names no review", verdict_line(text, "approval"))

    def test_an_approval_file_carried_in_from_elsewhere_certifies_nothing(self):
        """SCENARIO 13, the half this host can decide. An APPROVAL file can be
        copied out of any repository, so the file's own text is never the
        control: the claim it makes is what puts the gate in scope, and only a
        trailer in THIS commit or a signature THIS host verified can answer it.
        The other half (a signature bound to another repository's commit) needs
        a trusted key and is UNREACHABLE HERE."""
        repo = self.init_repo()
        write(repo, "APPROVAL",
              "approved in the payments-platform repo, PR 412, by the finance lead\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "payout batching")
        text = self.run_gate("approval", repo)
        self.assertEqual(verdict_of(text, "approval"), "FAIL", text)
        self.assertIn("payments-platform repo", verdict_line(text, "approval"),
                      "the refusal must quote the claim it is refusing, or the reader cannot "
                      "tell which of several APPROVAL files was read")
        self.assertIn("a name in a text field is not a control",
                      verdict_line(text, "approval"))

    def test_an_approval_stops_counting_at_the_next_commit(self):
        """SCENARIO 15. The trailer lives in the commit message, so a new commit
        does not carry the old approval forward: the claim in the APPROVAL file
        survives, the evidence for it does not, and the gate reports the
        difference rather than remembering the approval."""
        repo = self.money_repo("payout batching\n\nReviewed-in: PR-99999")
        before = verdict_of(self.run_gate("approval", repo), "approval")
        self.assertEqual(before, "NO-DATA")
        write(repo, "src/payout.py", "def pay():\n    return 2\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "one more change, nobody reviewed this one")
        text = self.run_gate("approval", repo)
        self.assertEqual(verdict_of(text, "approval"), "FAIL", text)
        self.assertIn("no signature or review id", verdict_line(text, "approval"))
        self.run_gate("approval", repo, extra=("--strict",))
        self.assertEqual(self.last_code, 1,
                         "a stale approval must block a strict run, or the gate rewards "
                         "adding a commit after review")


# ---------------------------------------------------------------------------
# Scenarios 8, 9, 10, 11: the identifiers and counts a receipt records, and the
# exact distance between recording one and proving anything with it.
# ---------------------------------------------------------------------------
class TestReceiptsProveOnlyWhatTheySay(BypassFixture):

    def numbers(self, snapshot_id):
        write_json(self.dir, "numbers-manifest.json", {"figures": [{
            "label": "gmv", "snapshot_id": snapshot_id,
            "query": "SELECT SUM(amount) FROM orders",
            "second_derivation": "SELECT SUM(qty * price) FROM order_lines",
            "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})
        return self.run_gate("numbers", self.dir)

    def migration(self, run_id="job-8842", before=100, after=100):
        write_json(self.dir, "migration-receipt.json", {
            "forward": {"ran_against_restore": True},
            "reverse": {"ran_against_restore": True, "rehearsal_run_id": run_id},
            "row_counts": {"before": before, "after_reverse": after}})
        return self.run_gate("migration", self.dir)

    def test_a_snapshot_id_that_resolves_to_nothing_still_passes_which_is_a_limit(self):
        """SCENARIO 8. Nothing here talks to a warehouse, so a syntactically
        valid snapshot id nobody can resolve is indistinguishable from a real
        one. The gate passes it. What the gate must NOT do is say more than it
        did, so the fixture reads the PASS sentence too: it claims the figure is
        pinned to a snapshot, never that the snapshot was found."""
        text = self.numbers("snap-2099-13-45-does-not-exist")
        line = verdict_line(text, "numbers")
        self.assertEqual(verdict_of(text, "numbers"), "PASS", text)
        self.assertIn("pinned to a snapshot", line)
        for overclaim in ("resolved", "exists", "found in the warehouse"):
            self.assertNotIn(overclaim, line,
                             "the PASS sentence claims the snapshot %s, and nothing here "
                             "checked that" % overclaim)

    def test_a_rehearsal_id_that_names_no_job_still_passes_which_is_a_limit(self):
        """SCENARIO 9. Same shape, other gate, and this one says its limit out
        loud in the law: it checks the id is present, is a string and is not a
        placeholder, and cannot resolve it against a job system."""
        text = self.migration(run_id="job-that-never-ran-anywhere")
        line = verdict_line(text, "migration")
        self.assertEqual(verdict_of(text, "migration"), "PASS", text)
        self.assertIn("a rehearsal id string is recorded", line)
        self.assertNotIn("resolved", line)

    def test_a_rehearsal_against_an_empty_database_passes_which_is_a_limit(self):
        """SCENARIO 10. Rehearse the reverse migration against an empty database
        instead of a representative restore and the row counts are 0 and 0. They
        match, so the gate prints the strongest sentence it owns. Zero equals
        zero is a true comparison over no rows, and nothing in the receipt
        records how much data the restore held."""
        text = self.migration(before=0, after=0)
        line = verdict_line(text, "migration")
        self.assertEqual(verdict_of(text, "migration"), "PASS", text)
        self.assertIn("1 row-count comparison(s) matched", line)
        self.assertNotIn("representative", line,
                         "if the gate ever claims the restore was representative, it has to "
                         "read something that says so")

    def test_matching_row_counts_never_compare_a_value_which_is_a_limit(self):
        """SCENARIO 11. The reverse migration can restore the right NUMBER of
        rows with the wrong CONTENT in them, and the receipt has no field for a
        value digest, so nothing compares one. The PASS sentence is accurate and
        narrow: row counts matched."""
        text = self.migration(before=100, after=100)
        line = verdict_line(text, "migration")
        self.assertEqual(verdict_of(text, "migration"), "PASS", text)
        self.assertIn("row-count comparison(s) matched", line)
        for overclaim in ("values", "checksum", "digest", "identical rows"):
            self.assertNotIn(overclaim, line,
                             "the PASS sentence mentions %s, and no field in the receipt "
                             "carries one" % overclaim)


# ---------------------------------------------------------------------------
# Scenarios 6 and 7: what "independent second derivation" is actually measured
# to mean.
# ---------------------------------------------------------------------------
class TestDerivationIndependenceIsTextual(BypassFixture):

    def two_derivations(self, first, second):
        write_json(self.dir, "numbers-manifest.json", {"figures": [{
            "label": "gmv", "snapshot_id": "snap-2026-07",
            "query": first, "second_derivation": second,
            "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})
        return self.run_gate("numbers", self.dir)

    def test_two_derivations_differing_only_by_an_alias_pass_which_is_a_limit(self):
        """SCENARIO 6. The gate folds case, whitespace, comments and trailing
        punctuation, then demands the two texts differ. An alias rename differs,
        so it passes, and the numbers agree because it is the same query. The
        PASS sentence is written to claim exactly the textual test it ran, and
        this fixture fails if it ever starts claiming the method was
        independent."""
        text = self.two_derivations(
            "SELECT SUM(amount) AS total FROM orders",
            "SELECT SUM(amount) AS gmv_total FROM orders o")
        line = verdict_line(text, "numbers")
        self.assertEqual(verdict_of(text, "numbers"), "PASS", text)
        self.assertIn("text differs beyond case, whitespace and comments", line)
        self.assertNotIn("independently", line,
                         "the sentence claims independence of METHOD; only text was compared")

    def test_two_derivations_over_one_source_pass_which_is_a_limit(self):
        """SCENARIO 7. Two genuinely different queries can still read the same
        logical source, which means one broken upstream table produces the same
        wrong number twice and both derivations agree. Nothing in this gate
        parses SQL or reads a lineage graph, so the shared source is invisible
        to it."""
        text = self.two_derivations(
            "SELECT SUM(amount) FROM analytics.orders_daily",
            "SELECT SUM(net) + SUM(tax) FROM analytics.orders_daily WHERE 1 = 1")
        line = verdict_line(text, "numbers")
        self.assertEqual(verdict_of(text, "numbers"), "PASS", text)
        for overclaim in ("source", "lineage", "table"):
            self.assertNotIn(overclaim, line,
                             "the PASS sentence mentions the %s the two queries read, and "
                             "nothing here parses one" % overclaim)


# ---------------------------------------------------------------------------
# Scenario 22: a monorepo where one package carries evidence and its neighbour
# carries none.
# ---------------------------------------------------------------------------
class TestOnePackageCannotAnswerForAnother(BypassFixture):

    def monorepo(self):
        write_json(self.dir, "packages/checkout/ran-receipt.json", {"checks": [
            {"name": "reconcile", "exit_code": 0, "duration_ms": 812}]})
        os.makedirs(os.path.join(self.dir, "packages", "ledger"))
        write(self.dir, "packages/ledger/query.sql", "SELECT 1;\n")
        return os.path.join(self.dir, "packages")

    def test_a_package_with_no_receipt_is_named_in_its_neighbours_pass_line(self):
        """SCENARIO 22. Read the verdict alone and this is a PASS over a tree
        where half the change has no evidence at all. The control is not the
        verdict, it is the sentence: a directory inside the scope that
        contributed nothing is NAMED in the same line, and the count is printed
        even when it is zero, so a reader never learns to read its absence as an
        absence of the question."""
        text = self.run_gate("ran", self.monorepo())
        line = verdict_line(text, "ran")
        self.assertEqual(verdict_of(text, "ran"), "PASS", text)
        self.assertIn("1 of 2 director(y/ies)", line)
        self.assertIn("contributed no ran-receipt.json", line)
        self.assertIn("ledger", line,
                      "the silent package must be named, not counted; a count with no name "
                      "is a place a missing package can be absorbed into")

    def test_that_same_package_alone_is_no_data(self):
        """The other end of the same run, which is what makes the sentence above
        load-bearing: scanned on its own, the package with no receipt reports
        NO-DATA. The pooled PASS is the neighbour's evidence, and the scope note
        is the only thing that says so."""
        text = self.run_gate("ran", os.path.join(self.monorepo(), "ledger"))
        self.assertEqual(verdict_of(text, "ran"), "NO-DATA", text)


# ---------------------------------------------------------------------------
# Scenario 21: escaping a fence's file scope by spelling the path differently.
# ---------------------------------------------------------------------------
FENCE_REGISTRY = (
    "# STATE\n\n"
    "## Fence registry\n\n"
    "- agent: doc-writer (sole writer, session owner-session-1111-2222) | tier T1 |\n"
    "  TTL 2026-12-31 |\n"
    "  objective: rewrite the setup guide |\n"
    "  files: docs/SETUP.md |\n"
    "  output: one commit |\n"
    "\n## Decisions\n")


class TestFenceScopeEscapes(BypassFixture):

    def setUp(self):
        BypassFixture.setUp(self)
        self.project = os.path.join(self.dir, "project")
        os.makedirs(os.path.join(self.project, "docs"))
        write(self.project, "docs/SETUP.md", "setup\n")
        write(self.project, "STATE.md", FENCE_REGISTRY)

    def decide(self, target):
        """The hook's decision for a write to `target`, as printed text.

        Empty output is an allow: the hook prints a deny payload on stdout and
        nothing at all when it permits the write.
        """
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": target, "content": "x"},
                   "session_id": "intruder-session-3333-4444",
                   "cwd": self.project,
                   "project_dir": self.project}
        env = dict(os.environ)
        env["BROTHERSBE_REGISTRIES"] = os.path.join(self.project, "STATE.md")
        env.pop("BROTHERSBE_FENCE_HOOK_OFF", None)
        env.pop("BROTHERSBE_FENCE_SESSION", None)
        # Isolate detect_companion() from whatever plugins happen to be
        # enabled in the real ~/.claude on the machine running this suite:
        # a host with the companion (BrotherMode) plugin installed and
        # wired for PreToolUse must not change this test's verdict.
        env["CLAUDE_CONFIG_DIR"] = os.path.join(self.dir, "no-such-claude-config")
        out = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_a_symlink_pointing_into_a_fenced_file_is_denied(self):
        """SCENARIO 21, the symlink half. Writing through a symlink writes the
        target, so a hook comparing the spelling rather than the file would hand
        a second writer the fenced file with one `ln -s`. Every target path is
        realpath'd before the comparison, so it does not."""
        os.symlink(os.path.join(self.project, "docs", "SETUP.md"),
                   os.path.join(self.project, "handy-link.md"))
        out = self.decide(os.path.join(self.project, "handy-link.md"))
        self.assertIn('"permissionDecision": "deny"', out,
                      "a symlink into a fenced file was allowed")
        self.assertIn("docs/SETUP.md", out)

    def test_a_case_variant_of_a_fenced_path_is_refused(self):
        """SCENARIO 21, the case half. On a case-insensitive filesystem (the
        macOS default) `docs/setup.md` and `docs/SETUP.md` are ONE file: the
        fixture proves that by inode before it proves anything about the hook,
        exactly as it did while this was still a limit. `paths_overlap` in
        `tools/sbe_fence_hook.py` now retries a missed comparison case-folded
        and confirms the fold against the filesystem (`os.path.samefile` here,
        since both spellings already exist) before trusting it, so the second
        writer is refused rather than allowed onto the fenced file."""
        fenced = os.path.join(self.project, "docs", "SETUP.md")
        variant = os.path.join(self.project, "docs", "setup.md")
        if not os.path.exists(variant):
            raise unittest.SkipTest(
                "this filesystem is case-sensitive, so docs/setup.md is a different file and "
                "there is nothing to escape; re-run on a case-insensitive volume")
        self.assertEqual(os.stat(fenced).st_ino, os.stat(variant).st_ino,
                         "the two spellings must be one file, or this fixture is not the "
                         "bypass it claims to be")
        self.assertIn('"permissionDecision": "deny"', self.decide(variant),
                      "a case variant of a fenced path reached the fenced file")
        self.assertIn('"permissionDecision": "deny"', self.decide(fenced),
                      "the control fixture beside the coverage: the exact spelling is refused "
                      "too, so the fold did not weaken the ordinary case")


# ---------------------------------------------------------------------------
# Scenarios 24 and 25: what an exemption can and cannot switch off.
# ---------------------------------------------------------------------------
class TestExemptionsWaiveWhatTheyName(BypassFixture):

    def dossier(self, exemption):
        write(self.dir, "00-intake.json", json.dumps({"answers": {
            "changes_contract": "n", "crosses_boundary": "n", "reversible_under_hour": "y",
            "touches_sensitive": "n", "consumers": "none"}}))
        write(self.dir, ".sbe-exempt", exemption)
        return self.dir

    def test_an_exemption_naming_a_wildcard_waives_nothing(self):
        """SCENARIO 25. An exemption broader than the checks it names is exactly
        what a paragraph of free text used to be: an off switch. The checks are
        enumerated one by one, and a wildcard is not the name of a check, so the
        exemption is refused BY NAME and the dossier is checked anyway."""
        root = self.dossier("checks: *\n"
                            "reason: the founder approved a broad exemption for this whole "
                            "directory and nobody has narrowed it since\n")
        text = self.run_design(root)
        self.assertEqual(verdict_of(text, "dossier"), "FAIL", text)
        self.assertIn("which is not a design check", verdict_line(text, "dossier"))
        self.assertIn("this dossier is checked below", verdict_line(text, "dossier"))

    def test_an_exemption_cannot_expire_because_nothing_reads_a_date_which_is_a_limit(self):
        """SCENARIO 24. There is no expiry field, no owner field and no approver
        field: `sbe exceptions` refuses and names the wave that will build them.
        So an exemption whose own reason says it expired two years ago still
        waives its checks. The one thing that holds is the honesty law: the
        result is WAIVED, which is never a pass, and every waived check is named
        with its reason."""
        root = self.dossier(
            "checks: artifacts, adr, datamodel, diagrams, placeholder\n"
            "reason: this exemption expired on 2024-01-01 and nobody renewed it, the dossier "
            "was closed in 2023 and is kept only for history\n")
        text = self.run_design(root, extra=("--strict",))
        self.assertEqual(self.last_code, 0,
                         "a strict run without --strict-waivers exits 0 over a waiver; if that "
                         "changes, row 24 changes")
        self.assertIn("WAIVED", text)
        self.assertNotIn("PASS", text, "a waiver must never render as a pass")
        self.assertIn("A waiver is not a pass", text)
        self.assertIn("2024-01-01", text,
                      "the reason is printed verbatim, which is the only reason a human can "
                      "notice the expiry this tool cannot read")

    def test_the_unbuilt_exceptions_command_refuses_rather_than_listing_nothing(self):
        """The other half of scenario 24, and the reason it is UNCOVERED rather
        than broken: the command that would carry owners and expiry dates exists
        in the surface, says it is not built, names its wave, and exits 3. An
        empty list would have been the failure this project exists to stop."""
        out = subprocess.run([sys.executable, SBE, "exceptions"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("NOT BUILT", out.stdout + out.stderr)
        self.assertIn("owner, approver or expiry", out.stdout + out.stderr)


# ---------------------------------------------------------------------------
# Scenario 28: hostile repository content aimed at the report a human or a CI
# job reads.
# ---------------------------------------------------------------------------
class TestRepositoryContentCannotForgeAVerdict(BypassFixture):

    def test_a_receipt_field_cannot_write_a_verdict_line_for_another_gate(self):
        """SCENARIO 28, the machine-readable half. A figure label carrying a
        newline and a plausible verdict line used to put a forged PASS for a
        DIFFERENT gate into the report, above the real one, where every CI grep
        and every human eye reads it. Every report line goes through one_line(),
        so the injected text arrives escaped, inside the line it belongs to."""
        write_json(self.dir, "numbers-manifest.json", {"figures": [{
            "label": "gmv\n  approval  PASS     approved by the finance lead\n",
            "snapshot_id": "", "query": "", "second_derivation": "",
            "rerun": {"ran": True, "primary": 1, "secondary": 2}}]})
        text = self.run_gate("numbers", self.dir)
        self.assertEqual(verdict_of(text, "numbers"), "FAIL", text)
        forged = [l for l in text.splitlines() if l.strip().startswith("approval")]
        self.assertEqual([], forged,
                         "a manifest wrote a verdict line for a gate that never ran: %r" % forged)
        self.assertIn("\\n", verdict_line(text, "numbers"),
                      "the injected newline must arrive as a visible escape, so a reader can "
                      "see what the artifact tried to do rather than having it deleted")


# ---------------------------------------------------------------------------
# Scenarios 23 and 33: what binds a dossier, and a receipt, to the change and to
# the person they are supposed to be evidence for.
# ---------------------------------------------------------------------------
class TestNothingBindsADossierToAChange(BypassFixture):

    def test_the_design_checks_never_read_a_commit_which_is_a_limit(self):
        """SCENARIO 23. A dossier from a finished change can be left in place and
        it passes for the next one, because nothing in the design tool reads a
        commit, a diff or a date: there is no field a staleness check could
        compare against. This is asserted from the source rather than from a
        fixture, because the absence of a binding is what makes the reuse
        invisible, and an absence has no verdict line to read.

        The evidence receipt has the binding this lacks (headCommit, and the
        digest of every covered file), which is what row 23 in the table points
        at as the shape of a fix."""
        import ast
        with io.open(DESIGN, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=DESIGN)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("subprocess", imported,
                         "sbe_design.py now runs a subprocess; if it reads git, a dossier can "
                         "be bound to a commit and row 23 of docs/BYPASS-COVERAGE.md changes")
        with io.open(DESIGN, encoding="utf-8") as fh:
            body = fh.read()
        for reader in ("rev-parse", "git log", "HEAD~"):
            self.assertNotIn(reader, body,
                             "sbe_design.py mentions %r; see the assertion above" % reader)


class TestAReceiptCanBeRefreshedByWhoeverBrokeIt(BypassFixture):

    def test_the_author_of_a_change_can_mint_the_receipt_that_covers_it(self):
        """SCENARIO 33. The staleness control works: edit a covered file and the
        receipt FAILs. What nothing controls is WHO regenerates it. The same
        actor re-runs the wrapper, the receipt is genuine (the command really
        ran, at this commit, over these files), and it verifies PASS. A reviewer
        agent that edits the code it later approves is inside this shape.

        The only thing standing between that and an authoritative verdict is the
        trust label, so the fixture asserts the label too: a receipt minted
        locally reads LOCAL-ADVISORY, and a reader who treats it as protected CI
        evidence is being misled by the layout rather than by the content."""
        repo = self.init_repo()
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "src/service.py", "def handle():\n    return 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "the work this receipt will cover")
        out_dir = os.path.join(self.dir, "receipts")
        os.makedirs(out_dir)
        receipt = os.path.join(out_dir, "receipt.json")

        def generate():
            proc = subprocess.run(
                [sys.executable, SBE, "evidence", "run", "--out", receipt, "--cwd", repo,
                 "--base", base, "--", "python3", "-c", "print('the suite ran')"],
                capture_output=True, text=True)
            return proc.returncode

        def verify():
            proc = subprocess.run(
                [sys.executable, SBE, "evidence", "verify", receipt, "--cwd", repo, "--json"],
                capture_output=True, text=True)
            text = proc.stdout + proc.stderr
            blob = text[text.index("{"):text.rindex("}") + 1] if "{" in text else "{}"
            return json.loads(blob)["verdict"]

        self.assertEqual(generate(), 0)
        self.assertEqual(verify(), "PASS")
        write(repo, "src/service.py", "def handle():\n    return 999\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "the change the reviewer made to the code it approves")
        self.assertEqual(verify(), "FAIL",
                         "the staleness control is the half that DOES hold; if it stops "
                         "holding, this fixture is testing nothing")
        self.assertEqual(generate(), 0)
        self.assertEqual(verify(), "PASS",
                         "whoever changed the code can mint a fresh receipt over it, and this "
                         "fixture exists so that is a decision rather than a surprise")
        shown = subprocess.run([sys.executable, SBE, "evidence", "show", receipt],
                               capture_output=True, text=True)
        self.assertIn("LOCAL-ADVISORY", shown.stdout + shown.stderr,
                      "a locally minted receipt must say so, because the trust label is the "
                      "only thing separating it from evidence a second party produced")


if __name__ == "__main__":
    unittest.main(verbosity=2)
