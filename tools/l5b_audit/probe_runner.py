"""L5b.3 probe_runner: run one named unittest id read from the environment.

The fire prober (verify.py) is the only command runner in this sub unit,
and RULE 10 forbids a computed name or path in argv. So the test id
travels only through env["L5B_TEST_ID"], and argv stays the literal
["python3", "-m", "tools.l5b_audit.probe_runner"]. No piped Python
source on a command line, no eval, no exec, no shell.

Unknown, corrupt or missing input BLOCKS: exit 2 and a NO-DATA line on
stderr, never a silent green.
"""
import os
import sys
import unittest

ENV_KEY = "L5B_TEST_ID"


def selected_test_id(env):
    if not isinstance(env, dict):
        raise ValueError("env must be a dict, got %s" % type(env).__name__)
    value = env.get(ENV_KEY)
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % ENV_KEY)
    stripped = value.strip()
    if not stripped:
        raise ValueError("%s must be a non-empty string" % ENV_KEY)
    return stripped


def main(env=None):
    if env is None:
        env = dict(os.environ)
    try:
        test_id = selected_test_id(env)
    except ValueError as exc:
        sys.stderr.write("NO-DATA: %s\n" % exc)
        return 2
    try:
        suite = unittest.TestLoader().loadTestsFromName(test_id)
    except (ImportError, AttributeError, ValueError, TypeError, SyntaxError) as exc:
        sys.stderr.write("NO-DATA: cannot load %s: %s\n" % (test_id, exc))
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.testsRun == 0:
        sys.stderr.write("NO-DATA: zero tests ran for %s\n" % test_id)
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
