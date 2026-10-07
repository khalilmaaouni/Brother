#!/usr/bin/env python3
"""L0.1 decision and invariant gate.

This module is the L0.1 gate of the Brother 1.1.0 unification close. It
reads the founder decisions, the waivers, and the existing unit status,
then runs the EXISTING scripts/release_invariant.py in the given worktree
in process and reads the invariant's stdout.

It returns an exit code, never a bare boolean:

  0  decisions are answered, the invariant exits 0, and every link the
     invariant printed as NO-DATA has a matching, unexpired waiver.
  2  BLOCKED: an unresolved or deferred decision without a valid waiver,
     a red invariant, a NO-DATA link without a valid waiver, or a
     missing or corrupt decisions or waivers document. None of these is
     the safe case, so none is allowed through.
  3  NO-DATA: the unit status is corrupt, truncated, missing a confirmed
     top key, missing an expected unit id, or carrying an extra unit id.

Hostile input (a wrong type, None, a str where a Path belongs, a
directory where a file belongs, bytes that are not UTF-8, a bool where an
int belongs, NaN in place of an answer) is refused with the module's own
deliberate ValueError by the loaders, and refused with exit 2 or 3 by the
gate. Nothing here crashes with a raw interpreter exception.

Standard library only. This module never imports subprocess: the
worktree invariant is loaded and called in process and its stdout and
stderr are captured. No string read from any file is ever executed as a
shell command.
"""
import contextlib
import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path

RUN_ID = "brother-unify-1.1.0-2026-09-19"

#: The 23 unit ids read from the existing status shape. L0.1 validates the
#: status against exactly this inventory; the close step adds "L0" later.
EXPECTED_UNIT_IDS = (
    "U0", "U2", "U1", "U3", "U4", "U5", "B", "C", "D", "F", "E", "K", "A",
    "U6", "J", "U7", "G", "OR-1", "OR-2", "OR-3", "OR-4", "U8", "OR-5",
)

STATUS_TOP_KEYS = (
    "run_id", "stamp_note", "generated_at", "head", "units",
    "decisions_waiting", "decisions_recorded", "risks",
)

DECISION_IDS = ("release_invariant_failure", "handover_pack_client_term")
DECISION_SCOPE = "Brother 1.1.0 L0 closure only"
DECISION_ANSWERS = ("APPROVED", "REJECTED", "DEFERRED")
WAIVER_SCOPES = (
    "invariant_nodata", "deferred_decision", "blocked_unit", "partial_unit",
    "clean_receipt_exception",
)
WAIVER_LINK_SCOPES = ("invariant_nodata", "blocked_unit", "partial_unit")

EXIT_PASS = 0
EXIT_BLOCKED = 2
EXIT_NODATA = 3

NO_DATA_PREFIX = "NO-DATA:"

#: Exception types a broken or hostile release_invariant.py can raise while
#: it is loaded or called. Every one of them becomes a NO-DATA result; a
#: failure is never allowed to read as a pass, and never escapes the gate.
_INVARIANT_FAILURES = (
    OSError, ImportError, SyntaxError, ValueError, TypeError, NameError,
    AttributeError, RuntimeError, LookupError, ArithmeticError,
)


def _as_path(value, name):
    """Every public path argument routes through here, so a str, bytes,
    int, float, None, list or dict is refused with ValueError before any
    file is touched."""
    if not isinstance(value, Path):
        raise ValueError("%s must be a pathlib.Path, not %s"
                         % (name, type(value).__name__))
    return value


def _read_json(path):
    """Read one JSON document as BYTES, decode UTF-8, then json.load. Any
    failure (missing, directory, unreadable, not UTF-8, not JSON) is a
    deliberate ValueError, never a raw OSError or decode error."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc)) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("%s is not valid UTF-8: %s" % (path, exc)) from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("%s is not valid JSON: %s" % (path, exc)) from exc


def _require_str(value, name):
    if not isinstance(value, str):
        raise ValueError("%s must be a string, not %s"
                         % (name, type(value).__name__))
    return value


def _require_optional_str(value, name):
    if value is None:
        return value
    return _require_str(value, name)


def _require_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an int, not %s"
                         % (name, type(value).__name__))
    return value


def _is_iso_with_offset(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_expired(expires_at):
    """An unparseable or offset-less timestamp counts as expired, so a
    malformed waiver can never be the safe case."""
    if not _is_iso_with_offset(expires_at):
        return True
    parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return parsed < datetime.now(timezone.utc)


def _iter_waivers(waivers_data):
    if not isinstance(waivers_data, dict):
        return
    entries = waivers_data.get("waivers")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if isinstance(entry, dict):
            yield entry


def _find_waiver(waivers_data, waiver_id, scope, evidence_ref=None):
    for entry in _iter_waivers(waivers_data):
        if entry.get("waiver_id") != waiver_id:
            continue
        if entry.get("scope") != scope:
            continue
        if scope in WAIVER_LINK_SCOPES and entry.get("evidence_ref") != evidence_ref:
            continue
        if _is_expired(entry.get("expires_at")):
            continue
        return True
    return False


def _has_link_waiver(waivers_data, link):
    for entry in _iter_waivers(waivers_data):
        if entry.get("scope") != "invariant_nodata":
            continue
        if entry.get("evidence_ref") != link:
            continue
        if _is_expired(entry.get("expires_at")):
            continue
        return True
    return False


def load_status(path):
    """Load and validate the existing unit status. Any parse failure, a
    missing confirmed top key, a wrong type for a confirmed key, a missing
    expected unit id, an extra unit id, or a non-object unit entry raises
    ValueError, which the gate turns into NO-DATA (exit 3)."""
    path = _as_path(path, "path")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError("status must be a JSON object")
    for key in STATUS_TOP_KEYS:
        if key not in data:
            raise ValueError("status is missing confirmed key %r" % (key,))
    if data["run_id"] != RUN_ID:
        raise ValueError("status run_id %r is not the L0 run id"
                         % (data["run_id"],))
    _require_str(data["stamp_note"], "stamp_note")
    _require_str(data["generated_at"], "generated_at")
    _require_str(data["head"], "head")
    if not isinstance(data["units"], dict):
        raise ValueError("status units must be a JSON object")
    if not isinstance(data["decisions_waiting"], list):
        raise ValueError("status decisions_waiting must be a list")
    if not isinstance(data["decisions_recorded"], list):
        raise ValueError("status decisions_recorded must be a list")
    if not isinstance(data["risks"], list):
        raise ValueError("status risks must be a list")
    units = data["units"]
    observed = set(units)
    expected = set(EXPECTED_UNIT_IDS)
    missing = sorted(expected - observed)
    if missing:
        raise ValueError("status is missing expected unit ids %s" % (missing,))
    extra = sorted(observed - expected)
    if extra:
        raise ValueError("status carries extra unit ids %s" % (extra,))
    for unit_id, entry in units.items():
        if not isinstance(entry, dict):
            raise ValueError("unit %r must be a JSON object" % (unit_id,))
    return data


def load_decisions(path):
    """Load and validate the founder decisions document. A corrupt file,
    a wrong schema, a wrong run id, an invalid answer, a duplicate or
    missing decision id, a bool unresolved_count, or a null count that
    disagrees with unresolved_count raises ValueError."""
    path = _as_path(path, "path")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError("decisions must be a JSON object")
    if data.get("schema_version") != "l0.founder-decisions.v1":
        raise ValueError("decisions schema_version is not l0.founder-decisions.v1")
    if data.get("run_id") != RUN_ID:
        raise ValueError("decisions run_id is not the L0 run id")
    _require_str(data.get("created_at"), "created_at")
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("decisions must be a list")
    if len(decisions) != len(DECISION_IDS):
        raise ValueError("decisions must hold exactly %d entries"
                         % len(DECISION_IDS))
    unresolved = _require_int(data.get("unresolved_count"), "unresolved_count")
    if not 0 <= unresolved <= len(DECISION_IDS):
        raise ValueError("unresolved_count must be between 0 and %d"
                         % len(DECISION_IDS))
    seen = set()
    null_count = 0
    for entry in decisions:
        if not isinstance(entry, dict):
            raise ValueError("each decision must be a JSON object")
        decision_id = entry.get("id")
        if decision_id not in DECISION_IDS:
            raise ValueError("unknown decision id %r" % (decision_id,))
        if decision_id in seen:
            raise ValueError("duplicate decision id %r" % (decision_id,))
        seen.add(decision_id)
        question = entry.get("question")
        _require_str(question, "question")
        if not question:
            raise ValueError("decision %r has an empty question" % (decision_id,))
        answer = entry.get("answer")
        if answer is None:
            null_count += 1
        elif answer not in DECISION_ANSWERS:
            raise ValueError("decision %r has an invalid answer %r"
                             % (decision_id, answer))
        _require_optional_str(entry.get("answered_by"), "answered_by")
        _require_optional_str(entry.get("answered_at"), "answered_at")
        _require_optional_str(entry.get("evidence_ref"), "evidence_ref")
        if entry.get("scope") != DECISION_SCOPE:
            raise ValueError("decision %r scope must be %r"
                             % (decision_id, DECISION_SCOPE))
        _require_optional_str(entry.get("waiver_id"), "waiver_id")
    if seen != set(DECISION_IDS):
        raise ValueError("decisions are missing one of the two required ids")
    if unresolved != null_count:
        raise ValueError("unresolved_count %d does not equal the %d null answer(s)"
                         % (unresolved, null_count))
    return data


def load_waivers(path):
    """Load and validate the waivers document. A corrupt file, a wrong
    schema, a duplicate or empty waiver_id, an invalid scope, an empty
    reason or evidence_ref, or a timestamp without an offset raises
    ValueError."""
    path = _as_path(path, "path")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError("waivers must be a JSON object")
    if data.get("schema_version") != "l0.waivers.v1":
        raise ValueError("waivers schema_version is not l0.waivers.v1")
    if data.get("run_id") != RUN_ID:
        raise ValueError("waivers run_id is not the L0 run id")
    _require_str(data.get("created_at"), "created_at")
    entries = data.get("waivers")
    if not isinstance(entries, list):
        raise ValueError("waivers must be a list")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each waiver must be a JSON object")
        waiver_id = entry.get("waiver_id")
        _require_str(waiver_id, "waiver_id")
        if not waiver_id:
            raise ValueError("waiver_id must not be empty")
        if waiver_id in seen:
            raise ValueError("duplicate waiver_id %r" % (waiver_id,))
        seen.add(waiver_id)
        if entry.get("scope") not in WAIVER_SCOPES:
            raise ValueError("waiver %r has an invalid scope" % (waiver_id,))
        reason = entry.get("reason")
        _require_str(reason, "reason")
        if not reason:
            raise ValueError("waiver %r has an empty reason" % (waiver_id,))
        _require_str(entry.get("waived_by"), "waived_by")
        if not _is_iso_with_offset(entry.get("waived_at")):
            raise ValueError("waiver %r waived_at must be ISO 8601 with offset"
                             % (waiver_id,))
        if not _is_iso_with_offset(entry.get("expires_at")):
            raise ValueError("waiver %r expires_at must be ISO 8601 with offset"
                             % (waiver_id,))
        evidence_ref = entry.get("evidence_ref")
        _require_str(evidence_ref, "evidence_ref")
        if not evidence_ref:
            raise ValueError("waiver %r has an empty evidence_ref" % (waiver_id,))
    return data


def require_all_answered(data, waivers):
    """(all_answered, unresolved): every decision is APPROVED or REJECTED,
    or a DEFERRED answer is backed by a matching, unexpired
    deferred_decision waiver. A null answer is never answered. A wrong
    type for data or waivers, or an answer outside the enum, raises
    ValueError."""
    if not isinstance(data, dict):
        raise ValueError("data must be a dict, not %s" % (type(data).__name__,))
    if not isinstance(waivers, dict):
        raise ValueError("waivers must be a dict, not %s"
                         % (type(waivers).__name__,))
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("data carries no decision list")
    all_answered = True
    unresolved = 0
    for entry in decisions:
        if not isinstance(entry, dict):
            raise ValueError("each decision must be a dict")
        answer = entry.get("answer")
        if answer is None:
            all_answered = False
            unresolved += 1
        elif answer == "DEFERRED":
            waiver_id = entry.get("waiver_id")
            if not isinstance(waiver_id, str) or not waiver_id:
                all_answered = False
            elif not _find_waiver(waivers, waiver_id, "deferred_decision"):
                all_answered = False
        elif answer in ("APPROVED", "REJECTED"):
            continue
        else:
            raise ValueError("unexpected answer %r" % (answer,))
    return all_answered, unresolved


def _load_invariant_module(worktree):
    script = worktree / "scripts" / "release_invariant.py"
    if not script.is_file():
        raise FileNotFoundError("no release_invariant.py under %s"
                                % (worktree,))
    spec = importlib.util.spec_from_file_location("_l0_release_invariant",
                                                  str(script))
    if spec is None or spec.loader is None:
        raise ImportError("cannot build an import spec for %s" % (script,))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except _INVARIANT_FAILURES as exc:
        raise ImportError("release invariant failed while loading: %s"
                          % (exc,)) from exc
    return module


def run_release_invariant(worktree):
    """Run the existing release invariant in process and capture its exit
    code, stdout and stderr. A missing script, an unloadable script, or a
    script that raises without setting an exit code is NO-DATA: the result
    is exit_code 2, never a pass and never a crash escaping the caller."""
    worktree = _as_path(worktree, "worktree")
    out = io.StringIO()
    err = io.StringIO()
    try:
        module = _load_invariant_module(worktree)
    except _INVARIANT_FAILURES as exc:
        return {"exit_code": EXIT_BLOCKED, "stdout": out.getvalue(),
                "stderr": "release invariant could not load: %s" % (exc,)}
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            outcome = module.main([])
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, bool):
            code = int(code)
        if not isinstance(code, int):
            code = EXIT_BLOCKED
        return {"exit_code": code, "stdout": out.getvalue(),
                "stderr": err.getvalue()}
    except _INVARIANT_FAILURES as exc:
        return {"exit_code": EXIT_BLOCKED, "stdout": out.getvalue(),
                "stderr": err.getvalue() + "\n" + str(exc)}
    if isinstance(outcome, bool):
        code = int(outcome)
    elif isinstance(outcome, int):
        code = outcome
    else:
        code = EXIT_BLOCKED
    return {"exit_code": code, "stdout": out.getvalue(), "stderr": err.getvalue()}


def check_blocked(status_path, decisions_path, waivers_path, worktree):
    """The L0.1 gate. Returns 0 to pass, 2 to BLOCK, 3 for NO-DATA. Every
    path argument must be a pathlib.Path; anything else raises ValueError."""
    status_path = _as_path(status_path, "status_path")
    decisions_path = _as_path(decisions_path, "decisions_path")
    waivers_path = _as_path(waivers_path, "waivers_path")
    worktree = _as_path(worktree, "worktree")

    try:
        load_status(status_path)
    except ValueError:
        return EXIT_NODATA

    try:
        decisions_data = load_decisions(decisions_path)
    except ValueError:
        return EXIT_BLOCKED

    try:
        waivers_data = load_waivers(waivers_path)
    except ValueError:
        return EXIT_BLOCKED

    try:
        all_answered, _unresolved = require_all_answered(decisions_data,
                                                         waivers_data)
    except ValueError:
        return EXIT_BLOCKED

    if not all_answered:
        return EXIT_BLOCKED

    result = run_release_invariant(worktree)
    if result.get("exit_code") != EXIT_PASS:
        return EXIT_BLOCKED

    stdout = result.get("stdout")
    if not isinstance(stdout, str):
        return EXIT_BLOCKED

    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith(NO_DATA_PREFIX):
            continue
        link = stripped[len(NO_DATA_PREFIX):].strip()
        if " (not a pass" in link:
            link = link.split(" (not a pass", 1)[0].strip()
        if not _has_link_waiver(waivers_data, link):
            return EXIT_BLOCKED

    return EXIT_PASS
