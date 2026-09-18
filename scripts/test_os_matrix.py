"""What scripts/os_matrix.py must keep true: platform differences are
reported rather than assumed, and an unknown platform is NO-DATA, never
treated as POSIX. The property under test is NOT "capability() returns a
string for every input" (a module that defaulted every unknown pair to
"yes" would also pass that). It is that a pair this table does not pin
(an unrecognised platform, an unrecognised operation, or a known platform
with a deliberately unmeasured cell, such as Cygwin's three POSIX-shaped
rows) never comes back as "yes", and never gets silently attempted by a
caller relying on assumption_is_safe() as the gate.

Every platform and operation is INJECTED as a plain string, never read
from sys.platform, so this suite passes or fails identically whichever
machine runs it (the one exception, TestCurrentPlatform, checks only that
current_platform() equals sys.platform itself, never a hardcoded OS name).

Expected facts below are a SEPARATE pinned copy of the matrix, typed by
hand rather than imported from os_matrix.CAPABILITY_TABLE: comparing the
module's table against itself would pass even if every cell were silently
changed to "yes", since both sides of the comparison would move together.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import os_matrix as OM  # noqa: E402

# A second, independent copy of the expected facts, one entry per
# (platform, operation) pair this table claims to pin. Kept flat (not
# nested) so a single wrong cell shows up as one failing subtest naming
# exactly that pair, never a whole-dict diff to eyeball.
EXPECTED = {
    ("darwin", "process_group_kill"): "yes",
    ("darwin", "advisory_file_lock"): "yes",
    ("darwin", "hardcoded_tmp_path"): "yes",
    ("darwin", "which_lookup"): "yes",
    ("linux", "process_group_kill"): "yes",
    ("linux", "advisory_file_lock"): "yes",
    ("linux", "hardcoded_tmp_path"): "yes",
    ("linux", "which_lookup"): "yes",
    ("win32", "process_group_kill"): "no",
    ("win32", "advisory_file_lock"): "no",
    ("win32", "hardcoded_tmp_path"): "no",
    ("win32", "which_lookup"): "yes",
    # cygwin's three POSIX-shaped cells are pinned as "unmeasured", not
    # as any particular NO-DATA wording: the test asserts the NODATA
    # prefix, never the exact sentence, so a docstring-only rewording of
    # the reason never breaks this suite.
    ("cygwin", "which_lookup"): "yes",
}

#: The cygwin cells that must be NO-DATA (asserted by prefix, not by the
#: exact sentence in os_matrix.CAPABILITY_TABLE).
CYGWIN_NODATA_OPERATIONS = (
    "process_group_kill", "advisory_file_lock", "hardcoded_tmp_path",
)


class EveryPinnedCellMatchesAnIndependentExpectedMatrix(unittest.TestCase):
    """Enumerates every platform in OM.PLATFORMS against every operation in
    OM.OPERATIONS and checks the fact against EXPECTED above, a copy typed
    independently of the module under test."""

    def test_every_known_platform_and_operation_pair(self):
        for platform in OM.PLATFORMS:
            for operation in OM.OPERATIONS:
                with self.subTest(platform=platform, operation=operation):
                    fact = OM.capability(platform, operation)
                    if (platform, operation) in EXPECTED:
                        self.assertEqual(fact, EXPECTED[(platform, operation)])
                    else:
                        self.assertTrue(platform == "cygwin" and
                                         operation in CYGWIN_NODATA_OPERATIONS,
                                         "pair (%r, %r) is in neither EXPECTED "
                                         "nor the cygwin NO-DATA set; the test "
                                         "matrix and the module have drifted "
                                         "apart" % (platform, operation))
                        self.assertTrue(
                            fact.startswith(OM.NODATA),
                            "cygwin %r expected a NO-DATA fact, got %r"
                            % (operation, fact))

    def test_platforms_and_operations_constants_cover_exactly_what_the_test_expects(self):
        # Guards the enumeration itself: if PLATFORMS or OPERATIONS shrinks
        # or grows without this test file being updated, the coverage
        # claim above (every platform, every operation) would quietly
        # narrow. Caught here rather than by a pair silently never being
        # iterated.
        self.assertEqual(set(OM.PLATFORMS), {"darwin", "linux", "win32", "cygwin"})
        self.assertEqual(set(OM.OPERATIONS), {
            "process_group_kill", "advisory_file_lock",
            "hardcoded_tmp_path", "which_lookup",
        })


class UnknownPlatformIsNeverTreatedAsPosix(unittest.TestCase):
    """The deciding property, named directly: an unknown platform must
    never read as "yes" for a POSIX-shaped operation, must never raise,
    and must be distinguishable from a genuine "no"."""

    def test_unrecognised_platform_string_is_nodata_not_yes(self):
        fact = OM.capability("freebsd", "process_group_kill")
        self.assertTrue(fact.startswith(OM.NODATA))
        self.assertNotEqual(fact, "yes")
        self.assertIn("freebsd", fact)

    def test_unrecognised_platform_never_raises_for_any_operation(self):
        for operation in OM.OPERATIONS:
            with self.subTest(operation=operation):
                fact = OM.capability("plan9", operation)
                self.assertTrue(fact.startswith(OM.NODATA))

    def test_empty_string_platform_is_nodata(self):
        fact = OM.capability("", "which_lookup")
        self.assertTrue(fact.startswith(OM.NODATA))

    def test_none_platform_is_nodata_not_a_crash(self):
        fact = OM.capability(None, "which_lookup")
        self.assertTrue(fact.startswith(OM.NODATA))

    def test_non_string_platform_is_nodata_not_a_crash(self):
        fact = OM.capability(12345, "process_group_kill")
        self.assertTrue(fact.startswith(OM.NODATA))

    def test_unrecognised_operation_on_a_known_platform_is_nodata(self):
        fact = OM.capability("darwin", "reboot_host")
        self.assertTrue(fact.startswith(OM.NODATA))
        self.assertIn("reboot_host", fact)

    def test_none_operation_is_nodata_not_a_crash(self):
        fact = OM.capability("darwin", None)
        self.assertTrue(fact.startswith(OM.NODATA))


class AssumptionIsSafeNeverReadsUnknownAsPermission(unittest.TestCase):
    """assumption_is_safe() is the go/no-go gate a caller uses before
    attempting a POSIX-only operation. "we do not know" must never come
    back True."""

    def test_true_only_for_the_exact_string_yes(self):
        self.assertTrue(OM.assumption_is_safe("darwin", "process_group_kill"))
        self.assertTrue(OM.assumption_is_safe("linux", "advisory_file_lock"))

    def test_false_for_an_explicit_no(self):
        self.assertFalse(OM.assumption_is_safe("win32", "process_group_kill"))

    def test_false_for_unmeasured_cygwin_cell(self):
        self.assertFalse(OM.assumption_is_safe("cygwin", "advisory_file_lock"))

    def test_false_for_unknown_platform(self):
        self.assertFalse(OM.assumption_is_safe("freebsd", "which_lookup"))

    def test_false_for_unknown_operation(self):
        self.assertFalse(OM.assumption_is_safe("darwin", "reboot_host"))

    def test_true_for_which_lookup_on_every_known_platform(self):
        # The deliberate contrast case: an operation that is "yes"
        # everywhere this table has a row, so this suite proves the gate
        # can pass at all, not only refuse.
        for platform in OM.PLATFORMS:
            with self.subTest(platform=platform):
                self.assertTrue(OM.assumption_is_safe(platform, "which_lookup"))


class ReportCoversExactlyTheRequestedOperations(unittest.TestCase):
    def test_default_report_has_one_entry_per_operation(self):
        row = OM.report("darwin")
        self.assertEqual(set(row), set(OM.OPERATIONS))

    def test_report_on_unknown_platform_still_returns_every_key(self):
        row = OM.report("freebsd")
        self.assertEqual(set(row), set(OM.OPERATIONS))
        for operation, fact in row.items():
            with self.subTest(operation=operation):
                self.assertTrue(fact.startswith(OM.NODATA))

    def test_report_honours_a_narrowed_operations_list(self):
        row = OM.report("win32", operations=("which_lookup",))
        self.assertEqual(row, {"which_lookup": "yes"})


class FindingsNameEveryUnsafeOperationAndNothingElse(unittest.TestCase):
    def test_no_findings_on_darwin(self):
        self.assertEqual(OM.findings("darwin"), [])

    def test_no_findings_on_linux(self):
        self.assertEqual(OM.findings("linux"), [])

    def test_windows_finds_exactly_the_three_unsupported_operations(self):
        found = OM.findings("win32")
        self.assertEqual(len(found), 3)
        for operation in ("process_group_kill", "advisory_file_lock",
                          "hardcoded_tmp_path"):
            with self.subTest(operation=operation):
                self.assertTrue(any(operation in line for line in found))
        self.assertFalse(any("which_lookup" in line for line in found))

    def test_cygwin_finds_exactly_the_three_unmeasured_operations(self):
        found = OM.findings("cygwin")
        self.assertEqual(len(found), 3)
        self.assertFalse(any("which_lookup" in line for line in found))

    def test_unknown_platform_finds_every_named_operation(self):
        found = OM.findings("plan9")
        self.assertEqual(len(found), len(OM.OPERATIONS))

    def test_finding_text_names_operation_and_platform(self):
        found = OM.findings("win32", operations=("process_group_kill",))
        self.assertEqual(len(found), 1)
        self.assertIn("process_group_kill", found[0])
        self.assertIn("win32", found[0])


class CurrentPlatformReadsTheLiveMachineOnly(unittest.TestCase):
    """The one function this suite does not, and cannot, pin: it must
    equal sys.platform on whatever machine runs the suite, never a
    hardcoded OS name."""

    def test_equals_sys_platform(self):
        self.assertEqual(OM.current_platform(), sys.platform)


class MainPrintsJsonAndReturnsZero(unittest.TestCase):
    def _capture(self, argv):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = OM.main(argv)
        return code, buf.getvalue()

    def test_explicit_platform_argument_is_used_verbatim(self):
        code, out = self._capture(["win32"])
        self.assertEqual(code, 0)
        import json
        payload = json.loads(out)
        self.assertEqual(payload["platform"], "win32")
        self.assertEqual(payload["capabilities"], OM.report("win32"))

    def test_no_argument_falls_back_to_current_platform(self):
        code, out = self._capture([])
        self.assertEqual(code, 0)
        import json
        payload = json.loads(out)
        self.assertEqual(payload["platform"], OM.current_platform())

    def test_unknown_platform_argument_still_exits_zero_with_findings_visible(self):
        # main() reports, it never refuses: an unknown platform is a fact
        # to print (every capability NO-DATA), not a crash.
        code, out = self._capture(["plan9"])
        self.assertEqual(code, 0)
        import json
        payload = json.loads(out)
        for fact in payload["capabilities"].values():
            self.assertTrue(fact.startswith(OM.NODATA))


# Standing edge list, the rest: "a concurrent second actor", "expired or
# stale", "already done", "partially done" and "the actor is the same as
# last time" are OUT OF SCOPE for this module. os_matrix holds no store,
# no lock and no persisted state: every call reads a pinned in-memory
# table and returns a fresh value, so there is nothing here that can be
# stale, partially done, or contended by a second actor. "Many" and "exactly
# one" are covered by the full-enumeration test above (every platform times
# every operation) and the single-operation report()/findings() cases.
# "Corrupt or truncated input" is covered by the non-string and None
# platform/operation cases: nothing here parses a byte stream that could
# truncate.


if __name__ == "__main__":
    unittest.main()
