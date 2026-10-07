"""Verify an Antigravity end to end log against the L1b contract.

REQ-FIRE-5, REQ-INVOKED, REQ-ADVERSARIAL, REQ-CORRUPT-CLOSED,
REQ-NO-DATA, REQ-TIMEOUT.

Fail closed. A missing, empty, unreadable or non utf-8 log is no_data and
the command line refuses with exit code 2. A log whose entries do not all
share one run_id, whose event_sequence is not 1 to 5, whose recomputed raw
or stderr hash fails, whose adversarial decision is not "deny", whose
corrupt PostToolUse or PreInvocation entry is marked "pass" or lacks a
recomputing stderr hash carrying the expected warning text, or whose
adapter_wall_ms exceeds the per event cap, all fail verification. A no_data
entry, an entry whose adapter_invoked is False, an empty identity field, a
malformed run id or start time, an out of range exit code, or a non bool
schema_only flag also fail or refuse.
"""

import hashlib
import json
import os
import re
import sys

VALID_EVENT_NAMES = frozenset({
    "PreToolUse",
    "PostToolUse",
    "PreInvocation",
    "PostInvocation",
    "Stop",
})

VALID_DECISION_KINDS = frozenset({
    "allow",
    "deny",
    "ask",
    "force_ask",
    "terminate",
    "continue",
    "no_data",
})

VALID_OUTCOMES = frozenset({"pass", "fail", "no_data"})

VALID_ENVIRONMENTS = frozenset({"sandbox", "real"})

VALID_PLATFORMS = frozenset({"linux", "darwin", "windows"})

NO_DATA = "no_data"

# Wall clock caps in milliseconds, indexed by event_sequence 1 to 5.
# REQ-TIMEOUT requires the verifier to recompute this from the log alone and
# never trust the stored outcome.
EVENT_SEQUENCE_TO_CAP_MS = {
    1: 15 * 1000,
    2: 15 * 1000,
    3: 10 * 1000,
    4: 10 * 1000,
    5: 30 * 1000,
}

_EXPECTED_EVENT_ORDER = [1, 2, 3, 4, 5]

REQUIRED_FIELDS = {
    "run_id": str,
    "environment": str,
    "started_at": str,
    "host_name": str,
    "host_version": str,
    "os_platform": str,
    "plugin_path_abs": str,
    "manifest_sha256": str,
    "hooks_sha256": str,
    "mcp_config_sha256": str,
    "event_name": str,
    "event_sequence": int,
    "tool_name": str,
    "tool_input_sha256": str,
    "raw_in_path": str,
    "raw_in_sha256": str,
    "raw_out_path": str,
    "raw_out_sha256": str,
    "decision_verbatim": str,
    "decision_kind": str,
    "adapter_invoked": bool,
    "adapter_exit_code": int,
    "adapter_wall_ms": int,
    "stderr_path": str,
    "stderr_sha256": str,
    "hook_cwd_abs": str,
    "outcome": str,
}

# Identity fields carry a verbatim value or the literal no_data, never "".
NON_EMPTY_IDENTITY_FIELDS = ("host_name", "host_version", "tool_name")

# Paths that must always carry a real value, never "".
NON_EMPTY_PATH_FIELDS = ("plugin_path_abs", "hook_cwd_abs")

# The expected stderr warning for a corrupt non blocking event, per
# REQ-CORRUPT-CLOSED.
EXPECTED_CORRUPT_WARNING = {
    "PostToolUse": b"PostToolUse received corrupt payload",
    "PreInvocation": b"PreInvocation received corrupt payload and cannot block by contract",
}

_HEX_DIGITS = frozenset("0123456789abcdef")

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hex(value, length):
    if not isinstance(value, str):
        return False
    if len(value) != length:
        return False
    lowered = value.lower()
    return all(char in _HEX_DIGITS for char in lowered)


def _looks_like_sha256(value):
    return _is_hex(value, 64)


def _looks_like_run_id(value):
    return _is_hex(value, 32)


def _looks_like_iso8601_utc(value):
    if not isinstance(value, str):
        return False
    return _ISO_UTC_RE.match(value) is not None


def _validate_entry_schema(entry):
    if not isinstance(entry, dict):
        return "entry is not a JSON object"
    for field, expected in REQUIRED_FIELDS.items():
        if field not in entry:
            return "missing field %s" % field
        value = entry[field]
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                return "field %s must be int, got %s" % (field, type(value).__name__)
        elif expected is bool:
            if not isinstance(value, bool):
                return "field %s must be bool, got %s" % (field, type(value).__name__)
        elif expected is str:
            if not isinstance(value, str):
                return "field %s must be str, got %s" % (field, type(value).__name__)
    if not _looks_like_run_id(entry["run_id"]):
        return "run_id must be 32 hex"
    if not _looks_like_iso8601_utc(entry["started_at"]):
        return "started_at must be ISO8601 UTC"
    if entry["environment"] not in VALID_ENVIRONMENTS:
        return "environment must be sandbox or real"
    if entry["os_platform"] not in VALID_PLATFORMS:
        return "os_platform must be linux, darwin or windows"
    if entry["event_name"] not in VALID_EVENT_NAMES:
        return "unknown event name %s" % entry["event_name"]
    if entry["event_sequence"] not in _EXPECTED_EVENT_ORDER:
        return "event_sequence must be 1 to 5, got %s" % entry["event_sequence"]
    if entry["decision_kind"] not in VALID_DECISION_KINDS:
        return "decision_kind invalid"
    if entry["outcome"] not in VALID_OUTCOMES:
        return "outcome invalid"
    exit_code = entry["adapter_exit_code"]
    if exit_code != -1 and not 0 <= exit_code <= 255:
        return "adapter_exit_code must be -1 or 0 to 255, got %s" % exit_code
    if entry["adapter_wall_ms"] < 0:
        return "adapter_wall_ms must be non negative"
    for field in ("manifest_sha256", "hooks_sha256", "mcp_config_sha256"):
        if not _looks_like_sha256(entry[field]):
            return "%s must be 64 hex" % field
    for field in ("raw_in_sha256", "raw_out_sha256"):
        if not _looks_like_sha256(entry[field]):
            return "%s must be 64 hex" % field
    for field in ("stderr_sha256", "tool_input_sha256"):
        value = entry[field]
        if value != NO_DATA and not _looks_like_sha256(value):
            return "%s must be 64 hex or no_data" % field
    for field in NON_EMPTY_IDENTITY_FIELDS + NON_EMPTY_PATH_FIELDS:
        if entry[field] == "":
            return "%s must not be empty" % field
    # REQ-VERSION-PIN: a run with no captured host version pins nothing.
    if entry["host_version"] == NO_DATA:
        return "host_version is no_data: the run pinned no host version"
    return None


_RAW_PATH_FIELDS = ("raw_in_path", "raw_out_path", "stderr_path")


def _resolve_raw_paths(entry, log_dir):
    """A copy of ``entry`` whose relative raw paths are read against the
    log's own directory, never the reader's working directory, so a log
    and its raw bytes verify wherever they are checked out together."""
    entry = dict(entry)
    for key in _RAW_PATH_FIELDS:
        value = entry.get(key)
        if isinstance(value, str) and value and value != NO_DATA and not os.path.isabs(value):
            entry[key] = os.path.normpath(os.path.join(log_dir, value))
    return entry


def _is_corrupt_input(event_name, raw_bytes):
    if event_name not in VALID_EVENT_NAMES:
        return True
    if not raw_bytes or not raw_bytes.strip():
        return True
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    if event_name in ("PreInvocation", "PostInvocation"):
        value = data.get("invocationNum")
        if isinstance(value, bool) or not isinstance(value, int):
            return True
    if event_name == "Stop":
        reason = data.get("terminationReason")
        idle = data.get("fullyIdle")
        if not isinstance(reason, str) or not reason.strip():
            return True
        if not isinstance(idle, bool):
            return True
    return False


def _corrupt_entry_error(entry):
    raw_in_path = entry["raw_in_path"]
    if not raw_in_path or not os.path.isfile(raw_in_path):
        return None
    with open(raw_in_path, "rb") as handle:
        raw_bytes = handle.read()
    event_name = entry["event_name"]
    if not _is_corrupt_input(event_name, raw_bytes):
        return None
    if entry["outcome"] == "pass":
        return "corrupt %s entry marked pass" % event_name
    if entry["stderr_sha256"] == NO_DATA:
        return "corrupt %s missing stderr_sha256" % event_name
    stderr_path = entry["stderr_path"]
    if not stderr_path or not os.path.isfile(stderr_path):
        return "corrupt %s missing stderr file" % event_name
    with open(stderr_path, "rb") as handle:
        stderr_bytes = handle.read()
    expected = EXPECTED_CORRUPT_WARNING[event_name]
    if expected not in stderr_bytes:
        return "corrupt %s stderr missing expected warning" % event_name
    return None


def verify_log(log_path, schema_only=False):
    if not isinstance(log_path, str):
        raise ValueError("log_path must be a string")
    if not isinstance(schema_only, bool):
        raise ValueError("schema_only must be a bool")
    if not os.path.exists(log_path):
        return {"ok": False, "reason": "no_data: log file missing", "entries": []}
    if not os.path.isfile(log_path):
        return {"ok": False, "reason": "no_data: log path is not a regular file", "entries": []}
    try:
        with open(log_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return {"ok": False, "reason": "no_data: cannot read log: %s" % exc, "entries": []}
    if not raw.strip():
        return {"ok": False, "reason": "no_data: log file is empty", "entries": []}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"ok": False, "reason": "no_data: log is not utf-8: %s" % exc, "entries": []}

    entries = []
    for index, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            return {"ok": False, "reason": "corrupt JSON on line %d: %s" % (index + 1, exc), "entries": entries}
        schema_error = _validate_entry_schema(entry)
        if schema_error:
            return {"ok": False, "reason": "schema error line %d: %s" % (index + 1, schema_error), "entries": entries}
        entries.append(_resolve_raw_paths(entry, os.path.dirname(os.path.abspath(log_path))))

    if schema_only:
        return {"ok": True, "reason": "schema only passed", "entries": entries}

    if len(entries) != 5:
        return {"ok": False, "reason": "expected 5 entries, got %d" % len(entries), "entries": entries}

    run_ids = sorted({entry["run_id"] for entry in entries})
    if len(run_ids) != 1:
        return {"ok": False, "reason": "entries span multiple run_id values: %s" % run_ids, "entries": entries}

    sequences = [entry["event_sequence"] for entry in entries]
    if sequences != _EXPECTED_EVENT_ORDER:
        return {"ok": False, "reason": "event_sequence must be 1 to 5 monotonic, got %s" % sequences, "entries": entries}

    for entry in entries:
        if entry["outcome"] == NO_DATA:
            return {"ok": False, "reason": "no_data entry present on seq %d" % entry["event_sequence"], "entries": entries}
        if entry["adapter_invoked"] is not True:
            return {"ok": False, "reason": "adapter_invoked is not True on seq %d" % entry["event_sequence"], "entries": entries}

    for entry in entries:
        for hash_key, path_key in (("raw_in_sha256", "raw_in_path"), ("raw_out_sha256", "raw_out_path")):
            stored = entry[hash_key]
            path = entry[path_key]
            if not path or not os.path.isfile(path):
                return {"ok": False, "reason": "raw file missing for %s: %s" % (hash_key, path), "entries": entries}
            try:
                computed = _sha256_file(path)
            except OSError as exc:
                return {"ok": False, "reason": "cannot hash %s: %s" % (path, exc), "entries": entries}
            if computed != stored:
                return {"ok": False, "reason": "%s mismatch: stored %s computed %s" % (hash_key, stored, computed), "entries": entries}

        if entry["stderr_sha256"] != NO_DATA:
            path = entry["stderr_path"]
            if not path or not os.path.isfile(path):
                return {"ok": False, "reason": "stderr file missing: %s" % path, "entries": entries}
            computed = _sha256_file(path)
            if computed != entry["stderr_sha256"]:
                return {"ok": False, "reason": "stderr_sha256 mismatch for entry %d" % entry["event_sequence"], "entries": entries}

        if entry["tool_name"] == "dangerous_unknown_tool" and entry["decision_kind"] != "deny":
            return {"ok": False, "reason": "adversarial tool did not deny", "entries": entries}

        if entry["adapter_exit_code"] != 0 and entry["decision_kind"] == "allow":
            return {"ok": False, "reason": "nonzero exit with allow decision", "entries": entries}

        cap = EVENT_SEQUENCE_TO_CAP_MS.get(entry["event_sequence"])
        if cap is not None and entry["adapter_wall_ms"] > cap:
            return {"ok": False, "reason": "adapter_wall_ms exceeds cap for seq %d" % entry["event_sequence"], "entries": entries}

        if entry["event_name"] in ("PostToolUse", "PreInvocation"):
            corrupt_error = _corrupt_entry_error(entry)
            if corrupt_error:
                return {"ok": False, "reason": corrupt_error, "entries": entries}

    return {"ok": True, "reason": "all checks passed", "entries": entries}


def main(argv):
    if not isinstance(argv, list):
        raise ValueError("argv must be a list")
    for argument in argv:
        if not isinstance(argument, str):
            raise ValueError("argv entries must be strings")
    if len(argv) < 2:
        print("usage: verify_log.py <log_path> [--schema-only]", file=sys.stderr)
        return 2
    log_path = argv[1]
    if log_path.startswith("-"):
        print("error: log_path must not start with '-'", file=sys.stderr)
        return 2
    schema_only = False
    for flag in argv[2:]:
        if flag == "--schema-only":
            schema_only = True
        else:
            print("error: unknown flag %s" % flag, file=sys.stderr)
            return 2
    try:
        result = verify_log(log_path, schema_only=schema_only)
    except ValueError as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    if result.get("ok"):
        print("OK")
        return 0
    reason = result.get("reason", "")
    print("FAIL: %s" % reason, file=sys.stderr)
    if str(reason).startswith(NO_DATA):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
