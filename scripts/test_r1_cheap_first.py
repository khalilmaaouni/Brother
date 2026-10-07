"""R1.2 cheap first and horizon clocks for scripts/cut_preflight.py.

The module under test transitively imports a collaborator that this suite
is not allowed to load, so this suite reads the module as bytes and asserts
the R1.2 patterns and their order directly, without importing or executing
the module. Every assertion below names a pattern this slice adds to the
module, so the suite is red on the unchanged tree.
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "cut_preflight.py")


def source_text():
    """The module under test as bytes, decoded as utf-8. A file this suite
    cannot read or decode is an error, never a silent empty string."""
    try:
        with open(SOURCE, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise AssertionError("cannot read the module under test: %s" % exc)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError("the module under test is not utf-8: %s" % exc)


def function_body(name):
    """The source of top level function `name`, its body only, up to the
    next top level def. A missing def is an error, never an empty string."""
    text = source_text()
    marker = "\ndef %s(" % name
    i = text.find(marker)
    if i == -1:
        raise AssertionError("no top level def %s in the module" % name)
    i += 1
    j = text.find("\ndef ", i + 1)
    return text[i:j] if j != -1 else text[i:]


class RunAllRefusesHostileInput(unittest.TestCase):
    """RQ-008: corrupt, missing or unrecognized input BLOCKS with a
    ValueError before any check runs, never a silent accept and never a raw
    crash from the interpreter."""

    def test_root_refused_before_any_check(self):
        body = function_body("run_all")
        self.assertIn("if not isinstance(root, str) or not root:", body)
        self.assertIn("raise ValueError(\"root must be a non-empty string", body)

    def test_version_refused_before_any_check(self):
        body = function_body("run_all")
        self.assertIn("if not isinstance(version, str) or not version:", body)
        self.assertIn("raise ValueError(\"version must be a non-empty string", body)

    def test_now_refused_unless_a_datetime(self):
        body = function_body("run_all")
        self.assertIn(
            "if now is not None and not isinstance(now, datetime.datetime):",
            body)

    def test_a_bool_horizon_is_refused_because_True_reads_as_one_hour(self):
        text = source_text()
        self.assertIn("def _finite_hours(value):", text)
        self.assertIn("isinstance(value, bool)", text)

    def test_a_nan_or_infinite_horizon_is_refused(self):
        text = source_text()
        self.assertIn("value != value", text)
        self.assertIn("float(\"inf\")", text)
        self.assertIn("float(\"-inf\")", text)

    def test_run_all_routes_horizon_through_finite_hours(self):
        body = function_body("run_all")
        self.assertIn("_finite_hours(horizon_hours)", body)

    def test_check_pack_clock_routes_horizon_through_finite_hours(self):
        body = function_body("check_pack_clock")
        self.assertIn("_finite_hours(horizon_hours)", body)


class CheapestFirst(unittest.TestCase):
    """RQ-005: cheap refusals block before the minutes long gate starts, and
    the local commit text read runs before the network read of the remote."""

    def test_the_local_read_runs_before_the_network_read(self):
        body = function_body("run_all")
        local = body.index(
            "check_changelog_text(root, previous_cut_commit(root, version), runner)")
        network = body.index(
            "check_public_remote(root, version, remote, runner)")
        self.assertLess(local, network)

    def test_a_cheap_refusal_stops_the_minutes_long_gate(self):
        body = function_body("run_all")
        self.assertIn(
            "if virgin and not any(r[0] == REFUSED for r in results):", body)


class HorizonClocks(unittest.TestCase):
    """RQ-006: clock gates are read as of now plus horizon, and a clock
    reading that is not a datetime is refused before any command starts."""

    def test_check_exceptions_refuses_a_non_datetime_at(self):
        body = function_body("check_exceptions")
        self.assertIn("if not isinstance(at, datetime.datetime):", body)

    def test_check_readiness_rows_refuses_a_non_datetime_at(self):
        body = function_body("check_readiness_rows")
        self.assertIn("if not isinstance(at, datetime.datetime):", body)

    def test_run_all_builds_at_as_now_plus_horizon(self):
        body = function_body("run_all")
        self.assertIn(
            "at = now + datetime.timedelta(hours=horizon_hours)", body)

    def test_horizon_default_is_six_hours(self):
        text = source_text()
        self.assertIn("DEFAULT_HORIZON_HOURS = 6", text)

    def test_pack_max_age_is_twenty_four_hours(self):
        text = source_text()
        self.assertIn("PACK_MAX_AGE_HOURS = 24", text)


if __name__ == "__main__":
    unittest.main()
