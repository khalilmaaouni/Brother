"""Work in progress gate for the COE priority loop, sub unit P0.3.

RQ-WIP: the landing lane carries at most WIP_LIMIT active units.
RQ-STOP: a new open is refused while the lane is full.

The state file is JSON, read as bytes, and it is the single source of
the active count:

    {"schema": "brother-coe-wip-v1", "active": ["P0.3"]}

An empty or whitespace only state file reads as zero active units. A
missing file, a corrupt file, an unknown key, a duplicate key, a
duplicate unit id, a non string unit id and every hostile argument are
refusals: a returned False from wip_gate_check or open_unit, or a
ValueError that names the reason. Nothing here answers from a stale
count, nothing here silently accepts, and nothing here raises a raw
interpreter error.

Missing input blocks: a state file that is absent or unreadable is a
refusal, never a count of zero.
"""

import contextlib
import hashlib
import json
import os
import threading
import time

WIP_LIMIT = 2
STATE_SCHEMA = "brother-coe-wip-v1"
LOCK_WAIT_SECONDS = 2.0
LOCK_POLL_SECONDS = 0.01
CACHE_MAX_ENTRIES = 512

_CACHE_LOCK = threading.Lock()
_UPDATE_LOCK = threading.Lock()
_DECISION_CACHE = {}
_CACHE_STATS = {"hits": 0, "misses": 0}


def _require_path(state_path):
    if type(state_path) is not str:
        raise ValueError(
            "wip gate refuses a state path of type %s; a str is required"
            % type(state_path).__name__
        )
    if not state_path.strip():
        raise ValueError("wip gate refuses an empty state path")
    return state_path


def _require_unit_id(unit_id):
    if type(unit_id) is not str:
        raise ValueError(
            "wip gate refuses a unit id of type %s; a str is required"
            % type(unit_id).__name__
        )
    if not unit_id.strip():
        raise ValueError("wip gate refuses an empty unit id")
    return unit_id


def _refuse_json_constant(name):
    raise ValueError(
        "wip state refuses the json constant %s; unknown input blocks" % name
    )


def _no_duplicate_keys(pairs):
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError(
                "wip state repeats the json key %r; refusing" % key
            )
        seen.add(key)
    return dict(pairs)


def _decode_state(raw):
    """Turn state file bytes into the list of active unit ids."""
    if not raw.strip():
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("wip state bytes are not utf-8 text; refusing")
    try:
        payload = json.loads(
            text,
            parse_constant=_refuse_json_constant,
            object_pairs_hook=_no_duplicate_keys,
        )
    except ValueError as exc:
        raise ValueError("wip state is not valid json; refusing: %s" % exc)
    if type(payload) is not dict:
        raise ValueError("wip state must be a json object; refusing")
    if set(payload.keys()) != {"schema", "active"}:
        raise ValueError("wip state keys must be exactly schema and active; refusing")
    if payload["schema"] != STATE_SCHEMA:
        raise ValueError("wip state schema is not %s; refusing" % STATE_SCHEMA)
    active = payload["active"]
    if type(active) is not list:
        raise ValueError("wip state field active must be a list; refusing")
    seen = set()
    for entry in active:
        if type(entry) is not str:
            raise ValueError(
                "wip state active entries must be strings; refusing %r" % (entry,)
            )
        if not entry.strip():
            raise ValueError("wip state carries an empty unit id; refusing")
        if entry in seen:
            raise ValueError(
                "wip state repeats the unit id %r; counts that cannot all be true are corrupt"
                % entry
            )
        seen.add(entry)
    return list(active)


def _read_state_bytes(state_path):
    _require_path(state_path)
    try:
        with open(state_path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        raise ValueError(
            "wip state file is missing: %s; missing input blocks" % state_path
        )
    except IsADirectoryError:
        raise ValueError(
            "wip state path is a directory: %s; refusing" % state_path
        )
    except OSError as exc:
        raise ValueError(
            "wip state file is unreadable: %s; refusing with %s"
            % (state_path, exc.strerror)
        )


def _state_digest(raw):
    return hashlib.sha256(raw).hexdigest()


def active_lane_count(state_path: str) -> int:
    """Read the state file as bytes and return the count of active units."""
    return len(_decode_state(_read_state_bytes(state_path)))


def wip_gate_check(active_count: int) -> bool:
    """Return True when one more unit may enter the landing lane."""
    if type(active_count) is not int:
        raise ValueError(
            "wip gate refuses active_count of type %s; an int is required"
            % type(active_count).__name__
        )
    if active_count < 0:
        raise ValueError(
            "wip gate refuses the negative active_count %d; a count of active units cannot be below zero"
            % active_count
        )
    return active_count < WIP_LIMIT


def gate_decision(state_path: str) -> bool:
    """Decide whether a NEW unit may open, re-reading the state file.

    The decision is cached by the hash of the state file bytes, so a
    state that has not moved is answered from the cache and a state that
    has moved is never answered from a stale count.
    """
    raw = _read_state_bytes(state_path)
    key = (os.path.abspath(state_path), _state_digest(raw))
    with _CACHE_LOCK:
        if key in _DECISION_CACHE:
            _CACHE_STATS["hits"] += 1
            return _DECISION_CACHE[key]
    decision = wip_gate_check(len(_decode_state(raw)))
    with _CACHE_LOCK:
        if len(_DECISION_CACHE) >= CACHE_MAX_ENTRIES:
            _DECISION_CACHE.clear()
        _DECISION_CACHE[key] = decision
        _CACHE_STATS["misses"] += 1
    return decision


def decision_cache_info():
    """Return the decision cache counters as a tuple of hits and misses."""
    with _CACHE_LOCK:
        return (_CACHE_STATS["hits"], _CACHE_STATS["misses"])


def clear_caches():
    """Drop every cached decision and zero the counters."""
    with _CACHE_LOCK:
        _DECISION_CACHE.clear()
        _CACHE_STATS["hits"] = 0
        _CACHE_STATS["misses"] = 0


@contextlib.contextmanager
def _state_file_lock(state_path):
    lock_path = state_path + ".wip.lock"
    handle = None
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while handle is None:
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError(
                    "wip state lock is held by another writer: %s; refusing rather than counting an unread state"
                    % lock_path
                )
            time.sleep(LOCK_POLL_SECONDS)
        except OSError as exc:
            raise ValueError(
                "wip state lock cannot be created: %s; refusing with %s"
                % (lock_path, exc.strerror)
            )
    try:
        yield
    finally:
        try:
            os.close(handle)
        finally:
            try:
                os.remove(lock_path)
            except OSError:
                pass


def _write_state(state_path, active):
    payload = json.dumps(
        {"schema": STATE_SCHEMA, "active": list(active)},
        sort_keys=True,
        separators=(",", ": "),
    )
    data = (payload + "\n").encode("utf-8")
    directory = os.path.dirname(os.path.abspath(state_path))
    temp_path = os.path.join(directory, ".%s.wip-write" % os.path.basename(state_path))
    with open(temp_path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, state_path)


def open_unit(state_path: str, unit_id: str) -> bool:
    """Open one unit in the landing lane.

    Returns True when the unit is now active, and False when the lane is
    full and the open is refused. Raises ValueError for a hostile unit
    id, a unit id that is already active, a missing or corrupt state
    file, or a writer lock that cannot be taken.
    """
    _require_path(state_path)
    unit_id = _require_unit_id(unit_id)
    with _UPDATE_LOCK:
        with _state_file_lock(state_path):
            active = _decode_state(_read_state_bytes(state_path))
            if unit_id in active:
                raise ValueError(
                    "wip gate refuses to open %r; it is already active" % unit_id
                )
            if not wip_gate_check(len(active)):
                return False
            active.append(unit_id)
            _write_state(state_path, active)
    return True
