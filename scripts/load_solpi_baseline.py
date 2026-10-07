"""Load and validate the pinned SoL-Pi baseline (unit L4.1, baseline lock).

One JSON file pins, for each of the four SoL-Pi mechanisms, the source it came
from and the number Brother has to beat. This module reads that file and
enforces REQ-01 (BASELINE-CITATION) and REQ-10 (BASELINE-EXPIRY): a record
missing a source id, a source version, a retrieval date, a well formed SHA-256
pin, a non empty quote or an existing excerpt file is NO-DATA and BLOCKS.
Nothing here estimates and nothing here falls back to a safe default: unknown,
corrupt or missing input raises NoDataError.

The excerpt file is the local, archived copy of the quoted passage, and every
source_quote must be verifiably present in it, so a summary can never drift
away from the text it was compressed from.

Standard library only, Python 3.9 compatible.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Literal

from scripts.gate_order import CorruptLogError, NoDataError

__all__ = [
    "CorruptLogError",
    "MechanismBaseline",
    "NoDataError",
    "load_solpi_baseline",
    "sha256_file",
]


class _NoData(NoDataError):
    """NO-DATA refusal raised here; also catchable as a ValueError."""


_MECHANISM_IDS = (
    "action_fusion",
    "online_context_compact",
    "observation_pack",
    "evidence_preserving_reducer",
)

_TEXT_FIELDS = (
    "paper_ref",
    "metric",
    "unit",
    "source_quote",
    "source_path",
    "source_sha256",
    "source_version",
    "retrieved_at",
    "excerpt_path",
)

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_ISO_DATE_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")

_POSITIVE_INFINITY = float("inf")
_NEGATIVE_INFINITY = float("-inf")


@dataclass(frozen=True)
class MechanismBaseline:
    mechanism_id: Literal[
        "action_fusion",
        "online_context_compact",
        "observation_pack",
        "evidence_preserving_reducer",
    ]
    paper_ref: str
    metric: str
    value: float
    unit: str
    source_quote: str
    source_path: str
    source_sha256: str
    source_version: str
    retrieved_at: str
    excerpt_path: str


def _refuse(message: str) -> None:
    raise _NoData(message)


def _require_path_argument(caller: str, path) -> str:
    if isinstance(path, bool) or not isinstance(path, str) or not path.strip():
        _refuse("%s: path must be a non empty str, got %r" % (caller, path))
    return path


def _read_bytes(caller: str, path: str) -> bytes:
    if not os.path.exists(path):
        _refuse("%s: file does not exist: %s" % (caller, path))
    if not os.path.isfile(path):
        _refuse("%s: not a regular file: %s" % (caller, path))
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise _NoData("%s: cannot read %s: %s" % (caller, path, exc)) from exc


def _stat_snapshot(path: str):
    stat = os.stat(path)
    return (stat.st_size, stat.st_mtime_ns)


def _resolve_excerpt_path(baseline_path: str, excerpt_path: str) -> str:
    if os.path.isabs(excerpt_path):
        return excerpt_path
    base = os.path.dirname(os.path.abspath(baseline_path))
    return os.path.normpath(os.path.join(base, excerpt_path))


def _reject_json_constant(name: str):
    _refuse("load_solpi_baseline: non finite number %r in the baseline" % name)


def sha256_file(path: str) -> str:
    """Return the lowercase SHA-256 hex digest of the bytes at `path`.

    Hostile input is refused rather than guessed: a missing file, a directory,
    a byte string or a non string path is NO-DATA.
    """
    named = _require_path_argument("sha256_file", path)
    raw = _read_bytes("sha256_file", named)
    return hashlib.sha256(raw).hexdigest()


def load_solpi_baseline(path: str) -> Dict[str, MechanismBaseline]:
    """Return the four locked mechanism baselines, or raise NoDataError.

    REQ-01 BASELINE-CITATION and REQ-10 BASELINE-EXPIRY are enforced here:
    every citation field must be present and well formed, the SHA-256 pin must
    be a 64 character lowercase hex digest, the retrieval date must be an ISO
    date, the excerpt file must exist and be non empty, and every source_quote
    must be verifiably present in that excerpt.
    """
    named = _require_path_argument("load_solpi_baseline", path)
    try:
        before = _stat_snapshot(named)
        raw = _read_bytes("load_solpi_baseline", named)
        after = _stat_snapshot(named)
    except _NoData:
        raise
    except OSError as exc:
        raise _NoData(
            "load_solpi_baseline: cannot read %s: %s" % (named, exc)
        ) from exc
    if before != after:
        _refuse(
            "load_solpi_baseline: %s changed while it was being read "
            "(concurrent write); NO-DATA" % named
        )
    if not raw.strip():
        _refuse("load_solpi_baseline: baseline file is empty: %s" % named)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _NoData(
            "load_solpi_baseline: %s is not utf-8 text: %s" % (named, exc)
        ) from exc
    try:
        document = json.loads(text, parse_constant=_reject_json_constant)
    except _NoData:
        raise
    except ValueError as exc:
        raise _NoData(
            "load_solpi_baseline: corrupt json in %s: %s" % (named, exc)
        ) from exc
    if not isinstance(document, dict):
        _refuse(
            "load_solpi_baseline: the top level of %s must be a json object" % named
        )
    seen = set(document)
    expected = set(_MECHANISM_IDS)
    if seen != expected:
        _refuse(
            "load_solpi_baseline: %s must name exactly the four SoL-Pi mechanisms; "
            "missing=%s unknown=%s"
            % (named, sorted(expected - seen), sorted(seen - expected))
        )
    records = {}
    for mechanism_id in _MECHANISM_IDS:
        records[mechanism_id] = _build_record(
            mechanism_id, document[mechanism_id], named
        )
    return records


def _build_record(mechanism_id: str, body, baseline_path: str) -> MechanismBaseline:
    where = "load_solpi_baseline[%s]" % mechanism_id
    if not isinstance(body, dict):
        _refuse("%s: record must be a json object" % where)
    required = ("mechanism_id", "value") + _TEXT_FIELDS
    missing = [field for field in required if field not in body]
    if missing:
        _refuse("%s: missing fields %s" % (where, missing))
    if body["mechanism_id"] != mechanism_id:
        _refuse(
            "%s: mechanism_id must be %r, got %r"
            % (where, mechanism_id, body["mechanism_id"])
        )
    text = {}
    for field in _TEXT_FIELDS:
        raw_value = body[field]
        if not isinstance(raw_value, str) or not raw_value.strip():
            _refuse(
                "%s: %s must be a non empty str, got %r" % (where, field, raw_value)
            )
        text[field] = raw_value
    raw_number = body["value"]
    if isinstance(raw_number, bool) or not isinstance(raw_number, (int, float)):
        _refuse("%s: value must be a finite number, got %r" % (where, raw_number))
    try:
        number = float(raw_number)
    except (OverflowError, ValueError) as exc:
        raise _NoData(
            "%s: value cannot be represented as a float: %r" % (where, raw_number)
        ) from exc
    if (
        number != number
        or number == _POSITIVE_INFINITY
        or number == _NEGATIVE_INFINITY
    ):
        _refuse("%s: value must be a finite number, got %r" % (where, raw_number))
    if not _SHA256_RE.match(text["source_sha256"]):
        _refuse(
            "%s: source_sha256 is not a well formed lowercase SHA-256 pin "
            "(stale or corrupt): %r" % (where, text["source_sha256"])
        )
    if not _ISO_DATE_RE.match(text["retrieved_at"]):
        _refuse(
            "%s: retrieved_at must be an ISO date (YYYY-MM-DD), got %r"
            % (where, text["retrieved_at"])
        )
    excerpt = _resolve_excerpt_path(baseline_path, text["excerpt_path"])
    excerpt_raw = _read_bytes(where, excerpt)
    if not excerpt_raw.strip():
        _refuse("%s: excerpt file is empty or blank: %s" % (where, excerpt))
    try:
        excerpt_text = excerpt_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _NoData(
            "%s: excerpt %s is not utf-8 text: %s" % (where, excerpt, exc)
        ) from exc
    if text["source_quote"] not in excerpt_text:
        _refuse(
            "%s: source_quote is not verifiably present in the archived excerpt "
            "%s" % (where, excerpt)
        )
    return MechanismBaseline(
        mechanism_id=mechanism_id,
        paper_ref=text["paper_ref"],
        metric=text["metric"],
        value=number,
        unit=text["unit"],
        source_quote=text["source_quote"],
        source_path=text["source_path"],
        source_sha256=text["source_sha256"],
        source_version=text["source_version"],
        retrieved_at=text["retrieved_at"],
        excerpt_path=text["excerpt_path"],
    )
