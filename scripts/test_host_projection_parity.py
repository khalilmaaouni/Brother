#!/usr/bin/env python3
"""Tests for scripts/host_projection_parity.py.

Calibrated both ways: a fixture pair with identical bodies must pass, and a
fixture pair built to disagree must fail, because a check that cannot fail
verifies nothing. Also proves the real committed tree passes today.

Python 3.9 floor, standard library only, no network.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import host_projection_parity as hpp  # noqa: E402
import codex_surface  # noqa: E402


def _write_skill(base, name, frontmatter_lines, body):
    skill_dir = os.path.join(base, name)
    os.makedirs(skill_dir, exist_ok=True)
    text = '---\n' + '\n'.join(frontmatter_lines) + '\n---\n' + body
    with open(os.path.join(skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
        f.write(text)


class CodexSkillsBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle_skills = os.path.join(self.tmp.name, 'bundle-skills')
        self.codex_skills_dir = os.path.join(self.tmp.name, 'codex-skills')

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_bodies_pass(self):
        _write_skill(self.bundle_skills, 'one',
                     ['name: one', 'description: a thing'], 'do the thing\n')
        _write_skill(self.codex_skills_dir, 'one',
                     ['name: one', 'description: a thing'], 'do the thing\n')
        problems, checked = hpp.check_codex_skills_boundary(
            self.bundle_skills, self.codex_skills_dir)
        self.assertEqual(problems, [])
        self.assertEqual(checked, 1)

    def test_frontmatter_may_differ_body_may_not(self):
        """Exactly what the roadmap allows: syntax/frontmatter changes,
        capability meaning (the body) does not."""
        _write_skill(self.bundle_skills, 'one',
                     ['name: one', 'description: a thing',
                      'disable-model-invocation: true'],
                     'do the thing\n')
        _write_skill(self.codex_skills_dir, 'one',
                     ['name: one', 'description: a thing'],
                     'do the thing\n')
        problems, checked = hpp.check_codex_skills_boundary(
            self.bundle_skills, self.codex_skills_dir)
        self.assertEqual(problems, [])
        self.assertEqual(checked, 1)

    def test_a_diverged_body_is_a_real_failure(self):
        """The calibration this test requires: a projection that silently
        rewrote the instructions must be caught, or this check verifies
        nothing."""
        _write_skill(self.bundle_skills, 'one',
                     ['name: one', 'description: a thing'], 'do the thing\n')
        _write_skill(self.codex_skills_dir, 'one',
                     ['name: one', 'description: a thing'],
                     'do a DIFFERENT thing\n')
        problems, checked = hpp.check_codex_skills_boundary(
            self.bundle_skills, self.codex_skills_dir)
        self.assertEqual(checked, 1)
        self.assertEqual(len(problems), 1)
        self.assertIn('one', problems[0])
        self.assertIn('body differs', problems[0])

    def test_missing_bundle_skills_is_no_data(self):
        os.makedirs(self.codex_skills_dir, exist_ok=True)
        missing = os.path.join(self.tmp.name, 'does-not-exist')
        problems, checked = hpp.check_codex_skills_boundary(
            missing, self.codex_skills_dir)
        self.assertEqual(checked, 0)
        self.assertTrue(any('NO-DATA' in p for p in problems))

    def test_an_entry_present_only_on_one_side_is_skipped_not_failed(self):
        """Coverage is measured elsewhere (surface_budget.py); this check's
        job is body parity where both sides exist."""
        _write_skill(self.bundle_skills, 'only-here', ['name: only-here'], 'x\n')
        problems, checked = hpp.check_codex_skills_boundary(
            self.bundle_skills, self.codex_skills_dir + '-absent-but-parent-exists')
        # codex_skills_dir itself missing entirely is still NO-DATA at the
        # directory level, proven by test_missing above via the other side;
        # here both dirs exist but the entry is one-sided.
        os.makedirs(self.codex_skills_dir, exist_ok=True)
        problems, checked = hpp.check_codex_skills_boundary(
            self.bundle_skills, self.codex_skills_dir)
        self.assertEqual(problems, [])
        self.assertEqual(checked, 0)


class RealContentBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = self.tmp.name
        self.bundle_skills = os.path.join(self.repo_root, 'bundle', 'skills')

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self, product, skill_dir, body):
        source = os.path.join(self.repo_root, 'products', product, 'skills',
                               skill_dir)
        os.makedirs(source, exist_ok=True)
        with open(os.path.join(source, 'SKILL.md'), 'w', encoding='utf-8') as f:
            f.write('---\nname: %s\n---\n%s' % (skill_dir, body))

    def test_matching_mirror_passes(self):
        for canonical, (product, skill_dir) in codex_surface.REAL_CONTENT_SKILLS.items():
            self._seed(product, skill_dir, 'real instructions for %s\n' % canonical)
            _write_skill(self.bundle_skills, canonical, ['name: %s' % canonical],
                         'real instructions for %s\n' % canonical)
        problems, checked = hpp.check_real_content_boundary(
            self.repo_root, self.bundle_skills)
        self.assertEqual(problems, [])
        self.assertEqual(checked, len(codex_surface.REAL_CONTENT_SKILLS))

    def test_a_stubbed_mirror_is_a_real_failure(self):
        """If codex_surface.py ever regressed to stubbing a
        REAL_CONTENT_SKILLS entry instead of mirroring it, this must fail."""
        for canonical, (product, skill_dir) in codex_surface.REAL_CONTENT_SKILLS.items():
            self._seed(product, skill_dir, 'real instructions\n')
        first = list(codex_surface.REAL_CONTENT_SKILLS)[0]
        _write_skill(self.bundle_skills, first, ['name: %s' % first],
                     'a generic stub pointing elsewhere\n')
        for canonical in list(codex_surface.REAL_CONTENT_SKILLS)[1:]:
            _write_skill(self.bundle_skills, canonical, ['name: %s' % canonical],
                         'real instructions\n')
        problems, _checked = hpp.check_real_content_boundary(
            self.repo_root, self.bundle_skills)
        self.assertTrue(any(first in p and 'body differs' in p for p in problems))


class RealTree(unittest.TestCase):
    def test_the_real_committed_tree_passes_today(self):
        code, lines = hpp.run_check()
        self.assertEqual(code, 0, '\n'.join(lines))
        self.assertTrue(any(line.startswith('PASS:') for line in lines))


if __name__ == '__main__':
    unittest.main()
