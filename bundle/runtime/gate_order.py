#!/usr/bin/env python3
"""Propose a reordering of required_fast.sh checks based on gate log history.

This tool reads gate logs, aggregates per-check run/fail/mean-seconds data,
and prints a proposed order of checks by descending fail rate per second.
It never modifies required_fast.sh.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone


class NoDataError(ValueError):
    """Raised when a log has no result lines or input is missing. A ValueError: a documented refusal is a ValueError,
    and the landing fuzz reads it as a refusal (FX-08)."""
    pass


class CorruptLogError(ValueError):
    """Raised when a log contains a malformed result line. A ValueError: a documented refusal is a ValueError, and the
    landing fuzz reads it as a refusal (FX-08)."""
    pass


RESULT_RE = re.compile(
    r'^(?P<status>PASS|FAIL|NO-DATA)\s+'
    r'exit\s+(?P<exit>-?\d+)\s+'
    r'(?P<name>\S+)\s+'
    r'(?P<seconds>\d+)s\s+'
    r'(?P<summary>.*)$'
)

RESULT_PREFIX_RE = re.compile(r'^(PASS|FAIL|NO-DATA)\s+exit\b')


def parse_log(path):
    """Parse a gate log and return a list of result dicts.

    Each dict has keys: name, status, exit, seconds, summary.
    A log with no result lines raises NoDataError.
    A missing file raises NoDataError.
    A malformed result line raises CorruptLogError.
    Hostile input: a path that is not str, bytes or os.PathLike, or a log
    that is not valid UTF-8, is refused with NoDataError or CorruptLogError,
    never a raw interpreter exception.
    """
    if not isinstance(path, (str, bytes, os.PathLike)):
        raise NoDataError(f"gate log path must be a path: {path!r}")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError as exc:
        raise CorruptLogError(f"cannot decode log {path}: {exc}") from exc
    except OSError as exc:
        raise NoDataError(f"cannot read log {path}: {exc}") from exc

    results = []
    for lineno, line in enumerate(lines, 1):
        line = line.rstrip('\n')
        m = RESULT_RE.match(line)
        if m:
            results.append({
                'name': m.group('name'),
                'status': m.group('status'),
                'exit': int(m.group('exit')),
                'seconds': int(m.group('seconds')),
                'summary': m.group('summary'),
            })
        elif RESULT_PREFIX_RE.match(line):
            raise CorruptLogError(
                f"{path}:{lineno}: malformed result line: {line!r}"
            )
        # Other lines (headers, summaries, comments) are ignored.

    if not results:
        raise NoDataError(f"{path}: no result lines")

    return results


def parse_log_rows(path):
    """Return the parsed rows of a gate log; reuse of parse_log only.

    Additive L5d.c helper. No existing name, default, output or exit code
    changes. A path that is not a string is refused with NoDataError rather
    than crashing inside open().
    """
    if not isinstance(path, str):
        raise NoDataError(f"gate log path must be a string: {path!r}")
    return parse_log(path)


def history(log_paths):
    """Aggregate per-check runs, fails, and mean seconds across logs.

    Returns a dict: check name -> {'runs': int, 'fails': int, 'mean_seconds': float}.
    Raises NoDataError if any log has no result lines or cannot be read.
    Raises CorruptLogError if any log has a malformed result line.
    Hostile input: None, a bare string, or any non-sequence of paths is
    refused with NoDataError, never a raw interpreter exception.
    """
    if not isinstance(log_paths, (list, tuple)):
        raise NoDataError(f"log_paths must be a list or tuple of paths: {log_paths!r}")
    agg = {}
    for path in log_paths:
        results = parse_log(path)
        for r in results:
            name = r['name']
            entry = agg.setdefault(name, {'runs': 0, 'fails': 0, 'seconds_total': 0})
            entry['runs'] += 1
            if r['status'] == 'FAIL':
                entry['fails'] += 1
            entry['seconds_total'] += r['seconds']

    if not agg:
        raise NoDataError("no checks found in any log")

    hist = {}
    for name, entry in agg.items():
        runs = entry['runs']
        hist[name] = {
            'runs': runs,
            'fails': entry['fails'],
            'mean_seconds': entry['seconds_total'] / runs if runs > 0 else 0.0,
        }
    return hist


def _fail_rate(entry):
    runs = entry['runs']
    return entry['fails'] / runs if runs > 0 else 0.0


def _score(entry):
    rate = _fail_rate(entry)
    mean = entry['mean_seconds']
    if mean > 0:
        return rate / mean
    return float('inf') if rate > 0 else 0.0


def propose_order(history, current_order):
    """Return the same checks reordered by descending fail rate per second.

    Ties are broken by the original position in current_order.
    Raises ValueError if the sets of checks differ.
    Hostile input: a non-dict history, or a None, string or unhashable
    current_order, is refused with ValueError, never a raw exception.
    """
    if not isinstance(history, dict):
        raise ValueError(f"history must be a dict: {history!r}")
    if not isinstance(current_order, list) or not all(isinstance(name, str) for name in current_order):
        raise ValueError(f"current_order must be a list of strings: {current_order!r}")
    current_set = set(current_order)
    history_set = set(history.keys())
    if current_set != history_set:
        missing = current_set - history_set
        extra = history_set - current_set
        raise ValueError(
            f"check set mismatch: missing from history {sorted(missing)}, "
            f"extra in history {sorted(extra)}"
        )

    order_index = {name: i for i, name in enumerate(current_order)}
    return sorted(
        current_order,
        key=lambda name: (-_score(history[name]), order_index[name]),
    )


def expected_time_to_first_failure(order, history):
    """Return the expected time to the first failure, given a failure occurs.

    Checks are assumed independent. The expected time is conditional on at
    least one check failing; if no check has ever failed in history, this
    function raises NoDataError because the expectation is undefined.
    Raises ValueError if order and history check sets differ.
    Hostile input: a non-dict history, or a None, string or non-string-list
    order, is refused with ValueError, never a raw exception.
    """
    if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
        raise ValueError(f"order must be a list of strings: {order!r}")
    if not isinstance(history, dict):
        raise ValueError(f"history must be a dict: {history!r}")
    if set(order) != set(history.keys()):
        raise ValueError("order and history check sets differ")

    p_no_failure = 1.0
    numerator = 0.0
    for name in order:
        entry = history[name]
        p = _fail_rate(entry)
        mean = entry['mean_seconds']
        numerator += mean * p_no_failure * p
        p_no_failure *= (1.0 - p)

    p_any_failure = 1.0 - p_no_failure
    if p_any_failure <= 0.0:
        raise NoDataError(
            "no failures in history, expected time to first failure is undefined"
        )
    return numerator / p_any_failure


def parse_current_order(gate_path):
    """Extract the current check order from required_fast.sh.

    Returns the unique check names in the order they first appear in
    run_check calls. Raises NoDataError if the file is missing or has no
    run_check lines.
    """
    if not os.path.exists(gate_path):
        raise NoDataError(f"gate script not found: {gate_path}")

    order = []
    seen = set()
    try:
        with open(gate_path, 'r', encoding='utf-8') as f:
            for line in f:
                m = re.search(r'\brun_check\s+"([^"]+)"', line)
                if m:
                    name = m.group(1)
                    if name not in seen:
                        seen.add(name)
                        order.append(name)
    except OSError as exc:
        raise NoDataError(f"cannot read gate script {gate_path}: {exc}") from exc

    if not order:
        raise NoDataError(f"no run_check lines found in {gate_path}")
    return order


# NEW C0.3 functions: pin, forecast, read_pinned_order, constrained_order.
# Existing propose_order, expected_time_to_first_failure, parse_current_order,
# parse_log, history, NoDataError and CorruptLogError are unchanged.
def forecast_seconds(order, hist, margin="p90"):
    """Return (expected, margin) seconds for the whole cut.

    Raises NoDataError if any gate has fewer than 10 runs or no duration
    samples, or if order or history is empty. Raises ValueError on set
    mismatch or bad margin.
    """
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise ValueError("order must be a list of strings")
    if not isinstance(hist, dict):
        raise ValueError("hist must be a dict")
    if not order or not hist:
        raise NoDataError("empty order or history")
    if set(order) != set(hist.keys()):
        raise ValueError("order and history check sets differ")
    if margin not in ("p90", "max"):
        raise ValueError("margin must be p90 or max")
    total_mu = 0.0
    total_var = 0.0
    for name in order:
        entry = hist[name]
        if not isinstance(entry, dict):
            raise ValueError("history entry must be a dict")
        runs = entry.get('runs')
        if type(runs) is not int or runs < 10:
            raise NoDataError(f"gate {name} has fewer than 10 runs")
        seconds = entry.get('seconds')
        if not isinstance(seconds, list) or len(seconds) < 2:
            raise NoDataError(f"gate {name} has no duration samples")
        clean = []
        for v in seconds:
            if type(v) is bool or not isinstance(v, (int, float)):
                raise ValueError("duration sample must be numeric")
            if math.isnan(v) or math.isinf(v):
                raise ValueError("duration sample must be finite")
            clean.append(float(v))
        mu = sum(clean) / len(clean)
        var = sum((d - mu) ** 2 for d in clean) / (len(clean) - 1)
        total_mu += mu
        total_var += var
    sd = math.sqrt(total_var)
    if margin == "p90":
        z = 1.2815515655446004
    else:
        z = 2.0
    return (total_mu, z * sd)


def pin_order(current_order, hist, out_path, gate_sha256, policy_sha256):
    """Write an OrderPin JSON and return it. Existing functions unchanged."""
    if not isinstance(current_order, list) or not all(isinstance(x, str) for x in current_order):
        raise ValueError("current_order must be a list of strings")
    if not isinstance(hist, dict):
        raise ValueError("hist must be a dict")
    if set(current_order) != set(hist.keys()):
        raise ValueError("check set mismatch")
    for label, sha in (("gate_sha256", gate_sha256), ("policy_sha256", policy_sha256)):
        if not isinstance(sha, str) or len(sha) != 64 or not all(c in '0123456789abcdef' for c in sha):
            raise ValueError(f"{label} must be 64 hex")
    expected, margin = forecast_seconds(current_order, hist, margin="p90")
    pin = {
        "schema_version": "c0.orderpin.1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "gate_script_sha256": gate_sha256,
        "policy_sha256": policy_sha256,
        "mandatory_set": list(current_order),
        "order": list(current_order),
        "history_window": {"logs": 0, "from": "UNKNOWN", "to": "UNKNOWN"},
        "expected_seconds": expected,
        "p90_seconds": expected + margin,
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(pin, f, sort_keys=True, indent=2)
        f.write('\n')
    return pin


def read_pinned_order(path):
    """Read and validate an OrderPin file.

    Every unreadable, non-UTF-8, empty, torn, non-object or schema-mismatched
    input raises CorruptLogError naming the path.  Missing files and
    directories are corrupt input too, never a silent default.
    """
    if not isinstance(path, str):
        raise CorruptLogError(f"pinned order path must be a string: {path!r}")
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as exc:
        raise CorruptLogError(f"cannot read pinned order {path!r}: {exc}") from exc
    try:
        text = raw.decode('utf-8')
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CorruptLogError(f"corrupt pinned order {path!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise CorruptLogError(f"pinned order must be a JSON object: {path!r}")
    if data.get('schema_version') != 'c0.orderpin.1':
        raise CorruptLogError(f"pinned order schema mismatch: {path!r}")
    if not isinstance(data.get('mandatory_set'), list) or not isinstance(data.get('order'), list):
        raise CorruptLogError(f"pinned order missing set or order: {path!r}")
    if set(data['mandatory_set']) != set(data['order']):
        raise CorruptLogError(f"pinned order set mismatch: {path!r}")
    return data


def constrained_order(order, hist, dag):
    """Greedy DAG-constrained order by descending prior p/c, current tie break."""
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise ValueError("order must be a list of strings")
    if not isinstance(hist, dict):
        raise ValueError("hist must be a dict")
    if set(order) != set(hist.keys()):
        raise ValueError("order and history check sets differ")
    edges = []
    if isinstance(dag, dict):
        edges = dag.get('edges', [])
    if not isinstance(edges, list):
        raise ValueError("dag edges must be a list")
    prereq = {name: set() for name in order}
    for edge in edges:
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            raise ValueError("dag edge must be a pair")
        before, after = edge
        if before not in prereq or after not in prereq:
            raise ValueError("dag edge names unknown")
        prereq[after].add(before)
    order_index = {name: i for i, name in enumerate(order)}
    remaining = set(order)
    placed = []
    while remaining:
        ready = [n for n in remaining if not (prereq[n] & remaining)]
        if not ready:
            raise ValueError("dag cycle")
        def score(name):
            entry = hist[name]
            runs = entry.get('runs', 0)
            fails = entry.get('fails', 0)
            if type(runs) is int and runs >= 10:
                p = (fails + 1) / (runs + 20)
            else:
                p = 0.05
            mean = entry.get('mean_seconds', 0.0)
            if not isinstance(mean, (int, float)) or isinstance(mean, bool) or mean <= 0:
                return float('inf') if p > 0 else 0.0
            return p / mean
        pick = max(ready, key=lambda n: (score(n), -order_index[n]))
        placed.append(pick)
        remaining.remove(pick)
    return placed


def main(argv=None):
    if argv is not None:
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            raise ValueError(
                f"argv must be a list of strings or None: {argv!r}"
            )
    parser = argparse.ArgumentParser(
        description="Propose a reordering of required_fast.sh checks from gate logs. "
                    "This is a report only; required_fast.sh is never modified."
    )
    parser.add_argument(
        "logs",
        nargs="+",
        help="gate log files to aggregate",
    )
    parser.add_argument(
        "--gate",
        default="scripts/required_fast.sh",
        help="path to required_fast.sh (default: scripts/required_fast.sh)",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1

    try:
        current_order = parse_current_order(args.gate)
        hist = history(args.logs)
        proposed = propose_order(hist, current_order)
        try:
            ettf = expected_time_to_first_failure(proposed, hist)
            ettf_str = f"{ettf:.2f}s"
        except NoDataError as exc:
            ettf_str = f"NO-DATA: {exc}"

        print("Proposed order (report only, required_fast.sh not modified):")
        for i, name in enumerate(proposed, 1):
            entry = hist[name]
            rate = _fail_rate(entry)
            score = _score(entry)
            print(
                f"{i:2d}. {name:25s} "
                f"runs={entry['runs']:3d} "
                f"fails={entry['fails']:3d} "
                f"mean={entry['mean_seconds']:6.2f}s "
                f"fail_rate={rate:.3f} "
                f"score={score:.4f}"
            )
        print(f"Expected time to first failure: {ettf_str}")
    except (NoDataError, CorruptLogError, ValueError) as exc:
        print(f"NO-DATA: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
