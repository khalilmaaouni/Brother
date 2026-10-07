"""Measure a real long-running session's token cost from a session token log.

Unit L4.2 of the SoL-Pi harness efficiency assessment. Reads a JSONL session
token log that records one real run's token accounting and returns a frozen
TokenMeasurement. Enforces REQ-02 MEASURED-DELTA-SAME-DEFINITION: the reported
total_tokens must equal the sum of the four components, and the raw log hash
is recomputed from the file bytes so a stale or swapped log is NO-DATA.

Unknown, corrupt, missing, stale or concurrently modified input is NO-DATA and
BLOCKS. Nothing here estimates.

Standard library only, Python 3.9 compatible.
"""

import hashlib
import json
import os
import sys
from dataclasses import dataclass

try:
    from scripts.gate_order import CorruptLogError, NoDataError
except ImportError:  # pragma: no cover - direct script execution
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _ROOT = os.path.dirname(_HERE)
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from scripts.gate_order import CorruptLogError, NoDataError


__all__ = [
    "CorruptLogError",
    "NoDataError",
    "TokenMeasurement",
    "main",
    "parse_token_log",
    "sha256_file",
    "write_measurement",
]


class _NoData(NoDataError):
    """NO-DATA refusal raised here; also catchable as a ValueError."""


_TEXT_FIELDS = (
    "run_id",
    "session_id",
    "started_at",
    "ended_at",
    "model",
    "counter_name",
    "counter_version",
    "tree_hash",
    "task_path",
    "task_sha256",
    "command",
)

_INT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
    "exit_code",
)

_REQUIRED_FIELDS = _TEXT_FIELDS + _INT_FIELDS

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
)


@dataclass(frozen=True)
class TokenMeasurement:
    run_id: str
    session_id: str
    started_at: str
    ended_at: str
    model: str
    counter_name: str
    counter_version: str
    tree_hash: str
    task_path: str
    task_sha256: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    raw_log_path: str
    raw_log_sha256: str
    command: str
    exit_code: int


def _refuse(message: str) -> None:
    raise _NoData(message)


def _require_path_argument(caller: str, path) -> str:
    if isinstance(path, bool) or not isinstance(path, str) or not path.strip():
        _refuse("%s: path must be a non empty str, got %r" % (caller, path))
    return path


def _stat_snapshot(path: str):
    stat = os.stat(path)
    return (stat.st_size, stat.st_mtime_ns)


def _reject_json_constant(name: str):
    _refuse("parse_token_log: non finite number %r in the token log" % (name,))


def sha256_file(path: str) -> str:
    """Return the lowercase SHA-256 hex digest of the bytes at `path`.

    A missing file, a directory, a byte string path or a non string path is
    NO-DATA, never a guessed default.
    """
    named = _require_path_argument("sha256_file", path)
    if not os.path.exists(named):
        _refuse("sha256_file: file does not exist: %s" % named)
    if not os.path.isfile(named):
        _refuse("sha256_file: not a regular file: %s" % named)
    try:
        with open(named, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise _NoData("sha256_file: cannot read %s: %s" % (named, exc)) from exc
    return hashlib.sha256(raw).hexdigest()


def parse_token_log(path: str) -> TokenMeasurement:
    """Parse a one record JSONL session token log into a TokenMeasurement.

    REQ-02 MEASURED-DELTA-SAME-DEFINITION is enforced here: total_tokens must
    equal input_tokens + output_tokens + cache_read_tokens + cache_write_tokens,
    and raw_log_sha256 is recomputed from the file bytes. An empty log, a
    missing file, a directory, a non utf-8 body, a stale or concurrently
    appended file, a missing or ill typed field, or a total that does not
    match its components is NO-DATA and BLOCKS. A malformed JSON line raises
    CorruptLogError.
    """
    named = _require_path_argument("parse_token_log", path)
    if not os.path.exists(named):
        _refuse("parse_token_log: log file does not exist: %s" % named)
    if not os.path.isfile(named):
        _refuse("parse_token_log: not a regular file: %s" % named)
    try:
        before = _stat_snapshot(named)
        with open(named, "rb") as handle:
            raw = handle.read()
        after = _stat_snapshot(named)
    except _NoData:
        raise
    except OSError as exc:
        raise _NoData("parse_token_log: cannot read %s: %s" % (named, exc)) from exc
    if before != after:
        _refuse(
            "parse_token_log: %s changed while it was being read "
            "(concurrent append); NO-DATA" % named
        )
    if not raw.strip():
        _refuse("parse_token_log: log file is empty: %s" % named)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _NoData(
            "parse_token_log: %s is not utf-8 text: %s" % (named, exc)
        ) from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        _refuse("parse_token_log: log file has no records: %s" % named)
    if len(lines) != 1:
        raise CorruptLogError(
            "parse_token_log: expected exactly one record in %s, found %d"
            % (named, len(lines))
        )
    try:
        record = json.loads(lines[0], parse_constant=_reject_json_constant)
    except _NoData:
        raise
    except ValueError as exc:
        raise CorruptLogError(
            "parse_token_log: corrupt json in %s: %s" % (named, exc)
        ) from exc
    if not isinstance(record, dict):
        raise CorruptLogError(
            "parse_token_log: record in %s must be a json object" % named
        )
    missing = [field for field in _REQUIRED_FIELDS if field not in record]
    if missing:
        _refuse("parse_token_log: missing fields %s in %s" % (missing, named))
    text_values = {}
    for field in _TEXT_FIELDS:
        value = record[field]
        if not isinstance(value, str) or not value.strip():
            _refuse(
                "parse_token_log: %s must be a non empty str, got %r"
                % (field, value)
            )
        text_values[field] = value
    int_values = {}
    for field in _INT_FIELDS:
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int):
            _refuse(
                "parse_token_log: %s must be an int, got %r" % (field, value)
            )
        int_values[field] = value
    for field in _TOKEN_FIELDS:
        if int_values[field] < 0:
            _refuse(
                "parse_token_log: %s must be non negative, got %r"
                % (field, int_values[field])
            )
    computed_total = (
        int_values["input_tokens"]
        + int_values["output_tokens"]
        + int_values["cache_read_tokens"]
        + int_values["cache_write_tokens"]
    )
    if int_values["total_tokens"] != computed_total:
        _refuse(
            "parse_token_log: total_tokens %d does not equal the sum of its "
            "components %d (REQ-02 MEASURED-DELTA-SAME-DEFINITION)"
            % (int_values["total_tokens"], computed_total)
        )
    return TokenMeasurement(
        run_id=text_values["run_id"],
        session_id=text_values["session_id"],
        started_at=text_values["started_at"],
        ended_at=text_values["ended_at"],
        model=text_values["model"],
        counter_name=text_values["counter_name"],
        counter_version=text_values["counter_version"],
        tree_hash=text_values["tree_hash"],
        task_path=text_values["task_path"],
        task_sha256=text_values["task_sha256"],
        input_tokens=int_values["input_tokens"],
        output_tokens=int_values["output_tokens"],
        cache_read_tokens=int_values["cache_read_tokens"],
        cache_write_tokens=int_values["cache_write_tokens"],
        total_tokens=int_values["total_tokens"],
        raw_log_path=named,
        raw_log_sha256=hashlib.sha256(raw).hexdigest(),
        command=text_values["command"],
        exit_code=int_values["exit_code"],
    )


def write_measurement(m: TokenMeasurement, out_path: str) -> None:
    """Write `m` as a sorted key JSON object at `out_path`.

    A measurement that is not a TokenMeasurement, or an out path that is not a
    non empty string, is NO-DATA. The write is atomic: it lands through a
    sibling temporary file and os.replace, so a reader never sees a torn file.
    """
    if not isinstance(m, TokenMeasurement):
        _refuse(
            "write_measurement: measurement must be a TokenMeasurement, got %r"
            % (type(m).__name__,)
        )
    named = _require_path_argument("write_measurement", out_path)
    payload = {
        "run_id": m.run_id,
        "session_id": m.session_id,
        "started_at": m.started_at,
        "ended_at": m.ended_at,
        "model": m.model,
        "counter_name": m.counter_name,
        "counter_version": m.counter_version,
        "tree_hash": m.tree_hash,
        "task_path": m.task_path,
        "task_sha256": m.task_sha256,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "cache_read_tokens": m.cache_read_tokens,
        "cache_write_tokens": m.cache_write_tokens,
        "total_tokens": m.total_tokens,
        "raw_log_path": m.raw_log_path,
        "raw_log_sha256": m.raw_log_sha256,
        "command": m.command,
        "exit_code": m.exit_code,
    }
    try:
        text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise _NoData(
            "write_measurement: cannot serialize measurement: %s" % exc
        ) from exc
    tmp_path = named + ".partial"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, named)
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise _NoData(
            "write_measurement: cannot write %s: %s" % (named, exc)
        ) from exc


def _existing_matches(out_path: str, log_path: str) -> bool:
    if not os.path.isfile(out_path):
        return False
    try:
        with open(out_path, "rb") as handle:
            raw = handle.read()
        existing = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if not isinstance(existing, dict):
        return False
    recorded = existing.get("raw_log_sha256")
    if not isinstance(recorded, str) or not recorded:
        return False
    try:
        current = sha256_file(log_path)
    except NoDataError:
        return False
    return recorded == current


def main(argv=None):
    """Command line entry point: measure one session token log.

    Usage: measure_session_tokens LOG OUT

    Returns 0 when the measurement was written or already matched, 2 when any
    input, argument or output is NO-DATA. Never raises for a hostile argv or a
    hostile file: those land as exit code 2, which required_fast.sh already
    treats as NO-DATA and never as a pass.
    """
    if argv is None:
        argv = list(sys.argv[1:])
    if not isinstance(argv, (list, tuple)):
        sys.stderr.write(
            "NO-DATA: measure_session_tokens: argv must be a list or tuple of strings\n"
        )
        return 2
    args = list(argv)
    for item in args:
        if not isinstance(item, str):
            sys.stderr.write(
                "NO-DATA: measure_session_tokens: argv must be a list or tuple of strings\n"
            )
            return 2
    if len(args) != 2:
        sys.stderr.write(
            "NO-DATA: measure_session_tokens: usage: measure_session_tokens LOG OUT\n"
        )
        return 2
    log_path, out_path = args
    if _existing_matches(out_path, log_path):
        return 0
    try:
        measurement = parse_token_log(log_path)
        write_measurement(measurement, out_path)
    except (NoDataError, CorruptLogError) as exc:
        sys.stderr.write("NO-DATA: %s\n" % exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
