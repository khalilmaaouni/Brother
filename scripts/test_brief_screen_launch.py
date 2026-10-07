#!/usr/bin/env python3
"""FX-09.7: the loop launcher turns the grader's screen block on, beside the advisors it already turns on.

The code default of BROTHER_BRIEF_SCREEN stays "off" (today's brief); a loop run gets "on" unless the operator exports
"off" before the launch. This reads loop_until.sh as text and never runs it. Run: python3 -B scripts/test_brief_screen_launch.py"""
import os
import re
import sys
import unittest

LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
sys.path.insert(0, LOOP)
import grade_build as G  # noqa: E402

LINE = 'export BROTHER_BRIEF_SCREEN="${BROTHER_BRIEF_SCREEN:-on}"'


class TheLauncherTurnsTheScreenOn(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(LOOP, "loop_until.sh"), encoding="utf-8") as fh:
            self.text = fh.read()

    def test_the_launcher_exports_the_screen_on_by_default(self):
        self.assertEqual(self.text.count(LINE), 1)
        self.assertEqual(len(re.findall(r"^\s*export BROTHER_BRIEF_SCREEN=", self.text, re.M)), 1)
        default = re.search(r"BROTHER_BRIEF_SCREEN:-(\w+)\}", self.text).group(1)
        self.assertEqual(G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": default}), "on")

    def test_the_export_sits_before_the_first_pass(self):
        self.assertLess(self.text.index(LINE), self.text.index("BROTHER_BUILD_PLAN=\"${BROTHER_BUILD_PLAN:-off}\"") + 200)

    def test_the_code_default_is_still_off(self):
        self.assertEqual(G.brief_screen_mode({}), "off")

    def test_every_driver_default_before_proof_start_is_in_the_pairs_frozen_environment(self):
        """2026-10-04: the pair launcher freezes the environment at preflight and the driver verifies it at proof start;
        FX-09's BROTHER_BRIEF_SCREEN default was exported by the driver only, so every RB read "FAIL drift in
        environment" and no proof could start. Every BROTHER_ name the driver exports before proof-start is either a
        per run name the freeze leaves out (freeze_manifest.VOLATILE_ENV) or one proof_pair.sh puts into E first."""
        import freeze_manifest as F
        before = self.text[:self.text.index("proof-start")]
        exported = set(re.findall(r"^\s*export (BROTHER_[A-Z_]+)=", before, re.M))
        exported |= set(re.findall(r"\bexport (BROTHER_[A-Z_]+)=", before))
        self.assertIn("BROTHER_BRIEF_SCREEN", exported)
        with open(os.path.join(LOOP, "proof_pair.sh"), encoding="utf-8") as fh:
            pair = fh.read()
        put = set(re.findall(r"^\s*(?:get \w+ >/dev/null \|\| )?put (BROTHER_[A-Z_]+) ", pair, re.M))
        missing = sorted(exported - put - set(F.VOLATILE_ENV))
        self.assertEqual(missing, [], "the driver would add these to the frozen environment: %s" % missing)



class PairDefaultsEqualDriverDefaults(unittest.TestCase):
    """The pair puts each driver default itself so the freeze sees it; the VALUE must be the driver's own, or the
    proof measures a setup the loop does not run by default (review 2026-10-04: REPAIR_ADVISOR and BUILD_PLAN were
    put "on" while the driver defaulted them "off" since plan E step 2f)."""

    def test_every_pair_default_equals_the_driver_default(self):
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "loop", "loop_until.sh"), encoding="utf-8") as fh:
            driver = dict(re.findall(r'(BROTHER_[A-Z_]+)="\$\{\1:-([^}]*)\}"', fh.read()))
        with open(os.path.join(root, "loop", "proof_pair.sh"), encoding="utf-8") as fh:
            pair = dict(re.findall(r"get (BROTHER_[A-Z_]+) >/dev/null \|\| put \1 (\S+)", fh.read()))
        self.assertTrue(pair, "the pair puts no driver default: the parse found nothing")
        for name, value in sorted(pair.items()):
            self.assertIn(name, driver, "%s is put by the pair but has no driver default" % name)
            self.assertEqual(value, driver[name], "%s: pair puts %r, driver defaults %r" % (name, value, driver[name]))


class PairEnvironmentCarriesTheAccount(unittest.TestCase):
    """Claude finds its Keychain login by account name; the pair's env -i once kept only HOME, PATH and LANG, so the
    driver's refresh and every seat read "Not logged in" (proof pairs of 2026-10-04 and 2026-10-05)."""

    def test_the_pair_environment_keeps_user_and_logname(self):
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "loop", "proof_pair.sh"), encoding="utf-8") as fh:
            text = fh.read()
        line = next((l for l in text.splitlines() if "env -i HOME=" in l and "LAUNCH_ENV" not in l and "E+=" in l), "")
        self.assertTrue(line, "the env -i line that builds E was not found")
        self.assertIn('USER="', line)
        self.assertIn('LOGNAME="', line)

if __name__ == "__main__":
    unittest.main()
