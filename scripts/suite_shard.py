"""suite_shard: one selector for the k-th of n class shards.

A long test suite opts in with a load_tests hook that reads BROTHER_TEST_SHARD
and hands its suite to select(). Unset or empty, the hook returns the suite
unchanged, so the full battery, the grader and a developer see every test.
Shards are dealt round robin over the top level children that hold tests (the
classes), so they are disjoint and complete by construction and a class's
fixture is paid once.
"""
import re
import unittest
from typing import Optional, Tuple

MAX_SHARDS = 16

_SPEC = re.compile(r"([1-9][0-9]?)/([1-9][0-9]?)")


class ShardError(ValueError):
    """A shard spec or a select() argument that cannot be honoured."""


def parse_spec(spec: Optional[str]) -> Optional[Tuple[int, int]]:
    """Turn "k/n" into (k, n). None or "" means no sharding, so None."""
    if spec is None:
        return None
    if not isinstance(spec, str):
        raise ShardError(
            "shard spec must be a string such as '1/2', got %s"
            % type(spec).__name__)
    if spec == "":
        return None
    match = _SPEC.fullmatch(spec)
    if match is None:
        raise ShardError(
            "shard spec must match k/n with 1 <= k <= n <= %d, got %s"
            % (MAX_SHARDS, spec[:40]))
    k = int(match.group(1))
    n = int(match.group(2))
    if n > MAX_SHARDS or k > n:
        raise ShardError(
            "shard spec must match k/n with 1 <= k <= n <= %d, got %s"
            % (MAX_SHARDS, spec[:40]))
    return (k, n)


def select(tests: unittest.TestSuite, spec: Optional[str]) -> unittest.TestSuite:
    """The k-th of n class shards of a suite, or the suite itself."""
    if not isinstance(tests, unittest.TestSuite):
        raise ShardError(
            "tests must be a unittest.TestSuite, got %s"
            % type(tests).__name__)
    parsed = parse_spec(spec)
    if parsed is None:
        return tests
    k, n = parsed
    classes = [child for child in tests if child.countTestCases() > 0]
    picked = [child for i, child in enumerate(classes) if i % n == k - 1]
    if not picked:
        raise ShardError(
            "shard %d/%d selects no test from %d class(es)"
            % (k, n, len(classes)))
    return unittest.TestSuite(picked)
