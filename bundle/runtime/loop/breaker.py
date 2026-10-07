#!/usr/bin/env python3
"""The breaker that parks a role instead of a run.

State lives under BROTHER_OR_STATE_ROOT: breakers.json (the state),
breakers.lock (the flock target), breaker-events.jsonl (append only) and
quarantined copies named breakers.corrupt-<UTC yyyymmddTHHMMSSZ>.json.

Every public function refuses a hostile argument with ValueError before it
touches state. An unreadable state file or a held lock raises BreakerNoData
so the caller refuses the call (fail closed). A corrupt file is quarantined
and replaced CLOSED.
"""
import fcntl
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

STATUSES = ("ANSWERED", "EMPTY", "MALFORMED", "TIMEOUT", "LIMIT", "OVERLOAD",
            "AUTH", "TRANSPORT_DOWN", "PROVIDER_REFUSED", "REFUSED_BY_GATE", "CONFIG")
COUNTING = ("LIMIT", "OVERLOAD", "AUTH", "TRANSPORT_DOWN", "EMPTY", "MALFORMED")
TRIP_AT = 3
STREAK_WINDOW_S = 600.0
DEFAULT_WAIT_S = {"bridge": 300.0, "claude": 3600.0, "codex": 3600.0}
LOCK_WAIT_S = 10.0
MAX_RESET_AHEAD_S = 8 * 86400.0
SCHEMA = "brother-breaker-v1"

ACCOUNT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ISO_RE = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:[.][0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2}))"
)

_MODE_WARNED = []

# CONFIG: THE PROGRAM DOES NOT KNOW THE MODEL (2026-09-30 incident: a Claude only run pinned an older `claude` that
# answered every call with "[claude-code:unrecognized_model]"; all 78 attempts were counted as model losses and 7 sub
# units parked EXHAUSTED in 6 minutes). No retry, no other prompt and no other round can change that answer, so it is a
# configuration fault, never a model's failure. Matched only on a FAILED call (non zero exit or an error result), over
# the WHOLE raw text before any truncation. A generic 404, or a successful answer that merely discusses these words,
# is not CONFIG. failure_ledger.py carries this same pattern (a test holds the two equal).
CONFIG_PATTERN = (r"unrecognized_model|model_not_found|not_found_error[^\n]{0,200}model|unsupported[ _-]model"
                  r"|model[^\n]{0,80}\bnot supported|does not support (?:the |this )?model|issue with the selected model"
                  r"|\bmodel\b[^\n]{0,80}\bdoes not exist|not a valid model|No endpoints found for"
                  r"|invalid_model|\bunknown model\b|model_not_available|requested model is not available"
                  r"|HTTP 404[^\n]{0,200}\bmodel\b|\bmodel\b[^\n]{0,200}HTTP 404")
CONFIG_RE = re.compile(CONFIG_PATTERN, re.I)


def is_config(text):
    """True when this failure text says the program or endpoint does not know the requested model."""
    return isinstance(text, str) and bool(CONFIG_RE.search(text))


def _without(text, *echoes):
    """text with every echo of the prompt or the answer taken out: a phrase a model read or wrote is not an error."""
    for e in echoes:
        if isinstance(e, str) and len(e.strip()) >= 8:
            text = text.replace(e, " ")
    return text


def config_channels(transport, stdout, stderr, claude_doc=None, prompt=None):
    """THE ONLY TEXT CONFIG IS READ FROM (attack R1 F3, 2026-10-01): structured error channels. stderr, with the prompt
    and the answer taken out (codex exec echoes the prompt; a brief quoting this file must not trip its own model), and
    for Claude the result record's error type fields (error, error_type, subtype), never its result text, which is the
    model's own words. Measured 2026-10-01 against the 2.1.251 CLI: stderr carries "[claude-code:unrecognized_model]",
    while the result says only "does not support this model" at api_error_status 400."""
    parts = [_without(stderr or "", prompt, stdout)]
    if transport == "claude" and isinstance(claude_doc, dict):
        for k in ("error", "error_type", "subtype"):
            v = claude_doc.get(k)
            if v:
                if isinstance(v, dict):   # an API error object: its type first, then its message, as the API writes it
                    parts.append(" ".join(str(v.get(f) or "") for f in ("type", "code", "message")))
                else:
                    parts.append(v if isinstance(v, str) else json.dumps(v, sort_keys=True))
    return "\n".join(parts)


def config_key(transport, model):
    """The mandatory configuration breaker's key: one per (transport, canonical model id), account free, because an
    unknown model is unknown to the program for every account."""
    if not isinstance(transport, str) or transport not in ("bridge", "claude", "codex"):
        raise ValueError("transport must be bridge, claude or codex")
    if not isinstance(model, str) or not model or ":" in model or "\n" in model:
        raise ValueError("model must be a non-empty string without a colon or a newline")
    return "config:%s:%s" % (transport, model)


class BreakerNoData(ValueError):
    """The state cannot be read, written or locked; the caller refuses the call. A ValueError, like every refusal
    in the loop (FX-08, scripts/test_refusal_classes.py): the landing fuzz reads any other base as a crash."""


def _is_num(v):
    if isinstance(v, bool):
        return False
    if not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except (TypeError, ValueError, OverflowError):
        return False


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def state_root(env=None):
    if env is not None and not isinstance(env, dict):
        raise ValueError("env must be a dict or None")
    e = env if env is not None else os.environ
    v = e.get("BROTHER_OR_STATE_ROOT")
    if v is None or v == "":
        return os.path.expanduser("~/.claude/brother-or-dispatch-state")
    if not isinstance(v, str):
        raise ValueError("BROTHER_OR_STATE_ROOT must be a string")
    return v


def mode(env=None):
    if env is not None and not isinstance(env, dict):
        raise ValueError("env must be a dict or None")
    e = env if env is not None else os.environ
    v = e.get("BROTHER_BREAKER")
    if v is None or v == "":
        return "off"
    if not isinstance(v, str):
        raise ValueError("BROTHER_BREAKER must be a string")
    low = v.strip().lower()
    if low == "off":
        return "off"
    if low == "on":
        return "on"
    if not _MODE_WARNED:
        _MODE_WARNED.append(True)
        sys.stderr.write(
            "BREAKER NO-DATA: BROTHER_BREAKER=%s is not on or off; read as off\n" % v)
    return "off"


def account_of(transport, argv, env=None):
    if not isinstance(transport, str) or transport not in ("bridge", "claude", "codex"):
        raise ValueError("transport must be bridge, claude or codex")
    if not isinstance(argv, list):
        raise ValueError("argv must be a list")
    for a in argv:
        if not isinstance(a, str):
            raise ValueError("argv members must be strings")
    if env is not None and not isinstance(env, dict):
        raise ValueError("env must be a dict or None")
    e = env if env is not None else os.environ
    if transport == "bridge":
        acct = None
        i = 0
        n = len(argv)
        while i < n:
            a = argv[i]
            if a == "--account":
                if i + 1 < n:
                    acct = argv[i + 1]
                break
            if a.startswith("--account="):
                acct = a.split("=", 1)[1]
                break
            i += 1
        if not acct:
            u = e.get("USER")
            acct = u if isinstance(u, str) and u else "default"
    else:
        acct = "default"
    if not isinstance(acct, str) or not ACCOUNT_RE.match(acct):
        raise ValueError("account must match [A-Za-z0-9._-]{1,64}")
    return acct


def key(transport, account, model, status, shadow=False):
    if not isinstance(transport, str) or transport not in ("bridge", "claude", "codex"):
        raise ValueError("transport must be bridge, claude or codex")
    if not isinstance(account, str) or not ACCOUNT_RE.match(account):
        raise ValueError("account must match [A-Za-z0-9._-]{1,64}")
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")
    if model == "shadow":
        raise ValueError("model name shadow is refused")
    if not isinstance(status, str) or status not in COUNTING:
        raise ValueError("status must be a counting status")
    if not isinstance(shadow, bool):
        raise ValueError("shadow must be a bool")
    if status in ("LIMIT", "OVERLOAD", "EMPTY", "MALFORMED"):
        base = transport + ":" + account + ":" + model
    else:
        base = transport + ":" + account
    if shadow:
        return base + ":shadow"
    return base


def keys_for(transport, account, model, shadow=False):
    return [key(transport, account, model, "LIMIT", shadow),
            key(transport, account, model, "AUTH", shadow)]


def family(row):
    if not isinstance(row, dict):
        raise ValueError("row must be a dict")
    t = row.get("transport")
    if t == "claude":
        return "anthropic"
    if t == "codex":
        return "openai"
    if t == "bridge":
        rid = row.get("id")
        if isinstance(rid, str) and "/" in rid:
            return rid.split("/", 1)[0]
    return None


def parse_reset(text, now=None):
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if now is not None and not _is_num(now):
        raise ValueError("now must be a number")
    t = float(now) if now is not None else time.time()
    for m in ISO_RE.finditer(text):
        s = m.group(1)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            continue  # sbe: allow-silent a candidate that is not a real timestamp is not a reset time; the scan reads the next one
        if dt.tzinfo is None:
            continue
        try:
            epoch = dt.timestamp()
        except (OverflowError, OSError, ValueError):
            continue
        if epoch < t:
            continue
        if epoch > t + MAX_RESET_AHEAD_S:
            continue
        return epoch
    return None


def classify_bridge(returncode, stdout, stderr, prompt=None):
    if not _is_int(returncode):
        raise ValueError("returncode must be an int")
    if not isinstance(stdout, str):
        raise ValueError("stdout must be a string")
    if not isinstance(stderr, str):
        raise ValueError("stderr must be a string")
    if returncode == 0:
        return "ANSWERED" if stdout.strip() else "EMPTY"
    text = stdout + "\n" + stderr
    if is_config(config_channels("bridge", stdout, stderr, None, prompt)):   # stderr only: stdout is the answer
        return "CONFIG"
    if "HTTP 429 from OpenRouter" in text:
        return "LIMIT"
    if re.search(r"HTTP (502|503|524|529)", text):
        return "OVERLOAD"
    if re.search(r"HTTP (401|403)", text):
        return "AUTH"
    if "NO-DATA: no valid key" in text:
        return "AUTH"
    if "NO-DATA: empty answer from" in text:
        return "EMPTY"
    if re.search(r"no answer from .* within", text):
        return "TRANSPORT_DOWN"
    return "MALFORMED"


def classify_cli(transport, returncode, stdout, stderr, claude_doc, prompt=None):
    if not isinstance(transport, str) or transport not in ("claude", "codex"):
        raise ValueError("transport must be claude or codex")
    if not _is_int(returncode):
        raise ValueError("returncode must be an int")
    if not isinstance(stdout, str):
        raise ValueError("stdout must be a string")
    if not isinstance(stderr, str):
        raise ValueError("stderr must be a string")
    if claude_doc is not None and not isinstance(claude_doc, dict):
        raise ValueError("claude_doc must be a dict or None")
    if transport == "claude":
        failed = returncode != 0 or (isinstance(claude_doc, dict) and bool(claude_doc.get("is_error")))
        if failed and is_config(config_channels("claude", stdout, stderr, claude_doc, prompt)):
            return "CONFIG"
        if isinstance(claude_doc, dict):
            if claude_doc.get("is_error"):
                return "MALFORMED"
            if claude_doc.get("stop_reason") == "refusal":
                return "PROVIDER_REFUSED"
        if not stdout.strip():
            return "EMPTY"
        return "ANSWERED"
    if returncode != 0:
        return "CONFIG" if is_config(config_channels("codex", stdout, stderr, None, prompt)) else "MALFORMED"
    return "ANSWERED" if stdout.strip() else "EMPTY"


def _state_path(root):
    return os.path.join(root, "breakers.json")


def _ensure_root(root):
    if os.path.isdir(root):
        return
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        raise BreakerNoData("cannot create state root: %s" % exc)


class _Locked(object):
    def __init__(self, root):
        self.root = root
        self.fd = None

    def __enter__(self):
        _ensure_root(self.root)
        path = os.path.join(self.root, "breakers.lock")
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise BreakerNoData("cannot open the breaker lock: %s" % exc)
        deadline = time.time() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    fd = self.fd
                    self.fd = None
                    try:
                        os.close(fd)
                    except OSError:
                        pass  # sbe: allow-silent cleanup on the way to raising BreakerNoData just below, which is the error that surfaces
                    raise BreakerNoData("the breaker lock is held")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass  # sbe: allow-silent the close below releases the flock anyway, and the state write already succeeded or raised
            try:
                os.close(self.fd)
            except OSError:
                pass  # sbe: allow-silent a close failure on a lock fd loses no data; a flock it leaves held makes the next _Locked read BreakerNoData 'the breaker lock is held', which surfaces as NO-DATA
            self.fd = None
        return False


def _fresh():
    return {"schema": SCHEMA, "keys": {}}


def _write(root, data):
    path = _state_path(root)
    try:
        fd, tmp = tempfile.mkstemp(prefix=".breakers.", suffix=".tmp", dir=root)
    except OSError as exc:
        raise BreakerNoData("cannot create a temp file: %s" % exc)
    try:
        try:
            os.write(fd, json.dumps(data, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass  # sbe: allow-silent cleanup on the way to raising BreakerNoData just below, which is the error that surfaces
        raise BreakerNoData("cannot write breaker state: %s" % exc)


def _event(root, row):
    path = os.path.join(root, "breaker-events.jsonl")
    try:
        with open(path, "ab") as f:
            f.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as exc:
                sys.stderr.write("breaker: event %r written but not synced to %s: %s\n"
                                 % (row.get("event"), path, exc))
    except OSError as exc:
        # The event log is the audit trail of every trip and close: a lost row is said out loud
        # (2026-09-30), never raised, because the breaker state itself was already written.
        sys.stderr.write("breaker: event %r NOT recorded in %s: %s\n" % (row.get("event"), path, exc))


def _quarantine(root, why):
    path = _state_path(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "breakers.corrupt-%s.json" % stamp
    dest = os.path.join(root, name)
    try:
        os.replace(path, dest)
    except OSError as exc:
        raise BreakerNoData("cannot quarantine breaker state: %s" % exc)
    data = _fresh()
    _write(root, data)
    _event(root, {"event": "quarantine", "name": name, "why": why, "t": time.time()})
    sys.stderr.write(
        "BREAKER ALARM: corrupt breaker file quarantined as %s\n" % name)
    return data


def _validate_record(rec):
    if not isinstance(rec, dict):
        return "record is not an object"
    if rec.get("state") not in ("closed", "open", "half_open"):
        return "unknown state"
    for field in ("first_at", "opened_at", "until", "wait_s"):
        v = rec.get(field)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "time field is not a number"
    sid = rec.get("streak_ids")
    if sid is not None and not isinstance(sid, list):
        return "streak_ids must be a list"
    claim = rec.get("claim")
    if claim is not None and not isinstance(claim, dict):
        return "claim must be an object"
    return None


def _load(root):
    path = _state_path(root)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        data = _fresh()
        _write(root, data)
        return data
    except IsADirectoryError as exc:
        raise BreakerNoData("breaker state path is a directory: %s" % exc)
    except OSError as exc:
        raise BreakerNoData("breaker state unreadable: %s" % exc)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _quarantine(root, "not valid JSON")
    if not isinstance(data, dict):
        return _quarantine(root, "top level is not an object")
    if data.get("schema") != SCHEMA:
        return _quarantine(root, "wrong schema")
    keys = data.get("keys")
    if not isinstance(keys, dict):
        return _quarantine(root, "keys is not an object")
    for k, rec in list(keys.items()):
        if not isinstance(k, str) or not k:
            return _quarantine(root, "bad key")
        problem = _validate_record(rec)
        if problem:
            return _quarantine(root, problem)
    return data


def _blank_record():
    return {"state": "closed", "streak": 0, "streak_ids": [], "first_at": 0.0,
            "opened_at": 0.0, "until": 0.0, "wait_s": 0.0, "why": "",
            "trips": 0, "claim": None}


def _first_line(detail):
    if not detail:
        return ""
    return detail.splitlines()[0][:300]


def _cut(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit] + "[cut %d chars]" % (len(text) - limit)


def record(key, status, call_id, detail="", now=None):
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a non-empty string")
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("status must be a member of STATUSES")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call_id must be a non-empty string")
    if not isinstance(detail, str):
        raise ValueError("detail must be a string")
    if now is not None and not _is_num(now):
        raise ValueError("now must be a number")
    root = state_root()
    t = float(now) if now is not None else time.time()
    with _Locked(root):
        data = _load(root)
        keys = data["keys"]
        rec = keys.get(key)
        if status == "CONFIG" or key.startswith("config:"):
            return _record_config(root, data, key, rec, status, call_id, detail, t)
        if rec is None:
            rec = _blank_record()
            keys[key] = rec
        if status == "ANSWERED":
            rec["streak"] = 0
            rec["streak_ids"] = []
            if rec.get("state") == "half_open":
                rec["state"] = "closed"
                rec["claim"] = None
                rec["until"] = 0.0
                rec["wait_s"] = 0.0
                _event(root, {"event": "close", "key": key, "t": t})
            _write(root, data)
            return rec
        if status not in COUNTING:
            rec["why"] = status + ": " + _first_line(detail)
            _write(root, data)
            return rec
        if call_id in (rec.get("streak_ids") or []):
            return rec
        if rec.get("state") == "half_open":
            prev = rec.get("wait_s") or 0.0
            new_wait = max(prev * 2.0, 60.0)
            rec["wait_s"] = new_wait
            rec["until"] = t + new_wait
            rec["state"] = "open"
            rec["opened_at"] = t
            rec["trips"] = (rec.get("trips") or 0) + 1
            rec["why"] = status + ": " + _first_line(detail)
            rec["claim"] = None
            rec["streak"] = 0
            rec["streak_ids"] = []
            rec["first_at"] = 0.0
            _write(root, data)
            _event(root, {"event": "reopen", "key": key, "status": status,
                          "t": t, "until": rec["until"], "wait_s": new_wait})
            return rec
        first = rec.get("first_at") or 0.0
        if first and (t - first) > STREAK_WINDOW_S:
            rec["streak"] = 1
            rec["streak_ids"] = [call_id]
            rec["first_at"] = t
        else:
            if not first:
                rec["first_at"] = t
            rec["streak"] = (rec.get("streak") or 0) + 1
            rec["streak_ids"] = (rec.get("streak_ids") or []) + [call_id]
        if rec["streak"] >= TRIP_AT:
            transport = key.split(":", 1)[0]
            default_wait = DEFAULT_WAIT_S.get(transport, 3600.0)
            parsed = parse_reset(detail, now=t)
            rec["until"] = parsed if parsed is not None else t + default_wait
            rec["wait_s"] = rec["until"] - t
            rec["state"] = "open"
            rec["opened_at"] = t
            rec["trips"] = (rec.get("trips") or 0) + 1
            rec["why"] = status + ": " + _first_line(detail)
            rec["claim"] = None
            _write(root, data)
            _event(root, {"event": "trip", "key": key, "status": status,
                          "t": t, "until": rec["until"],
                          "detail": _cut(detail, 2000)})
            return rec
        rec["why"] = status + ": " + _first_line(detail)
        _write(root, data)
        if status == "MALFORMED":
            _event(root, {"event": "unrecognised", "key": key,
                          "status": status, "t": t,
                          "detail": _cut(detail, 2000)})
        return rec


def _record_config(root, data, key, rec, status, call_id, detail, t):
    """THE CONFIGURATION BREAKER opens on the FIRST CONFIG, whatever the mode, the three failure threshold or shadow:
    one call is enough to know the program does not know the model. Each opening is a new GENERATION. Only a CONFIG
    changes it; any other status on a config key (a stale success from a call admitted before the trip) is ignored,
    and only close_config() with a proof newer than the opening closes it. Called under the lock."""
    if not key.startswith("config:") or status != "CONFIG":
        return rec if rec is not None else _blank_record()   # a stale answer never closes a configuration fault
    if rec is None:
        rec = _blank_record()
        data["keys"][key] = rec
    was_open = rec.get("state") == "open"
    rec.update(config=True, state="open", until=0.0, wait_s=0.0, claim=None, streak=0, streak_ids=[], first_at=0.0,
               why="CONFIG: " + _first_line(detail))
    if not was_open:
        rec["opened_at"] = t
        rec["generation"] = int(rec.get("generation") or 0) + 1
        rec["trips"] = (rec.get("trips") or 0) + 1
    _write(root, data)
    if not was_open:
        _event(root, {"event": "config_trip", "key": key, "call_id": call_id, "t": t,
                      "generation": rec["generation"], "detail": _cut(detail, 2000)})
    return rec


def close_config(key, proved_at, now=None):
    """(True, why) when a reachability proof made AFTER this key's opening closes it; (False, why) otherwise. A proof
    older than, or equal to, the opening is stale evidence about the previous configuration and never closes it."""
    if not isinstance(key, str) or not key.startswith("config:"):
        raise ValueError("key must be a config key")
    if not _is_num(proved_at):
        raise ValueError("proved_at must be a number")
    root = state_root()
    t = float(now) if now is not None else time.time()
    with _Locked(root):
        data = _load(root)
        rec = data["keys"].get(key)
        if rec is None or rec.get("state") != "open":
            return True, "not open"
        if float(proved_at) <= float(rec.get("opened_at") or 0.0):
            return False, "the proof at %s is not newer than the opening at %s (generation %s)" % (
                proved_at, rec.get("opened_at"), rec.get("generation"))
        rec.update(state="closed", claim=None, why="", closed_at=t)
        _write(root, data)
        _event(root, {"event": "config_close", "key": key, "t": t, "proved_at": float(proved_at),
                      "generation": rec.get("generation")})
        return True, "closed by the proof at %s" % proved_at


def config_open(key):
    """'' when the configuration breaker for key is closed or absent, else why it is open. Reads only; an unreadable
    state is open (BreakerNoData is said as the reason): an unknown never reads as the safe case."""
    if not isinstance(key, str) or not key.startswith("config:"):
        raise ValueError("key must be a config key")
    root = state_root()
    try:
        with _Locked(root):
            rec = _load(root)["keys"].get(key)
    except BreakerNoData as exc:
        return "the configuration breaker cannot be read: %s" % exc
    if isinstance(rec, dict) and rec.get("config") and rec.get("state") == "open":
        return "%s open since %s (%s)" % (key, _hhmm(rec.get("opened_at") or 0.0), (rec.get("why") or "CONFIG")[:160])
    return ""


def last_config_close():
    """The newest time any configuration breaker was closed by a fresh proof, 0.0 when none or unreadable. A closing
    proof is a FACT for a sub unit parked CONFIG_WAIT (runner_pool): the fault it waited on is proven gone."""
    root = state_root()
    try:
        with _Locked(root):
            keys = _load(root)["keys"]
    except BreakerNoData:
        return 0.0
    times = [r.get("closed_at") for k, r in keys.items() if k.startswith("config:") and isinstance(r, dict)
             and r.get("state") == "closed" and _is_num(r.get("closed_at"))]
    return float(max(times)) if times else 0.0


def _hhmm(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "??:??"


def admit(key_list, call_id, ttl_s=360.0, now=None):
    if not isinstance(key_list, list):
        raise ValueError("key_list must be a list")
    for k in key_list:
        if not isinstance(k, str) or not k:
            raise ValueError("key_list members must be non-empty strings")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call_id must be a non-empty string")
    if not _is_num(ttl_s):
        raise ValueError("ttl_s must be a number")
    if now is not None and not _is_num(now):
        raise ValueError("now must be a number")
    root = state_root()
    t = float(now) if now is not None else time.time()
    with _Locked(root):
        data = _load(root)
        keys = data["keys"]
        half = False
        changed = False
        for item in key_list:
            rec = keys.get(item)
            if rec is None:
                continue
            if rec.get("config") and rec.get("state") == "open":
                if changed:
                    _write(root, data)
                return ("OPEN", "config: %s open since %s (%s); a fresh reachability proof closes it" % (
                    item, _hhmm(rec.get("opened_at") or 0.0), (rec.get("why") or "CONFIG")[:160]))
            if rec.get("state") == "open":
                until = rec.get("until") or 0.0
                if t < until:
                    why_status = (rec.get("why") or "").split(":", 1)[0] or "?"
                    reason = "capacity: %s open until %s (%s)" % (
                        item, _hhmm(until), why_status)
                    if changed:
                        _write(root, data)
                    return ("OPEN", reason)
                rec["state"] = "half_open"
                rec["claim"] = None
                rec["streak"] = 0
                rec["streak_ids"] = []
                rec["first_at"] = 0.0
                changed = True
            if rec.get("state") == "half_open":
                half = True
                claim = rec.get("claim")
                if isinstance(claim, dict):
                    cid = claim.get("id")
                    at = claim.get("at") or 0.0
                    ttl = claim.get("ttl_s") or 0.0
                    if cid == call_id and t < at + ttl:
                        continue
                    if t < at + ttl:
                        if changed:
                            _write(root, data)
                        return ("OPEN", "capacity: %s half open already claimed" % item)
                rec["claim"] = {"id": call_id, "pid": os.getpid(),
                                "at": t, "ttl_s": float(ttl_s)}
                changed = True
                _event(root, {"event": "half_open_claim", "key": item,
                              "call_id": call_id, "t": t,
                              "ttl_s": float(ttl_s)})
                continue
        if changed:
            _write(root, data)
        return ("HALF_OPEN" if half else "CLOSED", None)


def open_keys(*, now=None):
    if now is not None and not _is_num(now):
        raise ValueError("now must be a number")
    root = state_root()
    with _Locked(root):
        data = _load(root)
        out = []
        for k in data["keys"]:
            rec = data["keys"][k]
            if isinstance(rec, dict) and rec.get("state") == "open":
                out.append(k)
        return out


def summary(run_start, env=None):
    if not _is_num(run_start):
        raise ValueError("run_start must be a number")
    if env is not None and not isinstance(env, dict):
        raise ValueError("env must be a dict or None")
    if mode(env) == "off":
        return (["BREAKER off"], None)
    root = state_root(env)
    try:
        with _Locked(root):
            data = _load(root)
            lines = []
            for k in sorted(data["keys"]):
                rec = data["keys"][k]
                if isinstance(rec, dict) and rec.get("state") == "open" and rec.get("config"):
                    lines.append("BREAKER %s open (CONFIG, generation %s): held until a fresh reachability proof" % (
                        k, rec.get("generation")))
                elif isinstance(rec, dict) and rec.get("state") == "open":
                    until = rec.get("until") or 0.0
                    why_status = (rec.get("why") or "").split(":", 1)[0] or "?"
                    lines.append("BREAKER %s open until %s (%s)" % (
                        k, _hhmm(until), why_status))
            if not lines:
                lines.append("BREAKER closed")
            try:
                for name in sorted(os.listdir(root)):
                    if name.startswith("breakers.corrupt-") and name.endswith(".json"):
                        p = os.path.join(root, name)
                        try:
                            mt = os.path.getmtime(p)
                        except OSError as exc:
                            # A quarantined file whose age cannot be read is still an alarm (2026-09-30).
                            lines.append("BREAKER ALARM: corrupt breaker file quarantined as %s "
                                         "(its age could not be read: %s)" % (name, exc))
                            continue
                        if mt >= run_start:
                            lines.append(
                                "BREAKER ALARM: corrupt breaker file quarantined as %s" % name)
            except OSError as exc:
                # An alarm scan that could not run is NO-DATA, never a quiet "no alarm" (2026-09-30).
                lines.append("BREAKER NO-DATA: the quarantine alarm scan could not list %s: %s" % (root, exc))
            return (lines, None)
    except BreakerNoData as exc:
        return (["BREAKER NO-DATA: %s" % exc], str(exc))


def _rmtree(path):
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        p = os.path.join(path, name)
        if os.path.isdir(p) and not os.path.islink(p):
            _rmtree(p)
        else:
            try:
                os.remove(p)
            except OSError:
                pass  # sbe: allow-silent selftest-only temp tree cleanup; a leftover file changes no verdict
    try:
        os.rmdir(path)
    except OSError:
        pass  # sbe: allow-silent selftest-only temp tree cleanup; a leftover directory changes no verdict


def _selftest_body():
    saved_root = os.environ.get("BROTHER_OR_STATE_ROOT")
    saved_mode = os.environ.get("BROTHER_BREAKER")
    made = []
    cases = []

    def fresh():
        d = tempfile.mkdtemp(prefix="breaker-selftest-")
        made.append(d)
        os.environ["BROTHER_OR_STATE_ROOT"] = d
        os.environ["BROTHER_BREAKER"] = "on"
        _MODE_WARNED[:] = []
        return d

    def case(name):
        def deco(fn):
            cases.append((name, fn))
            return fn
        return deco

    @case("missing file is created closed")
    def c_missing():
        fresh()
        state, reason = admit(["claude:default:opus55"], "c1")
        return (state == "CLOSED" and reason is None
                and os.path.isfile(_state_path(state_root())))

    @case("two LIMIT do not trip")
    def c_two_limit():
        fresh()
        record("claude:default:opus55", "LIMIT", "c1")
        record("claude:default:opus55", "LIMIT", "c2")
        state, _ = admit(["claude:default:opus55"], "c3")
        return state == "CLOSED"

    @case("three MALFORMED trip, two do not")
    def c_three_malformed():
        fresh()
        for i in range(3):
            record("bridge:u:m", "MALFORMED", "c%d" % i)
        state, _ = admit(["bridge:u:m"], "c9")
        return state == "OPEN"

    @case("unknown status refused")
    def c_unknown_status():
        fresh()
        try:
            record("bridge:u:m", "BOGUS", "c1")
        except BreakerNoData:
            return False   # a state refusal is not the input refusal this case is about
        except ValueError:
            return True
        return False

    @case("unknown mode is off and said")
    def c_unknown_mode():
        fresh()
        os.environ["BROTHER_BREAKER"] = "yes"
        _MODE_WARNED[:] = []
        import io as _io
        buf = _io.StringIO()
        olderr = sys.stderr
        sys.stderr = buf
        try:
            m = mode()
        finally:
            sys.stderr = olderr
        return m == "off" and "NO-DATA" in buf.getvalue()

    @case("unrecognised body is MALFORMED verbatim")
    def c_unrecognised_body():
        fresh()
        return classify_bridge(1, "", "some weird failure") == "MALFORMED"

    @case("corrupt file quarantined")
    def c_corrupt():
        fresh()
        root = state_root()
        with open(_state_path(root), "w") as f:
            f.write("not json")
        state, _ = admit(["bridge:u:m"], "c1")
        names = os.listdir(root)
        return state == "CLOSED" and any(
            n.startswith("breakers.corrupt-") for n in names)

    @case("truncated file quarantined")
    def c_truncated():
        fresh()
        root = state_root()
        with open(_state_path(root), "w") as f:
            f.write('{"schema": "brother-breaker-v1", "keys": {')
        state, _ = admit(["bridge:u:m"], "c1")
        return state == "CLOSED"

    @case("one half open claim")
    def c_one_claim():
        fresh()
        k = "bridge:u:m"
        t0 = 1000000.0
        for i in range(3):
            record(k, "LIMIT", "c%d" % i, now=t0)
        s1, _ = admit([k], "first", now=t0 + 4000.0)
        s2, _ = admit([k], "second", now=t0 + 4000.0)
        return s1 == "HALF_OPEN" and s2 == "OPEN"

    @case("stale streak restarts")
    def c_stale():
        fresh()
        k = "bridge:u:m"
        t0 = 1000000.0
        record(k, "LIMIT", "c1", now=t0)
        record(k, "LIMIT", "c2", now=t0 + 700.0)
        state, _ = admit([k], "c3", now=t0 + 700.0)
        return state == "CLOSED"

    @case("half open closes on ANSWERED")
    def c_half_close():
        fresh()
        k = "bridge:u:m"
        t0 = 1000000.0
        for i in range(3):
            record(k, "LIMIT", "c%d" % i, now=t0)
        admit([k], "probe", now=t0 + 4000.0)
        record(k, "ANSWERED", "probe", now=t0 + 4001.0)
        state, _ = admit([k], "after", now=t0 + 4002.0)
        return state == "CLOSED"

    @case("dead claim released")
    def c_dead_claim():
        fresh()
        k = "bridge:u:m"
        t0 = 1000000.0
        for i in range(3):
            record(k, "LIMIT", "c%d" % i, now=t0)
        admit([k], "first", now=t0 + 4000.0, ttl_s=10.0)
        s2, _ = admit([k], "second", now=t0 + 4020.0)
        return s2 == "HALF_OPEN"

    @case("same call id counted once")
    def c_same_id():
        fresh()
        k = "bridge:u:m"
        record(k, "LIMIT", "c1")
        record(k, "LIMIT", "c1")
        with open(_state_path(state_root())) as f:
            data = json.load(f)
        return data["keys"][k]["streak"] == 1

    @case("shadow trip leaves seat closed")
    def c_shadow():
        fresh()
        seat = key("bridge", "u", "m", "LIMIT", False)
        shadow = key("bridge", "u", "m", "LIMIT", True)
        for i in range(3):
            record(shadow, "LIMIT", "c%d" % i)
        state, _ = admit([seat], "c4")
        return state == "CLOSED" and seat != shadow

    @case("parsed reset wins")
    def c_parsed():
        fresh()
        k = "bridge:u:m"
        t0 = 1000000.0
        future = t0 + 3600.0
        stamp = datetime.fromtimestamp(
            future, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        parsed = parse_reset(stamp, now=t0)
        if parsed is None:
            return False
        for i in range(3):
            record(k, "LIMIT", "c%d" % i, detail="reset " + stamp, now=t0)
        with open(_state_path(state_root())) as f:
            data = json.load(f)
        return abs(data["keys"][k]["until"] - parsed) < 2.0

    @case("absurd reset ignored")
    def c_absurd():
        fresh()
        return parse_reset("2099-01-01T00:00:00+00:00", now=time.time()) is None

    @case("answer on closed key is a no op")
    def c_answer_noop():
        fresh()
        k = "bridge:u:m"
        record(k, "ANSWERED", "c1")
        with open(_state_path(state_root())) as f:
            data = json.load(f)
        return data["keys"][k]["streak"] == 0

    bad = []
    for name, fn in cases:
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        if not ok:
            bad.append(name)

    for d in made:
        _rmtree(d)
    if saved_root is None:
        os.environ.pop("BROTHER_OR_STATE_ROOT", None)
    else:
        os.environ["BROTHER_OR_STATE_ROOT"] = saved_root
    if saved_mode is None:
        os.environ.pop("BROTHER_BREAKER", None)
    else:
        os.environ["BROTHER_BREAKER"] = saved_mode
    _MODE_WARNED[:] = []
    if bad:
        for n in bad:
            print("FAIL: " + n)
        print("selftest: %d cases, FAILED: %d" % (len(cases), len(bad)))
        return 1
    print("selftest: %d cases, OK" % len(cases))
    return 0


def selftest():
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s"
              % (type(exc).__name__, str(exc)[:120]))
        return 1


def main(argv=None):
    if argv is None:
        argv = []
    if not isinstance(argv, list):
        raise ValueError("argv must be a list of strings")
    for a in argv:
        if not isinstance(a, str):
            raise ValueError("argv members must be strings")
    if not argv:
        print("usage: breaker.py --selftest | summary [run_start]")
        return 2
    verb = argv[0]
    if verb == "--selftest":
        return selftest()
    if verb == "summary":
        run_start = 0.0
        if len(argv) > 1:
            try:
                run_start = float(argv[1])
            except (TypeError, ValueError):
                print("usage: breaker.py summary [run_start]")
                return 2
        lines, nodata = summary(run_start)
        for line in lines:
            print(line)
        return 2 if nodata else 0
    print("usage: breaker.py --selftest | summary [run_start]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
