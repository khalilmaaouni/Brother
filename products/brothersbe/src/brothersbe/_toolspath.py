"""The one place `tools/` gets mounted onto `sys.path`.

`tools/` is not a package (no `__init__.py`, and it should not become one:
its own modules import each other flat, and `bin/sbe` ships the same way).
Six modules under `src/brothersbe/` need to reach flat modules that live
there (`sbe_plan`, `sbe_checks`, `sbe_intake`, `sbe_telemetry`, `sbe_gate`),
so each of them used to compute the absolute path to `tools/` and insert it
onto `sys.path` itself: six separate copies of the same three lines, one
per module, that could drift independently. This module is the single
computation the six now share.

Eager use, the common case, right after the stdlib imports at the top of a
module:

    from ._toolspath import mount
    mount()
    import sbe_plan  # noqa: E402

Lazy use, for the one module that cannot mount at import time
(`decisions.py`'s `registries()`: `tools/test_sbe_decisions.py` imports
`decisions.py` back, so mounting at module scope there would be a cycle):

    from ._toolspath import mount

    def registries():
        mount()
        from sbe_checks import Check, Pruner
        ...

`mount()` is idempotent, the same way each of the six copies already was:
it checks `sys.path` before inserting, so calling it from several modules
in the same process, in any order, mounts `tools/` exactly once.
"""
import os
import sys

#: `tools/` sits two directories up from this file: `src/brothersbe/` ->
#: `src/` -> the repository root -> `tools/`. Every one of the six modules
#: this replaces computed this same path from ITS OWN `__file__`; since all
#: six live in this same directory, deriving it once here from THIS file's
#: `__file__` gives the identical absolute path they each computed before.
TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools")


def mount():
    """Put the repository's `tools/` directory on `sys.path`, once.

    Returns the absolute path to `tools/`, so a caller that wants it (none
    of the six do today) does not have to recompute it.
    """
    tools = os.path.abspath(TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return tools
