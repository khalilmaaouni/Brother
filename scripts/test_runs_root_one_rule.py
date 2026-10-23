"""ONE rule decides where a run's records live, for the engine form and the launcher form alike.

MEASURED 2026-10-06 on e71e062c6, in a copy holding only bundle/ (which is all an install carries). The launcher,
`brother-run --continue`, kept records under <HOME>/.claude/brother-run. The form the shipped skill gives,
`python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" --continue --cwd <repo>`, created <install>/docs/plan/runs
INSIDE the plugin folder. The host replaces that folder on update, so runs started the documented way were deleted
by the next update, and "continue" through the door could not see them: two programs each computed a default.

The rule lives in ONE function (brother_state.state_root, which brother_run.default_runs_root returns) and the
launcher adds nothing to it:
  a development checkout (the source, <repository>/scripts/brother_run.py) keeps its runs in its own repository;
  an installed plugin (the shipped mirror under <plugin root>/runtime) keeps them per user, never in its folder;
  a layout that cannot be told apart goes per user too: unknown must never write into a folder an update deletes;
  an explicit --runs-root always wins.

Every case runs the ENTRY POINT as a child process, in a layout built here, with an environment built here (a
throwaway HOME, no BROTHER_ name of the caller's, no host session marker). The only mode driven is `--continue` with
nothing to continue: discovery, path resolution and one NO-DATA line. No worker and no model is ever reached: every
earlier run a case plants is recorded against NO target, so it is never resumed, under any mutation of the rule.

One condition per case. Run from the repository root: python3 -B scripts/test_runs_root_one_rule.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "bundle")
RUNTIME = os.path.join(BUNDLE, "runtime")
# under the caller's HOME, never the system temp folder: a run's records are not temp files, and the estate keeps
# its scratch under one root (under a landing suite, HOME is already a throwaway)
SCRATCH: str = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
NAMED_ROOT = re.compile(r"--runs-root (\S+)")
NOT_THE_ENGINE = shutil.ignore_patterns("RUNTIME-MANIFEST.json", "brother-run", "__pycache__")
RUNS = os.path.join("docs", "plan", "runs")


# THE BASELINE (2026-10-20). The files that already spelled the root when this guard landed (0b5f810b6), by path
# under the repository. The guard used to compare file modification times ("written after the guard file"), but a
# git checkout stamps every file with the moment it was written to disk, in no fixed order, so a fresh worktree or the
# closer's boxed copy read a baseline file as a new offence at random: I11 could never close and every loop build of
# I11.a graded red on this one case. Membership is a fact of the tree; a time is not. A file leaves the list by
# asking the ONE rule for the root (the work this unit closes at the source); nothing ever joins it.
KNOWN_SPELLERS = frozenset((
    "scripts/loop/test_brief_untrusted_notes.py",
    "scripts/loop/test_brother_login.py",
    "scripts/loop/test_deadline_drain.py",
    "scripts/loop/test_diag_frozen_root.py",
    "scripts/loop/test_diag_notes.py",
    "scripts/loop/test_grade_build_exact_apply.py",
    "scripts/loop/test_login_hold.py",
    "scripts/loop/test_loop_gap_wake.py",
    "scripts/loop/test_loop_plan_selection.py",
    "scripts/loop/test_native_worker.py",
    "scripts/loop/test_probes_off.py",
    "scripts/loop/test_redeploy_check.py",
    "scripts/loop/test_run_summary.py",
    "scripts/loop/test_run_summary_calls.py",
    "scripts/loop/test_run_summary_state.py",
    "scripts/loop/test_sandbox_first.py",
    "scripts/loop/test_stop_drain.py",
    "scripts/loop/test_wip_status.py",
    "scripts/test_annotations_store.py",
    "scripts/test_brother_antigravity_hook.py",
    "scripts/test_deploy_canary.py",
    "scripts/test_diag_round_candidate.py",
    "scripts/test_export_scratch_root.py",
    "scripts/test_hold_observed.py",
    "scripts/test_host_live_collect.py",
    "scripts/test_host_live_proof.py",
    "scripts/test_lander_box_tripwire.py",
    "scripts/test_loop_pass_code_root.py",
    "scripts/test_loop_until_ends.py",
    "scripts/test_proc_table_nodata.py",
    "scripts/test_proof_pair_rehearsal.py",
    "scripts/test_run_folder_identity.py",
    "scripts/test_runner_straggler_settles.py",
    "scripts/test_status_write_lock.py",
    "scripts/test_stop_run_identity.py",
    "scripts/test_unit_ledger_model_clock.py",
))


def _scratch_root_spellings() -> List[str]:
    """The three regex spellings of the scratch root no test file but this one may keep for itself.

    The third pattern uses .*? rather than [^)]* so it can cross the ) that os.path.expanduser("~") closes:
    that is what lets it match the joined spelling os.path.join(os.path.expanduser("~"), ".claude",
    "brother-scratch") the base tree keeps. A caller that asks the ONE rule for the root has no such literal.
    """
    return [
        r"~/.claude/brother-scratch",
        r"\$HOME/\.claude/brother-scratch",
        r"os\.path\.join\(.*?\.claude.*?brother-scratch",
    ]


def _not_the_shipped_runs(where, names):
    """What an install fixture leaves out of the shipped mirror: the caches, and the repository's own runs.

    The mirror is built from this repository, so it can carry the estate's own docs/plan/runs (a copy of it or a
    link to it). An install fixture must hold the shipped code alone: every case here plants exactly the earlier
    runs it names, and a mirrored copy of the estate's runs turns one planted run into a second notice, measured
    2026-10-19 as six red cases in this file on the base tree.
    """
    ignored = set(shutil.ignore_patterns("__pycache__")(where, names))
    shipped = os.path.join(BUNDLE, RUNS)
    for name in names:
        path = os.path.join(where, name)
        if path == shipped or shipped.startswith(path + os.sep):
            ignored.add(name)
        elif name == "runs" and os.path.basename(where) == "plan":
            ignored.add(name)
    return sorted(ignored)


class Base(unittest.TestCase):
    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        # the real path: git names a toplevel by its real path, and the cases compare what the engine prints
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="runs-root-rule-", dir=SCRATCH))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home, self.target = self.folder("home"), self.folder("target")
        self.per_user = os.path.join(self.home, ".claude", "brother-run")
        self.env = {"HOME": self.home, "PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8",
                    "TMPDIR": self.folder("tmp"), "PYTHONDONTWRITEBYTECODE": "1",
                    # every fixture sits under the caller's HOME, which on a landing host is a throwaway folder
                    # inside the checkout being graded: without this ceiling git walks up out of the fixture and
                    # the engine honestly reports the CHECKOUT's earlier runs as a second notice (six red cases)
                    "GIT_CEILING_DIRECTORIES": self.d}
        self.git(self.target, "init", "-q")
        self.git(self.target, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
                 "--allow-empty", "-m", "start")

    def git(self, where, *args):
        try:
            subprocess.run(["git"] + list(args), cwd=where, env=self.env, check=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            self.fail("git %s failed in %s (%s: %s)" % (args[0], where, type(exc).__name__, exc))

    def folder(self, name):
        path = os.path.join(self.d, name)
        os.makedirs(path)
        return path

    def install(self, root=None):
        """An install only copy: bundle/ alone, the plugin root of an installed plugin."""
        root = root or os.path.join(self.d, "install")
        shutil.copytree(BUNDLE, root, symlinks=True, ignore=_not_the_shipped_runs)
        return root

    def checkout(self):
        """A development checkout: the engine and everything it imports under <repository>/scripts."""
        repo = os.path.join(self.d, "repository")
        shutil.copytree(RUNTIME, os.path.join(repo, "scripts"), symlinks=True, ignore=NOT_THE_ENGINE)
        return repo

    def earlier_run(self, root, name="20260901T000000-an-earlier-run"):
        """A run the engine recognises, a folder holding one Work document, kept under root. It records NO target, so
        no case ever resumes it. Returns the runs folder it sits in."""
        os.makedirs(os.path.join(root, RUNS, name))
        with open(os.path.join(root, RUNS, name, "work.json"), "w", encoding="utf-8") as fh:
            fh.write('{"outcome": "an earlier outcome"}\n')
        return os.path.join(root, RUNS)

    def run_it(self, program, *extra, **named):
        """(exit code, combined output, stdout) of `<program> --continue --cwd <target>`."""
        try:
            r = subprocess.run([sys.executable, "-B", program, "--continue", "--cwd", self.target] + list(extra),
                               cwd=self.target, env=dict(self.env, **named), capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            self.fail("%s could not run (%s: %s)" % (program, type(exc).__name__, exc))
        return r.returncode, (r.stdout or "") + (r.stderr or ""), r.stdout or ""

    def discover(self, program, *extra, **named):
        """(root named in the printed next command or None, combined output). Exit 0 and the NO-DATA sentence are
        the premise of every case that uses this: discovery ran and nothing was resumed."""
        code, said, out = self.run_it(program, *extra, **named)
        self.assertEqual(code, 0, said[-800:])
        self.assertIn("no unfinished run found", said, said[-800:])
        # the NEXT COMMAND line (the one that carries --cwd), never a --runs-root quoted inside a notice above it
        command = [l for l in out.splitlines() if "--cwd" in l]
        self.assertEqual(len(command), 1, "one next command line is owed, got %d: %s" % (len(command), said[-800:]))
        named_root = NAMED_ROOT.search(command[0])
        return (named_root.group(1).strip("'\"") if named_root else None), said

    def notices(self, said):
        return [l for l in said.splitlines() if "earlier run(s)" in l]

    def runs_folders(self, base):
        """Every docs/plan/runs folder under base, relative to it: where this layout has been written."""
        return sorted(os.path.relpath(root, base) for root, _dirs, _files in os.walk(base) if root.endswith(RUNS))


class InstallOnlyCopy(Base):
    def setUp(self):
        Base.setUp(self)
        self.root = self.install()
        self.engine = os.path.join(self.root, "runtime", "brother_run.py")
        self.launcher = os.path.join(self.root, "runtime", "brother-run")

    def test_the_engine_form_keeps_runs_per_user_and_never_inside_the_install(self):
        """The form the shipped skill gives. The plugin folder is replaced on update: nothing may land in it."""
        root, said = self.discover(self.engine)
        self.assertEqual(root, self.per_user, said[-600:])
        self.assertEqual(self.runs_folders(self.root), [], "the engine form wrote inside the install")

    def test_the_launcher_form_uses_the_same_runs_root_as_the_engine_form(self):
        """Two forms, one answer: a run started by either is found by `--continue` through the other."""
        by_engine, _ = self.discover(self.engine)
        by_launcher, _ = self.discover(self.launcher)
        self.assertIsNotNone(by_launcher, "the launcher form named no runs root")
        self.assertEqual(by_launcher, by_engine)

    def test_an_explicit_runs_root_is_honoured_by_both_forms(self):
        chosen = os.path.join(self.d, "chosen")
        for program in (self.engine, self.launcher):
            root, said = self.discover(program, "--runs-root", chosen)
            self.assertEqual(root, chosen, "%s: %s" % (os.path.basename(program), said[-600:]))
        self.assertFalse(os.path.exists(self.per_user), "an explicit root still wrote to the per user location")
        self.assertEqual(self.runs_folders(self.root), [], "an explicit root still wrote inside the install")

    def test_an_install_that_already_holds_runs_says_so_and_writes_nothing_new_there(self):
        """Runs an earlier engine wrote inside the install are neither moved nor deleted nor silently ignored: one
        line says where they are and that they are not read by default."""
        earlier = self.earlier_run(self.root)
        before = sorted(os.listdir(earlier))
        root, said = self.discover(self.engine)
        notice = self.notices(said)
        self.assertEqual(len(notice), 1, "one line names the earlier runs, got %d: %s" % (len(notice), said[-700:]))
        self.assertIn("1 earlier run(s) are kept under %s," % earlier, notice[0])
        self.assertIn("not read by default", notice[0])
        self.assertEqual(root, self.per_user, said[-600:])
        self.assertEqual(sorted(os.listdir(earlier)), before, "something new was written among the earlier runs")
        self.assertEqual(self.runs_folders(self.root), [RUNS])

    def test_the_notice_names_the_exact_root_that_continues_those_runs(self):
        """`--runs-root` takes the folder that HOLDS docs/plan/runs: naming any other sends the reader nowhere."""
        self.earlier_run(self.root)
        _root, said = self.discover(self.engine)
        self.assertEqual(len(self.notices(said)), 1, said[-700:])
        self.assertIn("pass --runs-root %s " % self.root, self.notices(said)[0])

    def test_the_notice_counts_only_what_the_engine_recognises_as_a_run(self):
        earlier = self.earlier_run(self.root)
        os.makedirs(os.path.join(earlier, "notes"))
        os.makedirs(os.path.join(earlier, "a-folder-that-is-not-a-run"))
        _root, said = self.discover(self.engine)
        self.assertEqual(len(self.notices(said)), 1, said[-700:])
        self.assertIn("1 earlier run(s) are kept", self.notices(said)[0])

    def test_folders_that_are_not_runs_bring_no_notice_at_all(self):
        os.makedirs(os.path.join(self.root, RUNS, "notes"))
        _root, said = self.discover(self.engine)
        self.assertEqual(self.notices(said), [], "a notice about earlier runs, and there are none")

    def test_an_operator_who_names_the_install_itself_is_not_told_its_runs_are_unread(self):
        """The notice is true or absent: when the operator points the runs root AT the install, its earlier runs
        are the ones being read, so the line that says they are not read by default must not appear."""
        self.earlier_run(self.root)
        _root, said = self.discover(self.engine, BROTHER_RUNS_ROOT=self.root)
        self.assertEqual(self.notices(said), [])

    def test_an_unwritable_per_user_place_states_one_location_once_the_one_used(self):
        """The per user place cannot be written, so the engine falls back, as any default does. Where this run's
        records go is then said ONCE, and it is the place actually used."""
        os.makedirs(os.path.dirname(self.per_user))
        with open(self.per_user, "w", encoding="utf-8") as fh:
            fh.write("a file where the folder would be: nothing can be created under it\n")
        self.earlier_run(self.root)
        used, said = self.discover(self.engine)
        self.assertEqual(used, os.path.join(self.home, ".claude", "brother", "runs"), said[-700:])
        stated = [l for l in said.splitlines() if "records go to" in l]
        self.assertEqual(len(stated), 1, "where the records go is stated %d time(s): %s" % (len(stated), said[-900:]))
        self.assertIn("records go to %s " % used, stated[0])
        self.assertEqual(len(self.notices(said)), 1, said[-700:])

    def test_a_named_per_user_location_is_honoured_by_both_forms(self):
        """BROTHER_RUNS_ROOT was the launcher's alone; an operator who set it had the engine form ignore it."""
        named = os.path.join(self.d, "named-by-the-operator")
        for program in (self.engine, self.launcher):
            root, said = self.discover(program, BROTHER_RUNS_ROOT=named)
            self.assertEqual(root, named, "%s: %s" % (os.path.basename(program), said[-600:]))
        self.assertEqual(self.runs_folders(self.root), [], "a named location still wrote inside the install")

    def test_a_tilde_in_the_named_location_is_the_users_home(self):
        root, said = self.discover(self.engine, BROTHER_RUNS_ROOT="~/named-with-a-tilde")
        self.assertEqual(root, os.path.join(self.home, "named-with-a-tilde"), said[-600:])

    def test_a_relative_named_location_is_refused_and_nothing_is_written(self):
        """Relative to WHAT is the question a relative value cannot answer: it would land in whatever folder the
        tool was started from, usually the target repository itself. Refused by name, never resolved on a guess."""
        code, said, _out = self.run_it(self.engine, BROTHER_RUNS_ROOT="some/relative/place")
        self.assertNotEqual(code, 0, said[-600:])
        self.assertIn("BROTHER_RUNS_ROOT", said)
        self.assertIn("absolute", said)
        self.assertFalse(os.path.exists(os.path.join(self.target, "some")), "a relative value wrote into the target")
        self.assertEqual(self.runs_folders(self.root), [])
        self.assertFalse(os.path.exists(self.per_user))

    def test_a_named_location_inside_the_install_is_honoured_and_says_an_update_deletes_it(self):
        """An explicit choice is honoured, and told once what it costs."""
        inside = os.path.join(self.root, "my-own-runs")
        root, said = self.discover(self.engine, BROTHER_RUNS_ROOT=inside)
        self.assertEqual(root, inside, said[-600:])
        told = [l for l in said.splitlines() if "BROTHER_RUNS_ROOT" in l and inside in l and "update" in l]
        self.assertEqual(len(told), 1, "one line says an update deletes that folder, got %d: %s" % (len(told), said[-700:]))


class DevelopmentCheckout(Base):
    def setUp(self):
        Base.setUp(self)
        self.repo = self.checkout()
        self.engine = os.path.join(self.repo, "scripts", "brother_run.py")

    def test_the_default_stays_inside_the_checkout_as_it_always_has(self):
        root, said = self.discover(self.engine)
        self.assertIsNone(root, "a checkout's own default needs no --runs-root in the next command: %s" % said[-600:])
        self.assertEqual(self.runs_folders(self.repo), [RUNS])
        self.assertFalse(os.path.exists(self.per_user), "a development checkout wrote to the per user location")

    def test_an_explicit_runs_root_is_honoured(self):
        chosen = os.path.join(self.d, "chosen")
        root, said = self.discover(self.engine, "--runs-root", chosen)
        self.assertEqual(root, chosen, said[-600:])
        self.assertEqual(self.runs_folders(self.repo), [], "an explicit root still wrote inside the checkout")

    def test_a_named_per_user_location_never_moves_a_checkouts_runs(self):
        """BROTHER_RUNS_ROOT names the PER USER place. A development checkout keeps its runs in its own repository
        whatever that name says, as it did before the name reached the engine."""
        named = os.path.join(self.d, "named-by-the-operator")
        root, said = self.discover(self.engine, BROTHER_RUNS_ROOT=named)
        self.assertIsNone(root, said[-600:])
        self.assertEqual(self.runs_folders(self.repo), [RUNS])
        self.assertFalse(os.path.exists(named), "the named location was written from a development checkout")


class ACheckoutThatHoldsEarlierRuns(Base):
    def test_the_launcher_form_says_where_they_are_and_how_to_continue_one(self):
        """The launcher used to keep runs in the git checkout it sat in. It now goes per user (a cloned marketplace
        is a git checkout too, and cannot be told from a development checkout), so a developer who has runs in the
        checkout is TOLD where they are, never left to find them gone."""
        repo = self.folder("repository")
        self.git(repo, "init", "-q")
        self.install(os.path.join(repo, "bundle"))
        earlier = self.earlier_run(repo)
        root, said = self.discover(os.path.join(repo, "bundle", "runtime", "brother-run"))
        self.assertEqual(root, self.per_user, said[-600:])
        notice = self.notices(said)
        self.assertEqual(len(notice), 1, "one line names the checkout's earlier runs, got %d: %s" % (len(notice), said[-700:]))
        self.assertIn("1 earlier run(s) are kept under %s," % earlier, notice[0])
        self.assertIn("pass --runs-root %s " % repo, notice[0])


class ALayoutThatCannotBeToldApart(Base):
    def test_it_goes_to_the_per_user_location_never_beside_the_engine(self):
        """Neither the source under scripts/ nor the shipped mirror under runtime/: unknown. Unknown must never
        write into a folder an update may delete, so it is treated as an install."""
        somewhere = os.path.join(self.d, "somewhere")
        shutil.copytree(RUNTIME, os.path.join(somewhere, "engine"), symlinks=True, ignore=NOT_THE_ENGINE)
        root, said = self.discover(os.path.join(somewhere, "engine", "brother_run.py"))
        self.assertEqual(root, self.per_user, said[-600:])
        self.assertEqual(self.runs_folders(somewhere), [], "an unknown layout wrote beside the engine")


class ATestFileThatSpellsTheScratchRootIsRefused(unittest.TestCase):
    """No test file but this one may spell the scratch root: the ONE rule decides where a run's records live.

    This file keeps the root in SCRATCH (the auditor must know what to look for) and excludes itself from the
    scan, so neither its own constant nor its own pattern strings can match their own source. A spelling is
    refused once the guard exists; a file that asks the ONE rule for the root carries no such literal at all.
    """

    def _scanned_test_paths(self) -> List[str]:
        """Every test file the guard reads: scripts/test_*.py and scripts/loop/test_*.py, this file excepted."""
        paths = []
        for root in (os.path.join(REPO, "scripts"), os.path.join(REPO, "scripts", "loop")):
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                if not (name.startswith("test_") and name.endswith(".py")):
                    continue
                path = os.path.join(root, name)
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue
                paths.append(path)
        return paths

    def _refuse(self, path: str, match) -> None:
        """The one refusal, so a test can show it going red without a live offence in the tree."""
        self.fail(f"{path} spells the scratch root: {match.group(0)}")

    @unittest.skipUnless(os.path.isdir(os.path.join(REPO, "scripts")), "no scripts directory")
    def _offences(self, root: str, paths: List[str], known=KNOWN_SPELLERS):
        """(path, match) for every file under root that spells the scratch root and is not in `known`. Pure in the
        sense that matters: it reads file contents only, never a file's time, so a checkout's write order cannot
        change the verdict. A file that cannot be opened, or holds a byte that is not UTF-8, is read with
        replacement and never crashes the guard."""
        found = []
        for path in paths:
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue   # it moved, or it is a directory named test_*.py: only files reach open
            for pattern in _scratch_root_spellings():
                match = re.search(pattern, text)
                if match and rel not in known:
                    found.append((path, match))
                    break
        return found

    def test_no_test_spells_the_scratch_root(self) -> None:
        """An empty scan is a failure, never a silent pass. A file outside KNOWN_SPELLERS that spells the root is
        refused, naming the file and the matched text; a file in it is the work this unit closes at the source,
        not a new offence."""
        paths = self._scanned_test_paths()
        self.assertTrue(paths, "no test files found")
        self._check(REPO, paths)

    def _check(self, root: str, paths: List[str], known=KNOWN_SPELLERS) -> None:
        """The guard's verdict: the first offence fails the test through the one refusal."""
        for path, match in self._offences(root, paths, known):
            self._refuse(path, match)

    def test_the_check_refuses_a_planted_offender_by_name(self) -> None:
        """The wiring from the scan to the refusal, shown going red on a planted file (the real tree holds none)."""
        root, path = self._planted("test_planted.py", 'X = "~/.claude/brother-scratch"\n')
        with self.assertRaises(AssertionError) as caught:
            self._check(root, [path], known=frozenset())
        self.assertIn("test_planted.py spells the scratch root", str(caught.exception))

    def _planted(self, name: str, text: str):
        """A throwaway tree holding scripts/<name> with `text`, under the one scratch root's parent HOME."""
        root = tempfile.mkdtemp(prefix="one-rule-guard-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "scripts"))
        path = os.path.join(root, "scripts", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return root, path

    def test_a_new_file_that_spells_the_root_is_refused(self) -> None:
        root, path = self._planted("test_new_offender.py", 'X = "~/.claude/brother-scratch"\n')
        self.assertEqual([p for p, _ in self._offences(root, [path])], [path])

    def test_a_baseline_file_that_spells_the_root_is_not_a_new_offence(self) -> None:
        root, path = self._planted("test_old_offender.py", 'X = "~/.claude/brother-scratch"\n')
        self.assertEqual(self._offences(root, [path], known=frozenset({"scripts/test_old_offender.py"})), [])

    def test_the_verdict_never_reads_a_files_time(self) -> None:
        """The defect this replaces: an old offender written to disk after the guard read as new. Both orders of
        modification time give the same verdict for a new file and for a baseline file."""
        root, path = self._planted("test_timed.py", 'X = "$HOME/.claude/brother-scratch"\n')
        for stamp in (1.0, 4102444800.0):
            os.utime(path, (stamp, stamp))
            self.assertEqual(len(self._offences(root, [path], known=frozenset())), 1)
            self.assertEqual(self._offences(root, [path], known=frozenset({"scripts/test_timed.py"})), [])

    def test_the_baseline_never_names_this_guard(self) -> None:
        """This file excludes itself from the scan; naming it in the baseline would hide nothing and mean nothing."""
        self.assertNotIn("scripts/test_runs_root_one_rule.py", KNOWN_SPELLERS)

    def test_the_guard_recognises_the_joined_form(self) -> None:
        """The third pattern crosses the close paren of os.path.expanduser("~"), which is what lets it match the
        joined spelling the base tree keeps. Pure: no file is read and no temporary directory is made."""
        sample = 'SCRATCH = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")'
        for pattern in _scratch_root_spellings():
            if re.search(pattern, sample):
                return
        self.fail("no pattern matches the joined form of the scratch root")

    def test_the_guard_recognises_the_literal_spellings(self) -> None:
        """A tilde and $HOME are the literals a file keeps for itself; each has its own pattern."""
        for spelling in ("~/.claude/brother-scratch", "$HOME/.claude/brother-scratch"):
            self.assertTrue(any(re.search(p, spelling) for p in _scratch_root_spellings()),
                            "no pattern matches %s" % spelling)

    def test_the_refusal_names_the_file_and_the_matched_text(self) -> None:
        """The refusal itself is exercised: a check nobody has seen go red is not known to be able to fail."""
        with self.assertRaises(AssertionError) as caught:
            self._refuse("some/test_file.py", re.search(r"brother-scratch", "brother-scratch"))
        self.assertIn("some/test_file.py spells the scratch root: brother-scratch", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
