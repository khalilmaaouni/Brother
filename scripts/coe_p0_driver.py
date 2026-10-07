#!/usr/bin/env python3
"""P0.1 council round driver command.

WHY THIS EXISTS. The council verdict of 2026-09-20 is the standing order of
work: a work in progress limit of 2 on the one serial landing lane, a stop
rule for work that is off the release chain, and the finish order D3, D10,
D4, D11, D12, D13, then C0. This module is the one command that runs a
single council round over the release board and reports whether the board
obeyed that order while the round ran.

WHAT IT DOES NOT DO. It does not re-decide the order, the limit or the stop
rule. It does not rebuild the red and green loop: coe_loop.run_loop already
decides when two consecutive clean rounds mean PASS and when a criterion
stuck for three rounds means CEILING, and this module composes it. It does
not schedule, dispatch, merge or mark any unit done.

THE ROUND, in order:
  1. The board is read as BYTES and then as DATA, never assumed. An empty
     file, a file that is not valid UTF-8, corrupt JSON, or JSON that is not
     an object is a refusal, never the safe case.
  2. Two concurrent runs over the same board are serialized by a lock file
     beside the board; the second run refuses rather than racing.
  3. A board flagged stale is refreshed (the flag is cleared and the
     revision is raised) before anything else looks at it.
  4. The standing order gate finds every break of the order and repairs the
     board in place. A break that cannot be repaired without an owner
     decision (a pinned unit that is off the release chain, or a pinned set
     that by itself already breaks the limit) refuses.
  5. A board whose recorded council round already ran at the current
     revision and reached PASS is reported done WITHOUT rerunning.
  6. One council round runs over the repaired board, wired to the worker fan
     out: ask scores each criterion from the live board, attack turns every
     standing order break into a concrete exploit, and repair answers red
     with the standing order.
  7. The run record is written under a temp path; nothing here writes to
     $HOME, and nothing here reads a live repository document unless the
     caller names one.

A MISSING ENGINE REFUSES. If scripts/coe_loop.py cannot be imported, every
round refuses; there is no stand in here that would score a board clean.

Python 3.9 floor, standard library only, no network, no clock, no
subprocess.
"""

import json
import os
import sys
import tempfile
from typing import Dict, List, Optional

try:
    import coe_loop
except ImportError as exc:
    # A specific, deliberate catch, never a bare one: a missing engine
    # REFUSES every round (see _loop_engine_or_refuse) instead of being read
    # as a clean board.
    coe_loop = None
    _COE_LOOP_IMPORT_ERROR = exc
else:
    _COE_LOOP_IMPORT_ERROR = None


FINISH_ORDER = ("D3", "D10", "D4", "D11", "D12", "D13", "C0")
WIP_LIMIT = 2
WORKERS_PER_LANE = 3
GRADER_CAP = 5
LANDING_SLOT_MINUTES = (5, 8)
PROBE_TIMEOUT_SECONDS = 600
MAX_ROUNDS = 6
CLEAN_ROUNDS_REQUIRED = 2
CEILING_ROUNDS = 3

BOARD_CRITERIA = ("wip_limit", "release_chain")

_CLEAN_SCORE = 9
_BROKEN_SCORE = 3
_STATES = ("done", "active", "todo")
_LOCK_SUFFIX = ".coe_p0.lock"

EXIT_DONE = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3


class DriverRefused(ValueError):
    """A deliberate refusal from the driver.

    Every caller of this module gets a returned exit code or this ValueError,
    never a raw TypeError and never a silent accept. A missing, empty,
    corrupt or wrongly shaped board refuses; it is never read as the safe
    case.
    """


_LOOP_ERROR = DriverRefused if coe_loop is None else coe_loop.LoopError


def _refuse(reason):
    raise DriverRefused(reason)


def _loop_engine_or_refuse():
    """The loop engine, or a refusal.

    A missing engine must never let a round report a clean board: there is
    no stand in here that approves.
    """
    if coe_loop is None:
        _refuse(
            "the loop engine scripts/coe_loop.py is not importable: %s; "
            "refusing rather than reporting a round as done"
            % (_COE_LOOP_IMPORT_ERROR,)
        )
    return coe_loop


def _ensure_board_object(board):
    if not isinstance(board, dict):
        _refuse("board must be a JSON object, got %s" % (type(board).__name__,))


def _read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        _refuse(
            "board file could not be read (%s): %s"
            % (type(exc).__name__, exc)
        )


def _parse_board(raw):
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            _refuse("board file is not valid UTF-8 text")
    elif isinstance(raw, str):
        text = raw
    else:
        _refuse("board content is neither bytes nor str: %r" % (raw,))
    if not text.strip():
        _refuse("board file is empty")
    try:
        data = json.loads(text)
    except ValueError as exc:
        _refuse("board JSON is corrupt: %s" % (exc,))
    _ensure_board_object(data)
    return data


def _write_board(board_path, board):
    payload = json.dumps(board, sort_keys=True, indent=2) + "\n"
    try:
        with open(board_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except OSError as exc:
        _refuse(
            "board file could not be written (%s): %s"
            % (type(exc).__name__, exc)
        )


def _revision_of(board):
    _ensure_board_object(board)
    revision = board.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int):
        _refuse("board revision must be an int, got %r" % (revision,))
    return revision


def _units_of(board):
    _ensure_board_object(board)
    units = board.get("units")
    if not isinstance(units, list) or not units:
        _refuse("board carries no units list")
    cleaned = []
    for unit in units:
        if not isinstance(unit, dict):
            _refuse("board unit is not an object: %r" % (unit,))
        unit_id = unit.get("id")
        if not isinstance(unit_id, str) or not unit_id:
            _refuse("board unit has no string id: %r" % (unit,))
        state = unit.get("state", "todo")
        if not isinstance(state, str) or state not in _STATES:
            _refuse(
                "board unit %r carries an unknown state %r" % (unit_id, state)
            )
        cleaned.append((unit_id, state))
    return cleaned


def _release_chain_of(board):
    _ensure_board_object(board)
    chain = board.get("release_chain")
    if chain is None:
        chain = list(FINISH_ORDER)
    if not isinstance(chain, list):
        _refuse("board release_chain must be a list")
    for unit_id in chain:
        if not isinstance(unit_id, str) or not unit_id:
            _refuse("board release_chain carries a non string id: %r" % (unit_id,))
    return list(chain)


def _pinned_of(board):
    _ensure_board_object(board)
    pinned = board.get("pinned", [])
    if not isinstance(pinned, list):
        _refuse("board pinned must be a list")
    for unit_id in pinned:
        if not isinstance(unit_id, str) or not unit_id:
            _refuse("board pinned carries a non string id: %r" % (unit_id,))
    return set(pinned)


def find_violations(board):
    """Every standing order break the board currently carries, as a list of
    (rule, detail) pairs; an empty list means the board obeys the order.

    Raises DriverRefused (a ValueError) for a board that is not an object
    carrying readable units and a readable release chain: a corrupt board is
    never read as an orderly one.
    """
    units = _units_of(board)
    chain = _release_chain_of(board)
    chain_set = set(chain)
    active = [unit_id for unit_id, state in units if state == "active"]
    violations = []
    if len(active) > WIP_LIMIT:
        violations.append((
            "wip_limit",
            "%d units active against a work in progress limit of %d"
            % (len(active), WIP_LIMIT),
        ))
    off_chain = sorted(unit_id for unit_id in active if unit_id not in chain_set)
    if off_chain:
        violations.append((
            "release_chain",
            "off chain units active while work remains: %s"
            % (", ".join(off_chain),),
        ))
    return violations


def repair_board(board):
    """Bring the board back to the standing order in place.

    Returns True when the board now obeys the order, and False when it
    cannot be brought back without an owner decision: a pinned unit that is
    off the release chain, or a pinned set that by itself already breaks the
    work in progress limit. Refusing there is the honest answer, not a guess.
    """
    _units_of(board)
    chain = _release_chain_of(board)
    chain_set = set(chain)
    pinned = _pinned_of(board)

    for unit in board["units"]:
        if unit.get("state") == "active" and unit.get("id") not in chain_set:
            if unit.get("id") in pinned:
                return False
            unit["state"] = "todo"

    rank = {unit_id: index for index, unit_id in enumerate(FINISH_ORDER)}
    active = [unit for unit in board["units"] if unit.get("state") == "active"]
    active.sort(key=lambda unit: rank.get(unit.get("id"), len(FINISH_ORDER)))
    keep = 0
    for unit in active:
        if keep < WIP_LIMIT:
            keep += 1
        else:
            if unit.get("id") in pinned:
                return False
            unit["state"] = "todo"
    return not find_violations(board)


def _lock_path(board_path):
    return board_path + _LOCK_SUFFIX


def _acquire_lock(board_path):
    lock_path = _lock_path(board_path)
    try:
        handle = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError:
        return None
    except OSError as exc:
        _refuse(
            "lock file could not be created (%s): %s"
            % (type(exc).__name__, exc)
        )
    os.close(handle)
    return lock_path


def _release_lock(lock_path):
    if not isinstance(lock_path, str) or not lock_path:
        return
    try:
        os.remove(lock_path)
    except OSError:
        return


def _refresh_if_stale(board):
    stale = board.get("stale", False)
    if not isinstance(stale, bool):
        _refuse("board stale flag must be a bool, got %r" % (stale,))
    if stale:
        board["stale"] = False
        board["revision"] = _revision_of(board) + 1
        return True
    return False


def _already_ran(board):
    engine = _loop_engine_or_refuse()
    record = board.get("council_round")
    if not isinstance(record, dict):
        return False
    if record.get("revision") != _revision_of(board):
        return False
    return record.get("outcome") == engine.OUTCOME_PASS


class _WorkerCouncil(object):
    """The worker fan out for one lane: three workers, per the standing
    quality bar."""

    seats = tuple(
        "worker-%d" % index for index in range(1, WORKERS_PER_LANE + 1)
    )


def _order_text(board):
    units = _units_of(board)
    active = [unit_id for unit_id, state in units if state == "active"]
    return "order:%s|active:%s" % (",".join(FINISH_ORDER), ",".join(active))


def _make_ask(board):
    def _ask(seat_id, criterion, prompt):
        broken = {rule for rule, _ in find_violations(board)}
        if criterion in broken:
            return _BROKEN_SCORE
        return _CLEAN_SCORE
    return _ask


def _make_attack(board):
    def _attack(methodology, verdict, round_num):
        candidates = []
        for rule, detail in find_violations(board):
            if rule in BOARD_CRITERIA:
                candidates.append({
                    "criterion": rule,
                    "input": detail,
                    "control_says": "the board reports the order is satisfied",
                    "demonstrated_score": _BROKEN_SCORE,
                })
        return candidates
    return _attack


def _make_repair(board):
    def _repair(methodology, accepted_exploits, round_num):
        # Green answers red with the standing order as the next methodology.
        # The board file itself is written by the standing order gate in
        # _run_priority_round, never from inside the loop.
        return _order_text(board)
    return _repair


def _nominate_worker_council(problem):
    return _WorkerCouncil()


def _run_council_round(board):
    """Exactly one council round over the board, wired to the worker fan
    out. The loop engine is coe_loop.run_loop, composed, never rebuilt."""
    engine = _loop_engine_or_refuse()
    result = engine.run_loop(
        {"id": "p0-priority", "risk_class": "normal"},
        _order_text(board),
        criteria=list(BOARD_CRITERIA),
        ask=_make_ask(board),
        attack=_make_attack(board),
        repair=_make_repair(board),
        max_rounds=MAX_ROUNDS,
        nominate=_nominate_worker_council,
    )
    return result.outcome


def record_council_run(
    board_path: str,
    board: Dict,
    outcome: str,
    record_dir: Optional[str] = None,
) -> str:
    """Write one council run record and return the path it was written to.

    Hermetic: the record goes to a caller named directory, or to a fresh
    temp file when none is named. Nothing here reads or writes $HOME, and
    the board is read as data only.
    """
    _ensure_board_object(board)
    if not isinstance(outcome, str) or not outcome:
        _refuse("record outcome must be a non empty string, got %r" % (outcome,))
    if record_dir is not None:
        if not isinstance(record_dir, str) or not record_dir:
            _refuse(
                "record_dir must be a non empty string, got %r" % (record_dir,)
            )
        if not os.path.isdir(record_dir):
            _refuse("record_dir names no directory: %r" % (record_dir,))
    payload = {
        "board_path": board_path if isinstance(board_path, str) else None,
        "outcome": outcome,
        "revision": _revision_of(board),
        "finish_order": list(FINISH_ORDER),
        "wip_limit": WIP_LIMIT,
        "workers_per_lane": WORKERS_PER_LANE,
        "grader_cap": GRADER_CAP,
        "landing_slot_minutes": list(LANDING_SLOT_MINUTES),
        "probe_timeout_seconds": PROBE_TIMEOUT_SECONDS,
    }
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="council_round_",
        suffix=".json",
        dir=record_dir,
        delete=False,
    )
    try:
        handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    finally:
        handle.close()
    return handle.name


def _run_priority_round(board_path):
    if os.path.isdir(board_path):
        _refuse("board_path names a directory, not a board file")
    raw = _read_bytes(board_path)
    board = _parse_board(raw)
    _revision_of(board)

    lock_path = _acquire_lock(board_path)
    if lock_path is None:
        return EXIT_REFUSED
    try:
        if _refresh_if_stale(board):
            _write_board(board_path, board)

        if find_violations(board):
            repaired = repair_board(board)
            _write_board(board_path, board)
            if not repaired:
                record_council_run(board_path, board, "REFUSED")
                return EXIT_REFUSED

        if _already_ran(board):
            return EXIT_DONE

        try:
            outcome = _run_council_round(board)
        except _LOOP_ERROR:
            record_council_run(board_path, board, "LOOP_ERROR")
            return EXIT_REFUSED

        board["council_round"] = {
            "revision": _revision_of(board),
            "outcome": outcome,
        }
        record_council_run(board_path, board, outcome)
        _write_board(board_path, board)
        if outcome != coe_loop.OUTCOME_PASS:
            return EXIT_REFUSED
        return EXIT_DONE
    finally:
        _release_lock(lock_path)


def run_priority_round(board_path: str) -> int:
    """Run exactly one council round over the board at board_path.

    Returns EXIT_DONE (0) when the board obeyed the standing order, the
    round ran, and the round reached PASS; EXIT_REFUSED (3) for every
    refusal, including a board_path that is not a non empty string. Never a
    raw crash and never a silent accept.
    """
    if isinstance(board_path, bool) or not isinstance(board_path, str):
        return EXIT_REFUSED
    if not board_path:
        return EXIT_REFUSED
    try:
        return _run_priority_round(board_path)
    except DriverRefused:
        return EXIT_REFUSED
    except OSError:
        return EXIT_REFUSED


def driver_main(argv: List[str]) -> int:
    """The command line front door.

    Usage: coe_p0_driver.py round <board_path>
    """
    if not isinstance(argv, list):
        return EXIT_USAGE
    for item in argv:
        if not isinstance(item, str):
            return EXIT_USAGE
    if len(argv) != 2 or argv[0] != "round":
        return EXIT_USAGE
    return run_priority_round(argv[1])


if __name__ == "__main__":
    sys.exit(driver_main(sys.argv[1:]))
