"""R0 anti-drift: a document may not claim a control the control does not implement.

WHY THIS EXISTS. On 2026-08-24 the public README said "The caps
tests/test_surface.py enforces today:" and listed four numbers. That file
enforces none of them; the caps had been withdrawn two days earlier. Nobody had
lied. A claim was written once, the control changed underneath it, and NOTHING
COMPARED THE TWO. Fixing the sentence without adding this file would leave the
next drift exactly as undetectable as that one was.

WHAT THIS ASSERTS. Two invariants, both cheap and both about agreement between
a document and a file rather than about the document's prose.

  1. Every file named in the charter's "Enforced, by a file, today" list must
     EXIST. A control named in that list that is not on disk is the strongest
     possible form of this defect.
  2. The README's surface-caps claim and the surface test must agree. If the
     README says the caps are enforced, the test must contain a cap assertion.
     If the test contains no cap assertion, the README must say UNENFORCED.
     The pair moves together or this fails.

WHAT IT DOES NOT DO, stated rather than discovered later. It cannot read prose
for meaning, so it catches the two shapes that already bit rather than the
class. It is a regression pin, not a general truth checker, and a new claim in
a new sentence will not be caught by it.
"""
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
README = REPO / "README.md"
CHARTER = REPO / "docs" / "CHARTER.md"
SURFACE = REPO / "tests" / "test_surface.py"


def read(p):
    with p.open(encoding="utf-8") as fh:
        return fh.read()


class TestCharterNamesRealFiles(unittest.TestCase):
    """A control named as enforcing something must be a file that exists."""

    def test_enforced_list_names_existing_files(self):
        text = read(CHARTER)
        start = text.find("**Enforced, by a file, today:**")
        self.assertNotEqual(start, -1, "the charter's enforced list has been renamed or removed")
        end = text.find("**Stated as discipline", start)
        self.assertNotEqual(end, -1, "the charter's discipline list has been renamed or removed")
        block = text[start:end]

        named = set(re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|sh))`", block))
        self.assertTrue(named, "no enforcing file is named in the enforced list at all")
        missing = sorted(n for n in named if not (REPO / n).exists())
        self.assertEqual(
            missing, [],
            "the charter says these files enforce something and they are not on disk: %s"
            % ", ".join(missing),
        )


class TestCapClaimMatchesTheTest(unittest.TestCase):
    """The README's claim about the surface test must match that test's contents."""

    # A cap assertion would have to compare a count against one of these numbers.
    CAP_NUMBERS = ("9", "4", "13", "5", "31")

    def _test_counts_surfaces(self):
        src = read(SURFACE)
        # Look for an assertion comparing something to a cap number.
        for pat in (r"assertLessEqual\s*\(", r"assertLess\s*\(", r"<=\s*(9|4|13|5|31)\b"):
            if re.search(pat, src):
                return True
        return False

    def test_readme_and_surface_test_agree(self):
        readme = read(README)
        claims_enforced = "The caps `tests/test_surface.py` enforces today" in readme
        says_unenforced = "There is no headcount cap" in readme
        counts = self._test_counts_surfaces()

        if counts:
            self.assertTrue(
                claims_enforced,
                "tests/test_surface.py now counts surfaces, so the README must say so "
                "instead of calling the caps UNENFORCED. The pair moves together.",
            )
        else:
            self.assertFalse(
                claims_enforced,
                "the README claims tests/test_surface.py enforces the surface caps, and "
                "that file contains no cap assertion. This is the exact defect of "
                "2026-08-24: a claim written once while the control changed underneath it.",
            )
            self.assertTrue(
                says_unenforced,
                "the caps are not enforced by any file, so the README must say UNENFORCED "
                "in those words. A rule is not a control unless a file enforces it.",
            )


class TestUmbrellaVersionAgrees(unittest.TestCase):
    """Brother's own version must read the same in every place it is declared.

    leaf_pin_check.py checks the LEAVES against what they published. Nothing
    checked the umbrella against itself, so its two declaration sites could
    drift apart exactly the way the caps claim drifted from its test. They are
    both in this repository, so this is cheap and there is no excuse for it.
    """

    MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
    BUNDLE = REPO / "bundle" / ".claude-plugin" / "plugin.json"

    def _marketplace_version(self):
        import json
        with self.MARKETPLACE.open(encoding="utf-8") as fh:
            d = json.load(fh)
        for plug in d.get("plugins", []):
            if plug.get("name") == "brother":
                return plug.get("version")
        return None

    def _bundle_version(self):
        import json
        with self.BUNDLE.open(encoding="utf-8") as fh:
            return json.load(fh).get("version")

    def test_both_sites_declare_a_version(self):
        self.assertIsNotNone(self._marketplace_version(),
                             "the marketplace no longer declares a version for `brother`")
        self.assertIsNotNone(self._bundle_version(),
                             "the bundle manifest no longer declares a version")

    def test_the_two_declarations_agree(self):
        m, b = self._marketplace_version(), self._bundle_version()
        if m is None or b is None:
            self.skipTest("NO-DATA: a declaration site is missing, covered by the test above")
        self.assertEqual(
            m, b,
            "the umbrella declares two different versions of itself: marketplace.json "
            "says %r and bundle/.claude-plugin/plugin.json says %r. One product, one "
            "version, or a reader cannot tell which is true." % (m, b),
        )

    def test_still_zero_dot_x_until_stage_one(self):
        """The README promises 1.0.0 is EARNED at Stage 1, not assigned ahead of it."""
        b = self._bundle_version()
        if b is None:
            self.skipTest("NO-DATA: no bundle version to check")
        readme = read(README)
        promises_earned = "It earns 1.0.0 the moment Stage 1 actually completes" in readme
        if promises_earned:
            self.assertTrue(
                b.startswith("0."),
                "the README still promises 1.0.0 is EARNED at Stage 1 completion, and the "
                "bundle already declares %r. Either Stage 1 completed and the README must "
                "say so, or the version was assigned ahead of the evidence." % b,
            )


class TestHookTestsCoverTheirRegistration(unittest.TestCase):
    """A hook's TEST surface must be its REGISTRATION surface.

    WHY THIS EXISTS. On 2026-08-24 the repeat guard was found to record every
    successful Edit and Write as a FAILURE. The one line cause is a typo-class
    mistake anyone makes. The reason nobody caught it for as long as it existed
    is the shape this asserts against: the hook is registered on four tools and
    its 13 case suite exercised ONE of them, and the three untested tools were
    exactly the three that were broken.

    A suite that exercises a quarter of a hook's registration surface cannot see
    a defect living in the other three quarters, however many cases it has.

    SCOPE, stated plainly: this checks hooks that live in THIS repository
    against THIS repository's tests. It says nothing about hooks installed from
    elsewhere on a machine, which are not ours to test.
    """

    GUARD = REPO / "tools" / "repeat-guard" / "repeat_guard.py"
    GUARD_TESTS = REPO / "tools" / "repeat-guard" / "test_repeat_guard.py"

    # The tools the repeat guard is registered on, taken from the installer it
    # ships with rather than from a machine's settings, so a clone can check it.
    INSTALLER = REPO / "tools" / "repeat-guard" / "install.sh"

    def _registered_tools(self):
        if not self.INSTALLER.exists():
            return None
        with self.INSTALLER.open(encoding="utf-8") as fh:
            text = fh.read()
        # The installer assigns it as a python variable and then uses that
        # variable in the JSON it writes, so match the assignment, not the key.
        m = re.search(r'matcher\s*=\s*"([A-Za-z|]+)"', text)
        if not m:
            m = re.search(r'"matcher"\s*:\s*"([A-Za-z|]+)"', text)
        if not m:
            return None
        return {t for t in m.group(1).split("|") if t}

    def _tested_tools(self):
        with self.GUARD_TESTS.open(encoding="utf-8") as fh:
            text = fh.read()
        return set(re.findall(r'"tool_name"\s*:\s*"([A-Za-z]+)"', text))

    def test_every_registered_tool_appears_in_the_suite(self):
        registered = self._registered_tools()
        if registered is None:
            self.skipTest(
                "NO-DATA: could not read a matcher out of %s, so this cannot say "
                "what the hook is registered on" % self.INSTALLER
            )
        tested = self._tested_tools()
        self.assertTrue(tested, "the guard suite exercises no tool_name at all")
        missing = sorted(registered - tested)
        self.assertEqual(
            missing, [],
            "the repeat guard is registered on %s and its suite never builds a "
            "payload for %s. That gap is how a control passed 13 cases while "
            "being broken on three of the four tools it guards."
            % (", ".join(sorted(registered)), ", ".join(missing)),
        )


if __name__ == "__main__":
    unittest.main()
