"""The peak bench ledger: one JSON row per attempt, a receipt required.

PP1.c holds the row schema, the receipt refusal and the JSONL ledger.
The statistics (shares, bootstrap intervals, cost ratios) live in the
later sub units of this same module.
"""

import json
import math
import os
import random
import sys
from typing import Dict, List, Optional, Tuple

ROW_FIELDS: Tuple[str, ...] = ("task", "arm", "attempt", "receipt", "score", "passed", "cost_usd")

SHARE_KEYS: Tuple[str, ...] = ("task", "arm", "receipt")


def _number(value: object) -> Optional[float]:
    """Return a finite float for an int or a float, else None. Never raises."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    return result


def validate_row(row: object) -> Dict[str, object]:
    """Refuse a malformed row and return a normalised copy of a good one."""
    if not isinstance(row, dict):
        raise ValueError("peak_bench: row refused, not an object")
    if "receipt" not in row:
        raise ValueError("peak_bench: row refused, no receipt")
    for field in ROW_FIELDS:
        if field not in row:
            raise ValueError("peak_bench: row refused, " + field)
    receipt = row["receipt"]
    if not isinstance(receipt, str) or not receipt.strip():
        raise ValueError("peak_bench: row refused, no receipt")
    task = row["task"]
    if not isinstance(task, str) or not task.strip():
        raise ValueError("peak_bench: row refused, task")
    arm = row["arm"]
    if not isinstance(arm, str) or not arm.strip():
        raise ValueError("peak_bench: row refused, arm")
    attempt = row["attempt"]
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("peak_bench: row refused, attempt")
    score = _number(row["score"])
    if score is None or score < 0.0 or score > 1.0:
        raise ValueError("peak_bench: row refused, score")
    passed = row["passed"]
    if not isinstance(passed, bool):
        raise ValueError("peak_bench: row refused, passed")
    cost_usd = _number(row["cost_usd"])
    if cost_usd is None or cost_usd < 0.0:
        raise ValueError("peak_bench: row refused, cost_usd")
    result: Dict[str, object] = {}
    result["task"] = task.strip()
    result["arm"] = arm.strip()
    result["attempt"] = attempt
    result["receipt"] = receipt.strip()
    result["score"] = score
    result["passed"] = passed
    result["cost_usd"] = cost_usd
    for key in row:
        if key not in result:
            result[key] = row[key]
    return result


def new_row(
    task: str,
    arm: str,
    attempt: int,
    receipt: str,
    score: float,
    passed: bool,
    cost_usd: float,
) -> Dict[str, object]:
    """Build the seven field row and validate it, adding no rule of its own."""
    row = {
        "task": task,
        "arm": arm,
        "attempt": attempt,
        "receipt": receipt,
        "score": score,
        "passed": passed,
        "cost_usd": cost_usd,
    }
    return validate_row(row)


def read_ledger(path: str) -> List[Dict[str, object]]:
    """Read every row of a JSONL ledger, refusing anything partial or bad."""
    if not isinstance(path, str) or not path or os.path.isdir(path):
        raise ValueError("peak_bench: ledger unreadable")
    if not os.path.isfile(path):
        raise ValueError("peak_bench: ledger missing")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        raise ValueError("peak_bench: ledger corrupt")
    if not text:
        return []
    if not text.endswith("\n"):
        raise ValueError("peak_bench: ledger torn")
    rows: List[Dict[str, object]] = []
    for line in text.split("\n")[:-1]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("peak_bench: ledger corrupt")
        if not isinstance(parsed, dict):
            raise ValueError("peak_bench: ledger corrupt")
        rows.append(validate_row(parsed))
    return rows


def append_row(path: str, row: object) -> Dict[str, object]:
    """Validate a row, verify the ledger tail, then append exactly one line."""
    validated = validate_row(row)
    if not isinstance(path, str) or os.path.isdir(path) or not path:
        raise ValueError("peak_bench: ledger unreadable")
    if os.path.exists(path):
        read_ledger(path)
    try:
        text = json.dumps(validated, sort_keys=True) + "\n"
    except (TypeError, ValueError):
        raise ValueError("peak_bench: row refused, not json")
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        raise ValueError("peak_bench: ledger unreadable")
    return validated


def shares(rows: List[Dict[str, object]], key: str = "arm") -> Dict[str, float]:
    # Fraction of validated rows per value of a string valued row field.
    if not isinstance(rows, list):
        raise ValueError("peak_bench: rows refused")
    if not isinstance(key, str) or key not in SHARE_KEYS:
        raise ValueError("peak_bench: shares refused, key")
    validated = [validate_row(row) for row in rows]
    if not validated:
        return {}
    counts: Dict[str, int] = {}
    for row in validated:
        value = row[key]
        counts[value] = counts.get(value, 0) + 1
    total = len(validated)
    return {value: counts[value] / total for value in counts}


def bootstrap_interval(
    values: List[float],
    iterations: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> Tuple[float, float]:
    # Percentile bootstrap of the mean of values; hostile arguments refused.
    if not isinstance(values, list) or not values:
        raise ValueError("peak_bench: no values")
    numbers: List[float] = []
    for value in values:
        number = _number(value)
        if number is None:
            raise ValueError("peak_bench: values refused")
        numbers.append(number)
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("peak_bench: iterations refused")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("peak_bench: seed refused")
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, float)
        or not math.isfinite(alpha)
        or alpha <= 0.0
        or alpha >= 1.0
    ):
        raise ValueError("peak_bench: alpha refused")
    rng = random.Random(seed)
    size = len(numbers)
    means: List[float] = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(size):
            total += numbers[rng.randrange(size)]
        means.append(total / size)
    means.sort()
    lo = means[int(alpha / 2.0 * iterations)]
    hi = means[min(iterations - 1, int((1.0 - alpha / 2.0) * iterations))]
    return (lo, hi)


def cost_ratio(rows: List[Dict[str, object]], arm: str, baseline: str) -> float:
    # Total arm cost over total baseline cost; a zero baseline is refused.
    if not isinstance(rows, list):
        raise ValueError("peak_bench: rows refused")
    if not isinstance(arm, str) or not arm:
        raise ValueError("peak_bench: cost ratio refused, arm")
    if not isinstance(baseline, str) or not baseline:
        raise ValueError("peak_bench: cost ratio refused, arm")
    validated = [validate_row(row) for row in rows]
    total_arm = 0.0
    total_baseline = 0.0
    arm_rows = 0
    baseline_rows = 0
    for row in validated:
        if row["arm"] == arm:
            total_arm += row["cost_usd"]
            arm_rows += 1
        if row["arm"] == baseline:
            total_baseline += row["cost_usd"]
            baseline_rows += 1
    if arm_rows == 0:
        raise ValueError("peak_bench: cost ratio refused, no rows for " + arm)
    if baseline_rows == 0:
        raise ValueError("peak_bench: cost ratio refused, no rows for " + baseline)
    if total_baseline <= 0.0:
        raise ValueError("peak_bench: cost ratio refused, baseline cost zero")
    return total_arm / total_baseline


def summarise(rows: List[Dict[str, object]]) -> Dict[str, object]:
    # Per arm summary of validated rows: counts, shares, statistics, cost ratios.
    if not isinstance(rows, list):
        raise ValueError("peak_bench: rows refused")
    validated = [validate_row(row) for row in rows]
    if not validated:
        return {"rows": 0, "shares": {}, "arms": {}, "cost_ratio": {}}
    share_map = shares(validated, "arm")
    by_arm: Dict[str, List[Dict[str, object]]] = {}
    for row in validated:
        arm_name = row["arm"]
        if arm_name in by_arm:
            by_arm[arm_name].append(row)
        else:
            by_arm[arm_name] = [row]
    arms_map: Dict[str, Dict[str, object]] = {}
    for arm_name in sorted(by_arm):
        arm_rows = by_arm[arm_name]
        attempts = len(arm_rows)
        passed = 0
        total_cost = 0.0
        scores: List[float] = []
        for row in arm_rows:
            if row["passed"] is True:
                passed += 1
            total_cost += row["cost_usd"]
            scores.append(row["score"])
        score_mean = sum(scores) / len(scores)
        interval = bootstrap_interval(scores, seed=0)
        if passed == 0:
            cost_per_pass: Optional[float] = None
        else:
            cost_per_pass = total_cost / passed
        arms_map[arm_name] = {
            "attempts": attempts,
            "passed": passed,
            "score_mean": score_mean,
            "score_interval": [interval[0], interval[1]],
            "cost_usd": total_cost,
            "cost_per_pass": cost_per_pass,
        }
    cost_ratio_map: Dict[str, object] = {}
    ordered = sorted(by_arm)
    first_arm = ordered[0]
    first_cost = arms_map[first_arm]["cost_per_pass"]
    for arm_name in ordered[1:]:
        arm_cost = arms_map[arm_name]["cost_per_pass"]
        if arm_cost is None or first_cost is None or first_cost <= 0.0:
            cost_ratio_map[arm_name] = None
        else:
            cost_ratio_map[arm_name] = arm_cost / first_cost
    return {
        "rows": len(validated),
        "shares": share_map,
        "arms": arms_map,
        "cost_ratio": cost_ratio_map,
    }


def write_report(path: str, rows: List[Dict[str, object]]) -> str:
    # Summarise first, then guard the report path, then write sorted JSON.
    summary = summarise(rows)
    if not isinstance(path, str) or not path.strip() or os.path.isdir(path):
        raise ValueError("peak_bench: report path refused")
    text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        raise ValueError("peak_bench: report path refused")
    return text


def main(argv: Optional[List[str]] = None) -> int:
    # Command line: --ledger PATH and --report PATH, any order, later wins.
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(token, str) for token in argv):
        print("peak_bench: refused: usage: --ledger PATH --report PATH", file=sys.stderr)
        raise SystemExit(2)
    ledger = None
    report = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--ledger" or token == "--report":
            if index + 1 >= len(argv):
                print("peak_bench: refused: usage: --ledger PATH --report PATH", file=sys.stderr)
                return 2
            value = argv[index + 1]
            if token == "--ledger":
                ledger = value
            else:
                report = value
            index += 2
        else:
            print("peak_bench: refused: usage: --ledger PATH --report PATH", file=sys.stderr)
            return 2
    if ledger is None or report is None:
        print("peak_bench: refused: usage: --ledger PATH --report PATH", file=sys.stderr)
        return 2
    try:
        if os.path.realpath(ledger) == os.path.realpath(report):
            raise ValueError("peak_bench: ledger and report are the same path")
        rows = read_ledger(ledger)
        write_report(report, rows)
        print("peak_bench: read %d rows from %s" % (len(rows), ledger))
        print("peak_bench: report written to %s" % (report))
        return 0
    except ValueError as exc:
        print("peak_bench: refused: " + str(exc), file=sys.stderr)
        return 2
