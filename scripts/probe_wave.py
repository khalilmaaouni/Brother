"""probe_wave: aggregate one wave of probe outcomes into one verdict per lane.

M3.5 moves the wave runner into scripts/. It reads a wave grades directory,
finds one PASS variant per lane, reads the probe outcomes already captured for
that lane, and writes one row per lane. A lane is CLEAN only when its probes
ran and none was a crash or a wrong accept. A wave with zero probes across
every lane is NO-DATA, never CLEAN. A wave with no PASS lanes exits non zero
with a named reason. A brief carrying a private term is withheld and its
outcomes are never read.

R22 lane_variants reads a wave grades directory and returns one variant per
lane with a PASS grade.
R23 run_probe_wave writes a row per lane with probes run, crash count, wrong
accept count, fenced flag, and verdict.
R24 CLEAN requires at least one adversary whose probes ran with more than zero
probes and zero crash and zero wrong accept.
R25 zero probes across all adversaries is NO-DATA, never CLEAN.
R26 a batch that graded nothing exits non zero with a named reason.
R27 a brief carrying a private term is withheld and never sent.

Nothing here starts a process and nothing here reaches the network.
"""
from typing import Dict, List, Optional

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import grade_build  # noqa: E402
import probe_build  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NO_DATA = 2
EXIT_USAGE = 2

CLEAN = "CLEAN"
NO_DATA = "NO-DATA"
DIRTY = "DIRTY"
WITHHELD = "WITHHELD"
REFUSED = "REFUSED"

#: Every field R23 names for one row, in one place so a test can check shape.
ROW_FIELDS = ("lane", "variant", "probes_run", "crash", "wrong_accept",
              "fenced", "verdict")


def _is_text(value) -> bool:
    """True only for a string that carries something after stripping."""
    return isinstance(value, str) and value.strip() != ""


def _no_constant(name):
    """Reject NaN, Infinity and the other non standard JSON constants."""
    raise ValueError("non standard JSON constant %s" % name)


def _read_json_object(path: str) -> dict:
    """Read one file as bytes and return its JSON object, or ValueError."""
    if not _is_text(path):
        raise ValueError("path must be a non empty string")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("file is unreadable: %s: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not utf-8: %s: %s" % (path, exc))
    try:
        data = json.loads(text, parse_constant=_no_constant)
    except ValueError as exc:
        raise ValueError("file is not valid JSON: %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ValueError("file must hold a JSON object, got %s"
                         % type(data).__name__)
    return data


def _lane_paths(wave_dir: str):
    """Every <lane>.json under wave_dir, sorted, with its lane name."""
    if not _is_text(wave_dir):
        raise ValueError("wave_dir must be a non empty string")
    if not os.path.isdir(wave_dir):
        raise ValueError("wave_dir is not a directory: %s" % wave_dir)
    pairs = []
    try:
        names = os.listdir(wave_dir)
    except OSError as exc:
        raise ValueError("wave_dir is unreadable: %s: %s" % (wave_dir, exc))
    for name in sorted(names):
        if not isinstance(name, str):
            continue
        if not name.endswith(".json"):
            continue
        lane = name[:-5]
        if not lane:
            raise ValueError("lane file has an empty lane name: %s" % name)
        if os.sep in lane or (os.altsep and os.altsep in lane):
            raise ValueError("lane name carries a path separator: %s" % lane)
        pairs.append((lane, os.path.join(wave_dir, name)))
    return pairs


def lane_variants(wave_dir: str) -> Dict[str, str]:
    """One variant per lane with a PASS grade. R22.

    A lane file that is missing, unreadable, not utf-8, not JSON, not an
    object, or whose rows are not a list is corrupt input and raises
    ValueError. The row chosen is the first PASS in input order, through the
    grader's own first_pass, so a lane stops at its first win.
    """
    variants = {}
    for lane, path in _lane_paths(wave_dir):
        data = _read_json_object(path)
        rows = data.get("rows")
        if not isinstance(rows, list):
            raise ValueError("lane %s rows must be a list, got %s"
                             % (lane, type(rows).__name__))
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError("lane %s row %d is not an object"
                                 % (lane, index))
            verdict = row.get("verdict")
            if not _is_text(verdict):
                raise ValueError("lane %s row %d has no non empty string verdict"
                                 % (lane, index))
        row = grade_build.first_pass(rows)
        if row is None:
            continue
        variant = row.get("variant")
        if not _is_text(variant):
            raise ValueError("lane %s PASS row has no non empty string variant"
                             % lane)
        variants[lane] = variant
    return variants


def _ensure_dir(path: str):
    """Make probe_wave_dir, or raise ValueError if it cannot exist."""
    if not _is_text(path):
        raise ValueError("probe_wave_dir must be a non empty string")
    if os.path.exists(path) and not os.path.isdir(path):
        raise ValueError("probe_wave_dir exists and is not a directory: %s"
                         % path)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise ValueError("probe_wave_dir cannot be made: %s: %s"
                         % (path, exc))


def _read_brief(probe_wave_dir: str, lane: str) -> Optional[str]:
    """The brief text for one lane, or None when no brief was written."""
    path = os.path.join(probe_wave_dir, lane + ".brief")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("brief is unreadable: %s: %s" % (path, exc))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("brief is not utf-8: %s: %s" % (path, exc))


def _read_outcomes(probe_wave_dir: str, lane: str) -> List[dict]:
    """The captured probe outcomes for one lane, [] when none were written."""
    path = os.path.join(probe_wave_dir, lane + ".outcomes.json")
    if not os.path.isfile(path):
        return []
    data = _read_json_object(path)
    outcomes = data.get("outcomes")
    if not isinstance(outcomes, list):
        raise ValueError("lane %s outcomes must be a list, got %s"
                         % (lane, type(outcomes).__name__))
    for index, item in enumerate(outcomes):
        if not isinstance(item, dict):
            raise ValueError("lane %s outcome %d is not an object"
                             % (lane, index))
        outcome = item.get("outcome")
        if not _is_text(outcome):
            raise ValueError("lane %s outcome %d has no non empty string outcome"
                             % (lane, index))
    return outcomes


def _row_for(lane: str, variant: str, brief: Optional[str],
             outcomes: List[dict]) -> dict:
    """One row per R23, from one lane's brief and outcomes.

    R27 comes first: a brief carrying a private term is WITHHELD and fenced,
    and its outcomes are never counted. A lane with no brief at all, or with a
    brief and zero outcomes, is NO-DATA. R24: CLEAN needs probes run with zero
    crash and zero wrong accept.
    """
    if brief is not None and grade_build.private_hits(brief) > 0:
        return {"lane": lane, "variant": variant, "probes_run": 0,
                "crash": 0, "wrong_accept": 0, "fenced": True,
                "verdict": WITHHELD}
    if brief is None:
        return {"lane": lane, "variant": variant, "probes_run": 0,
                "crash": 0, "wrong_accept": 0, "fenced": False,
                "verdict": NO_DATA}
    probes_run = len(outcomes)
    crash = 0
    wrong_accept = 0
    for item in outcomes:
        outcome = item["outcome"].upper()
        if outcome == probe_build.CRASH:
            crash += 1
        elif outcome == probe_build.WRONG_ACCEPT:
            wrong_accept += 1
    if crash > 0 or wrong_accept > 0:
        verdict = DIRTY
    elif probes_run > 0:
        verdict = CLEAN
    else:
        verdict = NO_DATA
    return {"lane": lane, "variant": variant, "probes_run": probes_run,
            "crash": crash, "wrong_accept": wrong_accept, "fenced": False,
            "verdict": verdict}


def _write_row(probe_wave_dir: str, lane: str, row: dict):
    """Write one row beside the outcomes it was computed from."""
    path = os.path.join(probe_wave_dir, lane + ".row.json")
    body = json.dumps(row, sort_keys=True)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
    except OSError as exc:
        raise ValueError("row cannot be written: %s: %s" % (path, exc))


def run_probe_wave(wave_dir: str, probe_wave_dir: str,
                   lane_filter: Optional[str] = None) -> int:
    """Write one row per lane and return the batch exit code. R23 to R27.

    R26: no lane with a PASS grade is a refusal with a named reason and a non
    zero exit. R25: zero probes across every lane is NO-DATA and a non zero
    exit, never CLEAN. R23/R24: any DIRTY lane fails the batch, and a batch
    with at least one CLEAN lane and no DIRTY lane is EXIT_OK.
    """
    if not _is_text(wave_dir):
        raise ValueError("wave_dir must be a non empty string")
    if not _is_text(probe_wave_dir):
        raise ValueError("probe_wave_dir must be a non empty string")
    if lane_filter is not None and not _is_text(lane_filter):
        raise ValueError("lane_filter must be None or a non empty string")
    _ensure_dir(probe_wave_dir)
    variants = lane_variants(wave_dir)
    if lane_filter is not None:
        variants = {k: v for k, v in variants.items() if k == lane_filter}
    if not variants:
        print("REFUSED: no lane with a PASS grade to probe", file=sys.stderr)
        return EXIT_FAIL
    rows = []
    for lane in sorted(variants):
        variant = variants[lane]
        brief = _read_brief(probe_wave_dir, lane)
        if brief is not None and grade_build.private_hits(brief) > 0:
            outcomes = []
        else:
            outcomes = _read_outcomes(probe_wave_dir, lane)
        row = _row_for(lane, variant, brief, outcomes)
        _write_row(probe_wave_dir, lane, row)
        rows.append(row)
    any_clean = any(row["verdict"] == CLEAN for row in rows)
    any_dirty = any(row["verdict"] == DIRTY for row in rows)
    if any_dirty:
        print("FAIL: at least one lane is DIRTY", file=sys.stderr)
        return EXIT_FAIL
    if any_clean:
        print("PASS: %d lane(s) CLEAN"
              % sum(1 for r in rows if r["verdict"] == CLEAN))
        return EXIT_OK
    print("NO-DATA: zero probes ran across every lane", file=sys.stderr)
    return EXIT_NO_DATA


def main(argv: Optional[List[str]] = None) -> int:
    """Run one wave named on the command line.

    EXIT_OK when at least one lane is CLEAN, EXIT_FAIL when the arguments are
    wrong, when a lane file is corrupt, or when no lane has a PASS grade, and
    EXIT_NO_DATA when zero probes ran across every lane.
    """
    args = sys.argv[1:] if argv is None else argv
    if not isinstance(args, (list, tuple)):
        print("usage: probe_wave.py <wave_dir> <probe_wave_dir> [lane]",
              file=sys.stderr)
        return EXIT_USAGE
    if len(args) not in (2, 3):
        print("usage: probe_wave.py <wave_dir> <probe_wave_dir> [lane]",
              file=sys.stderr)
        return EXIT_USAGE
    for item in args:
        if not isinstance(item, str):
            print("usage: probe_wave.py <wave_dir> <probe_wave_dir> [lane]",
                  file=sys.stderr)
            return EXIT_USAGE
    wave_dir, probe_wave_dir = args[0], args[1]
    lane_filter = args[2] if len(args) == 3 else None
    try:
        return run_probe_wave(wave_dir, probe_wave_dir, lane_filter)
    except ValueError as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
