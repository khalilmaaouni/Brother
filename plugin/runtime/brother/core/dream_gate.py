#!/usr/bin/env python3
"""Gate ledger primitives for the dream RSI layer (unit D4.a).

A gate line is TSV, newline terminated, with no header and no stored
verdict. This module formats, parses and reads those lines; the verdict is
always rederived from the exit code so a stored file can never lie about it.
"""
from __future__ import annotations

import os
from typing import List, Optional

GATE = "dream.gate"

_PASS = "PASS"
_NO_DATA = "NO-DATA"
_FAIL = "FAIL"


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_exit_code(exit_code):
    if not _is_int(exit_code):
        raise ValueError("exit_code must be an int, got %r" % (exit_code,))
    if exit_code < 0 or exit_code > 255:
        raise ValueError("exit_code must be in 0..255, got %r" % (exit_code,))
    return exit_code


def _check_duration_ms(duration_ms):
    if not _is_int(duration_ms):
        raise ValueError("duration_ms must be an int, got %r" % (duration_ms,))
    if duration_ms < 0:
        raise ValueError("duration_ms must be >= 0, got %r" % (duration_ms,))
    return duration_ms


def _check_check_name(check_name):
    if not isinstance(check_name, str):
        raise ValueError("check_name must be a str, got %r" % (check_name,))
    if not check_name:
        raise ValueError("check_name must be non-empty")
    if "\t" in check_name or "\n" in check_name or "\r" in check_name:
        raise ValueError("check_name must not contain TAB or newline")
    return check_name


def gate_verdict(exit_code: int) -> str:
    """Return PASS for 0, NO-DATA for 2, FAIL otherwise."""
    _check_exit_code(exit_code)
    if exit_code == 0:
        return _PASS
    if exit_code == 2:
        return _NO_DATA
    return _FAIL


def format_gate_line(check_name: str, exit_code: int, duration_ms: int) -> str:
    """Return 'check_name TAB exit_code TAB duration_ms NEWLINE'."""
    _check_check_name(check_name)
    _check_exit_code(exit_code)
    _check_duration_ms(duration_ms)
    return "%s\t%d\t%d\n" % (check_name, exit_code, duration_ms)


def _parse_decimal(text, field, min_value, max_value=None):
    if not isinstance(text, str) or not text:
        raise ValueError("%s must be a decimal integer" % field)
    for ch in text:
        if ch not in "0123456789":
            raise ValueError("%s must be a decimal integer" % field)
    value = int(text)
    if value < min_value:
        raise ValueError("%s must be >= %d" % (field, min_value))
    if max_value is not None and value > max_value:
        raise ValueError("%s must be <= %d" % (field, max_value))
    return value


def parse_gate_line(line: str) -> dict:
    """Return {'check_name': str, 'exit_code': int, 'duration_ms': int, 'verdict': str}."""
    if not isinstance(line, str):
        raise ValueError("line must be a str, got %r" % (line,))
    if not line.endswith("\n"):
        raise ValueError("line must be newline terminated")
    if line.endswith("\r\n"):
        raise ValueError("line must use LF newline, not CRLF")
    line = line[:-1]
    if "\n" in line or "\r" in line:
        raise ValueError("line must not contain newline")
    parts = line.split("\t")
    if len(parts) != 3:
        raise ValueError("line must have exactly three TAB-separated fields")
    check_name = _check_check_name(parts[0])
    exit_code = _parse_decimal(parts[1], "exit_code", 0, 255)
    duration_ms = _parse_decimal(parts[2], "duration_ms", 0, None)
    return {
        "check_name": check_name,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "verdict": gate_verdict(exit_code),
    }


def read_gates(path: str) -> Optional[List[dict]]:
    """Return rows in file order, None when file absent, ValueError on corrupt line."""
    if not isinstance(path, str):
        raise ValueError("path must be a str, got %r" % (path,))
    if not path:
        raise ValueError("path must be non-empty")
    try:
        fh = open(path, "r", encoding="utf-8", newline="")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("cannot read gates file %r: %s" % (path, exc)) from None
    rows = []
    with fh:
        for raw in fh:
            if not raw.endswith("\n"):
                raise ValueError("gate line is not newline terminated: %r" % (raw,))
            rows.append(parse_gate_line(raw))
    return rows
