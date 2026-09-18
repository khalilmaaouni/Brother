#!/usr/bin/env python3
"""Tests for scripts/generic_skills_adapter.py.

Pins the asset kinds (the registry's own host set and generated-dir set) so
a silently added or dropped host is caught. Calibrated both ways per
project law: a matching fixture must pass, and a hand-drifted fixture must
fail, because a check that cannot fail verifies nothing. Also proves the
codex entry is the same tuple object as codex_skills.ACCEPTED_KEYS, not a
retyped copy, which is the whole deciding property this module exists for.

Python 3.9 floor, standard library only, no network.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generic_skills_adapter as gsa  # noqa: E402
import codex_skills  # noqa: E402


def _write_skill(base, name, frontmatter_lines, body):
    skill_dir = os.path.join(base, name)
    os.makedirs(skill_dir, exist_ok=True)
    text = "---\n" + "\n".join(frontmatter_lines) + "\n---\n" + body
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


class RegistryShape(unittest.TestCase):
    """The pinned expected set: every asset kind (host) this module knows
    about, enumerated rather than assumed."""

    def test_registry_hosts_are_pinned(self):
        self.assertEqual(set(gsa.CAPABILITY_REGISTRY), {"claude", "cursor", "codex"})

    def test_generated_dir_hosts_are_pinned(self):
        self.assertEqual(set(gsa.GENERATED_DIR), {"codex"})

    def test_verbatim_hosts_have_no_generated_dir(self):
        for host, accepted in gsa.CAPABILITY_REGISTRY.items():
            if accepted is None:
                self.assertNotIn(host, gsa.GENERATED_DIR)

    def test_codex_entry_is_the_same_tuple_object_as_codex_skills(self):
        """The deciding property: no manually drifting copy. If a future
        edit retypes the codex entry as a new tuple instead of importing
        codex_skills.ACCEPTED_KEYS, this must fail even though the two
        tuples would still compare equal."""
        self.assertIs(gsa.CAPABILITY_REGISTRY["codex"], codex_skills.ACCEPTED_KEYS)


class UnknownHost(unittest.TestCase):
    def test_build_for_host_raises_on_unknown_host(self):
        with self.assertRaises(ValueError):
            gsa.build_for_host("nonexistent-host")

    def test_check_for_host_raises_on_unknown_host(self):
        with self.assertRaises(ValueError):
            gsa.check_for_host("nonexistent-host")

    def test_unhashable_host_raises_valueerror_not_typeerror(self):
        with self.assertRaises(ValueError):
            gsa.build_for_host(["not", "hashable"])
        with self.assertRaises(ValueError):
            gsa.check_for_host(["not", "hashable"])


class VerbatimHosts(unittest.TestCase):
    def test_build_for_host_none_entry_returns_empty(self):
        for host in ("claude", "cursor"):
            files, problems = gsa.build_for_host(host)
            self.assertEqual(files, {})
            self.assertEqual(problems, [])

    def test_check_for_host_none_entry_is_not_an_empty_pass(self):
        """Empty must never look like verified clean."""
        for host in ("claude", "cursor"):
            problems, checked = gsa.check_for_host(host)
            self.assertEqual(checked, 0)
            self.assertEqual(len(problems), 1)
            self.assertIn("NO-DATA", problems[0])
            self.assertIn(host, problems[0])


class BuildForHostCodex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.tmp.name, "bundle-skills")
        _write_skill(self.source, "one",
                     ["name: one", "description: a thing",
                      "disable-model-invocation: true"], "do the thing\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_matches_codex_skills_build_directly(self):
        """The generic path must reproduce the specific one byte for
        byte: it is a caller of codex_skills.build, never a second
        implementation of frontmatter stripping."""
        generic_files, generic_problems = gsa.build_for_host("codex", self.source)
        direct_files, direct_problems = codex_skills.build(self.source)
        self.assertEqual(generic_problems, direct_problems)
        self.assertEqual(generic_files, direct_files)
        self.assertIn("one/SKILL.md", generic_files)
        self.assertNotIn("disable-model-invocation",
                          generic_files["one/SKILL.md"].decode("utf-8"))

    def test_unreadable_source_is_reported_not_read_as_empty(self):
        missing = os.path.join(self.tmp.name, "does-not-exist")
        files, problems = gsa.build_for_host("codex", missing)
        self.assertEqual(files, {})
        self.assertTrue(problems)


class CheckForHostCodex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.tmp.name, "bundle-skills")
        self.dest = os.path.join(self.tmp.name, "codex-skills")
        _write_skill(self.source, "one", ["name: one", "description: a thing"],
                     "do the thing\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _generate_dest(self):
        files, problems = codex_skills.build(self.source)
        self.assertEqual(problems, [])
        for rel, data in files.items():
            path = os.path.join(self.dest, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        return files

    def test_matching_tree_has_no_drift(self):
        expected = self._generate_dest()
        problems, checked = gsa.check_for_host("codex", self.source, self.dest)
        self.assertEqual(problems, [])
        self.assertEqual(checked, len(expected))

    def test_hand_edited_file_is_reported_as_drift(self):
        """The calibration this test requires: a manually drifted copy
        must be caught, or this check verifies nothing."""
        self._generate_dest()
        with open(os.path.join(self.dest, "one", "SKILL.md"), "a",
                   encoding="utf-8") as fh:
            fh.write("\na hand edit nobody generated\n")
        problems, checked = gsa.check_for_host("codex", self.source, self.dest)
        self.assertEqual(checked, 2)  # one/SKILL.md plus codex_skills.RECORD_NAME
        self.assertEqual(len(problems), 1)
        self.assertIn("drift", problems[0])
        self.assertIn("one/SKILL.md", problems[0])

    def test_missing_file_is_reported(self):
        self._generate_dest()
        os.remove(os.path.join(self.dest, "one", "SKILL.md"))
        problems, checked = gsa.check_for_host("codex", self.source, self.dest)
        self.assertTrue(any("missing" in p for p in problems))

    def test_stale_extra_file_is_reported(self):
        self._generate_dest()
        stale_dir = os.path.join(self.dest, "leftover")
        os.makedirs(stale_dir, exist_ok=True)
        with open(os.path.join(stale_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: leftover\ndescription: x\n---\nbody\n")
        problems, _checked = gsa.check_for_host("codex", self.source, self.dest)
        self.assertTrue(any("stale" in p for p in problems))

    def test_unreadable_source_is_zero_checked_not_zero_drift(self):
        missing = os.path.join(self.tmp.name, "does-not-exist")
        problems, checked = gsa.check_for_host("codex", missing, self.dest)
        self.assertEqual(checked, 0)
        self.assertTrue(problems)
        self.assertTrue(any("NO-DATA" in p for p in problems))

    def test_missing_dest_dir_is_reported_not_read_as_clean(self):
        missing_dest = os.path.join(self.tmp.name, "does-not-exist-dest")
        problems, checked = gsa.check_for_host("codex", self.source, missing_dest)
        self.assertEqual(checked, 0)
        self.assertTrue(any("missing entirely" in p for p in problems))

    def test_empty_string_dest_dir_is_refused_not_joined_to_cwd(self):
        problems, checked = gsa.check_for_host("codex", self.source, "")
        self.assertEqual(checked, 0)
        self.assertTrue(any("NO-DATA" in p for p in problems))

    def test_empty_source_with_missing_dest_is_not_a_silent_clean_pass(self):
        """Adversarial edge: an empty source tree must not combine with a
        missing dest to look like zero files, zero drift, all clean."""
        empty_source = os.path.join(self.tmp.name, "empty-source")
        os.makedirs(empty_source, exist_ok=True)
        missing_dest = os.path.join(self.tmp.name, "also-missing")
        problems, checked = gsa.check_for_host("codex", empty_source, missing_dest)
        self.assertEqual(checked, 0)
        self.assertTrue(problems, "an empty source plus a missing dest must "
                        "still report something, never an empty problem list")


class DefaultDestDir(unittest.TestCase):
    def test_host_without_generated_dir_entry_has_no_default(self):
        self.assertIsNone(gsa._default_dest_dir("claude"))

    def test_codex_default_points_under_repo_root(self):
        self.assertEqual(
            gsa._default_dest_dir("codex"),
            os.path.join(gsa.REPO_ROOT, "bundle", "codex-skills"))


class RealTree(unittest.TestCase):
    def test_real_tree_check_runs_and_returns_well_formed_output(self):
        """Exercises the real committed tree without asserting it is
        clean: another unit in this shared worktree may be regenerating
        bundle/codex-skills concurrently, so only the shape of the
        result, not its content, is safe to pin here."""
        problems, checked = gsa.check_for_host("codex")
        self.assertIsInstance(problems, list)
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
