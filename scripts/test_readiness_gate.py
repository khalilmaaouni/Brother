"""What the readiness gate must keep true.

Driven backwards, same discipline as test_parity_gate.py: a fixture evidence
file flips an item to PASS, removing it flips the item back to NO-DATA, and
the gate's own exit code is nonzero while any critical item is unproven and
zero only once every critical item passes.
"""
import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readiness_gate as RG  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _silent_main(argv):
    """RG.main() prints a full report; swallow it so a verbose test run's
    tail is the unittest summary, never a gate report a test happened to
    print last (stdout is block-buffered when redirected to a file, so an
    unswallowed print can land after unittest's own OK/FAILED line)."""
    with contextlib.redirect_stdout(io.StringIO()):
        return RG.main(argv)


def _write_script(path, exit_code=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import sys\nsys.exit(%d)\n" % exit_code)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


_GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com",
                GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com")


def _init_git_repo(d):
    """Turns tempdir d into a minimal one-commit git repo and returns HEAD's
    full SHA. The restore-drill record's commit binding runs a real `git
    merge-base --is-ancestor` against the checkout it is read from, so any
    fixture root that wants that row to bind (rather than read NO-DATA for
    an unverifiable commit) must actually be a git checkout."""
    subprocess.run(["git", "init", "-q"], cwd=d, check=True, env=_GIT_ENV)
    with open(os.path.join(d, ".marker"), "w", encoding="utf-8") as fh:
        fh.write("x")
    subprocess.run(["git", "add", "."], cwd=d, check=True, env=_GIT_ENV)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True, env=_GIT_ENV)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, check=True, env=_GIT_ENV,
                          stdout=subprocess.PIPE, text=True).stdout.strip()
    return sha


def _write_restore_drill(d, commit=None, drill_date=None, passed=True,
                          covered=None):
    rel = os.path.join("docs", "plan", "RESTORE-DRILL-ENTERPRISE-RESULT.json")
    os.makedirs(os.path.join(d, "docs", "plan"), exist_ok=True)
    doc = {"passed": passed}
    if commit is not None:
        doc["commit"] = commit
    if drill_date is not None:
        doc["drill_date"] = drill_date
    if covered is not None:
        doc["covered"] = covered
    with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return rel


def _write_generic_record(d, commit=None, passed=True, covered=None,
                           rel=os.path.join("docs", "r.json")):
    """A record for an item that declares NO freshness bar, so it exercises
    the record contract every kind "record" item now shares (row E107): the
    commit and content binding, with no drill_date of its own."""
    os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
    doc = {"passed": passed}
    if commit is not None:
        doc["commit"] = commit
    if covered is not None:
        doc["covered"] = covered
    with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return rel


def _write_covered_file(d, rel, text):
    """Writes a file the drill would have exercised and returns the covered
    entry naming it, so a fixture's record and its tree agree by
    construction rather than by a hand-copied hash."""
    full = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    return {"path": rel,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _make_orphan_head(d):
    """Replaces d's HEAD with an ORPHAN commit carrying the same working
    tree, the exact shape scripts/export_public.py's build_orphan_commit
    produces for the public tree. Every commit made before this call stops
    being an ancestor of HEAD, which is the state that refused the 1.0.2
    tag."""
    subprocess.run(["git", "checkout", "-q", "--orphan", "export"],
                   cwd=d, check=True, env=_GIT_ENV)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, env=_GIT_ENV)
    subprocess.run(["git", "commit", "-q", "-m", "export"],
                   cwd=d, check=True, env=_GIT_ENV)


class AnItemIsGrantedByEvidenceNeverByAssertion(unittest.TestCase):
    def test_a_missing_suite_file_is_no_data(self):
        d = tempfile.mkdtemp()
        verdict, evidence = RG._check_suite(d, os.path.join("scripts", "x.py"))
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("does not exist", evidence)

    def test_an_existing_passing_suite_flips_to_pass(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("scripts", "x.py")
        _write_script(os.path.join(d, rel), exit_code=0)
        verdict, _evidence = RG._check_suite(d, rel)
        self.assertEqual(verdict, RG.PASS)

    def test_removing_the_evidence_file_flips_it_back_to_no_data(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("scripts", "x.py")
        path = os.path.join(d, rel)
        _write_script(path, exit_code=0)
        self.assertEqual(RG._check_suite(d, rel)[0], RG.PASS)
        os.remove(path)
        self.assertEqual(RG._check_suite(d, rel)[0], RG.NODATA)

    def test_a_failing_suite_is_fail_not_no_data(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("scripts", "x.py")
        _write_script(os.path.join(d, rel), exit_code=1)
        self.assertEqual(RG._check_suite(d, rel)[0], RG.FAIL)

    def test_a_missing_record_is_no_data(self):
        d = tempfile.mkdtemp()
        verdict, evidence = RG._check_record(d, os.path.join("docs", "r.json"))
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("does not exist", evidence)

    def test_a_bound_record_with_passed_true_is_pass(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        rel = _write_generic_record(d, commit=sha, passed=True)
        self.assertEqual(RG._check_record(d, rel)[0], RG.PASS)

    def test_a_bound_record_with_passed_false_is_fail(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        rel = _write_generic_record(d, commit=sha, passed=False)
        self.assertEqual(RG._check_record(d, rel)[0], RG.FAIL)

    def test_a_bound_record_with_no_passed_field_is_no_data(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        rel = os.path.join("docs", "r.json")
        os.makedirs(os.path.join(d, "docs"), exist_ok=True)
        with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            json.dump({"commit": sha, "something_else": True}, fh)
        self.assertEqual(RG._check_record(d, rel)[0], RG.NODATA)

    def test_a_no_data_verdict_names_the_blocking_wbs_row(self):
        d = tempfile.mkdtemp()
        rows = RG.evaluate(d)
        tenancy = [r for r in rows if r["id"] == "tenancy-leakage-zero"][0]
        self.assertEqual(tenancy["verdict"], RG.NODATA)
        self.assertIn("VB3-03", tenancy["evidence"])


class EveryRecordItemIsBoundNotOnlyTheOneThatOptedIn(unittest.TestCase):
    """Row E107. Until 2026-09-04 the binding was a per-item `bind_commit`
    flag and every other record item fell through to a checker that read
    only passed=true. That is the same defect the evidence auditor found on
    the drill on 2026-09-03, still live for the next record item anyone adds,
    and a public clone could not have verified such an item at all. These
    cases drive the new contract backwards: an unbound record can never PASS
    on the generic path, a bound one PASSes without any drill_date, and the
    content binding (the only one an orphan history can satisfy) works for a
    record that declares no freshness bar."""

    def test_an_unbound_record_with_passed_true_is_no_data_not_pass(self):
        """The hole itself. Under the old contract this record read PASS."""
        d = tempfile.mkdtemp()
        _init_git_repo(d)
        rel = _write_generic_record(d, commit=None, passed=True)
        verdict, evidence = RG._check_record(d, rel)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("no commit field", evidence)

    def test_a_bound_record_needs_no_drill_date_when_no_bar_is_declared(self):
        """Freshness stays the restore drill's own bar, declared by that item
        as max_age_days, never imposed on every record."""
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        rel = _write_generic_record(d, commit=sha, passed=True)
        verdict, evidence = RG._check_record(d, rel)
        self.assertEqual(verdict, RG.PASS)
        self.assertNotIn("drill_date", evidence)
        self.assertNotIn("age ", evidence)

    def test_a_generic_record_binds_by_content_in_a_real_orphan_history(self):
        """The public clone's shape: export_public builds an ORPHAN commit,
        so ancestry cannot hold and content is the only readable binding."""
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        entry = _write_covered_file(d, "scripts/covered_tool.py", "print(1)\n")
        rel = _write_generic_record(d, commit=sha, passed=True, covered=[entry])
        _make_orphan_head(d)
        verdict, evidence = RG._check_record(d, rel)
        self.assertEqual(verdict, RG.PASS)
        self.assertIn("bound by content", evidence)
        self.assertIn("1 covered file(s)", evidence)

    def test_a_drifted_covered_file_sinks_a_generic_record_in_an_orphan_history(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        entry = _write_covered_file(d, "scripts/covered_tool.py", "print(1)\n")
        rel = _write_generic_record(d, commit=sha, passed=True, covered=[entry])
        _make_orphan_head(d)
        with open(os.path.join(d, "scripts", "covered_tool.py"), "w",
                   encoding="utf-8") as fh:
            fh.write("print(2)\n")
        verdict, evidence = RG._check_record(d, rel)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("covered_tool.py", evidence)

    def test_no_item_still_carries_the_retired_opt_in_flag(self):
        """The flag is what made an unbound record possible: an item that
        forgot it got the unbound checker silently. There is nothing to
        forget any more, and this fails the moment someone reintroduces it."""
        for spec in RG.ITEMS:
            self.assertNotIn("bind_commit", spec, spec["id"])

    def test_every_record_item_reads_no_data_when_its_record_is_unbound(self):
        """The structural guard, run over ITEMS rather than over one hard
        coded id, so a record item added later is covered the day it lands.
        Each record item's own path is written with passed=true and no
        commit, and the gate must refuse to certify any of them."""
        record_items = [s for s in RG.ITEMS if s["kind"] == "record"]
        self.assertTrue(record_items, "ITEMS holds no record item to bind")
        for spec in record_items:
            d = tempfile.mkdtemp()
            _init_git_repo(d)
            full = os.path.join(d, spec["path"])
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                json.dump({"passed": True, "drill_date": date.today().isoformat()}, fh)
            row = [r for r in RG.evaluate(d) if r["id"] == spec["id"]][0]
            self.assertEqual(row["verdict"], RG.NODATA, spec["id"])
            self.assertIn("no commit field", row["evidence"], spec["id"])


class TheRestoreDrillReadsNoDataForAnUnboundRecord(unittest.TestCase):
    """Evidence auditor, 2026-09-03: _check_record read only passed=true, so
    a stale drill certified any later tree forever. Driven backwards over a
    real one-commit git repository, same discipline as the suite/record
    tests above: a record naming HEAD's own commit and today's date PASSes
    (and prints the commit and age), a foreign commit, a missing commit
    field, and an over-the-bar date all read NO-DATA instead."""

    def test_head_commit_and_todays_date_is_pass_with_commit_and_age_printed(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        rel = _write_restore_drill(d, commit=sha, drill_date=date.today().isoformat())
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.PASS)
        self.assertIn(sha[:12], evidence)
        self.assertIn("age 0 days", evidence)

    def test_a_foreign_commit_is_no_data_naming_it(self):
        d = tempfile.mkdtemp()
        _init_git_repo(d)
        foreign = "abc123def456abc123def456abc123def456abc"
        rel = _write_restore_drill(d, commit=foreign, drill_date=date.today().isoformat())
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn(foreign[:12], evidence)

    def test_no_commit_field_is_no_data_naming_the_missing_field(self):
        d = tempfile.mkdtemp()
        _init_git_repo(d)
        rel = _write_restore_drill(d, commit=None, drill_date=date.today().isoformat())
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("no commit field", evidence)

    def test_a_record_older_than_the_freshness_bar_is_no_data_naming_its_age(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        stale = (date.today() - timedelta(days=RG.RESTORE_DRILL_MAX_AGE_DAYS + 1)).isoformat()
        rel = _write_restore_drill(d, commit=sha, drill_date=stale)
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("day(s) old", evidence)

    def test_a_record_exactly_on_the_freshness_bar_still_passes(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        edge = (date.today() - timedelta(days=RG.RESTORE_DRILL_MAX_AGE_DAYS)).isoformat()
        rel = _write_restore_drill(d, commit=sha, drill_date=edge)
        verdict, _evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.PASS)

    def test_no_drill_date_field_is_no_data(self):
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        rel = _write_restore_drill(d, commit=sha, drill_date=None)
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("no drill_date field", evidence)

    def test_a_commit_is_not_an_ancestor_when_root_is_not_a_git_checkout(self):
        d = tempfile.mkdtemp()  # never git-init'd
        self.assertIsNone(RG._commit_is_ancestor(d, "deadbeef"))

    def test_evaluate_on_a_bound_pass_reads_ready_end_to_end(self):
        """Same shape as TheGateExitsNonzeroWhileACriticalItemIsUnproven below,
        but proves the binding through the real evaluate()/main() path, not
        just the helper function."""
        d = _bound_all_critical_pass_root()
        self.assertEqual(_silent_main(["--root", d]), 0)

    def test_evaluate_flips_to_not_ready_when_the_bound_commit_is_foreign(self):
        d = _bound_all_critical_pass_root()
        _write_restore_drill(d, commit="abc123def456abc123def456abc123def456abc",
                              drill_date=date.today().isoformat())
        self.assertEqual(_silent_main(["--root", d]), 1)


class TheRestoreDrillBindsByContentWhereAncestryCannotExist(unittest.TestCase):
    """Measured 2026-09-04 on the 1.0.2 cut: `python3 scripts/export_public.py
    --push --tag v1.0.2` refused because the export tree's own gate read
    "Restore drill NO-DATA ... foreign commit". scripts/export_public.py
    builds that tree as an ORPHAN commit, so NO hub commit is ever an
    ancestor of it and the ancestry binding above cannot pass there by
    construction, for any drill, ever. Ancestry was a proxy for "the drill
    ran against this code"; these cases prove the same property measured
    directly, and prove it still refuses when the code has moved."""

    #: A file the drill exercised, and one line of it.
    COVERED_REL = "products/brothermode/tools/bm_vault.py"
    COVERED_TEXT = "print('vault')\n"

    def _foreign_root(self, covered_text=None, second=False):
        """A git checkout whose HEAD does not contain the commit the record
        names, carrying the covered file(s). Returns (dir, record rel,
        foreign sha)."""
        d = tempfile.mkdtemp()
        _init_git_repo(d)
        entries = [_write_covered_file(
            d, self.COVERED_REL,
            self.COVERED_TEXT if covered_text is None else covered_text)]
        if second:
            entries.append(_write_covered_file(
                d, "scripts/restore_drill_enterprise.py", "print('drill')\n"))
        foreign = "abc123def456abc123def456abc123def456abc"
        rel = _write_restore_drill(d, commit=foreign,
                                    drill_date=date.today().isoformat(),
                                    covered=entries)
        return d, rel, foreign

    def test_unchanged_covered_files_pass_a_foreign_commit_naming_the_binding(self):
        d, rel, foreign = self._foreign_root(second=True)
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.PASS, evidence)
        self.assertIn("bound by content: 2 covered file(s) unchanged since %s"
                      % foreign[:12], evidence)

    def test_a_changed_covered_file_is_no_data_naming_that_file(self):
        d, rel, _foreign = self._foreign_root()
        with open(os.path.join(d, *self.COVERED_REL.split("/")), "a",
                  encoding="utf-8") as fh:
            fh.write("# one byte of drift\n")
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn(self.COVERED_REL, evidence)
        self.assertIn("has changed since the drill ran", evidence)

    def test_a_missing_covered_file_is_no_data_naming_that_file(self):
        d, rel, _foreign = self._foreign_root()
        os.remove(os.path.join(d, *self.COVERED_REL.split("/")))
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn(self.COVERED_REL, evidence)
        self.assertIn("unreadable", evidence)

    def test_a_foreign_commit_with_no_covered_list_is_no_data_as_before(self):
        d = tempfile.mkdtemp()
        _init_git_repo(d)
        rel = _write_restore_drill(d, commit="abc123def456abc123def456abc123def456abc",
                                    drill_date=date.today().isoformat())
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("foreign commit", evidence)
        self.assertIn("carries no covered list", evidence)

    def test_an_empty_covered_list_is_no_data_not_a_vacuous_pass(self):
        """Every covered file matched, over zero files, is exactly the shape
        a population of nothing composes into a PASS."""
        d = tempfile.mkdtemp()
        _init_git_repo(d)
        rel = _write_restore_drill(d, commit="abc123def456abc123def456abc123def456abc",
                                    drill_date=date.today().isoformat(), covered=[])
        self.assertEqual(RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)[0], RG.NODATA)

    def test_a_covered_entry_missing_its_hash_is_no_data(self):
        d = tempfile.mkdtemp()
        _init_git_repo(d)
        _write_covered_file(d, self.COVERED_REL, self.COVERED_TEXT)
        rel = _write_restore_drill(d, commit="abc123def456abc123def456abc123def456abc",
                                    drill_date=date.today().isoformat(),
                                    covered=[{"path": self.COVERED_REL}])
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("no path or no sha256", evidence)

    def test_an_ancestor_commit_still_passes_without_claiming_a_content_binding(self):
        """The two bindings stay distinguishable in the evidence: a reader
        must be able to tell which one granted the PASS."""
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        rel = _write_restore_drill(d, commit=sha, drill_date=date.today().isoformat())
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.PASS)
        self.assertNotIn("bound by content", evidence)

    def test_a_real_orphan_tree_passes_by_content_and_fails_on_drift(self):
        """The 1.0.2 export tree's own shape, built rather than described:
        a real orphan commit, so the recorded commit is positively NOT an
        ancestor, driven both ways on the same tree."""
        d = tempfile.mkdtemp()
        sha = _init_git_repo(d)
        entries = [_write_covered_file(d, self.COVERED_REL, self.COVERED_TEXT)]
        rel = _write_restore_drill(d, commit=sha,
                                    drill_date=date.today().isoformat(),
                                    covered=entries)
        _make_orphan_head(d)
        self.assertIs(RG._commit_is_ancestor(d, sha), False)
        verdict, evidence = RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)
        self.assertEqual(verdict, RG.PASS, evidence)
        self.assertIn("bound by content: 1 covered file(s) unchanged since %s"
                      % sha[:12], evidence)
        with open(os.path.join(d, *self.COVERED_REL.split("/")), "a",
                  encoding="utf-8") as fh:
            fh.write("# drift\n")
        self.assertEqual(RG._check_record(
            d, rel, max_age_days=RG.RESTORE_DRILL_MAX_AGE_DAYS)[0], RG.NODATA)

    def test_evaluate_reads_ready_end_to_end_on_a_foreign_commit_bound_by_content(self):
        """Through the real evaluate()/main() path, not the helper alone."""
        d = _bound_all_critical_pass_root()
        entries = [_write_covered_file(d, self.COVERED_REL, self.COVERED_TEXT)]
        _write_restore_drill(d, commit="abc123def456abc123def456abc123def456abc",
                              drill_date=date.today().isoformat(), covered=entries)
        self.assertEqual(_silent_main(["--root", d]), 0)


class NoDataIsNeverAPass(unittest.TestCase):
    def test_no_data_blocks_a_critical_item_exactly_like_fail(self):
        rows = [{"id": "x", "title": "X", "critical": True, "verdict": RG.NODATA,
                 "evidence": ""}]
        self.assertEqual(len(RG.blocking(rows)), 1)

    def test_no_data_does_not_block_a_noncritical_item(self):
        rows = [{"id": "x", "title": "X", "critical": False, "verdict": RG.NODATA,
                 "evidence": ""}]
        self.assertEqual(RG.blocking(rows), [])

    def test_a_noncritical_fail_is_caught_but_a_noncritical_no_data_is_not(self):
        """The gap six acceptance reviewers all caught: a definite FAIL is a
        proven break and worse than a NO-DATA, so a non-critical FAIL must be
        surfaced even though it is off the critical path, while a non-critical
        NO-DATA stays a non-blocking honest unknown."""
        rows = [
            {"id": "f", "title": "F", "critical": False, "verdict": RG.FAIL, "evidence": ""},
            {"id": "n", "title": "N", "critical": False, "verdict": RG.NODATA, "evidence": ""},
            {"id": "p", "title": "P", "critical": False, "verdict": RG.PASS, "evidence": ""},
        ]
        caught = RG.noncritical_fails(rows)
        self.assertEqual([r["id"] for r in caught], ["f"])


def _bound_all_critical_pass_root():
    """Same fixture as _all_critical_pass_root below, module-level so both
    that class and TheRestoreDrillReadsNoDataForAnUnboundRecord can share it:
    a real one-commit git checkout whose restore-drill record names HEAD's
    own commit and today's date, so the record binds and every critical item
    reads PASS."""
    d = tempfile.mkdtemp()
    _write_script(os.path.join(d, "scripts", "test_make_benchmark_bundle.py"), 0)
    _write_script(os.path.join(d, "scripts", "test_tenancy_isolation.py"), 0)
    _write_script(os.path.join(d, "scripts", "test_policy_fail_closed.py"), 0)
    # V6/M4: japanese-threshold is now critical (see readiness_gate.py's
    # ITEMS comment), so a fixture claiming every critical item passes
    # must give it passing evidence too, not leave it absent.
    _write_script(os.path.join(d, "scripts", "test_japanese_threshold.py"), 0)
    sha = _init_git_repo(d)
    _write_restore_drill(d, commit=sha, drill_date=date.today().isoformat())
    return d


class TheGateExitsNonzeroWhileACriticalItemIsUnproven(unittest.TestCase):
    """Driven backwards on a full fixture root, same shape as production:
    every critical item's evidence path present and passing gives exit 0;
    removing any one of them gives a nonzero exit. Non-critical items are
    left absent (NO-DATA) throughout, and must never affect the exit code."""

    def _all_critical_pass_root(self):
        return _bound_all_critical_pass_root()

    def test_exit_zero_once_every_critical_item_passes(self):
        d = self._all_critical_pass_root()
        self.assertEqual(_silent_main(["--root", d]), 0)

    def test_exit_nonzero_when_one_critical_item_is_missing(self):
        d = self._all_critical_pass_root()
        os.remove(os.path.join(d, "docs", "plan", "RESTORE-DRILL-ENTERPRISE-RESULT.json"))
        self.assertEqual(_silent_main(["--root", d]), 1)

    def test_exit_nonzero_when_one_critical_item_fails(self):
        d = self._all_critical_pass_root()
        _write_script(os.path.join(d, "scripts", "test_policy_fail_closed.py"), 1)
        self.assertEqual(_silent_main(["--root", d]), 1)

    def test_a_missing_noncritical_item_never_blocks_a_clean_run(self):
        """reproducible-release-artifact stays absent in every fixture above
        (japanese-threshold turned critical at V6/M4 and is now given passing
        evidence by _all_critical_pass_root) and the gate still opens: proves
        criticality, not merely completeness, decides the exit code."""
        d = self._all_critical_pass_root()
        rows = RG.evaluate(d)
        noncritical = [r for r in rows if not r["critical"]]
        self.assertTrue(all(r["verdict"] == RG.NODATA for r in noncritical))
        self.assertEqual(_silent_main(["--root", d]), 0)

    def test_a_missing_critical_japanese_item_still_blocks(self):
        """V6/M4 contract: japanese-threshold flipped critical (see
        readiness_gate.py's ITEMS comment). Removing its evidence file must
        NOT read as a shrug-worthy NO-DATA off the critical path; an unproven
        critical item blocks READY exactly like a FAIL, same as any other
        critical row."""
        d = self._all_critical_pass_root()
        os.remove(os.path.join(d, "scripts", "test_japanese_threshold.py"))
        self.assertEqual(_silent_main(["--root", d]), 1)

    def test_a_no_data_noncritical_item_still_opens_the_gate(self):
        """The other side of the same coin: a non-critical NO-DATA (the suite
        file absent) must NOT block, so the stricter rule did not over-reach
        into treating unknowns as breaks."""
        d = self._all_critical_pass_root()
        # reproducible-release-artifact stays absent -> NO-DATA
        self.assertEqual(_silent_main(["--root", d]), 0)

    def _write_expectations(self, d, name, review_by):
        p = os.path.join(d, "docs", "plan", "BATTERY-EXPECTATIONS.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"checks": {name: {"class": "known_no_data",
                                         "review_by": review_by}}}, fh)

    def test_an_expired_declared_exception_forces_not_ready(self):
        """Red-team item 6, end to end: a declared exception past its review_by
        must block the gate, not just the consolidated battery verdict. A
        failure cemetery is not allowed."""
        d = self._all_critical_pass_root()
        self._write_expectations(d, "stale-x", "2026-01-01")
        self.assertEqual(_silent_main(["--root", d, "--today", "2026-06-01"]), 1)

    def test_a_future_dated_exception_does_not_block(self):
        d = self._all_critical_pass_root()
        self._write_expectations(d, "fresh-x", "2027-01-01")
        self.assertEqual(_silent_main(["--root", d, "--today", "2026-06-01"]), 0)

    def test_on_the_real_tree_today_the_gate_is_ready(self):
        """Calibration against the actual repository, updated when B3 and B4
        landed scripts/test_tenancy_isolation.py and
        scripts/test_policy_fail_closed.py (the Brother-side black-box
        proofs of VB3-03 and VB3-04, run against a vendored, frozen copy of
        the BrotherModeUp modules that merged those rows -- see
        scripts/fixtures/bmu_vault_seam/PROVENANCE.md) and the restore
        drill already recorded a pass. Every currently-defined critical
        item now proves itself; the gate must read READY. A gate that
        still reported NOT READY on this tree, with all three of those
        already landed, would be lying in the other direction."""
        self.assertEqual(_silent_main(["--root", REPO_ROOT]), 0)


class TheFifteenQuestionReferenceIsHonestNotInvented(unittest.TestCase):
    """The review's fifteen definition-of-done questions do not exist anywhere
    in this repository (docs/plan, docs/plan/research, or git history -- see
    readiness_gate.py's module docstring for the search log). Per this
    estate's own rule against fabricating evidence, this page must NOT claim
    fifteen numbered questions it cannot quote from a real source; it must
    name the gap. This test is the drift guard on that honesty, not on a
    fabricated count: it fails if the page starts asserting fifteen numbered
    questions again without a real source landing first."""

    PAGE = os.path.join(REPO_ROOT, "docs", "plan", "FIFTEEN-QUESTION-PR-BAR.md")

    def test_the_reference_page_exists(self):
        self.assertTrue(os.path.isfile(self.PAGE), self.PAGE)

    def test_it_names_the_row_of_record(self):
        with open(self.PAGE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("VB3-12", text)
        self.assertIn("VAULT-WBS-V2-2026-08-29.json", text)

    def test_it_reports_no_data_rather_than_inventing_fifteen_questions(self):
        with open(self.PAGE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(RG.NODATA, text)
        # A genuinely-sourced fifteen-question list would number them 1-15.
        # None of those markers should appear paired with question text here
        # today, because no such list was found.
        for n in range(1, 16):
            self.assertNotIn("%d. " % n, text.split("### What was searched")[0])


if __name__ == "__main__":
    unittest.main()
