"""Inventory Brother's own current harness behavior (unit L4.3).

For each behavior this inventory observes, the record carries the exact source
path it was read from and the exact function name inside it, which is REQ-01
BASELINE-CITATION applied to Brother's own tree, plus a measurement run id so a
later proposal can name the run that measured the before value.

REQ-12 FAIL-CLOSED-UNKNOWN is a control here, not a report. A harness root that
is missing, that is not a directory, or that arrives as a hostile value is
NO-DATA and BLOCKS. A source path the tree does not hold is recorded as ABSENT
and is never asserted to exist, and it is never silently dropped either: the
inventory stays complete, and an inventory shorter than the required minimum
BLOCKS.

Nothing here estimates and nothing here falls back to a safe default. No
existing public name is redefined. Standard library only, Python 3.9.
"""

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass

try:
    from scripts.gate_order import CorruptLogError, NoDataError
except ImportError:  # pragma: no cover - direct script execution
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _ROOT = os.path.dirname(_HERE)
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from scripts.gate_order import CorruptLogError, NoDataError


__all__ = [
    "ABSENT",
    "BEHAVIOR_CATALOG",
    "CorruptLogError",
    "CurrentBehavior",
    "MIN_RECORDS",
    "NoDataError",
    "PRESENT",
    "SCHEMA_VERSION",
    "inventory_current_behavior",
    "main",
    "presence",
    "read_inventory",
    "validate_measurement_run_ids",
    "write_inventory",
]


class _NoData(NoDataError):
    """NO-DATA refusal raised here; also catchable as a ValueError."""


SCHEMA_VERSION = "l4.current-behavior.1"
MIN_RECORDS = 4
PRESENT = "PRESENT"
ABSENT = "ABSENT"

_CATALOG_TEXT_FIELDS = (
    "behavior_id",
    "component",
    "file_path",
    "function_name",
    "observed_metric",
    "before_unit",
)

_RECORD_TEXT_FIELDS = (
    "behavior_id",
    "component",
    "file_path",
    "function_name",
    "observed_metric",
    "before_unit",
    "measurement_run_id",
)


@dataclass(frozen=True)
class CurrentBehavior:
    behavior_id: str
    component: str
    file_path: str
    function_name: str
    observed_metric: str
    before_value: float
    before_unit: str
    measurement_run_id: str


BEHAVIOR_CATALOG = (
    {
        "behavior_id": "b-action-fusion-fused-gate-runner",
        "component": "action_fusion",
        "file_path": "scripts/required_fast.sh",
        "function_name": "run_check",
        "observed_metric": "lines_naming_the_fused_runner",
        "before_unit": "count",
    },
    {
        "behavior_id": "b-observation-pack-result-parse",
        "component": "observation_pack",
        "file_path": "scripts/gate_order.py",
        "function_name": "parse_log",
        "observed_metric": "lines_naming_the_result_parser",
        "before_unit": "count",
    },
    {
        "behavior_id": "b-evidence-preserving-reducer-quote-check",
        "component": "evidence_preserving_reducer",
        "file_path": "scripts/load_solpi_baseline.py",
        "function_name": "load_solpi_baseline",
        "observed_metric": "lines_naming_the_archived_quote_check",
        "before_unit": "count",
    },
    {
        "behavior_id": "b-online-context-compact-token-measure",
        "component": "online_context_compact",
        "file_path": "scripts/measure_session_tokens.py",
        "function_name": "parse_token_log",
        "observed_metric": "lines_naming_the_token_parser",
        "before_unit": "count",
    },
    {
        "behavior_id": "b-observation-pack-evidence-capture",
        "component": "observation_pack",
        "file_path": "scripts/run_evidence.py",
        "function_name": "capture",
        "observed_metric": "lines_naming_the_durable_capture",
        "before_unit": "count",
    },
)


def _refuse(message):
    raise _NoData(message)


def _is_file(path):
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def _is_dir(path):
    try:
        return os.path.isdir(path)
    except (OSError, ValueError):
        return False


def _exists(path):
    try:
        return os.path.exists(path)
    except (OSError, ValueError):
        return False


def _destination_snapshot(path):
    try:
        stat = os.stat(path)
    except (OSError, ValueError):
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _require_root(caller, harness_root):
    if (
        isinstance(harness_root, bool)
        or not isinstance(harness_root, str)
        or not harness_root.strip()
    ):
        _refuse(
            "%s: harness_root must be a non empty str, got %r"
            % (caller, harness_root)
        )
    if not _exists(harness_root):
        _refuse("%s: harness_root does not exist: %s" % (caller, harness_root))
    if not _is_dir(harness_root):
        _refuse(
            "%s: harness_root is not a directory: %s" % (caller, harness_root)
        )
    return harness_root


def _require_out_path(caller, path):
    if isinstance(path, bool) or not isinstance(path, str) or not path.strip():
        _refuse("%s: path must be a non empty str, got %r" % (caller, path))
    return path


def _require_relative_path(caller, file_path):
    if (
        isinstance(file_path, bool)
        or not isinstance(file_path, str)
        or not file_path.strip()
    ):
        _refuse(
            "%s: file_path must be a non empty str, got %r" % (caller, file_path)
        )
    return file_path


def _read_bytes(caller, path):
    if not _exists(path):
        _refuse("%s: file does not exist: %s" % (caller, path))
    if not _is_file(path):
        _refuse("%s: not a regular file: %s" % (caller, path))
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (OSError, ValueError) as exc:
        raise _NoData("%s: cannot read %s: %s" % (caller, path, exc)) from exc


def _count_lines_containing(caller, path, token):
    raw = _read_bytes(caller, path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _NoData(
            "%s: %s is not utf-8 text: %s" % (caller, path, exc)
        ) from exc
    return sum(1 for line in text.splitlines() if token in line)


def _run_id(behavior_id, file_path, before_value):
    material = "%s|%s|%r" % (behavior_id, file_path, before_value)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return "run-" + digest[:16]


def _require_record_sequence(caller, records):
    if isinstance(records, bool) or not isinstance(records, (list, tuple)):
        _refuse(
            "%s: records must be a list or tuple of CurrentBehavior, got %r"
            % (caller, type(records).__name__)
        )
    for record in records:
        if not isinstance(record, CurrentBehavior):
            _refuse(
                "%s: every record must be a CurrentBehavior, got %r"
                % (caller, type(record).__name__)
            )
    return records


def _require_complete_inventory(caller, records):
    if not records:
        _refuse("%s: the inventory is empty; NO-DATA" % caller)
    if len(records) < MIN_RECORDS:
        _refuse(
            "%s: the inventory carries %d record(s), fewer than the required %d"
            % (caller, len(records), MIN_RECORDS)
        )
    return records


def presence(harness_root, file_path):
    """Return PRESENT when the source path exists, ABSENT when it does not."""
    root = _require_root("presence", harness_root)
    relative = _require_relative_path("presence", file_path)
    return PRESENT if _is_file(os.path.join(root, relative)) else ABSENT


def _build_behavior(root, entry):
    if not isinstance(entry, dict):
        _refuse(
            "inventory_current_behavior: catalog entry must be a dict, got %r"
            % (type(entry).__name__,)
        )
    for key in _CATALOG_TEXT_FIELDS:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            _refuse(
                "inventory_current_behavior: catalog entry %r has no usable %s"
                % (entry.get("behavior_id"), key)
            )
    relative = entry["file_path"]
    joined = os.path.join(root, relative)
    if _is_file(joined):
        before = float(
            _count_lines_containing(
                "inventory_current_behavior", joined, entry["function_name"]
            )
        )
    elif _exists(joined):
        _refuse(
            "inventory_current_behavior: %s exists but is not a regular file"
            % relative
        )
    else:
        before = 0.0
    return CurrentBehavior(
        behavior_id=entry["behavior_id"],
        component=entry["component"],
        file_path=relative,
        function_name=entry["function_name"],
        observed_metric=entry["observed_metric"],
        before_value=before,
        before_unit=entry["before_unit"],
        measurement_run_id=_run_id(entry["behavior_id"], relative, before),
    )


def inventory_current_behavior(harness_root):
    """Return one CurrentBehavior per catalogued behavior under harness_root.

    Every record names its source path and its function, which is REQ-01
    BASELINE-CITATION applied to Brother's own tree. A path the tree does not
    hold is still recorded, reports ABSENT through presence(), and carries
    before_value 0.0; it is never asserted to exist and never silently dropped,
    so the inventory stays complete. A hostile harness root, a corrupt source
    file, or an inventory that is too short is NO-DATA and BLOCKS.
    """
    root = _require_root("inventory_current_behavior", harness_root)
    records = []
    for entry in BEHAVIOR_CATALOG:
        records.append(_build_behavior(root, entry))
    _require_complete_inventory("inventory_current_behavior", records)
    return records


def _known_run_ids(caller, measurements):
    if isinstance(measurements, dict):
        return set(measurements.keys())
    if isinstance(measurements, (list, tuple, set, frozenset)):
        known = set()
        for item in measurements:
            if isinstance(item, str) and item.strip():
                known.add(item)
            elif (
                isinstance(item, dict)
                and isinstance(item.get("run_id"), str)
                and item["run_id"].strip()
            ):
                known.add(item["run_id"])
            else:
                _refuse(
                    "%s: measurement entries must be non empty run id strings "
                    "or dicts carrying a run_id" % caller
                )
        return known
    _refuse(
        "%s: measurements must be a mapping or a sequence of run ids, got %r"
        % (caller, type(measurements).__name__)
    )


def validate_measurement_run_ids(records, measurements):
    """BLOCK when any record names a run the measurements do not hold.

    A stale measurement_run_id is NO-DATA here, never a pointer that is
    silently accepted at a run which does not exist. A missing, empty or
    hostile measurements argument is NO-DATA too.
    """
    ordered = _require_record_sequence("validate_measurement_run_ids", records)
    _require_complete_inventory("validate_measurement_run_ids", ordered)
    known = _known_run_ids("validate_measurement_run_ids", measurements)
    if not known:
        _refuse(
            "validate_measurement_run_ids: no measurement run ids were "
            "supplied; NO-DATA"
        )
    stale = sorted(
        {
            record.measurement_run_id
            for record in ordered
            if record.measurement_run_id not in known
        }
    )
    if stale:
        _refuse(
            "validate_measurement_run_ids: run ids absent from the "
            "measurements: %s" % stale
        )
    return ordered


def _record_from_json(where, item):
    if not isinstance(item, dict):
        raise CorruptLogError(
            "read_inventory: every record in %s must be a json object" % where
        )
    values = {}
    for field in _RECORD_TEXT_FIELDS:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CorruptLogError(
                "read_inventory: record field %s in %s must be a non empty str"
                % (field, where)
            )
        values[field] = value
    raw_value = item.get("before_value")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise CorruptLogError(
            "read_inventory: before_value in %s must be a number" % where
        )
    number = float(raw_value)
    if number != number or number == float("inf") or number == float("-inf"):
        raise CorruptLogError(
            "read_inventory: before_value in %s must be finite" % where
        )
    return CurrentBehavior(
        behavior_id=values["behavior_id"],
        component=values["component"],
        file_path=values["file_path"],
        function_name=values["function_name"],
        observed_metric=values["observed_metric"],
        before_value=number,
        before_unit=values["before_unit"],
        measurement_run_id=values["measurement_run_id"],
    )


def read_inventory(path):
    """Read back an inventory document, refusing anything short or corrupt."""
    named = _require_out_path("read_inventory", path)
    opening = _destination_snapshot(named)
    raw = _read_bytes("read_inventory", named)
    closing = _destination_snapshot(named)
    if opening != closing:
        _refuse(
            "read_inventory: %s changed while it was being read (concurrent "
            "write); NO-DATA" % named
        )
    if not raw.strip():
        _refuse("read_inventory: %s is empty" % named)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _NoData(
            "read_inventory: %s is not utf-8 text: %s" % (named, exc)
        ) from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise CorruptLogError(
            "read_inventory: corrupt json in %s: %s" % (named, exc)
        ) from exc
    if not isinstance(document, dict):
        raise CorruptLogError(
            "read_inventory: %s must hold a json object" % named
        )
    if document.get("schema_version") != SCHEMA_VERSION:
        raise CorruptLogError(
            "read_inventory: %s schema_version mismatch" % named
        )
    raw_records = document.get("records")
    if not isinstance(raw_records, list):
        raise CorruptLogError(
            "read_inventory: records in %s must be a json list" % named
        )
    records = [_record_from_json(named, item) for item in raw_records]
    _require_complete_inventory("read_inventory", records)
    return records


def write_inventory(records, out_path):
    """Write an inventory document atomically, refusing a concurrent writer.

    An empty or too short inventory, a non CurrentBehavior entry, a hostile out
    path, a destination that changes while the document is prepared, or a write
    that cannot land is NO-DATA and BLOCKS. A write that cannot land is never a
    pass.
    """
    ordered = _require_record_sequence("write_inventory", records)
    _require_complete_inventory("write_inventory", ordered)
    named = _require_out_path("write_inventory", out_path)
    before = _destination_snapshot(named)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "records": [asdict(record) for record in ordered],
    }
    try:
        text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise _NoData(
            "write_inventory: cannot serialize the inventory: %s" % exc
        ) from exc
    after = _destination_snapshot(named)
    if before != after:
        _refuse(
            "write_inventory: %s changed while the inventory was being "
            "prepared (concurrent write); NO-DATA" % named
        )
    tmp_path = named + ".partial"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, named)
    except (OSError, ValueError) as exc:
        try:
            if _is_file(tmp_path):
                os.remove(tmp_path)
        except (OSError, ValueError):
            pass
        raise _NoData(
            "write_inventory: cannot write %s: %s" % (named, exc)
        ) from exc


def main(argv=None):
    """Command line entry point: inventory HARNESS_ROOT OUT.

    Returns 0 when the inventory was written, 2 when any argument, root or
    output is NO-DATA. A hostile argv lands as exit code 2, never a crash.
    """
    if argv is None:
        argv = list(sys.argv[1:])
    if not isinstance(argv, (list, tuple)):
        sys.stderr.write(
            "NO-DATA: inventory_current_behavior: argv must be a list or "
            "tuple of strings\n"
        )
        return 2
    args = list(argv)
    for item in args:
        if not isinstance(item, str):
            sys.stderr.write(
                "NO-DATA: inventory_current_behavior: argv must be a list or "
                "tuple of strings\n"
            )
            return 2
    if len(args) != 2:
        sys.stderr.write(
            "NO-DATA: inventory_current_behavior: usage: "
            "inventory_current_behavior HARNESS_ROOT OUT\n"
        )
        return 2
    harness_root, out_path = args
    try:
        records = inventory_current_behavior(harness_root)
        write_inventory(records, out_path)
    except (NoDataError, CorruptLogError) as exc:
        sys.stderr.write("NO-DATA: %s\n" % exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
