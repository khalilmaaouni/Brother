#!/usr/bin/env python3
"""Verify a released Cursor adapter in a detached clone and retain its evidence."""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile


def run_battery(tag, repo, evidence, signed_in=False):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('expected a version tag such as v1.0.15')
    evidence = pathlib.Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, argv, cwd=None):
        try:
            result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=1500)
            code, output = result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            code, output = 1, 'FAIL: command exceeded 1500 seconds'
        except OSError as error:
            code, output = 2, 'NO-DATA: ' + str(error)
        (evidence / (name + '.log')).write_text(output)
        checks.append({'name': name, 'command': argv, 'exit_code': code,
                       'verdict': 'PASS' if code == 0 else 'NO-DATA' if code == 2 else 'FAIL'})
        return code, output
    revision = None
    with tempfile.TemporaryDirectory(prefix='cursor-release-') as directory:
        tree = pathlib.Path(directory) / 'repo'
        code, _ = check('clone', ['git', 'clone', '--depth', '1', '--branch', tag, '--', repo, str(tree)])
        if code == 0:
            code, output = check('revision', ['git', 'rev-parse', 'HEAD'], str(tree))
            if code == 0:
                revision = output.strip()
                for name, args in [('package', ['scripts/test_cursor_plugin.py']),
                                   ('hooks', ['scripts/test_cursor_hook_run.py']),
                                   ('signed-out', ['scripts/cursor_smoke.py'])]:
                    check(name, [sys.executable] + args, str(tree))
                if signed_in:
                    check('signed-in', [sys.executable, 'scripts/cursor_smoke.py', '--signed-in'], str(tree))
    verdict = 'FAIL' if any(c['verdict'] == 'FAIL' for c in checks) else 'NO-DATA' if any(c['verdict'] == 'NO-DATA' for c in checks) else 'PASS'
    report = {'tag': tag, 'revision': revision, 'checks': checks, 'verdict': verdict,
              'signed_in': 'requested' if signed_in else 'NO-DATA: not requested'}
    (evidence / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(verdict + ': Cursor release preparation' + (' and signed-in verification' if signed_in else ' (signed-in: NO-DATA)'))
    print(evidence / 'receipt.json')
    return {'PASS': 0, 'FAIL': 1, 'NO-DATA': 2}[verdict]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repo', default='https://github.com/khalilmaaouni/Brother.git')
    parser.add_argument('--evidence', required=True, help='new directory outside the checkout')
    parser.add_argument('--signed-in', action='store_true')
    args = parser.parse_args()
    return run_battery(args.tag, args.repo, args.evidence, args.signed_in)


if __name__ == '__main__':
    raise SystemExit(main())
