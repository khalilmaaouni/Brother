"""model_call.py --selftest builds its own world: no BROTHER_ name of the caller's reaches its verdict.

MEASURED 2026-10-05 22:31 JST. The loop's closer ran FX-31's done check inside a proof phase and logged
"CLOSE-RED FX-31: the done_check exited 1: selftest: 19 cases, FAILED: a NON ZERO EXIT with a full body is refused",
on a tree where the same command passed by hand. Both were true. The closer's suite environment drops the run's own
choices (loop_switches.RUN_KNOBS) and the run directory, and keeps the proof keys (BROTHER_PROOF_PHASE,
BROTHER_PROOF_BASELINE, BROTHER_PROOF_BASELINE_SHA256). proof_ledger then refused every fake call of the selftest before
its fake runner ran ("proof policy and absolute run identity required"). That refusal is the proof gate working: the
defect was a selftest that read a regime it had not built. Twelve names flipped its verdict, each alone.

Every case runs the ENTRY POINT the way the closer runs it (a child `python -B scripts/loop/model_call.py --selftest`,
the exit code read from the process) with ONE name added to an environment that carries no BROTHER_ name, so only that
name can turn the case red. The last case replays the closer's whole environment through the closer's own two
functions (close_unit.done_check_env, then loop_switches.suite_env).

A tree that ships no plugin runtime admits no call at all, so there the selftest's verdict says nothing about the
environment: every case is skipped by name (NO-DATA), never passed.

Run from the repository root: python3 -B scripts/test_model_call_selftest_world.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")
SELFTEST = [sys.executable, "-B", os.path.join("scripts", "loop", "model_call.py"), "--selftest"]
POOL = os.path.join(ROOT, "plugin", "runtime", "brother", "core", "dispatch_semaphore.py")


class Base(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(POOL):
            self.skipTest("NO-DATA: this tree ships no plugin runtime, so the selftest admits no call here and its "
                          "verdict proves nothing about the environment")
        self.d = tempfile.mkdtemp(prefix="mc-world-")
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, "tmp"))
        # the caller's environment without one BROTHER_ name, and a HOME of its own: the selftest's scratch lands there
        self.clean = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        self.clean.update(HOME=self.d, TMPDIR=os.path.join(self.d, "tmp"))

    def folder(self, name):
        path = os.path.join(self.d, name)
        os.makedirs(path)
        return path

    def record(self, name, text):
        path = os.path.join(self.d, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def green(self, env, why):
        """The selftest EXITS ZERO under env and its verdict line agrees: the code is the verdict, the line is the reason."""
        try:
            r = subprocess.run(SELFTEST, capture_output=True, text=True, timeout=300, env=env, cwd=ROOT)
        except (OSError, subprocess.SubprocessError) as exc:
            self.fail("%s: the selftest could not run (%s: %s)" % (why, type(exc).__name__, exc))
        said = (r.stdout.strip().splitlines() or ["no output"])[-1]
        self.assertEqual(r.returncode, 0, "%s: exit %d, %s %s" % (why, r.returncode, said[:400], r.stderr[-300:]))
        self.assertTrue(said.startswith("selftest: ") and said.endswith(", OK"), "%s: exit 0 but it said %s" % (why, said[:400]))

    def one(self, name, value):
        self.green(dict(self.clean, **{name: value}), "%s alone" % name)


class OneNameAtATime(Base):
    def test_no_name_at_all_is_green(self):
        """The control: red here means the tree itself is red, and the cases below then prove nothing."""
        self.green(self.clean, "no BROTHER_ name")

    def test_a_proof_phase_never_reaches_the_selftest(self):
        self.one("BROTHER_PROOF_PHASE", "RB")

    def test_a_proof_baseline_never_reaches_the_selftest(self):
        self.one("BROTHER_PROOF_BASELINE", self.record("ledger-baseline.json", "{}"))

    def test_a_proof_baseline_hash_never_reaches_the_selftest(self):
        self.one("BROTHER_PROOF_BASELINE_SHA256", "0" * 64)

    def test_a_run_directory_with_no_start_record_never_reaches_the_selftest(self):
        self.one("BROTHER_RUN_DIR", self.folder("run"))

    def test_a_program_record_never_reaches_the_selftest(self):
        self.one("BROTHER_PROGRAM_RECORD", self.record("program-record.json", '{"proven": []}'))

    def test_the_loop_token_switch_never_reaches_the_selftest(self):
        self.one("BROTHER_LOOP_TOKEN", "on")

    def test_a_bridge_effort_below_the_floor_never_reaches_the_selftest(self):
        self.one("BROTHER_BRIDGE_EFFORT", "low")

    def test_a_raised_claude_effort_never_reaches_the_selftest(self):
        self.one("BROTHER_CLAUDE_EFFORT", "xhigh")

    def test_a_transports_restriction_never_reaches_the_selftest(self):
        self.one("BROTHER_TRANSPORTS", "claude")

    def test_a_code_root_that_ships_no_runtime_never_reaches_the_selftest(self):
        self.one("BROTHER_CODE_ROOT", self.folder("code-root"))

    def test_a_repo_root_that_ships_no_runtime_never_reaches_the_selftest(self):
        self.one("BROTHER_REPO_ROOT", self.folder("repo-root"))

    def test_a_codex_root_that_ships_no_runtime_never_reaches_the_selftest(self):
        self.one("BROTHER_CODEX_ROOT", self.folder("codex-root"))


class ItsOwnScratch(Base):
    """The selftest's own files live under ONE temp root it makes and removes, so its verdict depends on neither HOME nor
    a caller's scratch folder. MEASURED 2026-10-06 (review): once no BROTHER_ name survived, the child's working folder
    fell back to a folder under HOME, and with HOME unwritable the cwd case read FAILED on a tree that was fine; and
    every run left its two ledger files in the temp folder."""

    def unwritable_home(self):
        return self.record("home-is-a-file", "")   # nothing can be created under a regular file

    def test_an_unwritable_home_is_green_when_the_caller_names_a_scratch(self):
        self.green(dict(self.clean, HOME=self.unwritable_home(), BROTHER_MODEL_SCRATCH=self.folder("caller-scratch")),
                   "HOME unwritable, the caller names a scratch")

    def test_an_unwritable_home_is_green_with_no_caller_scratch(self):
        self.green(dict(self.clean, HOME=self.unwritable_home()), "HOME unwritable, no caller scratch")

    def test_nothing_of_the_selftest_remains_in_the_temp_root_it_used(self):
        tmp = self.folder("own-temp-root")
        self.green(dict(self.clean, TMPDIR=tmp), "a temp root of its own")
        self.assertEqual(sorted(os.listdir(tmp)), [], "the selftest left files in the temp root it used")


class TheClosersEnvironment(Base):
    def test_the_selftest_is_green_under_the_closers_own_suite_environment(self):
        """The names the lander logged for the FX-31 closer on 2026-10-05, each with a harmless value, through the two
        functions the closer itself applies. Whatever those two leave in, the selftest answers as it does alone."""
        sys.path.insert(0, LOOP)
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import close_unit
        import loop_switches
        run = self.folder("run")
        base = dict(self.clean,
                    BROTHER_PROOF_PHASE="RB", BROTHER_PROOF_BASELINE=self.record("ledger-baseline.json", "{}"),
                    BROTHER_PROOF_BASELINE_SHA256="0" * 64, BROTHER_PROOF_RUN_DIR=run, BROTHER_RUN_DIR=run,
                    BROTHER_CODE_ROOT=ROOT, BROTHER_LAUNCH_WORKTREE=ROOT,
                    BROTHER_FREEZE_MANIFEST=self.record("freeze.json", "{}"),
                    BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(self.d, "claude-calls.jsonl"),
                    BROTHER_OR_STATE_ROOT=self.folder("money"), BROTHER_RUNS_ROOT=self.folder("runs"),
                    BROTHER_SCRATCH=self.folder("scratch"), BROTHER_STOP_HOUR="7",
                    BROTHER_BRIEF_SCREEN="on", BROTHER_BUILD_PLAN="off", BROTHER_REPAIR_ADVISOR="off",
                    BROTHER_PROGRAM_RECORD=self.record("program-record.json", '{"proven": []}'),
                    BROTHER_PIN_MODEL="any-model", BROTHER_SCOPE="^(FX-31)$",
                    LOCAL_SLOTS="8", WORKERS_PER_ROUND="6")
        self.green(loop_switches.suite_env(close_unit.done_check_env(base), self.d), "the closer's suite environment")


if __name__ == "__main__":
    unittest.main(verbosity=1)
