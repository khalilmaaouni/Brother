#!/usr/bin/env python3
"""Test for mobile_toolchain_probe.py (EPIC M1.02). Every fixture here is a
synthetic, throwaway directory or a monkeypatch of shutil.which; no real
project's files or names are read or referenced anywhere in this file."""
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_toolchain_probe as MTP  # noqa: E402
import contract_check as CC  # noqa: E402


class MobileToolchainProbeTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mobile-toolchain-probe-test-")
        self.schema = CC.load_json(
            MTP.DEFAULT_SCHEMA, "mobile-toolchain-probe-v1 schema")

    def test_real_probe_is_internally_consistent(self):
        # Never asserts which tools ARE installed -- CI machines vary. Only
        # asserts the record is well-formed and self-consistent.
        record = MTP.probe()
        self.assertEqual(record["schema_version"], "mobile-toolchain-probe-v1")
        self.assertIn("probed_at_revision", record)
        self.assertEqual(
            sorted(record["tools"].keys()),
            ["adb", "gradle", "javac", "simctl", "xcodebuild", "xcresulttool"])
        for name, entry in record["tools"].items():
            self.assertIn(entry["status"], ("found", "no-data"), name)
            if entry["status"] == "found":
                self.assertNotEqual(entry["resolved_path"], "NO-DATA", name)
                self.assertNotEqual(entry["version"], "NO-DATA", name)
            else:
                self.assertEqual(entry["resolved_path"], "NO-DATA", name)
                self.assertEqual(entry["version"], "NO-DATA", name)
        self.assertIn(
            record["ios_toolchain_status"], ("complete", "partial", "none"))
        self.assertIn(
            record["android_toolchain_status"], ("complete", "partial", "none"))
        self.assertEqual(MTP.hand_rules(record), [])
        self.assertEqual(MTP.check(record, self.schema), [])

    def test_probe_has_no_input_surface_a_project_could_use_to_redirect_identity(self):
        # THE CORE INVARIANT (M1.02's own words): "record actual tool
        # versions and executable paths without allowing the project
        # profile to redirect trusted tool identity." The enforcement is
        # structural, not a runtime check: probe() accepts zero
        # parameters, so nothing -- not a project_dir, not a project
        # profile record, not a "hint" -- has any channel into tool
        # resolution. Prove the signature itself carries no such
        # parameter, and that passing one is refused outright.
        sig = inspect.signature(MTP.probe)
        self.assertEqual(
            len(sig.parameters), 0,
            "probe() must take no parameters at all; any parameter is a "
            "channel a caller (or a project profile) could use to "
            "redirect trusted tool identity")
        with self.assertRaises(TypeError):
            MTP.probe(project_dir=self.tmp)
        with self.assertRaises(TypeError):
            MTP.probe(tool_paths={"xcodebuild": "/tmp/evil/xcodebuild"})

    def test_a_planted_fake_binary_never_becomes_the_resolved_tool(self):
        # Belt-and-suspenders on top of the signature proof above: plant
        # an executable literally named "xcodebuild" in a throwaway
        # directory that is never placed on PATH and never named to
        # probe() in any way, then confirm it never appears as any
        # tool's resolved_path or leaks its fake version string. This
        # does NOT prove immunity to a caller who deliberately prepends a
        # hostile directory to their own process PATH before calling
        # probe() -- that is a compromised-environment scenario the
        # module's own docstring ("SCOPE OF THE GUARANTEE") explicitly
        # places out of this unit's scope, exactly like every other
        # PATH-trusting subprocess call in this codebase (including the
        # sibling module's own git resolution).
        fake_dir = tempfile.mkdtemp(prefix="mobile-toolchain-probe-fake-")
        fake_path = os.path.join(fake_dir, "xcodebuild")
        with open(fake_path, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\necho "FAKE 999.999"\n')
        os.chmod(fake_path, 0o755)

        record = MTP.probe()
        for name, entry in record["tools"].items():
            self.assertNotEqual(
                entry["resolved_path"], fake_path,
                "tool %s resolved to the planted fake!" % name)
            self.assertNotIn(
                fake_dir, entry["resolved_path"],
                "tool %s resolved into the fake dir!" % name)
            self.assertNotIn(
                "FAKE 999.999", entry["version"],
                "tool %s reported the planted fake version!" % name)

    def test_missing_tool_reports_no_data_for_exactly_that_tool(self):
        # Monkeypatch shutil.which to pretend exactly one tool (gradle) is
        # missing; every other tool must be left alone, and the record
        # must still satisfy hand_rules and the schema.
        real_which = shutil.which

        def fake_which(name, *args, **kwargs):
            if name == "gradle":
                return None
            return real_which(name, *args, **kwargs)

        with mock.patch("shutil.which", side_effect=fake_which):
            record = MTP.probe()

        gradle = record["tools"]["gradle"]
        self.assertEqual(gradle["status"], "no-data")
        self.assertEqual(gradle["resolved_path"], "NO-DATA")
        self.assertEqual(gradle["version"], "NO-DATA")
        self.assertEqual(MTP.hand_rules(record), [])
        self.assertEqual(MTP.check(record, self.schema), [])

    def test_a_resolvable_but_broken_stub_is_no_data_not_found(self):
        # Regression test for a real defect this module's own first test
        # run caught: macOS ships a stub /usr/bin/javac that shutil.which()
        # happily resolves even with no JDK installed; running it exits
        # nonzero and prints an error to stderr, not a version. Any
        # resolvable-but-broken tool (nonzero exit) must be NO-DATA, never
        # reported as found just because *some* text came out.
        real_which = shutil.which
        fake_dir = tempfile.mkdtemp(prefix="mobile-toolchain-probe-stub-")
        stub_path = os.path.join(fake_dir, "javac")
        with open(stub_path, "w", encoding="utf-8") as fh:
            fh.write(
                '#!/bin/sh\n'
                'echo "Unable to locate a Java Runtime." 1>&2\n'
                'exit 1\n')
        os.chmod(stub_path, 0o755)

        def fake_which(name, *args, **kwargs):
            if name == "javac":
                return stub_path
            return real_which(name, *args, **kwargs)

        with mock.patch("shutil.which", side_effect=fake_which):
            record = MTP.probe()

        javac = record["tools"]["javac"]
        self.assertEqual(javac["status"], "no-data")
        self.assertEqual(javac["resolved_path"], "NO-DATA")
        self.assertEqual(javac["version"], "NO-DATA")

    def test_first_line_skips_a_gradle_style_dash_separator_banner(self):
        # Regression test for a real defect the adversarial review pass
        # (Muse, via the OpenRouter bridge) caught before any Gradle
        # install was on hand to test live: real `gradle --version`
        # prints a banner separator line of dashes BEFORE the actual
        # "Gradle X.Y" line, so taking the literal first non-empty line
        # would report the separator as the version.
        banner = (
            "\n------------------------------------------------------------\n"
            "Gradle 8.5\n"
            "------------------------------------------------------------\n"
            "\nBuild time:   2023-11-29 14:08:57 UTC\n")
        self.assertEqual(MTP._first_line(banner), "Gradle 8.5")
        # A line of only dashes with nothing else in the text is still
        # honestly reported as empty (not silently swallowed into "found"
        # with garbage), which probe() then turns into NO-DATA.
        self.assertEqual(MTP._first_line("----\n----\n"), "")

    def test_hand_rules_flags_found_status_with_no_data_fields(self):
        record = {
            "tools": {
                "xcodebuild": {
                    "resolved_path": "NO-DATA",
                    "version": "NO-DATA",
                    "status": "found",
                },
            },
        }
        problems = MTP.hand_rules(record)
        self.assertTrue(problems)
        self.assertIn("xcodebuild", problems[0])

    def test_hand_rules_flags_no_data_status_with_real_fields(self):
        record = {
            "tools": {
                "gradle": {
                    "resolved_path": "/usr/bin/gradle",
                    "version": "8.0",
                    "status": "no-data",
                },
            },
        }
        problems = MTP.hand_rules(record)
        self.assertTrue(problems)
        self.assertIn("gradle", problems[0])

    def test_cli_prints_valid_json_and_exits_clean(self):
        result = subprocess.run(
            [sys.executable, MTP.__file__],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "stderr: %s" % result.stderr)
        printed = json.loads(result.stdout)
        self.assertEqual(printed["schema_version"], "mobile-toolchain-probe-v1")
        self.assertIn("tools", printed)


if __name__ == "__main__":
    unittest.main()
