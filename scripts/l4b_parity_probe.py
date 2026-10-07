#!/usr/bin/env python3
"""L4b.3 parity probe: two saved gate outputs agree on counts and names.

Specification, unit L4b.3 "Parity gate for counts and transition line": a
hermetic probe that parses two saved gate outputs, compares the PASS, FAIL and
NO-DATA counts plus the named checks carried by the transition line, and
reports a mismatch as failure. It writes no gate file and it changes no gate.

Callers: scripts/test_l4b_parity_probe.py, the runnable guard landed beside
this module, is the only caller today. Nothing in scripts/required_fast.sh or
scripts/check_all.sh imports a probe, and neither gate is edited here.

House rules obeyed here: empty, missing, corrupt or unrecognized input BLOCKS
with GateOutputError (a ValueError), never the safe case; a counts line that
contradicts the summary lines is corrupt input and raises; a wrong type is
refused with ValueError, never a raw TypeError and never a silent accept;
saved logs are read as bytes and a non-UTF-8 save is refused.
"""

import os
import re
from typing import Dict, List, Optional, Tuple

DEFAULT_TRANSITION_MARKER = "TRANSITION:"

# The verdict tokens run_check prints in its SUMMARY-STABLE line shape
# "VERDICT exit CODE NAME ..." (scripts/required_fast.sh, scripts/check_all.sh).
VERDICT_TOKENS = ("PASS", "FAIL", "NO-DATA")

_SUMMARY_RE = re.compile(r"^(PASS|FAIL|NO-DATA)\s+exit\s+(\S+)\s+(\S+)")
_VERDICT_HEAD_RE = re.compile(r"^(PASS|FAIL|NO-DATA)\s")
_CODE_RE = re.compile(r"^-?[0-9]+$")
_COUNTS_RE = re.compile(
    r"^COUNTS:\s*pass=([0-9]+)\s+fail=([0-9]+)\s+nodata=([0-9]+)\s*$"
)


class GateOutputError(ValueError):
    """Refusal for a missing, empty, corrupt or unrecognized gate output."""


def _require_text(value, what):
    # type: (object, str) -> str
    """Return value when it is a string, else raise GateOutputError."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise GateOutputError(
            "%s must be a string, got %s" % (what, type(value).__name__)
        )
    return value


def parse_gate_output(text, marker=DEFAULT_TRANSITION_MARKER):
    # type: (str, str) -> Dict[str, object]
    """Parse one saved gate output into counts and transition-line names.

    Returns a dict with int pass, fail and nodata, the sorted tuple of the
    transition line's named checks, and the sorted tuple of every check name
    the summary lines carried. Empty, missing, corrupt or unrecognized input
    blocks with GateOutputError; a counts line that contradicts the summary
    lines is corrupt input and raises rather than reading as a pass.
    """
    _require_text(text, "gate output")
    _require_text(marker, "transition marker")
    if marker.strip() == "":
        raise GateOutputError("transition marker is empty")
    if text.strip() == "":
        raise GateOutputError("gate output is empty")

    counts = {"PASS": 0, "FAIL": 0, "NO-DATA": 0}  # type: Dict[str, int]
    named = []  # type: List[str]
    transition_names = None  # type: Optional[Tuple[str, ...]]
    declared_counts = None  # type: Optional[Tuple[int, int, int]]
    for raw_line in text.splitlines():
        stripped = raw_line.rstrip("\r").strip()
        if stripped == "":
            continue
        summary = _SUMMARY_RE.match(stripped)
        if summary:
            code = summary.group(2)
            if not _CODE_RE.match(code):
                raise GateOutputError(
                    "malformed exit code %r on summary line: %r" % (code, stripped)
                )
            counts[summary.group(1)] += 1
            named.append(summary.group(3))
            continue
        if _VERDICT_HEAD_RE.match(stripped):
            raise GateOutputError("malformed summary line: %r" % (stripped,))
        declared = _COUNTS_RE.match(stripped)
        if declared:
            if declared_counts is not None:
                raise GateOutputError("gate output carries more than one counts line")
            declared_counts = (
                int(declared.group(1)),
                int(declared.group(2)),
                int(declared.group(3)),
            )
            continue
        if stripped.startswith(marker):
            if transition_names is not None:
                raise GateOutputError("gate output carries more than one transition line")
            body = stripped[len(marker):].strip()
            if body == "":
                raise GateOutputError("transition line names no checks")
            transition_names = tuple(sorted(body.split()))
            continue

    derived = (counts["PASS"], counts["FAIL"], counts["NO-DATA"])
    if derived == (0, 0, 0):
        raise GateOutputError("gate output carries no summary line")
    if declared_counts is not None and declared_counts != derived:
        raise GateOutputError(
            "counts line %r contradicts %r summary line(s)" % (declared_counts, derived)
        )
    if transition_names is None:
        raise GateOutputError("gate output carries no transition line")
    return {
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "nodata": counts["NO-DATA"],
        "transition_names": transition_names,
        "named_checks": tuple(sorted(named)),
    }


def check_parity_counts(before, after=None, marker=DEFAULT_TRANSITION_MARKER):
    # type: (str, Optional[str], str) -> bool
    """True only when the before and after runs agree on counts and names.

    RQ-PARITY-COUNTS: identical pass, fail and no-data counts and identical
    transition line named checks. A drift in either is reported as False. A
    missing after run, an empty run, a corrupt run or an unrecognized line
    blocks with GateOutputError, which is a ValueError, never a pass and never
    a crash.
    """
    _require_text(before, "before gate output")
    if after is None:
        raise GateOutputError(
            "check_parity_counts needs both the before and the after gate output"
        )
    _require_text(after, "after gate output")
    parsed_before = parse_gate_output(before, marker)
    parsed_after = parse_gate_output(after, marker)
    before_counts = (parsed_before["pass"], parsed_before["fail"], parsed_before["nodata"])
    after_counts = (parsed_after["pass"], parsed_after["fail"], parsed_after["nodata"])
    if before_counts != after_counts:
        return False
    if parsed_before["transition_names"] != parsed_after["transition_names"]:
        return False
    return True


def read_gate_output(path):
    # type: (str) -> str
    """Read one saved gate output as bytes and decode it as UTF-8.

    A path that is not a string, an empty path, a directory, a missing file,
    an unreadable file, an empty file or a file that is not UTF-8 blocks with
    GateOutputError. Nothing here ever returns text for a save it could not
    prove it read whole.
    """
    _require_text(path, "gate output path")
    if path.strip() == "":
        raise GateOutputError("gate output path is empty")
    if os.path.isdir(path):
        raise GateOutputError("gate output path is a directory: %s" % (path,))
    if not os.path.isfile(path):
        raise GateOutputError("gate output path is not a file: %s" % (path,))
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise GateOutputError("gate output at %s could not be read: %s" % (path, exc))
    if raw.strip() == b"":
        raise GateOutputError("gate output at %s is empty" % (path,))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateOutputError("gate output at %s is not UTF-8: %s" % (path, exc))


def screen_stale(before_path, after_path):
    # type: (str, str) -> bool
    """True when a pair cannot be trusted as a real before and after pair.

    The screen reads mtime plus size. The same file named twice, a zero byte
    side, an after file older than its before file, or both sides carrying one
    size and one mtime are stale. A wrong type, an empty path, a directory or
    a missing file blocks with GateOutputError.
    """
    _require_text(before_path, "before gate output path")
    _require_text(after_path, "after gate output path")
    if before_path.strip() == "" or after_path.strip() == "":
        raise GateOutputError("gate output path is empty")
    for candidate in (before_path, after_path):
        if os.path.isdir(candidate):
            raise GateOutputError("gate output path is a directory: %s" % (candidate,))
    try:
        before_stat = os.stat(before_path)
        after_stat = os.stat(after_path)
    except OSError as exc:
        raise GateOutputError("cannot stat the gate output pair: %s" % (exc,))
    try:
        same_file = os.path.samefile(before_path, after_path)
    except OSError:
        same_file = False
    if same_file:
        return True
    if before_stat.st_size == 0 or after_stat.st_size == 0:
        return True
    if after_stat.st_mtime_ns < before_stat.st_mtime_ns:
        return True
    if (
        after_stat.st_size == before_stat.st_size
        and after_stat.st_mtime_ns == before_stat.st_mtime_ns
    ):
        return True
    return False
