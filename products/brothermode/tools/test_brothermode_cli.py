#!/usr/bin/env python3
"""Contract tests for tools/brothermode_cli.py, the one deterministic
public runtime boundary (V3-FREEZE-2026-08-07.md answer 4; plan section
"Agent 4, Runtime Boundary Engineer").

WHAT THIS FILE PROVES
  Every one of the ten subcommands (start, status, next, review, deliver,
  view, doctor, recover, version, update) has at least one deterministic
  test below. The load-bearing one is TestStatusIsByteIdenticalToBmLead:
  it runs `brothermode_cli.py status` and `bm_lead.py status` as two real
  subprocesses against the SAME real, read-only project store and asserts
  their stdout is byte-identical. That is the actual evidence "this CLI
  is a thin dispatch layer, not a re-description" rests on; the rest of
  this file follows the same discipline (real subprocess, real behavior)
  wherever a real store can be used read-only, and a throwaway store
  under tempfile.TemporaryDirectory() wherever a subcommand writes.

WHY THE REAL STORE FOR status/next/doctor/version
  This worktree carries no .brothermode/ of its own; both `brothermode_
  cli.py` and `bm_lead.py` resolve root the identical way (bm_store.
  resolve_root's marker walk), so both climb to the same parent
  repository's store, exactly as a founder running either command from
  this checkout would. That climb IS the mechanism the H1 test at the
  bottom of this file reproduces as a DEFECT for a WRITE tool; reading
  status through it is exactly what the done-check in this run's own
  brief names as the load-bearing proof, so this file reads that project
  (release-21-final-night) read-only rather than avoiding the climb.
  Nothing in this file ever calls a mutating subcommand against that
  root: start, review, deliver, view, update and recover are each
  proven against a throwaway store or a throwaway git repository built
  fresh per test class.

THE H1 TEST (bottom of this file)
  ONE failing test, per V3-FREEZE-2026-08-07.md's Refutation adjudication
  ruling H1: nested worktrees resolve bm_store.resolve_root to the OUTER
  repository (because a linked worktree carries no .brothermode/ of its
  own), while a write tool's own file_path stays inside the worktree
  subdirectory, so tools/bm_fence_hook.py's canonical_target computes a
  worktree-prefixed path that a claim filed on the plain, root-relative
  path never matches. Reproduced directly against tools/bm_store.py and
  tools/bm_fence_hook.py, unmodified, with no real `git worktree add`
  needed: a plain nested directory under a marked root reproduces the
  identical resolve_root behavior the refutation verified on disk. The
  fix belongs to tools/bm_store.py or tools/bm_fence_hook.py, neither of
  which is this file's fence, so the test is marked
  @unittest.expectedFailure and cites the ruling rather than widening
  what this change touches.

Python 3.9, standard library only. Run:
  python3 tools/test_brothermode_cli.py

No em or en dashes anywhere in this file, its comments, or its output.
"""

import datetime
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

CLI_PATH = os.path.join(HERE, "brothermode_cli.py")
LEAD_PATH = os.path.join(HERE, "bm_lead.py")
PROJECT_PATH = os.path.join(HERE, "bm_project.py")
STORE_PATH = os.path.join(HERE, "bm_store.py")
FENCE_HOOK_PATH = os.path.join(HERE, "bm_fence_hook.py")
DOCTOR_PATH = os.path.join(REPO, "scripts", "doctor.py")

# The real, live project this worktree's own store already holds. Named
# directly in this run's brief as the done-check's own project id; using
# it here (read-only: status and next issue no write) is what makes the
# byte-identity proof real evidence instead of a fixture built to agree
# with itself.
REAL_PROJECT_ID = "release-21-final-night"


def _load(path, name):
    """Load a module by PATH, the identical technique every sibling test
    file in tools/ uses for tools/bm_store.py."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load(STORE_PATH, "bm_store")
sys.modules.setdefault("bm_store", bs)
fh = _load(FENCE_HOOK_PATH, "bm_fence_hook")
sys.modules.setdefault("bm_fence_hook", fh)


def _load_cli_module():
    """A fresh brothermode_cli.py module object, isolated from whatever
    the previous test did to it (mock.patch.object always restores the
    attribute it patched, but a fresh load per test class costs nothing
    and removes any doubt)."""
    return _load(CLI_PATH, "brothermode_cli")


def _clean_env(extra=None):
    """A copy of the real environment with BROTHERMODE_ROOT removed, so
    an ambient shell export never decides a throwaway test's outcome.
    `extra` layers throwaway overrides on top, matching tools/
    test_bm_project.py's own _run helper."""
    e = dict(os.environ)
    e.pop("BROTHERMODE_ROOT", None)
    if extra:
        e.update(extra)
    return e


def _run(script, args, cwd, env=None):
    """Drive `script` (an absolute path) as a real subprocess, capturing
    text stdout/stderr. This is the ONE subprocess runner every test
    below uses, for both brothermode_cli.py and the internal tools it
    wraps, so a byte-identity comparison always runs both sides through
    literally the same code path."""
    return subprocess.run(
        [sys.executable, script] + list(args),
        cwd=cwd, capture_output=True, text=True, env=env)


def _init_store(root, env=None):
    """python3 tools/bm_store.py init against `root`. Every mutating
    subcommand's throwaway fixture needs a real store to write into
    first: bm_project.py's own _store() refuses (create=False) rather
    than creating one, matching bm_store.py's own CLI convention."""
    r = _run(STORE_PATH, ["init"], root, env=env)
    if r.returncode != 0:
        raise AssertionError("bm_store.py init failed: %s"
                             % (r.stdout + r.stderr))
    return r


def _git(repo, *args):
    """One git call against a throwaway repo, output captured. Mirrors
    tools/test_bm_autosave.py's own _git helper exactly."""
    return subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)


def _init_repo(path):
    """A throwaway scratch repo, isolated from any real developer
    identity or signing config, so a test can never hang waiting on a
    GPG prompt or pick up ambient git config from the machine running
    it. Mirrors tools/test_bm_autosave.py's own _init_repo exactly."""
    r = _git(path, "init", "-q")
    if r.returncode != 0:
        raise AssertionError("git init failed: %s" % (r.stdout + r.stderr))
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "commit.gpgsign", "false")


def _write_consented_config(path, vault):
    """A minimal, consented BROTHERME_CONFIG, the shape tools/
    test_bm_view.py's own _write_consented_config uses: only
    setup_complete matters to scripts/setup.py's is_consented, the rest
    is there so a reader of the fixture sees a realistic config."""
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as fh_:
        json.dump({"setup_complete": True, "vault_path": vault,
                   "privacy_notice_version": "2026-08-01",
                   "installation_mode": "clone",
                   "security_mode": "standard"}, fh_)


# ---------------------------------------------------------------------------
# help / dispatch
# ---------------------------------------------------------------------------

class TestHelpAndDispatch(unittest.TestCase):

    def test_help_lists_all_ten_subcommands(self):
        r = _run(CLI_PATH, ["--help"], REPO, env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in ("start", "status", "next", "review", "deliver",
                     "view", "doctor", "recover", "version", "update"):
            self.assertIn(name, r.stdout,
                          "help text does not name subcommand %r" % name)

    def test_no_arguments_behaves_like_help(self):
        r = _run(CLI_PATH, [], REPO, env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("status", r.stdout)

    def test_unknown_subcommand_exits_two_and_names_it(self):
        r = _run(CLI_PATH, ["not-a-real-command"], REPO, env=_clean_env())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("not-a-real-command", r.stderr)


# ---------------------------------------------------------------------------
# status: THE LOAD-BEARING CONTRACT. Real store, read-only.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# next: real store, read-only (bm_project.py's cmd_next issues no write).
# ---------------------------------------------------------------------------

class TestNextIsByteIdenticalToBmProject(unittest.TestCase):

    def test_next_stdout_is_byte_identical(self):
        env = _clean_env()
        wrapped = _run(CLI_PATH, ["next", "--project-id", REAL_PROJECT_ID],
                       REPO, env=env)
        direct = _run(PROJECT_PATH, ["next", "--project-id", REAL_PROJECT_ID],
                      REPO, env=env)
        self.assertEqual(wrapped.returncode, direct.returncode)
        self.assertEqual(wrapped.stdout, direct.stdout)
        self.assertEqual(wrapped.stderr, direct.stderr)


# ---------------------------------------------------------------------------
# doctor: real installation, read-only by doctor.py's own design.
# ---------------------------------------------------------------------------

class TestDoctorIsByteIdenticalToDirectScript(unittest.TestCase):

    def test_doctor_stdout_is_byte_identical(self):
        env = _clean_env()
        wrapped = _run(CLI_PATH, ["doctor"], REPO, env=env)
        direct = _run(DOCTOR_PATH, [], REPO, env=env)
        self.assertEqual(wrapped.returncode, direct.returncode)
        self.assertEqual(wrapped.stdout, direct.stdout)

    def test_doctor_json_flag_forwards(self):
        # This test proves the FLAG FORWARDS, and nothing else. It says so
        # by comparing the wrapper against the script it wraps, which is
        # true on every machine, rather than by pinning doctor's verdict,
        # which is a fact about the machine and not about forwarding. The
        # earlier version asserted the verdict and was red on every hosted
        # runner for that reason; the verdict assertion now lives in
        # test_doctor_reports_only_expected_failures_on_a_real_install
        # below, where its precondition is stated instead of assumed.
        env = _clean_env()
        wrapped = _run(CLI_PATH, ["doctor", "--json"], REPO, env=env)
        direct = _run(DOCTOR_PATH, ["--json"], REPO, env=env)
        self.assertEqual(wrapped.returncode, direct.returncode)
        self.assertEqual(wrapped.stdout, direct.stdout)
        payload = json.loads(wrapped.stdout)
        self.assertIn("checks", payload)
        # Derived from doctor.py's own inventory, never hardcoded:
        # this line said 10 and went stale the day an eleventh check
        # shipped. The point of the assertion is that the wrapper
        # forwards EVERY check, not that there are exactly ten.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_doctor_for_count",
            DOCTOR_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(len(payload["checks"]), len(mod.CHECK_TITLES))

    def test_doctor_reports_only_expected_failures_on_a_real_install(self):
        # The install-health guard, kept word for word from where it used
        # to sit inside the forwarding test above, with the one thing it
        # always silently assumed now written down: that BrotherMode is
        # actually INSTALLED on the machine running it.
        #
        # doctor legitimately exits 1 mid-train when the manifest check
        # finds files newer than the last CHECKSUMS rebuild, which happens
        # by design between a wave's commits and its manifest-last rebuild
        # (M18). Pinning exit 0 made the gate order-dependent, measured
        # 2026-08-08 (check 9 FAIL, 31 files newer than the manifest,
        # every other check PASS). At that date the checksums check sat at
        # position 9 in CHECK_TITLES (scripts/doctor.py). store (commit
        # a83fc1c, "doctor grows to ten checks") and mode_wiring (commit
        # 997ec00, the H1-H4 fence-core close) were both inserted ahead of
        # it since, so the SAME check, "checksums" / "CHECKSUMS.sha256
        # self-check", now sits at position 11; the id below follows that
        # deliberate growth, not a new tolerance.
        #
        # On a bare checkout with no install (every GitHub Actions runner:
        # CI checks the repo out and runs the suite, it never runs
        # scripts/install.py or scripts/setup.py), checks 1 fence, 5
        # consent and 12 settings_json FAIL because there is no
        # ~/.claude/settings.json and setup has never been completed (ids
        # updated for the same CHECK_TITLES growth as above: consent and
        # settings_json themselves did not move, their position did).
        # Those three FAILs are doctor telling the exact truth about that
        # machine, so asserting them away would be asserting a falsehood.
        # There is nothing there for this guard to guard, so it skips and
        # says which check proved it.
        env = _clean_env()
        r = _run(CLI_PATH, ["doctor", "--json"], REPO, env=env)
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        by_key = {c["key"]: c for c in payload["checks"]}
        consent = by_key.get("consent", {})
        if consent.get("status") != "PASS":
            raise unittest.SkipTest(
                "BrotherMode is not installed on this machine (doctor's "
                "consent check says %r), so there is no install for this "
                "check to report on." % consent.get("status"))
        if r.returncode == 1:
            failing = [c["id"] for c in payload["checks"]
                       if c.get("status") == "FAIL"]
            self.assertEqual(failing, [11],
                             "doctor may only be red mid-train on the "
                             "manifest check; any other FAIL is real: %r"
                             % failing)


# ---------------------------------------------------------------------------
# version: the VERSION file's content.
# ---------------------------------------------------------------------------

class TestVersionReadsVersionFile(unittest.TestCase):

    def test_version_matches_the_file_on_disk(self):
        with io.open(os.path.join(REPO, "VERSION"), encoding="utf-8") as f:
            on_disk = f.read().strip()
        r = _run(CLI_PATH, ["version"], REPO, env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), on_disk)

    def test_version_json_flag(self):
        r = _run(CLI_PATH, ["version", "--json"], REPO, env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertIn("version", payload)


# ---------------------------------------------------------------------------
# Throwaway-store base class for the mutating subcommands: start, review,
# deliver, view. HOME, BROTHERMODE_VAULT, BROTHERSBE_VAULT and
# BROTHERMODE_ROOT are all pinned into a fresh tempfile.mkdtemp() tree,
# exactly the pattern tools/test_bm_view.py's ViewCase and tools/
# test_bm_store.py's throwaway-vault tests both use. Nothing in this
# class ever touches the real project store: _init_store runs against
# `self.root`, never against REPO.
# ---------------------------------------------------------------------------

class ThrowawayCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brothermode-cli-")
        self.root = os.path.join(self.tmp, "project")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.root)
        os.makedirs(self.home)
        self.vault = os.path.join(self.home, "Vault")
        self.config = os.path.join(self.tmp, "cfg", "config.json")
        self.env = _clean_env({
            "HOME": self.home,
            "BROTHERMODE_VAULT": self.vault,
            "BROTHERSBE_VAULT": self.vault,
            "BROTHERMODE_ROOT": self.root,
            "BROTHERME_CONFIG": self.config,
        })
        _init_store(self.root, env=self.env)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def consent(self):
        _write_consented_config(self.config, self.vault)

    def cli(self, *args):
        return _run(CLI_PATH, list(args), self.root, env=self.env)

    def project(self, *args):
        """bm_project.py, same throwaway root and env, for seeding
        prerequisite state (a task to review, for example) that is not
        itself one of this boundary's ten verbs."""
        return _run(PROJECT_PATH, list(args), self.root, env=self.env)



class TestStatusIsByteIdenticalToBmLead(ThrowawayCase):
    """The proof this whole boundary rests on: brothermode_cli.py status
    is not a re-description of tools/bm_lead.py's status, it IS tools/
    bm_lead.py's status, reached through one more layer of dispatch.
    Both sides run as real subprocesses with an identical environment so
    nothing outside the two binaries themselves can explain a difference.

    HERMETIC SINCE 2026-08-08, and the release court is why: this class
    used to read the author's own live store and the project id that
    happened to exist in it, so it passed in a warm worktree and FAILED
    in a fresh clone of the same commit ("no store exists"), which is
    exactly the environment CI and a contributor run. It now seeds its
    own throwaway store and project, so the load-bearing contract is
    proven everywhere rather than only here."""

    PROJECT = "byte-identity-fixture"

    def setUp(self):
        super(TestStatusIsByteIdenticalToBmLead, self).setUp()
        # bm_lead refuses before consent, so the fixture grants it the
        # shipped way, the same call every mutating class here makes.
        self.consent()
        seeded = _run(CLI_PATH, ["start", "--project-id", self.PROJECT,
                                 "--name", "Byte identity fixture",
                                 "--actor-name", "tester"],
                      self.root, env=self.env)
        self.assertEqual(seeded.returncode, 0,
                         seeded.stdout + seeded.stderr)

    def test_status_stdout_is_byte_identical(self):
        env = self.env
        wrapped = _run(CLI_PATH, ["status", "--project-id", self.PROJECT],
                       self.root, env=env)
        direct = _run(LEAD_PATH, ["status", "--project-id", self.PROJECT],
                      self.root, env=env)
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.assertEqual(wrapped.returncode, direct.returncode,
                         "exit codes differ: wrapped=%r direct=%r"
                         % (wrapped.returncode, direct.returncode))
        self.assertEqual(wrapped.stdout, direct.stdout,
                         "brothermode_cli.py status must be byte-identical "
                         "to tools/bm_lead.py status; this is the run's "
                         "load-bearing contract")
        self.assertEqual(wrapped.stderr, direct.stderr)

    def test_status_ic_flag_forwards_unchanged(self):
        """Proves argv forwards past the first flag too, not only the
        bare subcommand: --ic changes bm_lead.py's own output (an
        engineering-view block is appended), so this fails if the CLI
        ever stops passing the rest of argv straight through."""
        env = self.env
        wrapped = _run(CLI_PATH,
                       ["status", "--project-id", self.PROJECT, "--ic"],
                       self.root, env=env)
        direct = _run(LEAD_PATH,
                      ["status", "--project-id", self.PROJECT, "--ic"],
                      self.root, env=env)
        self.assertEqual(wrapped.stdout, direct.stdout)
        self.assertIn("Engineering view:", wrapped.stdout)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

class TestStartDelegatesToBmProject(ThrowawayCase):

    def test_start_creates_the_project_row(self):
        r = self.cli("start", "--project-id", "demo", "--name", "Demo",
                     "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("started project demo", r.stdout)
        # Verified against the store itself, not only the printed
        # sentence: bm_project.py's own status command reads the row
        # back, so a project that was not actually written would show
        # here even if cmd_start's text happened to lie.
        status = self.project("status", "--project-id", "demo")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn("demo", status.stdout)


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

class TestReviewDelegatesToBmProject(ThrowawayCase):

    def setUp(self):
        super(TestReviewDelegatesToBmProject, self).setUp()
        r = self.project("start", "--project-id", "demo", "--name", "Demo",
                         "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.project("task", "add", "--project-id", "demo",
                         "--task-id", "t1", "--title", "do the thing",
                         "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_review_records_evidence_and_transitions_the_task(self):
        # 'planned' -> 'ready' is the one legal move schema.py's
        # LEGAL_TRANSITIONS allows straight off task add; --to makes
        # that the target instead of the default 'verified', which a
        # fresh task cannot legally reach in one step.
        r = self.cli("review", "t1", "--project-id", "demo", "--to", "ready",
                     "--reason", "ready to start", "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(
            r.stdout,
            r"^reviewed task t1: evidence [0-9a-f]{32} recorded, "
            r"task -> ready$")

    def test_review_of_an_illegal_transition_refuses_exactly_like_direct_call(self):
        """A refusal is domain logic tools/bm_project.py already owns; this
        proves the CLI does not swallow, reword, or soften it."""
        wrapped = self.cli("review", "t1", "--project-id", "demo",
                           "--reason", "skip ahead", "--actor-name", "tester")
        self.assertEqual(wrapped.returncode, 1)
        self.assertIn("illegal transition", wrapped.stderr)


# ---------------------------------------------------------------------------
# deliver
# ---------------------------------------------------------------------------

class TestDeliverDelegatesToBmProject(ThrowawayCase):

    def setUp(self):
        super(TestDeliverDelegatesToBmProject, self).setUp()
        r = self.project("start", "--project-id", "demo", "--name", "Demo",
                         "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.project("task", "add", "--project-id", "demo",
                         "--task-id", "t1", "--title", "do the thing",
                         "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_deliver_partial_writes_the_packet(self):
        r = self.cli("deliver", "--project-id", "demo", "--partial")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("delivered demo PARTIALLY", r.stdout)
        self.assertTrue(
            os.path.isfile(os.path.join(self.root, "DELIVERY-PACKET.md")),
            "deliver did not write DELIVERY-PACKET.md through the same "
            "generated-document funnel bm_project.py's own deliver uses")

    def test_deliver_without_partial_refuses_exactly_like_direct_call(self):
        r = self.cli("deliver", "--project-id", "demo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("pass --partial", r.stderr)


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------

class TestViewDelegatesToBmView(ThrowawayCase):

    def setUp(self):
        super(TestViewDelegatesToBmView, self).setUp()
        self.consent()
        r = self.project("start", "--project-id", "demo", "--name", "Demo",
                         "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_view_writes_the_project_view_page(self):
        r = self.cli("view", "--project-id", "demo")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("The page is written", r.stdout)
        self.assertTrue(
            os.path.isfile(os.path.join(self.root, "PROJECT-VIEW.html")),
            "view did not write PROJECT-VIEW.html through bm_view.py's "
            "own render")

    def test_view_without_consent_refuses_exactly_like_direct_call(self):
        env = dict(self.env)
        env["BROTHERME_CONFIG"] = os.path.join(self.tmp, "missing.json")
        r = _run(CLI_PATH, ["view", "--project-id", "demo"], self.root,
                 env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("setup is not complete yet", r.stdout)


# ---------------------------------------------------------------------------
# recover
# ---------------------------------------------------------------------------

class TestRecoverDescribesCompactBoundaryHonestly(unittest.TestCase):
    """No BrotherMode store is needed for this one: tools/bm_autosave.py's
    recovery path works off git snapshot refs inside any git repository,
    and this test's whole point is the case where none exist, per ruling
    B1 (V3-FREEZE-2026-08-07.md, Refutation adjudication)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brothermode-cli-recover-")
        _init_repo(self.tmp)
        with io.open(os.path.join(self.tmp, "a.txt"), "w",
                     encoding="utf-8") as f:
            f.write("hello\n")
        _git(self.tmp, "add", "a.txt")
        r = _git(self.tmp, "commit", "-q", "-m", "init")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recover_states_the_compact_boundary_limit_when_nothing_is_found(self):
        r = _run(CLI_PATH, ["recover", self.tmp], REPO, env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("compact-boundary autosave snapshots only", r.stdout)
        self.assertIn("V3-FREEZE-2026-08-07.md", r.stdout)
        self.assertIn("no autosave found", r.stdout)

    def test_recover_states_the_limit_even_outside_a_git_repository(self):
        outside = tempfile.mkdtemp(prefix="brothermode-cli-not-git-")
        try:
            r = _run(CLI_PATH, ["recover", outside], REPO, env=_clean_env())
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("compact-boundary autosave snapshots only",
                          r.stdout)
            self.assertIn("not inside a git repository", r.stdout)
        finally:
            shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

class TestUpdateVersionKeyAndTagParsing(unittest.TestCase):
    """Pure unit tests, no subprocess and no network: the only genuinely
    new arithmetic this boundary adds (see brothermode_cli.py's own
    module docstring, hard rule 3)."""

    def test_version_key_orders_this_project_own_tag_history(self):
        cli = _load_cli_module()
        tags = ["v2.1.1", "v2.0.0-rc.7", "v2.0.0-rc.9", "v2.0.0-rc.8",
               "v2.1.0", "v2.0.0"]
        ordered = sorted(tags, key=cli._version_key)
        self.assertEqual(ordered,
                         ["v2.0.0-rc.7", "v2.0.0-rc.8", "v2.0.0-rc.9",
                          "v2.0.0", "v2.1.0", "v2.1.1"])

    def test_parse_ls_remote_tags_folds_peeled_annotated_refs(self):
        cli = _load_cli_module()
        text = ("aaa\trefs/tags/v1.0.0\n"
               "bbb\trefs/tags/v1.0.0^{}\n"
               "ccc\trefs/tags/v2.0.0\n"
               "ddd\trefs/heads/main\n")
        tags = cli._parse_ls_remote_tags(text)
        self.assertEqual(set(tags), {"v1.0.0", "v2.0.0"})
        self.assertEqual(tags["v1.0.0"], "bbb")


class TestUpdateAgainstALocalTagRepository(unittest.TestCase):
    """A tiny local git repository stands in for github.com, so every
    test in this class is offline and deterministic: `git ls-remote
    --tags <local path>` behaves identically to a real remote and needs
    no network access."""

    @classmethod
    def setUpClass(cls):
        cls.tagrepo = tempfile.mkdtemp(prefix="brothermode-cli-tagrepo-")
        _init_repo(cls.tagrepo)
        with io.open(os.path.join(cls.tagrepo, "x.txt"), "w",
                     encoding="utf-8") as f:
            f.write("x\n")
        _git(cls.tagrepo, "add", "x.txt")
        r = _git(cls.tagrepo, "commit", "-q", "-m", "init")
        if r.returncode != 0:
            raise AssertionError("git commit failed: %s"
                                 % (r.stdout + r.stderr))
        _git(cls.tagrepo, "tag", "v1.0.0")
        _git(cls.tagrepo, "tag", "v2.5.0")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tagrepo, ignore_errors=True)

    def test_update_against_an_unreachable_remote_refuses_to_guess(self):
        r = _run(CLI_PATH,
                 ["update", "--remote", "/definitely/does/not/exist/here"],
                 REPO, env=_clean_env())
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not run", r.stderr)
        self.assertIn("no version was guessed", r.stderr)

    def test_update_against_the_real_repository_reports_a_development_copy(self):
        """The real VERSION file in this tree carries a development
        identity (contains '.dev'), so tools/bm_project_facts.py's own
        facts() already reports release_tag=None for it; this proves
        that fact reaches the CLI's report unmodified, using the local
        tag repository as --remote so the assertion never depends on
        network access."""
        r = _run(CLI_PATH, ["update", "--remote", self.tagrepo], REPO,
                 env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("development copy", r.stdout)
        self.assertIn("Nothing to update", r.stdout)

    def _cmd_update_output(self, facts_payload, argv):
        """Run cmd_update in process with tools/bm_project_facts.py's own
        facts() replaced by a fixed payload, so the up-to-date and
        update-available branches are provable without a second fake
        repository standing in for this one's own VERSION, bm_store.py
        and test_all.py (what a real facts() call also reads)."""
        cli = _load_cli_module()

        class _FakeFacts(object):
            def facts(self, root):
                return facts_payload

        out = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = out
        try:
            with mock.patch.object(cli, "_load", return_value=_FakeFacts()):
                code = cli.cmd_update(argv)
        finally:
            sys.stdout = real_stdout
        return code, out.getvalue()

    def test_update_reports_up_to_date_when_release_tag_is_the_newest(self):
        code, text = self._cmd_update_output(
            {"version": "2.5.0", "release_tag": "v2.5.0",
            "repo_url": self.tagrepo},
            ["--remote", self.tagrepo])
        self.assertEqual(code, 0, text)
        self.assertIn("already have the newest version", text)

    def test_update_reports_an_update_is_available_with_exact_steps(self):
        code, text = self._cmd_update_output(
            {"version": "1.0.0", "release_tag": "v1.0.0",
            "repo_url": self.tagrepo},
            ["--remote", self.tagrepo])
        self.assertEqual(code, 0, text)
        self.assertIn("An update is available", text)
        self.assertIn("v2.5.0", text)
        self.assertIn("/plugin marketplace update brothermode-marketplace",
                      text)
        self.assertIn("git fetch --tags", text)
        self.assertIn("git checkout v2.5.0", text)
        # No mutating command actually ran: the steps are printed, never
        # executed. Proven structurally, not by hoping: this whole
        # function ran with the real self.tagrepo working tree, and its
        # checked-out tag is asserted unchanged.
        head = subprocess.run(["git", "-C", self.tagrepo, "describe",
                              "--tags"], capture_output=True, text=True)
        self.assertNotIn("v2.5.0-", head.stdout,
                         "a checkout must not have run: HEAD moved")


# ---------------------------------------------------------------------------
# H1: the cross-worktree fence-claim resolution defect (ONE bounded
# attempt, V3-FREEZE-2026-08-07.md Refutation adjudication ruling H1).
# ---------------------------------------------------------------------------

class TestH1CrossWorktreeFenceClaimResolution(unittest.TestCase):
    """Reproduces the exact mechanism architecture-refutation.md's H1
    names: a nested worktree carries no .brothermode/ of its own, so
    tools/bm_store.resolve_root climbs to the OUTER repository (verified
    live in this very worktree by every other test class in this file);
    tools/bm_fence_hook.canonical_target then computes the write's
    target relative to that OUTER root, which is the worktree-prefixed
    path (".claude/worktrees/<name>/<file>"), never the plain path a
    claim filed on "<file>" actually stores. A claim on the shared file
    therefore matches neither worktree's own write, and the fence
    allows a write it should deny.

    This test needs no real `git worktree add`: a plain nested directory
    under a directory that owns a .brothermode/ marker reproduces
    resolve_root's climb identically (confirmed against tools/
    bm_store.resolve_root directly before this test was written), which
    is the only mechanism this defect depends on.

    CLOSED in the finalization run, 2026-08-08: canonical_target and
    canonicalize_path both express paths through
    bm_store.strip_worktree_segments, so a write reaching a shared file
    through a linked worktree checkout compares by its
    worktree-relative spelling, the one a claim on the plain path
    stores. This test was the ruling's deliberately failing pin
    (expectedFailure); it now asserts the fence really denies."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brothermode-cli-h1-")
        # realpath up front: on macOS /var and /tmp are symlinks, and
        # every comparison inside resolve_root and canonical_target is
        # against a realpath'd root (tools/test_bm_fence_hook.py's own
        # FenceHookBase.setUp does the identical thing for the same
        # reason).
        self.root = os.path.realpath(self.tmp)
        os.makedirs(os.path.join(self.root, ".git"))
        self.worktree = os.path.join(self.root, ".claude", "worktrees",
                                     "demo")
        os.makedirs(self.worktree)
        with io.open(os.path.join(self.worktree, "pyproject.toml"), "w",
                     encoding="utf-8") as f:
            f.write("# fake shared file\n")
        with bs.Store(self.root):
            pass

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_claim_on_the_shared_file_should_deny_a_write_from_a_nested_worktree(self):
        # OWNER claims "pyproject.toml" the natural way: from the shared
        # root, the same string a founder running `bm_store.py claim
        # ... --files pyproject.toml` at the outer root would store
        # (canonicalize_path's cwd=None default resolves against root
        # itself for every Python-API caller, matching the CLI's own
        # documented rule).
        owner_label = fh.session_label(self.root, "session-owner")
        with bs.Store(self.root, create=False) as store:
            store.claim("lane-a", "persistent", objective="own pyproject",
                        files=["pyproject.toml"], session_id=owner_label)

        # OTHER writes the SAME logical file, but from inside the nested
        # worktree, exactly as Claude Code would present the payload
        # (tool_input.file_path is the real, absolute path of the file
        # being edited).
        payload = {
            "session_id": "session-other",
            "cwd": self.worktree,
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": os.path.join(self.worktree, "pyproject.toml"),
                "content": "changed by a different session",
            },
            "tool_use_id": "toolu_H1TEST",
        }
        decision, _notes = fh.decide(payload)

        # This is what a correct fence would do: deny, because
        # "pyproject.toml" is actively claimed by another session. Today
        # it does not: resolve_root(cwd=self.worktree) climbs to
        # self.root (confirmed: the worktree itself carries no
        # .brothermode/), but canonical_target then expresses the write
        # relative to self.root using the file's own absolute path,
        # yielding ".claude/worktrees/demo/pyproject.toml" -- a string
        # the claim's stored path "pyproject.toml" never matches -- so
        # decide() returns None (allow) instead.
        self.assertIsNotNone(
            decision,
            "H1 (V3-FREEZE-2026-08-07.md, Refutation adjudication ruling "
            "H1): a write to the SAME repository file from inside a "
            "nested worktree should be denied by a claim filed on that "
            "file's plain root-relative path, and today it is not, "
            "because canonical_target expresses the write worktree-"
            "prefixed while the claim is stored plain. Fix belongs to "
            "tools/bm_fence_hook.py canonical_target or tools/"
            "bm_store.py resolve_root, outside this change's fence.")


# ---------------------------------------------------------------------------
# continue: the handoff packet generator (Phase C step 1 of the 2026-08-08
# finalization plan, "The continuity protocol"). These tests were written
# and run RED before tools/bm_continue.py existed, which is the phase's own
# stated law: "Done-check: contract tests in tools/test_brothermode_cli.py
# for packet sections and dry-run; red first."
#
# WHAT THEY PIN
#   1. The ten sections the founder fixed on 2026-08-08, present and IN
#      ORDER, because a packet missing section 9 is a packet that loses a
#      founder decision.
#   2. Every section's content comes from STORE ROWS, not from prose the
#      generator invented: each content test seeds one row and then finds
#      that row's own text in the packet.
#   3. --dry-run writes the packet, prints the launch command, and launches
#      nothing.
#   4. The prompt sits IMMEDIATELY after -p in the launch argv. That is not
#      a style preference: `--add-dir` is variadic, so a prompt placed
#      after it is swallowed as another directory and the successor session
#      dies with "Input must be provided either through stdin or as a
#      prompt argument when using --print". That exact failure killed the
#      first relay launch of 2026-08-08 (relay-1.log in the handover
#      folder), so it is pinned as a test rather than remembered.
#   5. Two runs over the same rows produce byte-identical packets, the
#      same generated-view law tools/bm_project.py's render_canvas obeys.
# ---------------------------------------------------------------------------

CONTINUE_SECTIONS = (
    "## 1. North star and goals",
    "## 2. Where we stand, one paragraph",
    "## 3. Read next, in this order",
    "## 4. Next actions, ordered",
    "## 5. Evidence index",
    "## 6. Telemetry",
    "## 7. Lessons learnt",
    "## 8. Features pipeline",
    "## 9. Open founder decisions",
    "## 10. The continuity contract for the successor",
)


class ContinueCase(ThrowawayCase):
    """One throwaway project carrying at least one row behind every
    section that reads rows, so a content assertion can never pass on a
    hard-coded sentence."""

    PROJECT = "continuity-fixture"
    PACKET = "HANDOFF-PACKET.md"
    ACTOR = {"actor_type": "model", "actor_name": "tester",
             "session_id": "continue-fixture"}

    def setUp(self):
        super(ContinueCase, self).setUp()
        r = self.project(
            "start", "--project-id", self.PROJECT,
            "--name", "Continuity fixture",
            "--goal", "One clean main that IS the product",
            "--user-outcome", "A founder opens one page and sees the truth",
            "--success-criteria", "every done-check quoted green",
            "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.project("task", "add", "--project-id", self.PROJECT,
                         "--task-id", "t-next", "--title",
                         "wire the continue subcommand",
                         "--priority", "high", "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.project("task", "add", "--project-id", self.PROJECT,
                         "--task-id", "t-later", "--title",
                         "publish the progress view",
                         "--priority", "medium", "--actor-name", "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # t-next reaches 'ready' AND files one evidence row in the same
        # call, which is what puts it in section 4 and its evidence in
        # section 5.
        r = self.project("review", "t-next", "--project-id", self.PROJECT,
                         "--to", "ready", "--kind", "command",
                         "--ref", "python3 tools/test_brothermode_cli.py",
                         "--reason", "ready to start", "--actor-name",
                         "tester")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.evidence_id = r.stdout.split("evidence ", 1)[1].split(" ", 1)[0]

        with bs.Store(self.root, create=False) as store:
            # Section 9: a key decision nothing supersedes. control_offered
            # is required for a DECISION row to exist at all (record_insight
            # refusal R7), which is the store making the founder's choice
            # unskippable rather than this test being thorough.
            self.decision = store.record_insight(self.PROJECT, {
                "kind": "DECISION",
                "subject": "the launch switch",
                "claim": "the successor launch needs a settings allow rule",
                "evidence": "two classifier refusals, 2026-08-08",
                "evidence_class": "EXECUTED",
                "alternatives": [{"option": "leave a one-click chip instead",
                                  "why_not": "the founder may be asleep"}],
                "flip_condition": "the founder withdraws the allow rule",
                "confidence": "high",
                "decision_class": "GATE",
                "control_offered": 1,
            }, self.ACTOR)
            # Section 3: a recorded view carries the published Gantt url.
            store.record_view(self.PROJECT, {
                "kind": "PROJECT_VIEW",
                "rel_path": "PROJECT-VIEW.html",
                "fingerprint": "0123456789ab",
                "artifact_url": "https://claude.ai/code/artifact/fixture",
                "published_at": bs.now_iso(),
            }, self.ACTOR)
            # Section 7: one lesson, in the words that would be recognised
            # again.
            store.add_procedural(
                self.PROJECT,
                "launched the successor with the prompt after --add-dir",
                "failed",
                "--add-dir is variadic so the prompt was swallowed",
                "continue-fixture", self.ACTOR)

    def packet_path(self):
        return os.path.join(self.root, self.PACKET)

    def read_packet(self):
        with io.open(self.packet_path(), encoding="utf-8") as f:
            return f.read()

    def dry_run(self):
        r = self.cli("continue", "--project-id", self.PROJECT, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r


class TestContinuePacketSections(ContinueCase):

    def test_help_names_the_continue_verb(self):
        r = _run(CLI_PATH, ["--help"], REPO, env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("continue", r.stdout,
                      "help text does not name the continue subcommand")

    def test_dry_run_writes_the_packet_with_the_ten_sections_in_order(self):
        self.dry_run()
        self.assertTrue(os.path.isfile(self.packet_path()),
                        "continue --dry-run did not write %s" % self.PACKET)
        text = self.read_packet()
        positions = []
        for heading in CONTINUE_SECTIONS:
            self.assertIn(heading, text,
                          "the packet is missing section heading %r"
                          % heading)
            positions.append(text.index(heading))
        self.assertEqual(positions, sorted(positions),
                         "the ten sections are present but out of order; "
                         "the founder pinned this order on 2026-08-08")

    def test_section_one_carries_the_goal_and_success_checks_from_the_row(self):
        self.dry_run()
        text = self.read_packet()
        self.assertIn("One clean main that IS the product", text)
        self.assertIn("A founder opens one page and sees the truth", text)
        self.assertIn("every done-check quoted green", text)

    def test_read_next_carries_the_recorded_artifact_url(self):
        self.dry_run()
        self.assertIn("https://claude.ai/code/artifact/fixture",
                      self.read_packet())

    def test_next_actions_carry_the_ready_task(self):
        self.dry_run()
        text = self.read_packet()
        self.assertIn("t-next", text)
        self.assertIn("wire the continue subcommand", text)

    def test_evidence_index_carries_the_evidence_row(self):
        self.dry_run()
        text = self.read_packet()
        self.assertIn(self.evidence_id, text)
        self.assertIn("python3 tools/test_brothermode_cli.py", text)

    def test_lessons_learnt_carries_the_procedural_row(self):
        self.dry_run()
        self.assertIn("--add-dir is variadic so the prompt was swallowed",
                      self.read_packet())

    def test_features_pipeline_carries_the_not_yet_ready_task(self):
        self.dry_run()
        text = self.read_packet()
        self.assertIn("t-later", text)
        self.assertIn("publish the progress view", text)

    def test_open_founder_decisions_carry_the_unsuperseded_decision(self):
        self.dry_run()
        text = self.read_packet()
        self.assertIn("the successor launch needs a settings allow rule",
                      text)
        self.assertIn("leave a one-click chip instead", text)

    def test_two_runs_over_the_same_rows_are_byte_identical(self):
        self.dry_run()
        first = self.read_packet()
        self.dry_run()
        self.assertEqual(first, self.read_packet(),
                         "the packet is a generated view: two runs over "
                         "the same rows must be byte-identical, the same "
                         "law render_canvas obeys")


class TestContinueDryRunLaunchesNothing(ContinueCase):

    def test_dry_run_prints_the_command_and_says_it_launched_nothing(self):
        r = self.dry_run()
        self.assertIn("nohup claude -p", r.stdout,
                      "continue must print the exact launch command a human "
                      "or a session can run")
        self.assertIn(self.PACKET, r.stdout)
        self.assertIn("launched nothing", r.stdout)

    def test_dry_run_spawns_no_process(self):
        """Structural, not hopeful: cmd_continue is called in process with
        the module's own spawn function replaced, so a launch would be
        recorded here and nothing can reach a real `claude` binary."""
        cont = _load(os.path.join(HERE, "bm_continue.py"), "bm_continue")
        spawned = []
        out = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = out
        try:
            with mock.patch.object(cont, "spawn_successor",
                                   side_effect=lambda *a, **k: spawned.append(a)):
                code = cont.main(["continue", "--project-id", self.PROJECT,
                                  "--root", self.root, "--dry-run"])
        finally:
            sys.stdout = real_stdout
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(spawned, [],
                         "--dry-run launched a successor session")


class TestContinueRefusesWhatItCannotGenerate(ContinueCase):

    def test_unknown_project_refuses_and_writes_no_packet(self):
        r = self.cli("continue", "--project-id", "not-a-project",
                     "--dry-run")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not-a-project", r.stderr)
        self.assertFalse(os.path.isfile(self.packet_path()),
                         "a refused continue must leave no packet behind")


class TestContinueLaunchCommandShape(unittest.TestCase):
    """Pure unit tests over the command builder: no store, no subprocess."""

    def setUp(self):
        self.cont = _load(os.path.join(HERE, "bm_continue.py"), "bm_continue")

    def test_prompt_sits_immediately_after_dash_p(self):
        argv = self.cont.launch_argv("/tmp/HANDOFF-PACKET.md", "/tmp/proj")
        self.assertEqual(argv[0], "claude")
        self.assertEqual(argv[1], "-p")
        self.assertTrue(argv[2].strip(),
                        "the prompt must be a non-empty argument")
        self.assertIn("/tmp/HANDOFF-PACKET.md", argv[2])
        self.assertNotIn(argv[2], ("--add-dir",))
        add_dir = argv.index("--add-dir")
        self.assertGreater(add_dir, 2,
                           "--add-dir is variadic: anything after it is "
                           "read as a directory, so the prompt must come "
                           "first (relay-1.log, 2026-08-08)")

    def test_no_permission_mode_is_set_unless_one_is_asked_for(self):
        """The security posture of the whole verb, pinned. A shipped tool
        that hard-coded bypassPermissions would hand every stranger who
        installs BrotherMode a one-word way to detach an ungated agent,
        driven by a packet whose text other models wrote. Flagged in
        review on 2026-08-08; the default is now inherit-the-settings, and
        loosening it is typed per launch."""
        argv = self.cont.launch_argv("/tmp/HANDOFF-PACKET.md", "/tmp/proj")
        self.assertNotIn("--permission-mode", argv)
        self.assertNotIn("bypassPermissions", argv)
        asked = self.cont.launch_argv("/tmp/HANDOFF-PACKET.md", "/tmp/proj",
                                      permission_mode="bypassPermissions")
        self.assertEqual(asked[3], "--permission-mode")
        self.assertEqual(asked[4], "bypassPermissions")
        self.assertEqual(asked[2], argv[2],
                         "the prompt must stay at argv[2] whether or not a "
                         "permission mode was asked for")

    def test_printable_command_is_a_nohup_line_a_human_can_paste(self):
        argv = self.cont.launch_argv("/tmp/HANDOFF-PACKET.md", "/tmp/proj")
        line = self.cont.printable_command(argv, "/tmp/relay.log")
        self.assertTrue(line.startswith("nohup claude -p "), line)
        self.assertIn("/tmp/relay.log", line)
        self.assertTrue(line.rstrip().endswith("&"), line)

    def test_prompt_bounds_the_successor_to_one_turn(self):
        argv = self.cont.launch_argv("/tmp/HANDOFF-PACKET.md", "/tmp/proj")
        self.assertIn("one", argv[2].lower())
        self.assertIn("turn", argv[2].lower(),
                      "a headless session runs ONE turn then exits, so the "
                      "prompt must bound the mission to one turn")


# ---------------------------------------------------------------------------
# Successor liveness (Phase C step 3 of the 2026-08-08 finalization plan).
# Written and run RED before tools/bm_continue.py grew any of it, which is
# the phase's own stated law.
#
# WHY THIS EXISTS AT ALL
#   Step 1 shipped a launch that printed a process id and said, in its own
#   docstring, that a printed pid is a SPAWN and not a proof of life. That
#   honesty was correct and it was also a hole: a relay session could report
#   "successor launched", exit, and leave a program dead until the founder
#   noticed the next morning. Exactly that happened on 2026-08-08 (relay-1
#   died on a swallowed prompt and nobody knew). So the launch now WATCHES:
#   it waits a bounded time for the successor's first line of output, checks
#   the process is still there, and writes both into the store as an
#   evidence row that the next packet carries.
#
# WHAT THESE PIN
#   1. --dry-run records NOTHING. A dry run that filed liveness evidence
#      would be a lie on disk, and it is the one form of lie the store
#      cannot later distinguish from a real launch.
#   2. A launch records ONE evidence row against the project, carrying the
#      process id and the first log line verbatim.
#   3. THE THREE VERDICTS, each distinct, each recorded in the row itself:
#        SPOKE    a line arrived inside the window. Exit 0.
#        RUNNING  no line yet, but the process is still there. Exit 0.
#        GONE     no line and the process has exited. Exit 1.
#      The middle verdict is not a hedge, it is what the live canary of
#      2026-08-08 found: `claude -p` buffers its ENTIRE turn and flushes at
#      the end, so a healthy headless successor has a zero-byte log for
#      minutes. An earlier draft of this step treated silence alone as
#      death and failed a successor that was working perfectly. What
#      actually distinguishes the 2026-08-08 disaster (prompt swallowed by
#      the variadic --add-dir, session dead in the same second) is that the
#      PROCESS WAS GONE, and that is what GONE tests.
#   4. The evidence reaches the packet, so the next successor reads whether
#      the previous handover actually took.
# ---------------------------------------------------------------------------


class LivenessCase(ContinueCase):
    """A launch, with the spawn replaced by a fake that behaves like a
    successor: it writes to the log the caller opened and it has a real,
    live process id (this test process's own). Nothing here can reach a
    real `claude` binary; the live canary is run by hand and quoted in the
    commit, per the plan's own done-check."""

    FIRST_LINE = "canary: the successor is reading the packet"

    def setUp(self):
        super(LivenessCase, self).setUp()
        self.cont = _load(os.path.join(HERE, "bm_continue.py"), "bm_continue")
        # The relay's WIDTH brake counts live Claude sessions off this
        # machine's own transcript directory, and it runs inside
        # cmd_continue BEFORE the fake spawn below is ever reached. Left
        # ambient, that made every test in this class assert a fact about
        # the machine rather than about liveness recording. Measured A/B/A
        # on 2026-08-10, one commit, clean tree: FAILED (failures=6,
        # errors=5) with the machine's three sessions live against its cap
        # of 3, OK with BROTHERMODE_SESSION_CAP raised, FAILED again on the
        # default. So the whole suite went red because people were working,
        # and a gate that cannot pass while the founder's team works cannot
        # tell a real defect from a busy laptop.
        #
        # The count therefore gets its own empty directory, which is the
        # same isolation the subprocess helpers in this file already have
        # for free (_clean_env hands them a throwaway HOME, so they never
        # see the real ~/.claude/projects), and the same shape the
        # width-brake tests further down already set deliberately. Nothing
        # is weakened: the brake keeps its own dedicated coverage in
        # TestReviewFindingsOnTheBrake, which points this variable at a
        # populated fixture on purpose. This only stops the brake firing in
        # the tests where it is not the subject.
        self.sessions_root = os.path.join(self.tmp, "claude-sessions")
        os.makedirs(self.sessions_root)
        sessions = mock.patch.dict(
            os.environ,
            {"BROTHERMODE_PROJECTS_ROOT": self.sessions_root})
        sessions.start()
        self.addCleanup(sessions.stop)

    def relay_headroom(self):
        """(live sessions, cap) as the relay brake in tools/bm_continue.py
        will read them, taken through the same module and the same
        exclude-own-session rule so this cannot drift away from the code it
        predicts. A live count of None is the counter failing open, which
        the brake treats as no opinion, so it reads as headroom here too."""
        cap_mod = self.cont._session_cap()
        own = (os.environ.get("CLAUDE_SESSION_ID")
               or os.environ.get("BROTHERMODE_SESSION_ID"))
        live = cap_mod.count_live_sessions(cap_mod.projects_root(),
                                           exclude_session=own)
        return live, cap_mod.cap()

    def require_relay_headroom(self):
        """Refuse to pretend. With no headroom, cmd_continue stops at the
        brake and never spawns, so there is no launch for these tests to
        watch and their assertions would be measuring the machine. State
        that and skip, rather than fail on it or assert around it."""
        live, width_cap = self.relay_headroom()
        if live is not None and width_cap is not None and live >= width_cap:
            raise unittest.SkipTest(
                "the relay brake refuses this launch before any successor "
                "is spawned (%d live Claude sessions against a cap of %d), "
                "so there is no launch here to watch: rerun with "
                "%s=N above %d, or close a session."
                % (live, width_cap, self.cont._session_cap().CAP_ENV,
                   live))

    def dead_pid(self):
        """A process id that certainly existed and certainly does not now:
        a real child, run to completion and reaped. Inventing a number
        would risk colliding with a live process and turning this suite
        flaky on a busy machine."""
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait()
        return proc.pid

    def launch_with_fake_spawn(self, writes=None, extra_argv=(), pid=None):
        """Run cmd_continue in process with spawn_successor replaced.
        `writes` is the text the fake successor emits into its log before
        returning; None means a successor that says nothing at all. `pid`
        defaults to this test process, which is alive by definition."""
        self.require_relay_headroom()

        def fake_spawn(argv, log_path, cwd=None, env=None):
            if writes is not None:
                with io.open(log_path, "a", encoding="utf-8") as log:
                    log.write(writes)
            return os.getpid() if pid is None else pid

        out = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = out
        try:
            with mock.patch.object(self.cont, "spawn_successor",
                                   side_effect=fake_spawn):
                code = self.cont.main(
                    ["continue", "--project-id", self.PROJECT,
                     "--root", self.root, "--liveness-timeout", "1"]
                    + list(extra_argv))
        finally:
            sys.stdout = real_stdout
        return code, out.getvalue()

    def liveness_rows(self):
        with bs.Store(self.root, create=False) as store:
            rows = store.list_evidence("project", self.PROJECT, raw=True)
        return [r for r in rows
                if r.get("kind") == self.cont.LIVENESS_KIND]


class TestTheHeadroomGuardItself(LivenessCase):
    """The guard above is now the thing standing between this class and a
    false red, so it is itself a thing that can rot: a guard that skipped
    everything forever would leave this file reading green while proving
    nothing. A skip nobody has watched fire is not evidence, so BOTH of its
    branches are executed here rather than trusted."""

    def test_without_headroom_the_launch_guard_skips_and_says_why(self):
        # A cap of 0 is the honest, deterministic way to say "no relay may
        # start on this machine": tools/bm_session_cap.py clamps the
        # override at zero, and bm_continue refuses on live >= cap, so even
        # an idle machine has no headroom at 0. That is the same absent
        # precondition three live sessions against a cap of 3 produced on
        # 2026-08-10, reached without needing three real sessions.
        with mock.patch.dict(os.environ, {"BROTHERMODE_SESSION_CAP": "0"}):
            with self.assertRaises(unittest.SkipTest) as caught:
                self.launch_with_fake_spawn(writes=self.FIRST_LINE + "\n")
        reason = str(caught.exception)
        self.assertIn("BROTHERMODE_SESSION_CAP", reason,
                      "a skip has to tell its reader what to change, or it "
                      "is just a silence: %r" % reason)
        self.assertIn("cap of 0", reason,
                      "the skip must name the numbers it read, or nobody "
                      "can tell a busy machine from a broken guard: %r"
                      % reason)
        self.assertEqual(
            self.liveness_rows(), [],
            "the guard skipped and a liveness row was filed anyway, so "
            "something ran past the stop")

    def test_with_headroom_the_guard_stands_aside_and_the_launch_runs(self):
        # The fixture's whole contribution, asserted rather than assumed:
        # the live count this class reads is 0 no matter how many sessions
        # the founder's team has open, because setUp gave the counter its
        # own empty directory. This is the assertion that would have caught
        # the 2026-08-10 defect.
        live, _ = self.relay_headroom()
        self.assertEqual(live, 0,
                         "this class counts sessions off its own empty "
                         "directory, so the count must be 0 whatever the "
                         "machine is doing, and it read %r" % (live,))
        # The cap is pinned here rather than inherited so this branch stays
        # the OPPOSITE of the one above under every environment, including
        # a run that saturates the cap on purpose to watch the skip fire.
        with mock.patch.dict(os.environ, {"BROTHERMODE_SESSION_CAP": "1"}):
            code, stdout = self.launch_with_fake_spawn(
                writes=self.FIRST_LINE + "\n")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(len(self.liveness_rows()), 1,
                         "headroom was present and the launch still went "
                         "unwatched, so the guard is skipping work it "
                         "should have let through")


class TestDryRunRecordsNoLiveness(LivenessCase):

    def test_dry_run_files_no_liveness_evidence(self):
        """A dry run launches nothing, so it must claim nothing. An
        evidence row written here would be indistinguishable, forever,
        from one written by a real handover."""
        self.dry_run()
        self.assertEqual(
            self.liveness_rows(), [],
            "--dry-run filed successor-liveness evidence for a successor "
            "it never started")


class TestLaunchRecordsLiveness(LivenessCase):

    def test_one_row_carries_the_pid_and_the_first_log_line(self):
        code, stdout = self.launch_with_fake_spawn(
            writes=self.FIRST_LINE + "\nsecond line, not the first\n")
        self.assertEqual(code, 0, stdout)
        rows = self.liveness_rows()
        self.assertEqual(len(rows), 1,
                         "a launch must file exactly one liveness row, got "
                         "%d" % len(rows))
        note = rows[0].get("note") or ""
        self.assertIn(str(os.getpid()), note,
                      "the liveness note does not carry the successor's "
                      "process id: %r" % note)
        self.assertIn(self.FIRST_LINE, note,
                      "the liveness note does not carry the successor's "
                      "first log line: %r" % note)
        self.assertNotIn("second line, not the first", note,
                         "the FIRST log line is the evidence; the whole log "
                         "is not, because a log can carry a credential a "
                         "later line printed")

    def test_the_ref_points_at_the_log_a_human_can_open(self):
        code, stdout = self.launch_with_fake_spawn(writes=self.FIRST_LINE + "\n")
        self.assertEqual(code, 0, stdout)
        self.assertIn("successor-%s.log" % self.PROJECT,
                      self.liveness_rows()[0].get("ref") or "")

    def test_stdout_reports_the_observed_line_not_just_the_pid(self):
        code, stdout = self.launch_with_fake_spawn(writes=self.FIRST_LINE + "\n")
        self.assertEqual(code, 0, stdout)
        self.assertIn(self.FIRST_LINE, stdout,
                      "the founder reading this session's output must see "
                      "what the successor actually said, not only a number")

    def test_json_output_carries_the_liveness_verdict(self):
        code, stdout = self.launch_with_fake_spawn(
            writes=self.FIRST_LINE + "\n", extra_argv=("--json",))
        self.assertEqual(code, 0, stdout)
        payload = json.loads(stdout)
        self.assertTrue(payload.get("alive"),
                        "a successor that spoke and is still running must be "
                        "reported alive: %r" % payload)
        self.assertEqual(payload.get("first_log_line"), self.FIRST_LINE)
        self.assertTrue(payload.get("liveness_evidence_id"),
                        "the evidence id belongs in the machine-readable "
                        "output so a caller can cite the row: %r" % payload)


class TestSilentButRunningIsNotAFailure(LivenessCase):
    """Found by the live canary of 2026-08-08, not by reasoning: `claude
    -p` buffers its whole turn and flushes at the end, so a perfectly
    healthy successor sits on a zero-byte log for minutes. Process 33718
    was alive at 43 seconds with nothing written. Failing that successor
    would make every real handover report a disaster."""

    def test_a_running_successor_that_has_not_spoken_yet_passes(self):
        code, stdout = self.launch_with_fake_spawn(writes=None)
        self.assertEqual(code, 0, stdout)
        note = (self.liveness_rows()[0].get("note") or "").lower()
        self.assertIn("still running", note, note)
        self.assertIn("no output yet", note, note)

    def test_the_row_does_not_claim_the_successor_understood_anything(self):
        """The honest limit, pinned so a later edit cannot quietly upgrade
        it: this evidence proves a process exists, and nothing more."""
        code, stdout = self.launch_with_fake_spawn(writes=None)
        self.assertEqual(code, 0, stdout)
        note = (self.liveness_rows()[0].get("note") or "").lower()
        for overclaim in ("understood", "succeeded", "handover complete"):
            self.assertNotIn(overclaim, note, note)


class TestDeadSuccessorIsAFinding(LivenessCase):

    def test_a_successor_that_has_already_exited_fails(self):
        """The failure this whole step exists for. relay-1 on 2026-08-08
        spawned, died in the same second on a prompt the variadic
        --add-dir had swallowed, and was reported as launched."""
        code, stdout = self.launch_with_fake_spawn(writes=None,
                                                   pid=self.dead_pid())
        self.assertEqual(code, 1,
                         "a successor that spoke nothing and is already "
                         "gone must not be reported as a handover")
        rows = self.liveness_rows()
        self.assertEqual(len(rows), 1,
                         "a dead successor is still a launch that happened: "
                         "its evidence row is the record that it was watched "
                         "and found dead")
        note = (rows[0].get("note") or "").lower()
        self.assertIn("exited", note, note)
        self.assertIn("exited", stdout.lower())

    def test_a_successor_that_spoke_then_exited_still_passes(self):
        """A one-turn headless session is SUPPOSED to end. Output arrived,
        so the handover took; the process being gone afterwards is the
        normal shape of success, not a failure."""
        code, stdout = self.launch_with_fake_spawn(
            writes=self.FIRST_LINE + "\n", pid=self.dead_pid())
        self.assertEqual(code, 0, stdout)
        self.assertIn(self.FIRST_LINE,
                      self.liveness_rows()[0].get("note") or "")


class TestTheSecondHandoverDoesNotQuoteTheFirst(LivenessCase):
    """The log is opened in APPEND mode and named per project, so every
    relay in a program writes to the SAME file. Reading it from byte zero
    means hop two reports hop one's opening line as its own successor's
    proof: stale evidence, filed as fresh, on the hop nobody is watching.
    Found while building the live canary on 2026-08-08."""

    def test_only_output_written_after_this_launch_counts(self):
        log = os.path.join(self.root, "successor-%s.log" % self.PROJECT)
        with io.open(log, "w", encoding="utf-8") as f:
            f.write("a previous successor said this hours ago\n")
        code, stdout = self.launch_with_fake_spawn(
            writes=self.FIRST_LINE + "\n")
        self.assertEqual(code, 0, stdout)
        note = self.liveness_rows()[0].get("note") or ""
        self.assertIn(self.FIRST_LINE, note)
        self.assertNotIn("a previous successor said this hours ago", note,
                         "the launch quoted the PREVIOUS successor's line as "
                         "this one's proof of life")

    def test_a_silent_relaunch_over_an_old_log_is_not_read_as_speech(self):
        log = os.path.join(self.root, "successor-%s.log" % self.PROJECT)
        with io.open(log, "w", encoding="utf-8") as f:
            f.write("a previous successor said this hours ago\n")
        code, stdout = self.launch_with_fake_spawn(writes=None,
                                                   pid=self.dead_pid())
        self.assertEqual(code, 1,
                         "a dead, silent successor was passed off as alive "
                         "on the strength of an old log line")
        self.assertIn("GONE", self.liveness_rows()[0].get("note") or "")


class TestLivenessReachesTheNextPacket(LivenessCase):

    def test_the_evidence_index_carries_project_evidence_too(self):
        """Section 5 used to walk tasks only, so a project-subject row was
        invisible to the successor reading the packet. A handover whose own
        proof cannot be read by the next session proves nothing."""
        code, stdout = self.launch_with_fake_spawn(writes=self.FIRST_LINE + "\n")
        self.assertEqual(code, 0, stdout)
        self.dry_run()
        text = self.read_packet()
        self.assertIn(self.cont.LIVENESS_KIND, text,
                      "the regenerated packet does not carry the liveness "
                      "evidence the previous launch filed")
        self.assertIn(self.FIRST_LINE, text)


class TestFirstLogLineIsBounded(unittest.TestCase):
    """Pure unit tests over the watcher: no store, no subprocess."""

    def setUp(self):
        self.cont = _load(os.path.join(HERE, "bm_continue.py"), "bm_continue")
        self.tmp = tempfile.mkdtemp(prefix="bm-liveness-")
        self.log = os.path.join(self.tmp, "successor.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_the_first_non_empty_line_stripped(self):
        with io.open(self.log, "w", encoding="utf-8") as f:
            f.write("\n\n   hello from the successor   \nand more\n")
        self.assertEqual(
            self.cont.first_log_line(self.log, timeout=1.0),
            "hello from the successor")

    def test_a_missing_log_times_out_rather_than_raising(self):
        """A log that never appears is the ordinary shape of a successor
        that died before writing anything. It is a verdict, not a crash."""
        self.assertEqual(
            self.cont.first_log_line(self.log, timeout=0.3, poll=0.05), "")

    def test_the_wait_is_bounded_by_the_timeout_it_was_given(self):
        """FIXED 2026-08-10. This used to read `assertLess(time.time() -
        started, 5.0)`, which is an assertion about the MACHINE rather than
        about the code: it fails on a busy host while first_log_line is
        unchanged, and it passes on a fast host even if the function ignored
        its timeout entirely and simply happened to be quick.

        The shape asserted here instead is a RATIO between two timeouts,
        using the MINIMUM of several samples per timeout because noise only
        ever ADDS latency, so the minimum is the least contaminated sample
        available. That makes this a claim about the code: elapsed time
        TRACKS the timeout argument.

        It is also strictly stronger than the assertion it replaces. A
        function that ignored its timeout and waited a constant half second
        would have passed the old ceiling and fails this ratio, and a
        genuinely unbounded wait never returns at all, which the suite's own
        stall killer catches rather than a five second number."""
        def elapsed_for(timeout, samples=3):
            best = None
            for _ in range(samples):
                started = time.time()
                self.cont.first_log_line(self.log, timeout=timeout, poll=0.01)
                taken = time.time() - started
                if best is None or taken < best:
                    best = taken
            return best

        small = elapsed_for(0.10)
        large = elapsed_for(0.40)
        self.assertGreater(
            large / small, 2.0,
            "first_log_line must HONOUR its timeout: a four-fold longer "
            "timeout produced %.3fs against %.3fs, a ratio of %.2f, so the "
            "wait is not tracking the timeout it was given"
            % (large, small, large / small))

    def test_a_partial_line_without_a_newline_is_not_read_as_complete(self):
        """Half a line on disk is a write in flight, not output. Reading it
        would record a truncated first line as the successor's own words."""
        with io.open(self.log, "w", encoding="utf-8") as f:
            f.write("half a li")
        self.assertEqual(
            self.cont.first_log_line(self.log, timeout=0.3, poll=0.05), "")

    def test_a_long_line_is_truncated_so_the_store_row_stays_a_row(self):
        with io.open(self.log, "w", encoding="utf-8") as f:
            f.write("x" * 5000 + "\n")
        line = self.cont.first_log_line(self.log, timeout=1.0)
        self.assertLessEqual(len(line), self.cont.FIRST_LINE_MAX)
        self.assertTrue(line.startswith("x"))


class TestProcessAliveIsHonest(unittest.TestCase):

    def setUp(self):
        self.cont = _load(os.path.join(HERE, "bm_continue.py"), "bm_continue")

    def test_this_process_is_alive(self):
        self.assertTrue(self.cont.process_alive(os.getpid()))

    def test_a_pid_that_cannot_exist_is_not_alive(self):
        self.assertFalse(self.cont.process_alive(-1))


# ---------------------------------------------------------------------------
# The relay brake (added 2026-08-09, RED before tools/bm_continue.py grew
# any of it).
#
# WHY THIS EXISTS
#   Phase C shipped a relay whose continuity contract told every successor,
#   unconditionally, to launch its own successor before closing (CONTRACT_
#   LINES item 4). That is an instruction in prose, and an instruction in
#   prose is not a control. On 2026-08-08 the chain ran while the founder's
#   screen was off: sessions fanned out to fifteen concurrent runs and spent
#   roughly 8.4 million output tokens and 2.5 billion cache-read tokens
#   between 00:00Z and 10:00Z, and three of them died leaving uncommitted
#   work behind. Nothing in the launch path could have stopped it, because
#   nothing in the launch path ever DECIDED anything: cmd_continue read the
#   spend totals only to print them into section 6 of the packet.
#
#   So the brake is mechanical and it sits in front of the spawn. Three
#   independent stops, each sufficient on its own:
#     GENERATION  the chain carries its own depth and refuses past the cap.
#     DEADLINE    the chain carries a wall-clock end and refuses past it.
#     SPEND       a hard-stop verdict from the store refuses the spawn.
#   A refusal is a SUCCESS, not an error: the packet is still written, the
#   command is still printed, and a human can still run it by hand. What
#   the refusal removes is the machine's own ability to continue itself.
# ---------------------------------------------------------------------------


class TestRelayBrakeDecision(unittest.TestCase):
    """The decision function alone, with no store and no spawn, because a
    brake whose logic can only be exercised through a full launch is a
    brake nobody can prove."""

    def setUp(self):
        self.cont = _load(os.path.join(HERE, "bm_continue.py"), "bm_continue")

    def test_a_fresh_chain_inside_every_limit_is_allowed(self):
        self.assertIsNone(self.cont.relay_refusal(
            generation=0, maximum=8, deadline=None,
            now=datetime.datetime(2026, 8, 9, 12, 0, 0),
            spend={"verdict": "ok"}))

    def test_the_generation_cap_refuses_and_names_itself(self):
        reason = self.cont.relay_refusal(
            generation=8, maximum=8, deadline=None,
            now=datetime.datetime(2026, 8, 9, 12, 0, 0),
            spend={"verdict": "ok"})
        self.assertIsNotNone(reason, "generation 8 of a cap of 8 must refuse")
        self.assertIn("8", reason)

    def test_one_past_the_cap_still_refuses(self):
        """A chain that somehow overshot must not read as allowed again."""
        self.assertIsNotNone(self.cont.relay_refusal(
            generation=99, maximum=8, deadline=None,
            now=datetime.datetime(2026, 8, 9, 12, 0, 0),
            spend={"verdict": "ok"}))

    def test_a_passed_deadline_refuses(self):
        self.assertIsNotNone(self.cont.relay_refusal(
            generation=0, maximum=8,
            deadline=datetime.datetime(2026, 8, 9, 6, 0, 0),
            now=datetime.datetime(2026, 8, 9, 12, 0, 0),
            spend={"verdict": "ok"}))

    def test_a_deadline_still_ahead_allows(self):
        self.assertIsNone(self.cont.relay_refusal(
            generation=0, maximum=8,
            deadline=datetime.datetime(2026, 8, 9, 23, 0, 0),
            now=datetime.datetime(2026, 8, 9, 12, 0, 0),
            spend={"verdict": "ok"}))

    def test_a_hard_stop_spend_verdict_refuses(self):
        reason = self.cont.relay_refusal(
            generation=0, maximum=8, deadline=None,
            now=datetime.datetime(2026, 8, 9, 12, 0, 0),
            spend={"verdict": "hard-stop", "token_pct": 118.0})
        self.assertIsNotNone(reason, "a blown ceiling must stop the relay")
        self.assertIn("ceiling", reason.lower())

    def test_no_data_spend_does_not_refuse_on_its_own(self):
        """NO-DATA is never a pass and never a block, per the project's own
        law. A store with no contract must not silently kill a relay the
        founder started; the generation cap is what bounds that case, and
        it is why the cap has no off switch."""
        self.assertIsNone(self.cont.relay_refusal(
            generation=0, maximum=8, deadline=None,
            now=datetime.datetime(2026, 8, 9, 12, 0, 0),
            spend={"verdict": "no-data"}))


class TestRelayChainIsCarriedToTheSuccessor(unittest.TestCase):
    """The child must inherit the chain, or every hop restarts at zero and
    the cap counts to one forever."""

    def setUp(self):
        self.cont = _load(os.path.join(HERE, "bm_continue.py"), "bm_continue")

    def test_the_generation_increments_by_one(self):
        child = self.cont.successor_env(
            {"PATH": "/usr/bin"}, generation=3, maximum=8, deadline=None)
        self.assertEqual(child[self.cont.RELAY_GEN_ENV], "4")

    def test_the_cap_and_the_deadline_travel_with_it(self):
        child = self.cont.successor_env(
            {"PATH": "/usr/bin"}, generation=0, maximum=5,
            deadline=datetime.datetime(2026, 8, 10, 7, 30, 0))
        self.assertEqual(child[self.cont.RELAY_MAX_ENV], "5")
        self.assertTrue(child[self.cont.RELAY_DEADLINE_ENV]
                        .startswith("2026-08-10T07:30"))

    def test_the_parent_environment_survives(self):
        child = self.cont.successor_env(
            {"PATH": "/usr/bin", "HOME": "/home/x"}, generation=0,
            maximum=8, deadline=None)
        self.assertEqual(child["HOME"], "/home/x")

    def test_a_missing_generation_reads_as_zero(self):
        self.assertEqual(self.cont.relay_generation({}), 0)

    def test_a_junk_generation_reads_as_zero_rather_than_crashing(self):
        """A corrupt counter must not be the thing that takes the relay
        down, and zero is the safe floor because the cap still bounds the
        chain from there."""
        self.assertEqual(
            self.cont.relay_generation({self.cont.RELAY_GEN_ENV: "many"}), 0)


class TestTheBrakeStopsARealLaunch(LivenessCase):
    """The test the whole change exists for: at the cap, cmd_continue must
    start NOTHING, while still leaving the packet and the command behind so
    a human loses nothing."""

    def test_at_the_cap_no_successor_is_spawned(self):
        spawned = []

        def fake_spawn(*args, **kwargs):
            spawned.append(args)
            return os.getpid()

        out = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = out
        try:
            with mock.patch.dict(
                    os.environ, {self.cont.RELAY_GEN_ENV: "8",
                                 self.cont.RELAY_MAX_ENV: "8"}):
                with mock.patch.object(self.cont, "spawn_successor",
                                       side_effect=fake_spawn):
                    code = self.cont.main(
                        ["continue", "--project-id", self.PROJECT,
                         "--root", self.root, "--liveness-timeout", "1"])
        finally:
            sys.stdout = real_stdout
        text = out.getvalue()
        self.assertEqual(
            spawned, [],
            "the relay spawned a successor at its own generation cap")
        self.assertEqual(code, 0,
                         "a refusal to continue is a correct stop, not a "
                         "failure: %s" % text)
        self.assertIn("relay", text.lower())

    def test_at_the_cap_the_packet_is_still_written(self):
        out = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = out
        try:
            with mock.patch.dict(os.environ,
                                 {self.cont.RELAY_GEN_ENV: "8",
                                  self.cont.RELAY_MAX_ENV: "8"}):
                with mock.patch.object(
                        self.cont, "spawn_successor",
                        side_effect=AssertionError("must not spawn")):
                    self.cont.main(["continue", "--project-id", self.PROJECT,
                                    "--root", self.root])
        finally:
            sys.stdout = real_stdout
        packets = [n for n in os.listdir(self.root) if "HANDOFF" in n.upper()]
        self.assertTrue(packets,
                        "a stopped relay must still leave its packet, or "
                        "the stop destroys the handover it exists to make")

    def test_below_the_cap_a_successor_is_still_spawned(self):
        """The brake must not become the new failure: a normal relay hop
        keeps working."""
        with mock.patch.dict(os.environ,
                             {self.cont.RELAY_GEN_ENV: "0",
                              self.cont.RELAY_MAX_ENV: "8"}):
            code, stdout = self.launch_with_fake_spawn(writes="canary\n")
        self.assertEqual(code, 0, stdout)




class TestReviewFindingsOnTheBrake(LivenessCase):
    """The 2026-08-09 adversarial review of PR 36, continue-side findings,
    each red before the fix."""

    def test_finding1_width_is_bounded_not_only_depth(self):
        """One session calling continue k times fanned out k successors:
        depth 8 bounded chain LENGTH, nothing bounded WIDTH. The launch must
        consult the live session count and refuse at the cap."""
        import tempfile as _tf
        with _tf.TemporaryDirectory() as fake:
            for i in range(6):
                d = os.path.join(fake, "proj-%d" % i)
                os.makedirs(d)
                with io.open(os.path.join(d, "s%d.jsonl" % i), "w",
                             encoding="utf-8") as fh:
                    fh.write("{}\n")
            spawned = []
            out = io.StringIO()
            real_stdout = sys.stdout
            sys.stdout = out
            try:
                with mock.patch.dict(os.environ, {
                        "BROTHERMODE_PROJECTS_ROOT": fake,
                        "BROTHERMODE_SESSION_CAP": "4"}):
                    with mock.patch.object(
                            self.cont, "spawn_successor",
                            side_effect=lambda *a, **k: spawned.append(1) or os.getpid()):
                        code = self.cont.main(
                            ["continue", "--project-id", self.PROJECT,
                             "--root", self.root, "--liveness-timeout", "1"])
            finally:
                sys.stdout = real_stdout
            self.assertEqual(spawned, [],
                             "six live sessions against cap 4 and the relay "
                             "still spawned: width is unbounded")
            self.assertEqual(code, 0, out.getvalue())

    def test_reverify_the_relay_width_check_excludes_its_own_session(self):
        """The re-verification noted the relay counting its own transcript:
        effective headroom was cap minus one, and cap 1 deadlocked the
        sanctioned path even alone. With the session id in the environment,
        a lone session at cap 1 may spawn its one successor."""
        import tempfile as _tf
        with _tf.TemporaryDirectory() as fake:
            d = os.path.join(fake, "proj-own")
            os.makedirs(d)
            with io.open(os.path.join(d, "me.jsonl"), "w",
                         encoding="utf-8") as fh:
                fh.write("{}\n")
            code, stdout = None, None
            out = io.StringIO()
            real_stdout = sys.stdout
            sys.stdout = out
            try:
                with mock.patch.dict(os.environ, {
                        "BROTHERMODE_PROJECTS_ROOT": fake,
                        "BROTHERMODE_SESSION_CAP": "1",
                        "BROTHERMODE_SESSION_ID": "me"}):
                    with mock.patch.object(
                            self.cont, "spawn_successor",
                            side_effect=lambda *a, **k: os.getpid()):
                        code = self.cont.main(
                            ["continue", "--project-id", self.PROJECT,
                             "--root", self.root,
                             "--liveness-timeout", "1"])
            finally:
                sys.stdout = real_stdout
            self.assertEqual(code, 0, out.getvalue())
            self.assertNotIn("adds none", out.getvalue(),
                             "a lone session at cap 1 refused its own "
                             "successor: the launcher counted itself")

    def test_finding3_an_explicit_unparseable_deadline_refuses_the_launch(self):
        """--deadline 2026-08-09T18:00:00+09:00 silently parsed to None and
        the chain ran unbounded in time. Founder input that cannot be read
        fails CLOSED: no spawn, a plain error, exit 2."""
        spawned = []
        out = io.StringIO()
        err = io.StringIO()
        real_stdout, real_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            with mock.patch.object(
                    self.cont, "spawn_successor",
                    side_effect=lambda *a, **k: spawned.append(1) or os.getpid()):
                code = self.cont.main(
                    ["continue", "--project-id", self.PROJECT,
                     "--root", self.root, "--deadline", "not-a-time"])
        finally:
            sys.stdout, sys.stderr = real_stdout, real_stderr
        self.assertEqual(spawned, [], "an unreadable deadline still spawned")
        self.assertEqual(code, 2, "founder input that cannot be read is a "
                         "usage error, not a silent shrug")
        self.assertIn("deadline", err.getvalue().lower())

    def test_finding3_an_offset_deadline_parses_rather_than_vanishing(self):
        got = self.cont.relay_deadline(override="2026-08-09T18:00:00+09:00")
        self.assertIsNotNone(got, "a timezone offset made the deadline vanish")

    def test_finding6_the_printed_command_carries_the_chain_depth(self):
        """The printed line is for a human hand, and a hand-run successor
        restarted at generation zero. The line must carry the relay
        environment so even a copy-paste inherits the brake."""
        argv = self.cont.launch_argv("/tmp/HANDOFF-PACKET.md", ".")
        line = self.cont.printable_command(
            argv, "/tmp/relay.log",
            env_prefix={"BROTHERMODE_RELAY_GEN": "3",
                        "BROTHERMODE_RELAY_MAX": "8"})
        self.assertIn("BROTHERMODE_RELAY_GEN=3", line)
        self.assertIn("BROTHERMODE_RELAY_MAX=8", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
