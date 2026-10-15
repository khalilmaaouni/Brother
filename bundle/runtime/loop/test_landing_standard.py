#!/usr/bin/env python3
"""LS1.a: the four landing standard rules (scripts/loop/landing_standard.py), one fixture per guard.

Run: python3 -B scripts/loop/test_landing_standard.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import landing_standard as L  # noqa: E402


class OwnTest(unittest.TestCase):
    def test_the_rl3a_shape_is_refused(self):
        """RL3.a's done check ran RL3.b's test: a test the section does not name."""
        why = L.own_test_refusal("python3 -B scripts/test_rl3b.py", ["scripts/rl3a.py", "scripts/test_rl3a.py"])
        self.assertIn("OWN TEST", why)
        self.assertIn("scripts/test_rl3b.py", why)

    def test_a_named_test_passes(self):
        self.assertEqual(L.own_test_refusal("python3 -B scripts/test_rl3a.py", ["scripts/test_rl3a.py"]), "")

    def test_a_chain_whose_second_test_is_foreign_is_refused(self):
        cmd = "python3 -B scripts/test_a.py && /usr/bin/python3 -B scripts/test_b.py"
        self.assertIn("scripts/test_b.py", L.own_test_refusal(cmd, ["scripts/test_a.py"]))

    def test_a_dot_slash_path_is_normalized(self):
        self.assertEqual(L.own_test_refusal("python3 -B ./scripts/test_a.py", ["scripts/test_a.py"]), "")

    def test_no_done_check_fails_closed(self):
        for missing in (None, "", "   ", 7):
            with self.subTest(done=missing):
                self.assertEqual(L.own_test_refusal(missing, ["scripts/test_a.py"]), "OWN TEST: NO-DATA, no done check")

    def test_empty_section_paths_refuse_any_test(self):
        self.assertIn("OWN TEST", L.own_test_refusal("python3 -B scripts/test_a.py", []))


class Mirrored(unittest.TestCase):
    MIRRORED = {"bundle/runtime/loop/start_run.sh"}

    def test_a_loop_built_section_naming_a_mirrored_file_is_accepted(self):
        """LM1.c: the landing regenerates the mirror, so a mirrored source is no longer refused."""
        self.assertEqual(L.mirrored_refusal(["scripts/loop/start_run.sh"], self.MIRRORED, True), "")

    def test_a_loop_built_section_naming_bundle_itself_is_refused(self):
        self.assertIn("MIRRORED", L.mirrored_refusal(["bundle/skills/x/SKILL.md"], set(), True))

    def test_a_session_built_section_is_never_refused(self):
        self.assertEqual(L.mirrored_refusal(["scripts/loop/start_run.sh"], self.MIRRORED, False), "")

    def test_an_unmirrored_file_passes(self):
        self.assertEqual(L.mirrored_refusal(["scripts/run_evidence.py"], self.MIRRORED, True), "")

    def test_an_empty_mirror_set_refuses_only_bundle_paths(self):
        """Nothing is mirrored as far as the caller knows: only a literal bundle/ path is refused."""
        self.assertEqual(L.mirrored_refusal(["scripts/loop/start_run.sh"], set(), True), "")


class Checklist(unittest.TestCase):
    def test_missing_keys_are_named(self):
        section = "\n".join("%s yes" % k for k in L.CHECKLIST if k not in ("S5", "S9"))
        self.assertEqual(L.checklist_missing(section), ["S5", "S9"])

    def test_a_full_checklist_misses_nothing(self):
        self.assertEqual(L.checklist_missing("\n".join("%s ok" % k for k in L.CHECKLIST)), [])

    def test_s1_does_not_count_for_s10_to_s12(self):
        self.assertEqual(L.checklist_missing("S1 yes"), [k for k in L.CHECKLIST if k != "S1"])

    def test_keys_in_a_code_fence_count(self):
        self.assertEqual(L.checklist_missing("```\n" + "\n".join("%s x" % k for k in L.CHECKLIST) + "\n```"), [])

    def test_no_section_misses_everything(self):
        self.assertEqual(L.checklist_missing(None), list(L.CHECKLIST))


class SharedFile(unittest.TestCase):
    def test_two_units_on_one_file_the_later_waits(self):
        why = L.shared_file_hold("N5", ["scripts/x.py"], {"N12": {"scripts/x.py", "scripts/y.py"}})
        self.assertEqual(why, "S3: N5 shares scripts/x.py with N12 in flight")

    def test_disjoint_units_both_run(self):
        self.assertEqual(L.shared_file_hold("N5", ["scripts/a.py"], {"N12": {"scripts/b.py"}}), "")

    def test_a_unit_never_blocks_itself(self):
        self.assertEqual(L.shared_file_hold("N5", ["scripts/x.py"], {"N5": {"scripts/x.py"}}), "")

    def test_nothing_in_flight_holds_nothing(self):
        self.assertEqual(L.shared_file_hold("N5", ["scripts/x.py"], {}), "")


if __name__ == "__main__":
    unittest.main()
