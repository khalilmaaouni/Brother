"""Calibration for scripts/v3_judge.py, driven BOTH ways on every rule.

A judge that can only say APPLIED is a rubber stamp, and this estate has shipped one before
(a control nobody drove backwards is a claim, not a control). So every rule in v3_judge.RULES
gets three synthetic fixtures here: one where the lesson's subject never arises
(NOT-APPLICABLE), one where it arises and the discipline is present (APPLIED), and one where
it arises and the discipline is absent (DECLINED). test_every_rule_is_driven_both_ways fails
if a rule ever ships without a DECLINED fixture, so the coverage cannot quietly rot.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v3_judge as J  # noqa: E402


def diff(path, added):
    """A minimal unified diff touching `path` and adding `added` lines."""
    body = '\n'.join('+' + line for line in added)
    return ('diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -1,1 +1,%d @@\n%s\n'
            % (path, path, path, path, len(added), body))


#: lesson id -> (not-applicable diff, applied diff, declined diff)
FIXTURES = {
    'repeat-guard:def-space': (
        diff('a.py', ['x = 1']),
        diff('a.py', ['def f(path=None):']),
        diff('a.py', ['def f(path=MODULE_CONST):']),
    ),
    'repeat-guard:open-paren': (
        diff('a.py', ['x = 1']),
        diff('a.py', ['with open(path) as fh:']),
        diff('a.py', ['body = open(path).read()']),
    ),
    'repeat-guard:unittest': (
        diff('t.py', ['def test_it(self):', '    self.assertEqual(rows, 3)']),
        diff('t.py', ['def test_it(self):', '    self.assertIn("PASS", out)',
                      '    self.assertEqual(proc.returncode, 1)']),
        diff('t.py', ['def test_it(self):', '    self.assertIn("PASS", out)']),
    ),
    'repeat-guard:pipe-tail': (
        diff('a.sh', ['echo hello']),
        diff('a.sh', ['set -o pipefail', 'sh gate.sh | tail -3']),
        diff('a.sh', ['sh gate.sh | tail -3', 'echo $?']),
    ),
    'repeat-guard:grep-c': (
        diff('a.sh', ['echo hello']),
        diff('a.sh', ['n=$(grep -c X f 2>/dev/null)', '[ -n "$n" ] || n=0']),
        diff('a.sh', ['n=$(grep -c X f || echo 0)']),
    ),
    'repeat-guard:checksums': (
        diff('scripts/a.py', ['x = 1']),
        (diff('products/brothermode/tools/a.py', ['x = 1'])
         + diff('products/brothermode/CHECKSUMS.sha256', ['abc  tools/a.py'])),
        diff('products/brothermode/tools/a.py', ['x = 1']),
    ),
    'repeat-guard:shlex-split': (
        diff('a.py', ['x = 1']),
        diff('a.py', ['    "target_revision": target_revision or NODATA,']),
        diff('a.py', ['    "target_revision": target_revision,']),
    ),
    'repeat-guard:done-check': (
        diff('a.py', ['x = 1']),
        diff('a.py', ['print("PASS")', 'print("NO-DATA: the prose clause went unjudged")']),
        diff('a.py', ['print("PASS")']),
    ),
    'repeat-guard:certify': (
        diff('a.py', ['# the reviewed revision is recorded elsewhere']),
        diff('a.py', ['SHA = "b94c8ddf7076b6dfdb244d43a2c850783389e7e5"',
                      'run(["git", "cat-file", "-e", SHA])']),
        diff('a.py', ['run(["git", "rev-parse", sha])']),
    ),
}


class EveryRuleIsDrivenBothWays(unittest.TestCase):
    def test_every_rule_has_all_three_fixtures(self):
        self.assertEqual(sorted(FIXTURES), sorted(J.RULES),
                         'a rule with no fixture set is an unproven judge')

    def test_not_applicable_fixtures(self):
        for lesson_id, (na, _ap, _de) in FIXTURES.items():
            verdict, _ev = J.judge(na, lesson_id)
            self.assertEqual(verdict, J.NOT_APPLICABLE, lesson_id)

    def test_applied_fixtures(self):
        for lesson_id, (_na, ap, _de) in FIXTURES.items():
            verdict, _ev = J.judge(ap, lesson_id)
            self.assertEqual(verdict, J.APPLIED, lesson_id)

    def test_declined_fixtures(self):
        """The half that makes it a judge: every rule must be able to say DECLINED."""
        for lesson_id, (_na, _ap, de) in FIXTURES.items():
            verdict, _ev = J.judge(de, lesson_id)
            self.assertEqual(verdict, J.DECLINED, lesson_id)


class TheCLI(unittest.TestCase):
    def test_an_unknown_lesson_id_is_NO_DATA_not_a_verdict(self):
        fd, path = tempfile.mkstemp(suffix='.diff')
        os.close(fd)
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(diff('a.py', ['x = 1']))
            self.assertEqual(J.main(['--diff', path, '--lesson', 'no-such-lesson']), 2)
        finally:
            os.unlink(path)  # sbe: allow-silent the fixture is this test's own temp file

    def test_a_missing_diff_exits_rather_than_judging_nothing(self):
        with self.assertRaises(SystemExit):
            J.read_diff('/no/such/diff/anywhere.diff')

    def test_main_runs_every_rule_and_exits_0(self):
        fd, path = tempfile.mkstemp(suffix='.diff')
        os.close(fd)
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(diff('a.py', ['def f(path=None):']))
            self.assertEqual(J.main(['--diff', path]), 0)
        finally:
            os.unlink(path)  # sbe: allow-silent the fixture is this test's own temp file


if __name__ == '__main__':
    unittest.main()
