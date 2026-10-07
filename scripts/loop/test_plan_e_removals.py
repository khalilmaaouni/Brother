#!/usr/bin/env python3
"""Plan E steps 2d to 2f: the post run finisher, salvage of old PASS builds, the build plan stage, the repair advisor and
history based tuning are OFF unless their switch is exactly "on"; the spec score cache is gone.

Run: python3 -B scripts/loop/test_plan_e_removals.py
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_plan as BP  # noqa: E402
import loop_switches as SW  # noqa: E402

CLEAN = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}


def switch_cli(name, value=None):
    env = dict(CLEAN, **({name: value} if value is not None else {}))
    return subprocess.run([sys.executable, "-B", os.path.join(HERE, "loop_switches.py"), name], env=env, capture_output=True, text=True, timeout=30).returncode


class Switches(unittest.TestCase):
    def test_every_switch_is_off_unless_exactly_on(self):
        for name in ("BROTHER_FINISHER", "BROTHER_SALVAGE", "BROTHER_TUNING", "BROTHER_PROBES"):
            self.assertEqual(switch_cli(name), 1, name + " unset must be off")
            self.assertEqual(switch_cli(name, "yes"), 1, name + " misspelt must be off")
            self.assertEqual(switch_cli(name, " On "), 1, name + " spelt any other way must be off (exact match)")
            self.assertEqual(switch_cli(name, "on"), 0, name + " on must be on")

    def test_an_unknown_switch_is_refused(self):
        self.assertEqual(switch_cli("BROTHER_SOMETHING_ELSE"), 2)
        with self.assertRaises(ValueError):
            SW.on("BROTHER_SOMETHING_ELSE", {})


class Sites(unittest.TestCase):
    UNTIL = open(os.path.join(HERE, "loop_until.sh"), encoding="utf-8").read()
    PASS = open(os.path.join(HERE, "loop_pass.sh"), encoding="utf-8").read()

    def test_the_finisher_is_skipped_when_its_switch_is_off(self):
        start = self.UNTIL.index("# OFF BY PLAN E (step 2d")
        end = self.UNTIL.index('if [ -z "$FIN_SKIP" ]; then', start)
        block = 'FIN_SKIP=""; STOP_OK=1; BROTHER_FINISHER_MODEL=deepseek; LOOP_DIR=%s\n%s\necho "SKIP=$FIN_SKIP"' % (HERE, self.UNTIL[start:end])
        r = subprocess.run(["bash", "-c", block], env=CLEAN, capture_output=True, text=True, timeout=30)
        self.assertIn("off by plan E", r.stdout, r.stdout + r.stderr)
        r = subprocess.run(["bash", "-c", block], env=dict(CLEAN, BROTHER_FINISHER="on"), capture_output=True, text=True, timeout=30)
        self.assertIn("SKIP=\n", r.stdout + "\n", "on must not skip: " + r.stdout + r.stderr)

    def test_salvage_and_tuning_run_only_under_their_switch(self):
        self.assertRegex(self.PASS, r"if python3 -B ~/\.claude/bin/loop_switches\.py BROTHER_SALVAGE[^\n]*then\n  SALV=\$\(python3 -B ~/\.claude/bin/salvage\.py promote")
        # since D13 (review 17 finding 4) the sizing eval also needs a frozen code root: outside a proof the landing tree's
        # code never runs unboxed, so the switch AND the code root gate it
        self.assertRegex(self.PASS, r"if python3 -B ~/\.claude/bin/loop_switches\.py BROTHER_TUNING[^\n]*then\n  if \[ -n \"\$CODE_ROOT\" \]; then eval \"\$\(cd \"\$WT\" && python3 -B \"\$CODE_ROOT/scripts/adaptive_sizing\.py\"")
        self.assertEqual(self.PASS.count("salvage.py promote"), 1)
        self.assertEqual(self.PASS.count("adaptive_sizing.py"), 1)

    def test_the_advisors_default_off(self):
        self.assertIn('BROTHER_REPAIR_ADVISOR="${BROTHER_REPAIR_ADVISOR:-off}" BROTHER_BUILD_PLAN="${BROTHER_BUILD_PLAN:-off}"', self.UNTIL)
        self.assertFalse(BP.enabled("X.1", {}))
        self.assertFalse(BP.enabled("X.1", {"BROTHER_BUILD_PLAN": "maybe"}))
        self.assertTrue(BP.enabled("X.1", {"BROTHER_BUILD_PLAN": "on"}))


class NoChecker(unittest.TestCase):
    """Owner decision 2026-10-02 (docs/decisions/remove-checker-2026-10-02.json): the checker is removed for good.
    The pass never runs it, the runner never calls it, landing never gates on it, whatever BROTHER_CHECKER says."""

    def test_no_loop_stage_runs_or_reads_a_checker(self):
        for name in ("loop_pass.sh", "unit_runner.py", "land_batch.py"):
            src = open(os.path.join(HERE, name), encoding="utf-8").read()
            for word in ("check_wave", "checker_hold", "checker_fix", "BROTHER_CHECKER"):
                self.assertNotIn(word, src, "%s still names %s" % (name, word))

    def test_landing_has_no_checker_hold(self):
        sys.path.insert(0, HERE)
        import land_batch as LB
        self.assertFalse(hasattr(LB, "checker_hold") or hasattr(LB, "checker_fix"))


class NoScoreCache(unittest.TestCase):
    def test_the_scorer_keeps_no_cache(self):
        src = open(os.path.join(HERE, "spec_score.py"), encoding="utf-8").read()
        self.assertNotIn("_cache_key", src)
        self.assertNotIn("spec-scores.cache.json", src)


class SuiteEnvStateRoots(unittest.TestCase):
    """2026-10-03: L5a-7 reached READY four times and was dropped at landing, "FAILED (failures=21, errors=3)", every error
    "cannot open the breaker lock: [Errno 1] Operation not permitted". The grader points the state roots inside its
    sandbox; the landing's suite_env kept the run's own BROTHER_OR_STATE_ROOT, outside the sandbox's write roots. Every
    landing suite routes through suite_env, so the state roots live under the suite's own HOME here."""

    def test_state_roots_are_under_the_suite_home(self):
        base = {"PATH": "/usr/bin", "BROTHER_OR_STATE_ROOT": "/outside/run/money", "BROTHER_JEV_STATE_DIR": "/outside/jev"}
        env = SW.suite_env(base, "/sandbox/home")
        self.assertEqual(env["BROTHER_OR_STATE_ROOT"], os.path.join("/sandbox/home", "or-state"))
        self.assertEqual(env["BROTHER_JEV_STATE_DIR"], os.path.join("/sandbox/home", "jev-state"))

    def test_state_roots_are_set_even_when_the_run_set_none(self):
        env = SW.suite_env({"PATH": "/usr/bin"}, "/sandbox/home")
        self.assertTrue(env["BROTHER_OR_STATE_ROOT"].startswith("/sandbox/home" + os.sep))
        self.assertTrue(env["BROTHER_JEV_STATE_DIR"].startswith("/sandbox/home" + os.sep))


if __name__ == "__main__":
    unittest.main()
