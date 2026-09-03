#!/usr/bin/env python3
"""Tests for scripts/surface_budget.py.

Calibrated both ways: a fixture at or under the ceiling must pass (exit 0),
and a fixture built to exceed it must fail (exit 1), because a test that
cannot fail verifies nothing. Also proves the counting rule itself: a skill
marked `user-invocable: false` is excluded, one marked
`disable-model-invocation: true` is still counted, a missing repository is
NO-DATA (exit 2) rather than silently zero, and dot-directories such as a
`.git` object store or a `.claude/worktrees` checkout are never walked into
and double counted.

Python 3.9 floor, standard library only, no network.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import surface_budget as sb  # noqa: E402


def write_skill(skills_dir, name, frontmatter_lines=None):
    """Creates <skills_dir>/<name>/SKILL.md with the given frontmatter body
    lines (each a raw line, no trailing newline needed)."""
    skill_dir = os.path.join(skills_dir, name)
    os.makedirs(skill_dir, exist_ok=True)
    body = ['---']
    body.extend(frontmatter_lines or [])
    body.append('---')
    body.append('')
    body.append('# %s' % name)
    with open(os.path.join(skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(body) + '\n')


def write_command(commands_dir, name):
    os.makedirs(commands_dir, exist_ok=True)
    with open(os.path.join(commands_dir, name), 'w', encoding='utf-8') as f:
        f.write('# %s\n' % name)


class CountRepoSurface(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_plain_skill_with_no_frontmatter_field_is_counted(self):
        write_skill(os.path.join(self.root, 'skills'), 'plain', [
            'name: plain', 'description: does a thing'])
        commands, skills, _detail = sb.count_repo_surface(self.root)
        self.assertEqual((commands, skills), (0, 1))

    def test_user_invocable_false_is_excluded(self):
        skills_dir = os.path.join(self.root, 'skills')
        write_skill(skills_dir, 'hidden', [
            'name: hidden', 'user-invocable: false'])
        write_skill(skills_dir, 'visible', ['name: visible'])
        commands, skills, detail = sb.count_repo_surface(self.root)
        self.assertEqual(skills, 1)
        self.assertTrue(any('hidden' in line and 'excluded' in line
                             for line in detail))

    def test_disable_model_invocation_true_is_still_counted(self):
        """It stops the skill self-firing, not a person typing it."""
        write_skill(os.path.join(self.root, 'skills'), 'manual-only', [
            'name: manual-only', 'disable-model-invocation: true'])
        _commands, skills, _detail = sb.count_repo_surface(self.root)
        self.assertEqual(skills, 1)

    def test_commands_are_always_counted_no_frontmatter_check(self):
        write_command(os.path.join(self.root, 'commands'), 'do-thing.md')
        commands, _skills, _detail = sb.count_repo_surface(self.root)
        self.assertEqual(commands, 1)

    def test_non_md_files_in_commands_dir_are_ignored(self):
        commands_dir = os.path.join(self.root, 'commands')
        write_command(commands_dir, 'real.md')
        os.makedirs(commands_dir, exist_ok=True)
        with open(os.path.join(commands_dir, 'README.txt'), 'w') as f:
            f.write('not a command')
        commands, _skills, _detail = sb.count_repo_surface(self.root)
        self.assertEqual(commands, 1)

    def test_nested_commands_and_skills_dirs_are_both_found(self):
        """Mirrors this estate's real layouts: BrotherModeUp keeps
        commands/ and skills/ at the repo root, Brother keeps them under
        bundle/. Both must be counted the same way."""
        write_command(os.path.join(self.root, 'bundle', 'commands'), 'a.md')
        write_skill(os.path.join(self.root, 'bundle', 'skills'), 's1', [])
        commands, skills, _detail = sb.count_repo_surface(self.root)
        self.assertEqual((commands, skills), (1, 1))

    def test_dot_directories_are_pruned_not_double_counted(self):
        """A worktree checkout parked under .claude/worktrees/<x>/skills
        must never be walked into, or a single skill counts twice."""
        write_skill(os.path.join(self.root, 'skills'), 'real', [])
        write_skill(os.path.join(self.root, '.claude', 'worktrees', 'copy',
                                  'skills'), 'real', [])
        _commands, skills, _detail = sb.count_repo_surface(self.root)
        self.assertEqual(skills, 1)

    def test_a_skill_dir_entry_with_no_skill_md_is_not_counted(self):
        skills_dir = os.path.join(self.root, 'skills')
        os.makedirs(os.path.join(skills_dir, 'empty-dir'))
        _commands, skills, _detail = sb.count_repo_surface(self.root)
        self.assertEqual(skills, 0)


class Verdict(unittest.TestCase):
    def test_at_ceiling_passes(self):
        code, _msg = sb.verdict(total=47, missing=[], ceiling=47)
        self.assertEqual(code, 0)

    def test_under_ceiling_passes(self):
        code, _msg = sb.verdict(total=10, missing=[], ceiling=47)
        self.assertEqual(code, 0)

    def test_over_ceiling_fails(self):
        """The calibration this row requires: a surface above the target
        must produce a nonzero exit, or the gate verifies nothing."""
        code, _msg = sb.verdict(total=48, missing=[], ceiling=47)
        self.assertNotEqual(code, 0)
        self.assertEqual(code, 1)

    def test_missing_repository_is_no_data_not_a_pass(self):
        code, _msg = sb.verdict(total=0, missing=['SomeRepo'], ceiling=47)
        self.assertEqual(code, 2)
        self.assertNotEqual(code, 0)

    def test_missing_repository_outranks_being_under_ceiling(self):
        """A partial total that happens to sit under the ceiling is still
        NOT a pass: the total could not be certified complete."""
        code, _msg = sb.verdict(total=1, missing=['SomeRepo'], ceiling=47)
        self.assertEqual(code, 2)


class ComputeTotalCalibration(unittest.TestCase):
    """Drives the whole compute_total -> verdict path against constructed
    fixtures standing in for the real repositories, proving the gate goes
    red as well as green."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _repo_with_n_commands(self, name, n):
        repo = os.path.join(self.root, name)
        for i in range(n):
            write_command(os.path.join(repo, 'commands'), 'c%d.md' % i)
        return repo

    def test_fixture_surface_under_ceiling_exits_zero(self):
        repo = self._repo_with_n_commands('under', sb.CEILING - 1)
        total, missing, _lines = sb.compute_total([('Under', repo)])
        code, _msg = sb.verdict(total, missing, sb.CEILING)
        self.assertEqual(code, 0)

    def test_fixture_surface_over_ceiling_exits_nonzero(self):
        """The required calibration: build a surface bigger than the
        ceiling and require a nonzero exit code."""
        repo = self._repo_with_n_commands('over', sb.CEILING + 5)
        total, missing, _lines = sb.compute_total([('Over', repo)])
        self.assertGreater(total, sb.CEILING)
        code, _msg = sb.verdict(total, missing, sb.CEILING)
        self.assertNotEqual(code, 0)
        self.assertEqual(code, 1)

    def test_a_repo_path_that_does_not_exist_is_named_no_data(self):
        absent = os.path.join(self.root, 'does-not-exist')
        total, missing, lines = sb.compute_total([('Ghost', absent)])
        self.assertEqual(total, 0)
        self.assertEqual(missing, ['Ghost'])
        self.assertTrue(any('Ghost' in line and 'NO-DATA' in line
                             for line in lines))


class WhatOneInstallMustProduce(unittest.TestCase):
    """R11.1. The ceiling counts surface that EXISTS across every tree; the
    manifest counts what ONE INSTALL DELIVERsb. They are different questions and
    they had different answers on the day this was written."""

    def test_the_shipped_plugin_list_comes_from_the_marketplace_file(self):
        """Not from a constant. Add a plugin to the umbrella and the manifest
        grows without anyone remembering to edit this module."""
        import json
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"plugins": [{"name": "one"}, {"name": "two"}]}, fh)
        try:
            names, problem = sb.shipped_plugins(path)
            self.assertEqual(names, ["one", "two"], problem)
        finally:
            os.unlink(path)

    def test_a_missing_marketplace_file_is_NO_DATA_not_an_empty_list(self):
        """An empty list would make the manifest trivially satisfiable by
        installing nothing at all."""
        names, problem = sb.shipped_plugins("/no/such/marketplace.json")
        self.assertIsNone(names)
        self.assertIn("/no/such/marketplace.json", problem)

    def test_a_marketplace_naming_no_plugins_is_also_NO_DATA(self):
        import json
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"plugins": []}, fh)
        try:
            names, problem = sb.shipped_plugins(path)
            self.assertIsNone(names)
        finally:
            os.unlink(path)

    def test_the_real_manifest_builds_and_names_the_real_plugins(self):
        m, problem = sb.build_manifest()
        self.assertIsNotNone(m, problem)
        self.assertEqual(m["shipped_plugins"],
                         ["brother", "brothermode", "brothersbe"])

    def test_the_manifest_total_is_the_sum_of_its_own_entries(self):
        """A total that is not derivable from the parts it lists is a number
        nobody can check."""
        m, _ = sb.build_manifest()
        self.assertEqual(m["total"],
                         sum(len(v) for v in m["entries"].values()))

    def test_command_names_lose_their_md_because_that_is_how_they_register(self):
        m, _ = sb.build_manifest()
        for name in m["entries"]["brother"]:
            self.assertFalse(name.endswith(".md"), name)

    def test_a_plugin_with_no_tree_is_NO_DATA_rather_than_silently_dropped(self):
        """Dropping it would shrink the manifest and make a broken install pass,
        which is the failure this whole file exists to prevent."""
        import json
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"plugins": [{"name": "brother"}, {"name": "nosuch"}]}, fh)
        try:
            m, problem = sb.build_manifest(path)
            self.assertIsNone(m)
            self.assertIn("nosuch", problem)
        finally:
            os.unlink(path)

    def test_the_written_manifest_on_disk_matches_what_the_builder_computes(self):
        """Otherwise the file is a stale snapshot and the smoke checks history."""
        import json
        if not os.path.exists(sb.MANIFEST_PATH):
            self.skipTest("no manifest written yet")
        with open(sb.MANIFEST_PATH, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        computed, _ = sb.build_manifest()
        self.assertEqual(on_disk["total"], computed["total"])
        self.assertEqual(on_disk["entries"], computed["entries"])


if __name__ == '__main__':
    unittest.main()
