"""BrotherSBE as an importable package.

Deliberately thin at this stage. The working code still lives in `tools/`, where
509 evals and 34 unit tests point at it, and this package is a facade over those
tools rather than a reimplementation of them. Logic moves in behind this surface
one wave at a time, with the gates green at every commit. A rewrite that lands
all at once cannot be told apart from a rewrite that lands broken.

Python floor is 3.9: no match statements, no `X | Y` annotations, no
`from __future__ import annotations` tricks that later versions made free. That
is the system Python on the machine that maintains this, so it is the floor that
is actually tested rather than the one that sounds modern.
"""
import io
import os

#: The evidence schema this package emits and accepts. Bumped only when the
#: MEANING of a field changes, because a consumer that reads an old field with
#: new semantics is worse off than one that fails to parse.
SCHEMA_VERSION = "1.0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def repo_root():
    """The installation root: the directory holding VERSION, tools/ and SKILL.md.

    Computed from this file's location rather than from the caller's working
    directory, so `sbe` behaves the same whether it is run from a clone, from a
    plugin directory, or from inside somebody else's repository.
    """
    return _ROOT


def version():
    """The single version this project has. Read from the VERSION file rather
    than duplicated here: a second copy of a version number is a second version
    number, and `tools/test_sbe.py` already pins the plugin manifest to that
    same file for the same reason."""
    path = os.path.join(_ROOT, "VERSION")
    return io.open(path, encoding="utf-8").read().strip()


__version__ = version()
