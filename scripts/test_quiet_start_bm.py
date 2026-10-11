#!/usr/bin/env python3
"""QS1.a: BrotherMode's session start is quiet by default (docs/plan/specs/QS1.md).

WHAT IS PINNED. With no switch, products/brothermode/tools/bm_sessionstart.py starts none of the seven routine
programs (startup nags, progress page check, stall sweep, handover detect, handover owed, idle check, forecast
calibration) and prints no digest; with BROTHER_VERBOSE_START=1 (or BROTHERMODE_MAINTAINER=1) it starts all of them in
the order the file always used. The programs a person must see (the consent probe, the update notice, the compaction
hint, store health, reconcile) start in both modes, and a quiet start prints only the reconcile rows that need action.

HOW. In process: the module is loaded by path and its own `_run` is replaced by a recorder, so the assertion is on the
programs the start actually launches (the footprint), not on output text a fixture happens to produce. Once for real:
the file runs as a subprocess in a fake HOME with a consented config and an established git project, so the quiet
promise is also measured in bytes.

Python 3.9 and 3.13, standard library only, no network; nothing touches the real ~/.claude, ~/.brotherme or a vault.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "products", "brothermode", "tools")
SESSIONSTART = os.path.join(TOOLS, "bm_sessionstart.py")

ROUTINE = [
    ("bm_telemetry.py", "startup-nags"),
    ("bm_progress_check.py", "status"),
    ("bm_stall.py", "sweep"),
    ("bm_handover.py", "detect"),
    ("bm_handover.py", "owed"),
    ("bm_idle.py", "check"),
    ("bm_forecast.py", "calibrate", "--clock", "agent", "--basis", "judged"),
]
KEPT = [
    ("setup.py", "--consent-state"),
    ("bm_telemetry.py", "check-update"),
    ("bm_telemetry.py", "compact-hint"),
    ("bm_store.py", "verify"),
    ("bm_reconcile.py",),
]
#: The order the file has always started its programs in, for an established project. Verbose mode keeps it exactly.
VERBOSE_ORDER = [KEPT[0], ROUTINE[0], ROUTINE[1], ROUTINE[2], ROUTINE[3], ROUTINE[4], KEPT[1], ROUTINE[5],
                 ROUTINE[6], KEPT[2], KEPT[3], KEPT[4]]
QUIET_MARKERS = ("BROTHERMODE NAG", "BROTHERMODE SPEND", "progress page:", "bm_stall sweep", "OWED",
                 "newest pack", "usable pairs", "NO-DATA |", "VALID |")


def _load():
    spec = importlib.util.spec_from_file_location("bm_sessionstart_under_test", SESSIONSTART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def started_programs(env, first_run, reconcile=(0, ""), outputs=None):
    """(the programs main() started, as (basename, args...) tuples, and what it printed). `outputs` maps a program's
    basename to the (exit code, captured output) the recorder answers with; bm_reconcile.py answers `reconcile`."""
    mod = _load()
    seen = []

    def recorder(args, stdin_text=None, capture=False, keep_stderr=False, stderr_sink=None):
        call = (os.path.basename(args[0]),) + tuple(args[1:])
        seen.append(call)
        if call[0] == "bm_reconcile.py":
            # an optional third element is what the program wrote to stderr
            if stderr_sink is not None:
                stderr_sink.append(reconcile[2] if len(reconcile) > 2 else "")
            return reconcile[0], (reconcile[1] if capture else None)
        code, text = (outputs or {}).get(call[0], (0, ""))
        return code, (text if capture else None)

    mod._run = recorder
    mod._is_first_run = lambda: first_run
    mod._no_vault_bound = lambda: False
    mod._load_bm_repo_scope = lambda: None
    out = io.StringIO()
    old_env, old_stdin = dict(os.environ), sys.stdin
    try:
        for name in mod.VERBOSE_SWITCHES:
            os.environ.pop(name, None)
        os.environ.update(env)
        sys.stdin = io.StringIO("{}")
        with contextlib.redirect_stdout(out):
            code = mod.main()
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        sys.stdin = old_stdin
    if code != 0:
        raise AssertionError("main() exited %r" % code)
    return seen, out.getvalue()


class QuietByDefault(unittest.TestCase):

    def test_quiet_reads_reconcile_as_json(self):
        seen, _ = started_programs({}, first_run=False)
        self.assertIn(("bm_reconcile.py", "--json"), seen)

    def test_quiet_starts_no_routine_program(self):
        seen, _ = started_programs({}, first_run=False)
        for call in ROUTINE:
            self.assertNotIn(call, seen, "a quiet start launched %s" % (call,))

    def test_quiet_prints_no_digest(self):
        with io.open(os.path.join(TOOLS, "..", "DIGEST.md"), encoding="utf-8") as fh:
            first = fh.read().splitlines()[0]
        _, out = started_programs({}, first_run=False)
        self.assertNotIn(first, out)

    def test_kept_programs_start_in_both_modes(self):
        for env in ({}, {"BROTHER_VERBOSE_START": "1"}):
            seen, _ = started_programs(env, first_run=False)
            # a quiet start asks bm_reconcile.py for --json; the program started is the same one
            seen = [c[:1] if c[0] == "bm_reconcile.py" else c for c in seen]
            for call in KEPT:
                self.assertIn(call, seen, "%s did not start with %r" % (call, env))

    def test_verbose_keeps_the_original_order(self):
        seen, out = started_programs({"BROTHER_VERBOSE_START": "1"}, first_run=False)
        self.assertEqual(seen, VERBOSE_ORDER)
        with io.open(os.path.join(TOOLS, "..", "DIGEST.md"), encoding="utf-8") as fh:
            self.assertIn(fh.read().splitlines()[0], out)

    def test_maintainer_switch_is_the_same_switch(self):
        seen, _ = started_programs({"BROTHERMODE_MAINTAINER": "1"}, first_run=False)
        self.assertEqual(seen, VERBOSE_ORDER)

    def test_first_run_keeps_its_welcome_when_quiet(self):
        _, out = started_programs({}, first_run=True)
        self.assertIn("new project", out)
        # 1.1.1 retires the brothermode-start name: the welcome names the one door
        self.assertIn("To begin, say what you want done (in Claude Code: /brother).", out)
        # the hook also runs in Codex, which has no slash commands: /brother is only ever named for Claude Code
        self.assertEqual(out.count("/brother"), out.count("(in Claude Code: /brother)"), out)

    def test_only_exactly_one_opens_the_switch(self):
        mod = _load()
        for value in ("", "0", "true", "yes", " 1", "1 ", "01", "on"):
            for name in mod.VERBOSE_SWITCHES:
                self.assertFalse(mod.verbose_start({name: value}), "%s=%r opened the switch" % (name, value))
        self.assertFalse(mod.verbose_start({}))
        self.assertTrue(mod.verbose_start({"BROTHER_VERBOSE_START": "1"}))


def _row(cls, category, kind="record", subject="x", next_action="do it"):
    return {"class": cls, "category": category, "kind": kind, "subject": subject, "reason": "%s reason" % category,
            "next_action": next_action, "route": "founder"}


class ReconcileRowsThatNeedAction(unittest.TestCase):

    ROWS = json.dumps({"rows": [
        _row("VALID", "no-drift", subject="A", next_action=""),
        _row("NO-DATA", "no-upstream", kind="push-state", subject="/x"),
        _row("RECOVERABLE", "dead-owner-provisional", subject="B"),
        _row("STALE", "stale-fence", subject="C"),
        _row("CONFLICT", "duplicate-fence-lines", subject="D"),
    ]})

    def test_each_class(self):
        mod = _load()
        kept = mod.actionable_reconcile(1, self.ROWS).splitlines()
        self.assertEqual(kept[0], "bm_reconcile: 3 row(s) need action; BROTHER_VERBOSE_START=1 shows every row")
        self.assertEqual([k.split(" | ")[0] for k in kept[1:]], ["RECOVERABLE", "STALE", "CONFLICT"])

    def test_nothing_actionable_prints_nothing(self):
        mod = _load()
        self.assertEqual(mod.actionable_reconcile(1, json.dumps({"rows": [_row("VALID", "no-drift")]})), "")
        self.assertEqual(mod.actionable_reconcile(0, ""), "")
        self.assertEqual(mod.actionable_reconcile(0, None), "")
        self.assertEqual(mod.actionable_reconcile(127, ""), "")

    def _root(self, with_store):
        """A BROTHERMODE_ROOT for _store_on_disk(), with or without a store file in it."""
        root = tempfile.mkdtemp(prefix="test-quiet-start-root-")
        self.addCleanup(shutil.rmtree, root, True)
        if with_store:
            os.makedirs(os.path.join(root, ".brothermode"))
            io.open(os.path.join(root, ".brothermode", "store.sqlite3"), "w").close()
        return {"BROTHERMODE_ROOT": root}

    def test_no_data_rows_with_a_next_action_are_kept(self):
        """N2: the NO-DATA categories that ask the user to act survive a quiet start, each on its own."""
        for category, kind in (("open-dispatch-no-outcome", "controller-unit"),
                               ("settled-never-reviewed", "controller-unit"), ("store-unreadable", "store")):
            text = json.dumps({"rows": [_row("NO-DATA", category, kind=kind, subject="u1")]})
            _, out = started_programs(self._root(True), first_run=False, reconcile=(1, text))
            self.assertIn("NO-DATA | %s u1 | %s reason" % (kind, category), out, category)

    def test_store_unreadable_without_a_store_is_dropped(self):
        """A git repository with no store yet gets a store-unreadable row (git does not ignore .brothermode); with
        nothing on disk to leak or repair, a quiet start drops it."""
        text = json.dumps({"rows": [_row("NO-DATA", "store-unreadable", kind="store", subject="u1")]})
        _, out = started_programs(self._root(False), first_run=False, reconcile=(1, text))
        self.assertEqual(out, "")

    def test_reporting_no_data_rows_stay_dropped(self):
        text = json.dumps({"rows": [_row("NO-DATA", "no-upstream")]})
        _, out = started_programs({}, first_run=False, reconcile=(1, text))
        self.assertEqual(out, "")

    def test_no_remote_tracking_branch_is_quiet_by_default(self):
        """Round 3 note 2: a remote with no main or master gives this row at every start, and a fetch cannot fix
        that, so a quiet start prints nothing for it."""
        text = json.dumps({"rows": [_row("NO-DATA", "no-remote-tracking-branch", kind="record-distance")]})
        _, out = started_programs({}, first_run=False, reconcile=(1, text))
        self.assertEqual(out, "")

    def test_no_remote_tracking_branch_shows_when_verbose(self):
        text = ("bm_reconcile: 1 row(s)\nNO-DATA | record-distance /x | no local remote-tracking main/master branch "
                "is known for origin | `git fetch origin` then retry | route founder\n")
        _, out = started_programs({"BROTHER_VERBOSE_START": "1"}, first_run=False, reconcile=(1, text))
        self.assertIn("NO-DATA | record-distance /x | no local remote-tracking main/master branch", out)

    def test_unknown_category_shows(self):
        """Review note 3: a NO-DATA category nobody named is shown, never dropped without a word."""
        text = json.dumps({"rows": [_row("NO-DATA", "a-category-from-a-later-release")]})
        _, out = started_programs({}, first_run=False, reconcile=(1, text))
        self.assertIn("a-category-from-a-later-release reason", out)

    def test_unknown_class_shows(self):
        text = json.dumps({"rows": [_row("SURPRISE", "no-drift")]})
        _, out = started_programs({}, first_run=False, reconcile=(1, text))
        self.assertIn("SURPRISE | record x", out)

    def test_nonzero_exit_with_no_output_says_so(self):
        """Review note 4: a non-zero exit that printed nothing is one NO-DATA line, never silence."""
        for code in (1, 2):
            _, out = started_programs({}, first_run=False, reconcile=(code, ""))
            self.assertIn("NO-DATA: bm_reconcile exited %d with no output" % code, out)

    def test_stderr_is_kept_apart_from_the_json(self):
        """Review note 1: a warning on stderr reaches the user once and never breaks the row parse."""
        text = json.dumps({"rows": [_row("VALID", "no-drift", subject="A"), _row("CONFLICT", "dup", subject="D")]})
        warning = "bm_store: WARNING: containment skipped"
        _, out = started_programs({}, first_run=False, reconcile=(1, text, warning + "\n" + warning + "\n"))
        self.assertIn("CONFLICT | record D", out)
        self.assertNotIn("VALID", out)
        self.assertNotIn('"rows"', out)
        self.assertEqual(out.count(warning), 1, out)

    def test_reconcile_refusal_passes_through(self):
        """N1: bm_reconcile.py refusing (exit 2, a NO-DATA line that is not a row) is shown in a quiet start."""
        _, out = started_programs({}, first_run=False,
                                  reconcile=(2, "NO-DATA: could not load bm_stall.py (missing)\n"))
        self.assertIn("NO-DATA: could not load bm_stall.py", out)

    def test_reconcile_output_that_is_not_its_json_passes_through(self):
        _, out = started_programs({}, first_run=False, reconcile=(1, "Traceback (most recent call last):\n  boom\n"))
        self.assertIn("Traceback", out)

    def test_quiet_start_prints_only_action_rows(self):
        _, out = started_programs({}, first_run=False, reconcile=(1, self.ROWS))
        self.assertIn("CONFLICT | record D", out)
        self.assertNotIn("VALID | record A", out)
        self.assertNotIn("NO-DATA | push-state", out)

    def test_verbose_start_prints_every_row(self):
        text = "bm_reconcile: 1 row(s)\nVALID | record A | fine | (no action proposed) | route founder\n"
        seen, out = started_programs({"BROTHER_VERBOSE_START": "1"}, first_run=False, reconcile=(1, text))
        self.assertIn("VALID | record A", out)
        self.assertIn(("bm_reconcile.py",), seen)


class StoreHealthPrintsWhenQuiet(unittest.TestCase):
    """N3: a store verify line that needs the user prints with no switch set."""

    def test_schema_refusal_prints_quiet(self):
        line = "refused (schema-behind): the store is one schema behind; run init to migrate"
        _, out = started_programs({}, first_run=False, outputs={"bm_store.py": (2, line + "\n")})
        self.assertIn(line, out)

    def test_git_state_unknown_prints_quiet_with_a_store(self):
        """Review note 6: the refusal bm_store gives when it cannot tell whether git ignores the store."""
        root = tempfile.mkdtemp(prefix="test-quiet-start-root-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, ".brothermode"))
        io.open(os.path.join(root, ".brothermode", "store.sqlite3"), "w").close()
        line = "refused (git-state-unknown): the git index could not be read or parsed"
        _, out = started_programs({"BROTHERMODE_ROOT": root}, first_run=False,
                                  outputs={"bm_store.py": (2, line + "\n")})
        self.assertIn(line, out)

    def test_healthy_verify_is_silent(self):
        _, out = started_programs({}, first_run=False,
                                  outputs={"bm_store.py": (0, "verify: healthy, 0 problem(s)\n")})
        self.assertEqual(out, "")


class RealEstablishedProject(unittest.TestCase):
    """The file itself, as a subprocess, in a fake HOME and an established git project."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-quiet-start-bm-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(os.path.join(self.home, ".brotherme"))
        os.makedirs(os.path.join(self.project, "docs", "plan"))
        with io.open(os.path.join(self.home, ".brotherme", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"setup_complete": True, "vault_path": os.path.join(self.home, "Vault"),
                       "privacy_notice_version": "2026-08-01", "installation_mode": "clone",
                       "security_mode": "standard"}, fh)
        with io.open(os.path.join(self.project, "docs", "plan", "QUEUE.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        subprocess.run(["git", "init", "-q", self.project], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    def _run(self, **extra):
        env = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
        env.update(extra)
        r = subprocess.run([sys.executable, SESSIONSTART], cwd=self.project, env=env, input=b"{}",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-600:])
        return r.stdout

    def test_quiet_is_quiet_in_bytes(self):
        out = self._run()
        text = out.decode("utf-8", "replace")
        for marker in QUIET_MARKERS:
            self.assertNotIn(marker, text, "a quiet start printed %r" % marker)
        self.assertLessEqual(len(out), 512, text[:400])

    def _git(self, *args):
        subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"] + list(args),
                       cwd=self.project, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _init_store(self):
        env = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run([sys.executable, os.path.join(TOOLS, "bm_store.py"), "init"], cwd=self.project, env=env,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)

    def test_committed_store_is_reported_quiet(self):
        """C1: git tracks the cleartext store (forced past the ignore rule and committed); no switch is set."""
        self._init_store()
        self._git("add", "-f", "-A")
        self._git("commit", "-qm", "store")
        text = self._run().decode("utf-8", "replace")
        self.assertIn("refused (git-tracked-store)", text)

    def test_unignored_store_is_reported_quiet(self):
        """C1: a store on disk that git no longer ignores (every ignore rule emptied); no switch is set."""
        self._init_store()
        for rel in (os.path.join(".git", "info", "exclude"), ".gitignore"):
            path = os.path.join(self.project, rel)
            if os.path.exists(path):
                with io.open(path, "w", encoding="utf-8") as fh:
                    fh.write("")
        text = self._run().decode("utf-8", "replace")
        self.assertIn("refused (git-exposed-store)", text)

    def test_unignored_store_is_reported_when_the_root_variable_is_not_a_directory(self):
        """Review note 2: bm_store ignores a BROTHERMODE_ROOT that is not a directory and resolves from the current
        directory, so the store it finds there is the one the warning is about."""
        self._init_store()
        for rel in (os.path.join(".git", "info", "exclude"), ".gitignore"):
            path = os.path.join(self.project, rel)
            if os.path.exists(path):
                with io.open(path, "w", encoding="utf-8") as fh:
                    fh.write("")
        text = self._run(BROTHERMODE_ROOT=os.path.join(self.tmp, "not-a-directory")).decode("utf-8", "replace")
        self.assertIn("refused (git-exposed-store)", text)

    def test_skipped_containment_stays_within_the_quiet_budget(self):
        """Review note 1 at the entry point: BROTHERMODE_SKIP_GIT_CONTAINMENT=1 makes every store open warn on
        stderr; the quiet start shows the warning, no JSON, and stays within the 512 byte budget."""
        self._init_store()
        out = self._run(BROTHERMODE_SKIP_GIT_CONTAINMENT="1")
        text = out.decode("utf-8", "replace")
        self.assertIn("BROTHERMODE_SKIP_GIT_CONTAINMENT is set", text)
        self.assertNotIn('"rows"', text)
        self.assertLessEqual(len(out), 512, text[:600])

    def test_verbose_restores_the_routine_lines(self):
        text = self._run(BROTHER_VERBOSE_START="1").decode("utf-8", "replace")
        self.assertIn("BROTHERMODE NAG", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
