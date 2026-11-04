#!/usr/bin/env python3
"""c01_failfast_runner: run ONE suite file exactly as `python3 <suite>` would,
with unittest's fail fast on by default, and say whether it engaged.

WHY (C0.1, docs/plan/specs/C0.md, revised 2026-09-28). The first build of
C0.1 appended `-f` to the suite's own command line. Every module the suite
imports can see `sys.argv`, and an unmocked counterexample
(c01-safety-counterexample.log) had a helper branch on `len(sys.argv)` and
make the fast cut claim coverage the full serial chain denied. So fail fast
is switched on INSIDE this process instead, and the suite sees the start
state a plain script run gives it: `sys.argv == [suite]`, `sys.orig_argv`
reset to the plain command, `sys.path[0]` the resolved directory of the
suite, and a fresh `__main__` shaped the way the interpreter shapes one
(SourceFileLoader, `__package__`, `__spec__` and `__cached__` None; not
`runpy.run_path`, which was measured to give a different `__loader__` and
`__package__` on 3.13.14 and 3.9.6).

HOW. `unittest.main` / `unittest.TestProgram` (and the same two names in the
`unittest.main` module) become one subclass whose `failfast` DEFAULT is True.
An explicit caller argument, `failfast` included, is always honoured.
`unittest.TextTestRunner` is not patched: a suite that drives it directly
simply runs plainly.

THE STATUS LINE, written once to stdout after the suite finishes:
    C0.1 fail-fast: engaged; result: <error|failure|unexpected-success|success|unknown>
    C0.1 fail-fast: not-engaged; result: plain
`not-engaged` means no program consumed the injected default. For an engaged
run the result is a real token only when exactly one program ran at all, it
consumed the injected default, it used the default TextTestRunner with an
exact TextTestResult whose failfast is True, and the process is exiting on
the very SystemExit that program's runTests raised. Anything else is
`unknown`. A process that dies without reaching this point (os._exit, a
signal) writes no line, and the caller treats that as unproved.

The suite's own SystemExit is re-raised unchanged; any other exception is
re-raised unchanged after the status line. Standard library only, and this
runner never spawns a process.

Residual differences NOT claimed transparent: extra `sys.modules` entries
(this file's imports), the patched class identity, deeper stacks.
"""
import os
import sys
import types
import unittest
from importlib import machinery

PREFIX = "C0.1 fail-fast: "
_ORIG_PROGRAM = unittest.TestProgram
_ORIG_TEXT_RUNNER = unittest.TextTestRunner
_ORIG_TEXT_RESULT = unittest.TextTestResult
#: TestProgram.__init__ positional order after self: module, defaultTest,
#: argv, testRunner, testLoader, exit, verbosity, failfast. A caller passing
#: eight or more positional arguments supplied failfast explicitly.
_FAILFAST_POSITION = 7

#: Every program constructed while the suite ran, in order.
_PROGRAMS = []


class _FailFastProgram(_ORIG_PROGRAM):
    """unittest.TestProgram with failfast defaulting to True."""

    def __init__(self, *args, **kwargs):
        record = {"engaged": False, "exit": None, "program": self}
        _PROGRAMS.append(record)
        if "failfast" not in kwargs and len(args) <= _FAILFAST_POSITION:
            kwargs["failfast"] = True
            record["engaged"] = True
        self._c01_record = record
        _ORIG_PROGRAM.__init__(self, *args, **kwargs)

    def runTests(self):
        try:
            _ORIG_PROGRAM.runTests(self)
        except SystemExit as exc:
            self._c01_record["exit"] = exc
            raise


def _install():
    unittest.main = _FailFastProgram
    unittest.TestProgram = _FailFastProgram
    main_module = sys.modules.get("unittest.main")
    if main_module is not None:
        main_module.main = _FailFastProgram
        main_module.TestProgram = _FailFastProgram


def status(exiting):
    """The status line for this run. `exiting` is the exception object the
    process is about to exit on (None when the suite completed)."""
    engaged = [r for r in _PROGRAMS if r["engaged"]]
    if not engaged:
        return PREFIX + "not-engaged; result: plain"
    token = "unknown"
    if len(_PROGRAMS) == 1 and len(engaged) == 1:
        record = engaged[0]
        program = record["program"]
        result = getattr(program, "result", None)
        if (record["exit"] is not None and exiting is record["exit"]
                and getattr(program, "testRunner", None) is _ORIG_TEXT_RUNNER
                and type(result) is _ORIG_TEXT_RESULT
                and getattr(result, "failfast", None) is True):
            if result.errors:
                token = "error"
            elif result.failures:
                token = "failure"
            elif getattr(result, "unexpectedSuccesses", None):
                token = "unexpected-success"
            elif result.wasSuccessful():
                token = "success"
    return PREFIX + "engaged; result: " + token


def _emit(line):
    """Flush whatever the suite buffered, then write the line straight to
    file descriptor 1 so it cannot be swallowed by a replaced sys.stdout.
    A closed descriptor loses the line, which the caller reads as unproved."""
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    try:
        os.write(1, ("\n" + line + "\n").encode("utf-8"))
    except OSError:
        pass


def _script_file(path):
    """`__main__.__file__` as the interpreter sets it: an absolute path is
    kept verbatim, a relative one is joined to the cwd without normalizing
    (measured on 3.13.14 and 3.9.6, 2026-09-29)."""
    return path if os.path.isabs(path) else os.getcwd() + os.sep + path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: c01_failfast_runner.py <suite path>\n")
        return 2
    suite_path = args[0]
    with open(suite_path, "rb") as fh:
        source = fh.read()
    filename = _script_file(suite_path)
    code = compile(source, filename, "exec", dont_inherit=True)

    sys.argv = [suite_path]
    if hasattr(sys, "orig_argv"):
        sys.orig_argv = sys.orig_argv[:1] + [suite_path]
    sys.path[0] = os.path.dirname(os.path.realpath(suite_path))

    # Held until main returns, so the runner's own globals outlive the swap.
    runner_module = sys.modules.get("__main__")  # noqa: F841
    suite_main = types.ModuleType("__main__")
    suite_main.__file__ = filename
    suite_main.__loader__ = machinery.SourceFileLoader("__main__", filename)
    suite_main.__package__ = None
    suite_main.__spec__ = None
    suite_main.__cached__ = None
    suite_main.__builtins__ = sys.modules["builtins"]
    sys.modules["__main__"] = suite_main
    _install()
    try:
        exec(code, suite_main.__dict__)
    except SystemExit as exc:
        _emit(status(exc))
        raise
    except BaseException:
        _emit(status(None))
        raise
    _emit(status(None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
