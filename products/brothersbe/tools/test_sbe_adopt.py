#!/usr/bin/env python3
"""Fixtures for `sbe adopt` and `sbe init`. Run: python3 tools/test_sbe_adopt.py

Every test builds a real temporary git repository (or, for the refusal
fixture, deliberately does not) and runs the real `bin/sbe` command against
it. Nothing is mocked, matching `tools/test_sbe_impact.py`'s own rule: the
defect these controls exist for lives at the seam between what this tool can
actually read from a filesystem and what it claims, and a mocked filesystem
would test the mock.

`sbe adopt`'s workflow-file assertions (`TestWorkflowFiles`) read the shipped
YAML as TEXT and grep for the lines that matter, the same line-oriented
approach `evals/run_evals.py::_waiver_grep_pattern` already uses against
`brothersbe-gates.yml`. This project ships no YAML parser (Python 3.9 stdlib
only, and PyYAML is not a dependency here), so a structural check that
"parses" the file would have to bring one in or hand-roll one either way;
grepping the exact lines CI itself depends on is the same honesty trade this
project makes everywhere else: CI is the real parser, and this test checks
that the lines CI needs are actually present, not that the file is
well-formed YAML in general.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import shutil
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


def run_sbe(*args, **kwargs):
    cwd = kwargs.pop("cwd", None)
    argv = [sys.executable, SBE] + list(args)
    out = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    data = None
    stripped = out.stdout.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
    return out.returncode, data, out.stdout + out.stderr


def tree_hash(root):
    """One digest over every path and its bytes, excluding .git. Two calls
    against an unmodified tree produce the same digest; a single byte
    written anywhere changes it. This is the instrument fixture 1 (dry-run
    writes NOTHING) is built on, deliberately independent of whichever
    files this module happens to propose today.
    """
    digest = hashlib.sha256()
    # os.walk is walked DIRECTLY, never wrapped in sorted(): sorted() drains the
    # generator before the loop body runs, so assigning dirnames[:] prunes
    # nothing and the .git exclusion below was inert. It read as excluded and
    # hashed git's internals anyway, which made this instrument measure git's
    # own bookkeeping: a maintenance.lock that git created and removed mid-walk
    # crashed the macOS legs of CI with FileNotFoundError. Determinism comes
    # from sorting the collected paths afterwards instead.
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            found.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
    for rel, full in sorted(found):
        digest.update(rel.encode("utf-8"))
        with open(full, "rb") as fh:
            digest.update(fh.read())
    return digest.hexdigest()


class AdoptFixture(unittest.TestCase):
    """A fresh git repository per test, with one base commit already in it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "README.md", "base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)


class TestDryRunWritesNothing(AdoptFixture):
    """FIXTURE 1: dry-run writes NOTHING, in either command, proved by
    hashing the whole tree before and after rather than checking for the
    two or three paths this module happens to propose today."""

    def test_adopt_dry_run_writes_nothing(self):
        before = tree_hash(self.repo)
        code, data, text = run_sbe("adopt", self.repo, "--dry-run", "--json")
        after = tree_hash(self.repo)
        self.assertEqual(before, after, "sbe adopt --dry-run changed the tree: %s" % text)
        self.assertEqual(code, 0, text)
        self.assertTrue(data["proposals"], text)

    def test_adopt_default_is_also_dry_run(self):
        """`--dry-run` is the default even when the flag itself is omitted;
        the flag only makes the default explicit."""
        before = tree_hash(self.repo)
        code, _data, text = run_sbe("adopt", self.repo, "--json")
        after = tree_hash(self.repo)
        self.assertEqual(before, after, text)

    def test_init_dry_run_writes_nothing(self):
        before = tree_hash(self.repo)
        code, data, text = run_sbe("init", self.repo, "--dry-run", "--json")
        after = tree_hash(self.repo)
        self.assertEqual(before, after, "sbe init --dry-run changed the tree: %s" % text)
        self.assertEqual(code, 0, text)
        self.assertTrue(data["proposals"], text)


class TestApplyThenNoop(AdoptFixture):
    """FIXTURE 2: --apply writes the proposed files; a second --apply over
    the same tree is a no-op and says so."""

    def test_apply_writes_then_second_apply_is_a_noop(self):
        code, data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(data["applied"]["written"]),
                         [".brothersbe/policy.json", ".github/CODEOWNERS"], text)
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".brothersbe", "policy.json")))
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".github", "CODEOWNERS")))

        before = tree_hash(self.repo)
        code2, data2, text2 = run_sbe("adopt", self.repo, "--apply", "--json")
        after = tree_hash(self.repo)
        self.assertEqual(code2, 0, text2)
        self.assertEqual(before, after, "a second --apply over an unchanged tree wrote "
                                        "something: %s" % text2)
        self.assertEqual(data2["applied"]["written"], [],
                         "a second --apply must write nothing the second time")
        self.assertEqual(len(data2["applied"]["skipped"]), 2, text2)
        for item in data2["applied"]["skipped"]:
            self.assertIn("already matches", item["reason"], text2)


class TestForceRequired(AdoptFixture):
    """FIXTURE 3: an existing file that differs from the proposal is never
    overwritten without --force."""

    def test_existing_file_is_not_overwritten_without_force(self):
        planted = "# a CODEOWNERS a human already wrote\n*.py @someone\n"
        write(self.repo, ".github/CODEOWNERS", planted)

        code, data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        with io.open(os.path.join(self.repo, ".github", "CODEOWNERS"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), planted, "an existing file was overwritten without "
                                                 "--force")
        skipped_paths = [s["path"] for s in data["applied"]["skipped"]]
        self.assertIn(".github/CODEOWNERS", skipped_paths, text)

        code2, data2, text2 = run_sbe("adopt", self.repo, "--apply", "--force", "--json")
        self.assertEqual(code2, 0, text2)
        with io.open(os.path.join(self.repo, ".github", "CODEOWNERS"), encoding="utf-8") as fh:
            self.assertNotEqual(fh.read(), planted, "--force did not overwrite the existing "
                                                     "file: %s" % text2)
        self.assertIn(".github/CODEOWNERS", data2["applied"]["written"], text2)


class TestAdoptionReportNeverClaimsPresent(AdoptFixture):
    """FIXTURE 4, THE KILL CRITERION: the adoption report on a laptop-local
    repository marks branch protection (and every other GitHub-side
    protection) UNVERIFIABLE-HERE, and NEVER PRESENT, because nothing here
    holds a GitHub token or asked for one."""

    def test_branch_protection_is_unverifiable_here_never_present(self):
        code, data, text = run_sbe("adopt", self.repo, "--dry-run", "--json")
        self.assertEqual(code, 0, text)
        by_name = dict((p["name"], p["status"]) for p in data["protections"])
        self.assertEqual(by_name["branch-protection"], "UNVERIFIABLE-HERE", text)
        self.assertEqual(by_name["required-status-checks"], "UNVERIFIABLE-HERE", text)
        self.assertEqual(by_name["codeowners-review-required"], "UNVERIFIABLE-HERE", text)
        statuses = set(p["status"] for p in data["protections"])
        self.assertNotIn("PRESENT", statuses,
                         "sbe adopt claimed a GitHub-side protection PRESENT from a local "
                         "clone with no API access: %s" % text)
        for prot in data["protections"]:
            self.assertTrue(prot["missing"], "an UNVERIFIABLE-HERE protection must name what "
                                             "is missing: %s" % text)

    def test_even_with_a_codeowners_file_present_enforcement_stays_unverifiable(self):
        """A CODEOWNERS file existing locally is a fact this tool CAN read
        (reported under localFacts). It must not be read as proof that
        GitHub is configured to REQUIRE that review, which lives only on
        the platform."""
        write(self.repo, ".github/CODEOWNERS", "* @someone\n")
        code, data, text = run_sbe("adopt", self.repo, "--dry-run", "--json")
        self.assertEqual(code, 0, text)
        by_name = dict((p["name"], p["status"]) for p in data["protections"])
        self.assertEqual(by_name["codeowners-review-required"], "UNVERIFIABLE-HERE", text)
        local = dict((f["name"], f["status"]) for f in data["localFacts"])
        self.assertEqual(local["codeowners-file"], "PRESENT", text)


class TestStackDetectionShiftsThePolicy(AdoptFixture):
    """FIXTURE 5: a planted dbt project and a planted migrations directory
    each shift the proposed policy in the expected, named way."""

    def test_a_planted_dbt_project_adds_the_dbtModels_protected_path(self):
        write(self.repo, "models/staging/stg_orders.sql", "select 1\n")
        code, data, text = run_sbe("adopt", self.repo, "--dry-run", "--json")
        self.assertEqual(code, 0, text)
        self.assertTrue(data["detectedStack"]["hasDbtModels"], text)
        policy_proposal = [p for p in data["proposals"] if p["path"] == ".brothersbe/policy.json"][0]
        self.assertIn("dbtModels", policy_proposal["diff"],
                      "a detected dbt project must shift the proposed policy by name: %s"
                      % text)
        self.assertNotIn("migrations", policy_proposal["diff"], text)

    def test_a_planted_migrations_dir_adds_the_migrations_protected_path(self):
        write(self.repo, "migrations/0001_init.sql", "create table t (id int);\n")
        code, data, text = run_sbe("adopt", self.repo, "--dry-run", "--json")
        self.assertEqual(code, 0, text)
        self.assertTrue(data["detectedStack"]["hasMigrations"], text)
        policy_proposal = [p for p in data["proposals"] if p["path"] == ".brothersbe/policy.json"][0]
        self.assertIn('"migrations"', policy_proposal["diff"],
                      "a detected migrations directory must shift the proposed policy by "
                      "name: %s" % text)
        self.assertNotIn("dbtModels", policy_proposal["diff"], text)

    def test_neither_signal_present_proposes_neither_extra_path(self):
        code, data, text = run_sbe("adopt", self.repo, "--dry-run", "--json")
        self.assertEqual(code, 0, text)
        self.assertFalse(data["detectedStack"]["hasDbtModels"], text)
        self.assertFalse(data["detectedStack"]["hasMigrations"], text)
        policy_proposal = [p for p in data["proposals"] if p["path"] == ".brothersbe/policy.json"][0]
        self.assertNotIn("dbtModels", policy_proposal["diff"], text)
        self.assertNotIn('"migrations"', policy_proposal["diff"], text)


GHOST_LAYOUT_PATHS = (".claude-plugin/plugin.json", "hooks/", "src/brothersbe/evidence.py",
                      "src/brothersbe/__init__.py", ".github/workflows/", ".github/actions/",
                      "VERSION", "CHANGELOG.md", "CHECKSUMS.sha256")

LAYOUT_CATEGORIES = ("manifest", "hooks", "evidenceSchemas", "workflows", "releaseFiles")


def read_applied(repo):
    """(policy, codeowners, read_paths): the two files a real `--apply`
    left on disk, parsed and raw, plus the exact relative paths that were
    read. Three values, never two: a two-value return reads as a possible
    (verdict, evidence) pair to the registry lint, and it is neither. The
    new-law fixtures below read the applied end state rather than the diff
    text, because the end state is what a consumer repository actually
    lives with."""
    read_paths = (".brothersbe/policy.json", ".github/CODEOWNERS")
    with io.open(os.path.join(repo, ".brothersbe", "policy.json"), encoding="utf-8") as fh:
        policy = json.load(fh)
    with io.open(os.path.join(repo, ".github", "CODEOWNERS"), encoding="utf-8") as fh:
        codeowners = fh.read()
    return policy, codeowners, read_paths


class TestGhostPathsNeverProposed(AdoptFixture):
    """FIXTURE 5b, THE EXTERNAL-PROOF LAW (docs/EXTERNAL-PROOF-2026-07-31.md,
    open item 5, closed here): the proposal contains only paths that exist
    under the target root, plus the two paths the adoption kit itself
    creates, and every dropped category is NAMED rather than silently
    vanishing or, worse, proposed as a ghost. Each test in this class was
    calibrated by re-injecting the fixed-proposal behavior (the pre-fix
    adopt.py) and watching it fail before being counted green."""

    def test_a_bare_repo_proposes_no_ghost_paths(self):
        """The original defect: a fresh foreign clone got CODEOWNERS lines
        and protectedPaths for this repository's own layout, none of which
        exist there."""
        code, _data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        policy, codeowners, _read = read_applied(self.repo)
        proposed = []
        for entry in policy["protectedPaths"].values():
            proposed.extend(entry["paths"])
        for ghost in GHOST_LAYOUT_PATHS:
            self.assertNotIn(ghost, proposed,
                             "a path that does not exist in this tree was proposed for "
                             "protection: %s in %s" % (ghost, proposed))
        owner_lines = [ln for ln in codeowners.splitlines()
                       if ln.strip() and not ln.startswith("#")]
        for line in owner_lines:
            path = line.split()[0]
            self.assertNotIn(path, GHOST_LAYOUT_PATHS,
                             "CODEOWNERS proposes an owner for a ghost path: %s" % line)

    def test_only_the_self_created_entries_survive_in_a_bare_repo(self):
        """`.brothersbe/` (created by this very --apply) and `design/`
        (created by sbe init) are the whole proposal when nothing else
        exists; the five layout categories are all dropped."""
        code, _data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        policy, _codeowners, _read = read_applied(self.repo)
        self.assertEqual(sorted(policy["protectedPaths"]), ["dossiers", "policies"], text)

    def test_the_dossiers_entry_is_always_proposed(self):
        """`design/` does not exist in this fixture and is proposed anyway:
        it is not a layout guess, it is the directory sbe init will create,
        and the policy protecting it must exist before the dossiers do."""
        self.assertFalse(os.path.isdir(os.path.join(self.repo, "design")),
                         "fixture repo must start without a design/ directory")
        code, _data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        policy, _codeowners, _read = read_applied(self.repo)
        self.assertEqual(policy["protectedPaths"]["dossiers"]["paths"], ["design/"], text)

    def test_dropped_categories_are_named_in_notProposed(self):
        """Silence is the failure mode this note exists for: a reader of a
        thin proposal must be able to see WHAT was dropped and WHY, path by
        path, without diffing against this repository."""
        code, _data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        policy, _codeowners, _read = read_applied(self.repo)
        categories = policy["_notProposed"]["categories"]
        self.assertEqual(sorted(categories), sorted(LAYOUT_CATEGORIES),
                         "every dropped category must be named: %s" % categories)
        for key, entry in categories.items():
            self.assertTrue(entry["missingPaths"],
                            "%s is listed as dropped but names no missing path" % key)
        self.assertTrue(policy["_notProposed"]["note"],
                        "the _notProposed block must carry its own explanation")

    def test_codeowners_names_the_dropped_categories_in_a_comment(self):
        code, _data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        _policy, codeowners, _read = read_applied(self.repo)
        self.assertIn("# Not proposed (no such path under this root):", codeowners,
                      "CODEOWNERS must say what it deliberately left out: %s" % codeowners)
        for key in LAYOUT_CATEGORIES:
            self.assertIn(key, codeowners,
                          "CODEOWNERS must name dropped category %s: %s" % (key, codeowners))

    def test_a_partially_present_category_proposes_only_its_existing_paths(self):
        """Existence is checked per path, not per category: a repo with a
        VERSION file but no CHANGELOG gets a releaseFiles entry protecting
        VERSION alone, and the two missing siblings are named."""
        write(self.repo, "VERSION", "1.0\n")
        write(self.repo, "hooks/pre-commit.sh", "#!/bin/sh\n")
        code, _data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        policy, codeowners, _read = read_applied(self.repo)
        self.assertEqual(policy["protectedPaths"]["releaseFiles"]["paths"], ["VERSION"], text)
        self.assertEqual(policy["protectedPaths"]["hooks"]["paths"], ["hooks/"], text)
        self.assertEqual(policy["_notProposed"]["categories"]["releaseFiles"]["missingPaths"],
                         ["CHANGELOG.md", "CHECKSUMS.sha256"], text)
        self.assertNotIn("hooks", policy["_notProposed"]["categories"],
                         "a fully present category must not appear among the dropped")
        self.assertIn("VERSION @REPLACE-ME", codeowners, codeowners)
        self.assertNotIn("CHANGELOG.md @REPLACE-ME", codeowners, codeowners)

    def test_both_output_modes_name_the_dropped_categories(self):
        """The law is only as good as its visibility: `--json` carries a
        top-level notProposed block, and the human rendering prints one
        NOT-PROPOSED line per dropped category."""
        code, data, text = run_sbe("adopt", self.repo, "--dry-run", "--json")
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(data["notProposed"]["categories"]), sorted(LAYOUT_CATEGORIES),
                         text)
        code2, _data2, text2 = run_sbe("adopt", self.repo, "--dry-run")
        self.assertEqual(code2, 0, text2)
        for key in LAYOUT_CATEGORIES:
            self.assertIn("NOT-PROPOSED %s" % key, text2,
                          "the human rendering must name dropped category %s: %s"
                          % (key, text2))

    def test_second_apply_stays_a_noop_after_adopt_creates_its_own_paths(self):
        """The trap an existence filter walks into: the first --apply
        creates `.brothersbe/`, so a naive filter would propose MORE on the
        second run and break the determinism fixture 2 pins. The two
        self-created entries are unconditional for exactly this reason."""
        code, _data, text = run_sbe("adopt", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        before = tree_hash(self.repo)
        code2, data2, text2 = run_sbe("adopt", self.repo, "--apply", "--json")
        after = tree_hash(self.repo)
        self.assertEqual(code2, 0, text2)
        self.assertEqual(before, after,
                         "the paths the first --apply created shifted the second proposal: "
                         "%s" % text2)
        self.assertEqual(data2["applied"]["written"], [], text2)
        for item in data2["applied"]["skipped"]:
            self.assertIn("already matches", item["reason"],
                          "an unchanged tree must skip as identical, not as a conflicting "
                          "proposal; a filter that existence-checks the paths adopt itself "
                          "creates drifts here first: %s" % text2)


class TestAdoptOnThisRepository(unittest.TestCase):
    """FIXTURE 5c: the existence filter must not drop REAL paths. Run
    against this repository itself, where every layout path exists, nothing
    is dropped; a filter that trims a category here is filtering on
    something other than existence."""

    def test_every_layout_category_survives_where_its_paths_exist(self):
        code, data, text = run_sbe("adopt", ROOT, "--dry-run", "--json")
        self.assertEqual(code, 0, text)
        self.assertEqual(data["notProposed"]["categories"], {},
                         "a layout path that exists in this repository was dropped: %s"
                         % data["notProposed"])


class TestInitReceipt(AdoptFixture):
    """FIXTURE 6: the init receipt exists, lists every written path, and the
    uninstall instructions match that written set exactly."""

    def test_receipt_lists_every_written_path_and_uninstall_matches(self):
        code, data, text = run_sbe("init", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        receipt_path = os.path.join(self.repo, ".brothersbe", "install-receipt.json")
        self.assertTrue(os.path.exists(receipt_path), text)
        with io.open(receipt_path, encoding="utf-8") as fh:
            receipt = json.load(fh)

        self.assertEqual(sorted(receipt["writtenPaths"]),
                         [".brothersbe/config.json", ".brothersbe/install-receipt.json",
                          "design/.gitkeep"], text)
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".brothersbe", "config.json")))
        self.assertTrue(os.path.exists(os.path.join(self.repo, "design", ".gitkeep")))

        instructed_paths = set()
        for line in receipt["uninstallInstructions"]:
            self.assertTrue(line.startswith("rm -f "), text)
            instructed_paths.add(line[len("rm -f "):])
        self.assertEqual(instructed_paths, set(receipt["writtenPaths"]),
                         "uninstall instructions must name exactly the written set, no more "
                         "and no less: %s" % text)

        # A second --apply changes nothing, and the receipt on disk (including
        # its installedAt timestamp) is untouched rather than rewritten.
        before = tree_hash(self.repo)
        code2, data2, text2 = run_sbe("init", self.repo, "--apply", "--json")
        after = tree_hash(self.repo)
        self.assertEqual(code2, 0, text2)
        self.assertEqual(before, after, "a second sbe init --apply changed the tree: %s"
                         % text2)
        self.assertTrue(data2["skippedAsNoop"], text2)


class TestInitGitignoreLine(AdoptFixture):
    """FIXTURE 6b: `sbe init` appends its own `.gitignore` line above a
    one-line comment, idempotently, and never lists that file in the
    receipt's uninstall set -- a real incident this hardening exists for:
    a fresh clone that ran `sbe init --apply` tracked its own
    machine-specific install receipt because nothing ensured the ignore
    line existed."""

    def test_apply_creates_gitignore_with_comment_and_line(self):
        gitignore = os.path.join(self.repo, ".gitignore")
        self.assertFalse(os.path.exists(gitignore), "fixture repo must start without one")
        code, data, text = run_sbe("init", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        with io.open(gitignore, encoding="utf-8") as fh:
            body = fh.read()
        lines = body.splitlines()
        self.assertIn(".brothersbe/install-receipt.json", lines, text)
        line_idx = lines.index(".brothersbe/install-receipt.json")
        self.assertGreater(line_idx, 0, "the receipt line must sit under a comment: %s" % body)
        self.assertTrue(lines[line_idx - 1].startswith("#"),
                        "the line above the ignore entry must be a comment: %s" % body)
        self.assertIn(".gitignore", data["written"],
                     "sbe init --apply must report .gitignore among the paths it wrote: %s"
                     % text)

    def test_apply_appends_below_existing_gitignore_content_untouched(self):
        write(self.repo, ".gitignore", "*.pyc\nnode_modules/\n")
        code, data, text = run_sbe("init", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        with io.open(os.path.join(self.repo, ".gitignore"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("*.pyc\n", body, body)
        self.assertIn("node_modules/\n", body, body)
        self.assertIn(".brothersbe/install-receipt.json", body, body)

    def test_a_gitignore_that_already_carries_the_line_is_untouched(self):
        """Idempotent: present means the proposal is identical, and a
        dry-run over an already-compliant repository proposes nothing new
        for `.gitignore`, matching every other mutation this command makes."""
        write(self.repo, ".gitignore",
             "# already tracking the receipt\n.brothersbe/install-receipt.json\n")
        before = tree_hash(self.repo)
        code, data, text = run_sbe("init", self.repo, "--dry-run", "--json")
        after = tree_hash(self.repo)
        self.assertEqual(code, 0, text)
        self.assertEqual(before, after, text)
        gitignore_proposal = [p for p in data["proposals"] if p["path"] == ".gitignore"][0]
        self.assertTrue(gitignore_proposal["identical"],
                        "an already-compliant .gitignore must propose nothing new: %s" % text)

    def test_gitignore_is_never_in_the_receipts_uninstall_set(self):
        code, data, text = run_sbe("init", self.repo, "--apply", "--json")
        self.assertEqual(code, 0, text)
        receipt = data["receipt"]
        self.assertNotIn(".gitignore", receipt["writtenPaths"],
                         "sbe init only appends a line to .gitignore; it does not own the "
                         "file and must never list it as something an uninstall would "
                         "delete: %s" % text)
        for line in receipt["uninstallInstructions"]:
            self.assertNotIn(".gitignore", line,
                             "the uninstall instructions must never touch .gitignore: %s"
                             % text)


class TestInitRefusesOutsideGit(unittest.TestCase):
    """FIXTURE 7: sbe init outside a git repository refuses, and names why,
    rather than writing a config file with nowhere to be versioned."""

    def setUp(self):
        self.plain_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.plain_dir, ignore_errors=True)

    def test_apply_outside_git_repo_refuses_with_reason(self):
        before = tree_hash(self.plain_dir)
        code, _data, text = run_sbe("init", self.plain_dir, "--apply")
        after = tree_hash(self.plain_dir)
        self.assertNotEqual(code, 0, "sbe init --apply must refuse outside a git repository: "
                            "%s" % text)
        self.assertIn("git repository", text.lower(), text)
        self.assertEqual(before, after, "a refused init still wrote something: %s" % text)

    def test_dry_run_outside_git_repo_also_refuses(self):
        code, _data, text = run_sbe("init", self.plain_dir, "--dry-run")
        self.assertNotEqual(code, 0, text)
        self.assertIn("git repository", text.lower(), text)


class TestWorkflowFiles(unittest.TestCase):
    """FIXTURE 8: the product workflow carries both triggers, and the
    consumer workflow (and the composite action behind it) never invoke
    BrotherSBE's own test files. Line-oriented on purpose; see the module
    docstring for why this project does not parse the YAML to check it."""

    @staticmethod
    def _code_only(text):
        """The file with every full-line comment dropped, so a docstring-style
        explanation of what a workflow deliberately does NOT run (this
        project's own house style, all over its shipped YAML) cannot be
        mistaken for an invocation. A `#` that starts a line once stripped of
        leading whitespace is a comment line in YAML; nothing here strips an
        inline `#` after real content, because none of the greps below need
        to survive one."""
        return "\n".join(line for line in text.split("\n")
                         if not line.strip().startswith("#"))

    def setUp(self):
        with io.open(os.path.join(ROOT, ".github", "workflows", "brothersbe-gates.yml"),
                     encoding="utf-8") as fh:
            self.product_wf = fh.read()
        with io.open(os.path.join(ROOT, ".github", "workflows", "consumer-check.yml"),
                     encoding="utf-8") as fh:
            self.consumer_wf = fh.read()
        with io.open(os.path.join(ROOT, ".github", "actions", "sbe-consumer", "action.yml"),
                     encoding="utf-8") as fh:
            self.consumer_action = fh.read()
        self.consumer_wf_code = self._code_only(self.consumer_wf)
        self.consumer_action_code = self._code_only(self.consumer_action)

    # THESE TWO TESTS WERE INVERTED ON 2026-08-17, and the inversion is the
    # point rather than an accommodation. They asserted that both workflows
    # carry `pull_request:` and `push:` on main, which is exactly the shape the
    # founder law of 2026-08-16 forbids: 886 runs at 11 jobs each spent a free
    # month of CI minutes in two weeks, and nothing on this estate may fire by
    # itself again.
    #
    # A test encoding a repealed requirement does not fail safe. It fails LOUDLY
    # AGAINST THE FIX, which is exactly what happened: disarming the workflows
    # turned these two assertions red, and a session in a hurry reads that as
    # "the disarm broke something" and reverts the disarm to get back to green.
    # That is how a repealed rule reinstates itself through its own test suite.
    #
    # They assert the current law now, which is more coverage than deleting them
    # would have been: any automatic trigger, and any runner billed above the
    # Linux rate, fails here. Comment lines are stripped before matching, so the
    # history written into those files does not read as configuration.
    AUTO_TRIGGERS = ("push:", "pull_request:", "pull_request_target:", "schedule:")
    BILLED_RUNNERS = ("macos-", "windows-")

    def _assert_disarmed(self, text, which):
        body = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))
        for trigger in self.AUTO_TRIGGERS:
            self.assertNotIn(
                trigger, body,
                "%s carries the automatic trigger %r. Nothing on this estate may fire "
                "by itself (founder law 2026-08-16); a workflow that is genuinely "
                "needed is workflow_dispatch only, on ubuntu-latest." % (which, trigger))
        for runner in self.BILLED_RUNNERS:
            self.assertNotIn(
                runner, body,
                "%s names a %r runner. Linux bills 1x, Windows 2x and macOS 10x, so a "
                "2,000 minute allowance is roughly 200 macOS minutes." % (which, runner))
        self.assertIn(
            "workflow_dispatch:", body,
            "%s now has no trigger at all, which is not the same as disarmed: a "
            "workflow nobody can run on purpose is dead code, not a control." % which)

    def test_product_workflow_fires_only_when_a_human_dispatches_it(self):
        self._assert_disarmed(self.product_wf, "the product workflow")

    def test_consumer_workflow_fires_only_when_a_human_dispatches_it(self):
        self._assert_disarmed(self.consumer_wf, "the consumer workflow")

    def test_consumer_workflow_never_invokes_the_product_test_files(self):
        for banned in ("test_sbe.py", "test_sbe_adopt.py", "test_sbe_impact.py",
                      "test_sbe_evidence.py", "test_sbe_bypass.py", "test_sbe_fence_hook.py",
                      "run_evals.py", "test_no_data_class.py"):
            self.assertNotIn(banned, self.consumer_wf_code,
                             "the consumer workflow must never invoke %s: %s"
                             % (banned, self.consumer_wf_code))
            self.assertNotIn(banned, self.consumer_action_code,
                             "the consumer composite action must never invoke %s: %s"
                             % (banned, self.consumer_action_code))

    def test_consumer_action_only_calls_the_named_commands(self):
        """The composite action's own body may only call the four commands
        the spec names: impact, evidence verify, status, and design. A fifth
        subcommand slipping in would be scope creep past what a client asked
        BrotherSBE's own repository to test."""
        for allowed in ("impact", "evidence verify", "status", "design"):
            self.assertIn(allowed, self.consumer_action_code, self.consumer_action_code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
