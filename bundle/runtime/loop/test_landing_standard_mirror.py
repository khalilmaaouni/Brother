#!/usr/bin/env python3
"""LM1.c: S4 rewritten. A loop built section may name a mirrored source; it may never name bundle/ itself.

Drives scripts/loop/landing_standard.mirrored_refusal (the scorer's one call, spec_score.py) and the S4 paragraph in
docs/plan/specs/LOOP-LANDING-STANDARD.md. Pure calls; the real mirror list case reads `git ls-files bundle/runtime`
read only and skips (NO-DATA, never a pass) when git cannot run.

Run: python3 -B scripts/loop/test_landing_standard_mirror.py
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import landing_standard as L  # noqa: E402

STANDARD = os.path.join(ROOT, "docs", "plan", "specs", "LOOP-LANDING-STANDARD.md")
TWIN = {"bundle/runtime/loop/start_run.sh"}


class Mirror(unittest.TestCase):
    def test_a_loop_built_section_naming_a_mirrored_source_is_accepted(self):
        self.assertEqual(L.mirrored_refusal(["scripts/loop/start_run.sh"], TWIN, True), "")

    def test_a_loop_built_section_naming_bundle_itself_is_refused_in_every_spelling(self):
        for spelling in ("bundle/runtime/loop/x.py", "bundle", "Bundle/Runtime/x", "./bundle/x",
                         "scripts/../bundle/x", "  bundle/skills/x/SKILL.md  "):
            with self.subTest(path=spelling):
                why = L.mirrored_refusal([spelling], set(), True)
                self.assertTrue(why.startswith("MIRRORED: "), why)
                self.assertIn("(S4): " + L.BUNDLE_REMEDY, why)

    def test_the_refusal_text_is_exact(self):
        self.assertEqual(L.mirrored_refusal(["bundle/runtime/x.py"], TWIN, True),
                         "MIRRORED: bundle/runtime/x.py is under bundle/ (S4): " + L.BUNDLE_REMEDY)

    def test_a_session_built_section_is_never_refused(self):
        for built in (False, None, 0, ""):
            with self.subTest(loop_built=built):
                self.assertEqual(L.mirrored_refusal(["bundle/runtime/x.py", "scripts/loop/start_run.sh"], TWIN, built), "")

    def test_a_mixed_section_names_the_bundle_path(self):
        paths = ["scripts/loop/a%d.py" % i for i in range(10)] + ["bundle/hooks/hooks.json"]
        why = L.mirrored_refusal(paths, TWIN, True)
        self.assertIn("bundle/hooks/hooks.json", why)
        self.assertNotIn("scripts/loop/a", why)

    def test_the_first_bundle_path_wins(self):
        why = L.mirrored_refusal(["scripts/loop/x.py", "bundle/b.txt", "bundle/c.txt"], set(), True)
        self.assertIn("bundle/b.txt", why)
        self.assertNotIn("bundle/c.txt", why)

    def test_a_lookalike_folder_is_not_refused(self):
        for path in ("bundles/x", "scripts/bundle/x", "docs/bundle.md", "bundle.py", "scripts/loop/bundle_check.py"):
            with self.subTest(path=path):
                self.assertEqual(L.mirrored_refusal([path], set(), True), "")

    def test_hostile_inputs_never_crash(self):
        self.assertEqual(L.mirrored_refusal([], None, True), "")
        self.assertEqual(L.mirrored_refusal(None, None, True), "")
        self.assertEqual(L.mirrored_refusal([None, 5, ""], None, True), "")
        self.assertEqual(L.mirrored_refusal(5, 5, True), "")
        self.assertEqual(L.mirrored_refusal(["scripts/loop/start_run.sh"], None, True), "")
        self.assertIn("MIRRORED", L.mirrored_refusal("bundle/x", None, True))
        self.assertIn("MIRRORED", L.mirrored_refusal(iter(["a.py", "bundle/x"]), None, True))

    def test_the_mirror_set_is_ignored(self):
        paths = ["scripts/loop/start_run.sh"]
        self.assertEqual(L.mirrored_refusal(paths, TWIN, True), L.mirrored_refusal(paths, set(), True))

    def test_the_same_call_twice_returns_the_same_text(self):
        self.assertEqual(L.mirrored_refusal(["bundle/x"], TWIN, True), L.mirrored_refusal(["bundle/x"], TWIN, True))

    def test_a_section_already_rewritten_to_the_source_passes(self):
        self.assertEqual(L.mirrored_refusal(["scripts/loop/start_run.sh", "scripts/loop/test_start_run.py"], TWIN, True), "")


class Standard(unittest.TestCase):
    def test_the_standard_s4_text_says_the_landing_regenerates_the_mirror(self):
        with open(STANDARD, encoding="utf-8") as f:
            text = f.read()
        flat = " ".join(text.split())
        self.assertIn("S4. **A loop built sub unit never writes bundle/.**", flat)
        self.assertIn("the landing regenerates the mirror in its copy", flat)
        self.assertNotIn("The grader cannot regenerate", flat)
        self.assertIn("S4 bundle path ......... none", text)
        self.assertNotIn("S4 mirrored file", text)

    def test_the_checklist_key_still_reads_as_s4(self):
        self.assertEqual(L.checklist_missing("S4 bundle path ......... none"), [k for k in L.CHECKLIST if k != "S4"])


class RealList(unittest.TestCase):
    def test_the_real_mirror_list_does_not_refuse_the_loop_sources(self):
        try:
            out = subprocess.run(["git", "ls-files", "bundle/runtime"], cwd=ROOT, capture_output=True, text=True,
                                 timeout=60, check=True, stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest("NO-DATA: git ls-files bundle/runtime could not run (%s)" % type(exc).__name__)
        mirrored = {line.strip() for line in out.splitlines() if line.strip()}
        if not mirrored:
            self.skipTest("NO-DATA: git ls-files bundle/runtime listed nothing")
        sources = ["scripts/" + m[len("bundle/runtime/"):] for m in sorted(mirrored)]
        self.assertTrue(any(s.startswith("scripts/loop/") for s in sources))
        for src in sources:
            self.assertEqual(L.mirrored_refusal([src], mirrored, True), "", src)


if __name__ == "__main__":
    unittest.main()
