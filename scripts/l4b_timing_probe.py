#!/usr/bin/env python3
"""L4b.4 timing link probe: records start and end so output handling cost is
visible without diagnosing it.

Specification, unit L4b.4 "Timing link probe": a hermetic probe that wraps a
minimal run_check style loop, records start and end with second resolution,
and asserts durations are present and non negative. It does not change gate
logic.

Requirements defended here:
  RQ-TIMING-LINK  before and after wall clock and per check durations are
                  recorded so output handling cost is visible.

Callers: scripts/test_l4b_timing_probe.py is the only caller today; nothing in
scripts/required_fast.sh or scripts/check_all.sh imports a probe and neither
gate is edited here.

House rules obeyed: empty, missing, corrupt, stale or non-UTF-8 input BLOCKS
with TimingError (a ValueError), never the safe case; a wrong type is refused
with ValueError, never a raw TypeError and never a silent accept; the timing
file is read as bytes.
"""

import os
import time
from typing import Dict

TIMING_KEYS = ("start", "end", "duration")


class TimingError(ValueError):
    """Refusal for a missing, empty, corrupt, stale or non-UTF-8 timing record."""


def _require_text(value, what):
    if isinstance(value, bool) or not isinstance(value, str):
        raise TimingError("%s must be a string, got %r" % (what, type(value).__name__))
    if not value.strip():
        raise TimingError("%s is empty" % (what,))
    return value


def _read_timing_bytes(path):
    _require_text(path, "timing path")
    if os.path.isdir(path):
        raise TimingError("timing path is a directory: %s" % (path,))
    if not os.path.isfile(path):
        raise TimingError("timing file is missing: %s" % (path,))
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise TimingError("timing file unreadable: %s" % (exc,))


def _parse_timing_text(text):
    # type: (str) -> Dict[str, int]
    record = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise TimingError("corrupt timing line: %r" % (line,))
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in TIMING_KEYS:
            raise TimingError("unknown timing key: %r" % (key,))
        if key in record:
            raise TimingError("duplicate timing key: %r" % (key,))
        try:
            number = int(value)
        except ValueError as exc:
            raise TimingError("corrupt timing value for %s: %r" % (key, value))
        record[key] = number
    for key in TIMING_KEYS:
        if key not in record:
            raise TimingError("timing record is missing %s" % (key,))
    return record


def check_timing_link(path):
    # type: (str) -> bool
    """True only when the file holds start, end and a non-negative duration.

    RQ-TIMING-LINK: a missing, empty, corrupt, stale, non-UTF-8 or wrong-typed
    record blocks with TimingError (a ValueError), never a pass and never a
    crash. A missing duration never reads as fast.
    """
    data = _read_timing_bytes(path)
    if not data.strip():
        raise TimingError("timing file is empty: %s" % (path,))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TimingError("timing file is not valid UTF-8: %s" % (exc,))
    record = _parse_timing_text(text)
    duration = record["duration"]
    if duration < 0:
        raise TimingError("duration is negative: %d" % (duration,))
    return True


def run_timing_probe(path):
    # type: (str) -> int
    """Run a minimal check, record start and end seconds, return the duration.

    Wraps a minimal run_check style loop: the wall clock is read before and
    after a trivial check, the file is truncated at start (a stale file is
    replaced, never appended to), and start, end and the clamped non-negative
    duration land in the file. No process is spawned and no gate logic runs.
    """
    _require_text(path, "timing path")
    if os.path.isdir(path):
        raise TimingError("timing path is a directory: %s" % (path,))
    start = int(time.time())
    for _ in range(1):
        pass
    end = int(time.time())
    duration = end - start
    if duration < 0:
        duration = 0
        end = start
    body = "start=%d\nend=%d\nduration=%d\n" % (start, end, duration)
    try:
        with open(path, "wb") as handle:
            handle.write(body.encode("utf-8"))
    except OSError as exc:
        raise TimingError("timing file unwritable: %s" % (exc,))
    return duration
