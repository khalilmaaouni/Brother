"""P0.2 standing order publish.

Render and publish the council finish order D3, D10, D4, D11, D12, D13,
then C0 from the launch WBS board, and name the off chain units the stop
rule refuses to spend the landing lane on.

The board is read as bytes, decoded as utf-8 and parsed as JSON. A missing,
unreadable, corrupt or unknown shaped board is refused with a ValueError
naming the reason. Nothing here guesses, and nothing here accepts a bad
board as the safe case.
"""

import hashlib
import json
import os
import tempfile
from typing import Dict, Optional, Tuple

ORDER_UNITS = ["D3", "D10", "D4", "D11", "D12", "D13", "C0"]
WAITS_ON = {"C0": "L5d"}
STATE_TERMINAL = ("DONE",)
ORDER_DIR_NAME = "orders"
ORDER_FILE_NAME = "P0-STANDING-ORDER.md"
PUBLISH_WRITTEN = 0
PUBLISH_ALREADY_DONE = 2


def _require_path(board_path: str) -> str:
    if board_path is None:
        raise ValueError("board path is NO-DATA (None)")
    if isinstance(board_path, bool):
        raise ValueError("board path is NO-DATA (bool)")
    if not isinstance(board_path, str):
        raise ValueError("board path must be a str (got %s)" % type(board_path).__name__)
    if board_path == "":
        raise ValueError("board path is empty (NO-DATA)")
    if "\x00" in board_path:
        raise ValueError("board path contains a null byte (refused)")
    return board_path


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        raise ValueError("board file missing (NO-DATA): %s" % path)
    except IsADirectoryError:
        raise ValueError("board path is a directory, not a file (NO-DATA): %s" % path)
    except PermissionError:
        raise ValueError("board file unreadable (NO-DATA): %s" % path)
    except OSError as exc:
        raise ValueError("board file unreadable (NO-DATA): %s (%s)" % (path, exc.strerror or exc))


def _decode_board(raw: bytes, path: str) -> Dict[str, object]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("board file is not utf-8 (corrupt): %s" % path)
    try:
        document = json.loads(text)
    except ValueError:
        raise ValueError("board file is not valid JSON (corrupt): %s" % path)
    except RecursionError:
        raise ValueError("board file is nested too deeply (corrupt): %s" % path)
    if not isinstance(document, dict):
        raise ValueError("board root is not an object (corrupt): %s" % path)
    return document


def _units_of(document: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    units = document.get("units")
    if units is None:
        raise ValueError("board has no units list (NO-DATA)")
    if not isinstance(units, list):
        raise ValueError("board units is not a list (corrupt)")
    if len(units) == 0:
        raise ValueError("board units list is empty (NO-DATA)")
    seen = {}
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ValueError("board unit at index %d is not an object (corrupt)" % index)
        unit_id = unit.get("id")
        if not isinstance(unit_id, str) or unit_id == "":
            raise ValueError("board unit at index %d has no non empty string id (corrupt)" % index)
        if unit_id in seen:
            raise ValueError("board has duplicate unit id (corrupt): %s" % unit_id)
        seen[unit_id] = unit
    return seen


def _state_of(unit: Dict[str, object]) -> str:
    state = unit.get("state")
    if not isinstance(state, str) or state == "":
        raise ValueError("unit %s has no non empty string state (corrupt)" % unit.get("id"))
    return state


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _order_dir(board_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(board_path)), ORDER_DIR_NAME)


def _order_path(board_path: str) -> str:
    return os.path.join(_order_dir(board_path), ORDER_FILE_NAME)


def _read_order_bytes(path: str) -> Optional[bytes]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _write_atomic(path: str, data: bytes) -> None:
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise ValueError("cannot create order directory (NO-DATA): %s (%s)" % (directory, exc.strerror or exc))
    try:
        handle = tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=".order-", suffix=".tmp", delete=False)
    except OSError as exc:
        raise ValueError("cannot open order temp file (NO-DATA): %s (%s)" % (directory, exc.strerror or exc))
    temp_path = handle.name
    try:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    except OSError:
        handle.close()
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise ValueError("cannot write order temp file (NO-DATA): %s" % temp_path)
    handle.close()
    try:
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise ValueError("cannot place order file (NO-DATA): %s (%s)" % (path, exc.strerror or exc))


def _build_text(document: Dict[str, object], digest: str) -> str:
    units = _units_of(document)
    for unit_id in ORDER_UNITS:
        if unit_id not in units:
            raise ValueError("finish order unit missing from board (NO-DATA): %s" % unit_id)
    lines = []
    lines.append("# P0 standing order of work")
    lines.append("")
    lines.append("source digest: sha256:%s" % digest)
    lines.append("")
    lines.append("## Finish order")
    lines.append("")
    written = 0
    for unit_id in ORDER_UNITS:
        unit = units[unit_id]
        state = _state_of(unit)
        if state in STATE_TERMINAL:
            continue
        written = written + 1
        line = "%d. %s, state %s" % (written, unit_id, state)
        if unit_id in WAITS_ON:
            line = line + ", waits on %s" % WAITS_ON[unit_id]
        lines.append(line)
    if written == 0:
        lines.append("none: every unit in the finish order is DONE")
    lines.append("")
    lines.append("## Off chain and absent from the finish order")
    lines.append("")
    listed = 0
    for unit_id in sorted(units):
        if unit_id in ORDER_UNITS:
            continue
        unit = units[unit_id]
        state = _state_of(unit)
        if state in STATE_TERMINAL:
            continue
        listed = listed + 1
        lines.append("- %s, state %s" % (unit_id, state))
    if listed == 0:
        lines.append("none")
    lines.append("")
    return "\n".join(lines)


def _load(board_path: str) -> Tuple[str, bytes, Dict[str, object]]:
    path = _require_path(board_path)
    raw = _read_bytes(path)
    document = _decode_board(raw, path)
    return path, raw, document


def render_standing_order(board_path: str) -> str:
    """Return the standing order text for the board at board_path."""
    _path, raw, document = _load(board_path)
    return _build_text(document, _sha256(raw))


def publish_standing_order(board_path: str) -> int:
    """Write the standing order beside the board: 0 written, 2 already done."""
    path, raw, document = _load(board_path)
    data = _build_text(document, _sha256(raw)).encode("utf-8")
    target = _order_path(path)
    existing = _read_order_bytes(target)
    if existing is not None and existing == data:
        return PUBLISH_ALREADY_DONE
    _write_atomic(target, data)
    return PUBLISH_WRITTEN
