"""What the installed-surface check must keep true.

This closes R11's second clause, which was unassertable before bundle/MANIFEST
existed. The tests that matter are the ones proving it can go RED: a check that
only ever passes would leave clause two exactly as unchecked as it was, while
looking closed.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_installed_surface as C  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


DETAILS = """brothermode 3.4.2
  Some description

Component inventory
  Skills (3)  alpha, beta, gamma
  Agents (5)  reviewer, builder
"""

MANIFEST = {"shipped_plugins": ["brothermode"],
            "entries": {"brothermode": ["alpha", "beta", "gamma"]},
            "total": 3}


def workspace(manifest, logs):
    d = tempfile.mkdtemp(prefix="surface-")
    mpath = os.path.join(d, "MANIFEST.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    for plugin, text in logs.items():
        with open(os.path.join(d, "details-%s.log" % plugin), "w",
                  encoding="utf-8") as fh:
            fh.write(text)
    return mpath, d


class ItReadsTheListingItWasActuallyGiven(unittest.TestCase):
    def test_the_entry_names_come_out_of_the_skills_line(self):
        names, problem = C.parse_details(DETAILS)
        self.assertEqual(names, {"alpha", "beta", "gamma"}, problem)

    def test_a_listing_with_no_skills_line_is_a_problem_not_an_empty_set(self):
        """An empty set would report every promised entry as missing, which
        looks like a catastrophic install failure when the real fault is that
        this parser stopped understanding the output."""
        names, problem = C.parse_details("brothermode 3.4.2\n  nothing here\n")
        self.assertIsNone(names)
        self.assertIn("could not be read", problem)

    def test_a_declared_count_that_disagrees_with_the_names_is_a_problem(self):
        """The count is captured precisely so a listing that changes shape fails
        to parse rather than silently matching fewer names than it claims."""
        names, problem = C.parse_details("  Skills (9)  alpha, beta\n")
        self.assertIsNone(names)
        self.assertIn("parsed wrongly", problem)


class ItCanGoRed(unittest.TestCase):
    """The tests that make clause two a check rather than a decoration."""

    def test_a_missing_entry_FAILS_and_names_it(self):
        broken = DETAILS.replace("Skills (3)  alpha, beta, gamma",
                                 "Skills (2)  alpha, beta")
        mpath, d = workspace(MANIFEST, {"brothermode": broken})
        self.assertEqual(C.main(["--manifest", mpath, "--details-dir", d]),
                         C.EXIT_MISSING)

    def test_a_RENAMED_entry_fails_even_though_the_COUNT_still_matches(self):
        """The reason this compares names. A count passes when one entry is
        renamed and another added, which is exactly the drift an install check
        should catch."""
        renamed = DETAILS.replace("alpha, beta, gamma", "alpha, beta, GAMMA")
        mpath, d = workspace(MANIFEST, {"brothermode": renamed})
        self.assertEqual(C.main(["--manifest", mpath, "--details-dir", d]),
                         C.EXIT_MISSING)

    def test_an_install_that_delivers_everything_PASSES(self):
        mpath, d = workspace(MANIFEST, {"brothermode": DETAILS})
        self.assertEqual(C.main(["--manifest", mpath, "--details-dir", d]),
                         C.EXIT_MATCH)


class ExtraEntriesAreReportedAndDoNotFail(unittest.TestCase):
    def test_more_than_promised_still_passes(self):
        """The manifest counts the USER-INVOCABLE surface, and a plugin
        registers entries that are not typeable, so an install legitimately
        carries more. Measured on the real install: exactly the four brothermode
        skills marked user-invocable false."""
        more = DETAILS.replace("Skills (3)  alpha, beta, gamma",
                               "Skills (4)  alpha, beta, gamma, internal")
        mpath, d = workspace(MANIFEST, {"brothermode": more})
        self.assertEqual(C.main(["--manifest", mpath, "--details-dir", d]),
                         C.EXIT_MATCH)

    def test_the_extras_are_NAMED_so_the_difference_stays_visible(self):
        more = DETAILS.replace("Skills (3)  alpha, beta, gamma",
                               "Skills (4)  alpha, beta, gamma, internal")
        _, extra = C.compare(MANIFEST, {"brothermode":
                                        {"alpha", "beta", "gamma", "internal"}})
        self.assertEqual(extra, {"brothermode": ["internal"]})


class NoDataIsNeverAPass(unittest.TestCase):
    def test_a_plugin_with_no_details_log_is_NO_DATA(self):
        mpath, d = workspace(MANIFEST, {})
        self.assertEqual(C.main(["--manifest", mpath, "--details-dir", d]),
                         C.EXIT_NO_DATA)

    def test_an_unparseable_log_is_NO_DATA_and_not_a_missing_install(self):
        """Reading an unparseable log as an empty set would turn a broken
        parser into a catastrophic install failure, and a broken install into a
        clean one on the day the format changes the other way."""
        mpath, d = workspace(MANIFEST, {"brothermode": "nothing useful"})
        self.assertEqual(C.main(["--manifest", mpath, "--details-dir", d]),
                         C.EXIT_NO_DATA)

    def test_an_unreadable_manifest_is_NO_DATA(self):
        self.assertEqual(C.main(["--manifest", "/no/such/m.json",
                                 "--details-dir", "/tmp"]),
                         C.EXIT_NO_DATA)

    def test_the_three_exit_codes_are_three_distinct_values(self):
        self.assertEqual(len({C.EXIT_MATCH, C.EXIT_MISSING, C.EXIT_NO_DATA}), 3)


if __name__ == "__main__":
    unittest.main()
