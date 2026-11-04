"""Deterministic review replay, not source authentication.

Artifacts contain data only. Paths always come from the caller; no artifact
field is treated as a filename, command, module name or network address.
"""
import csv
import codecs
import hashlib
import io
import json
import math
import re


SCHEMA = "brotherds.audit-plan.v1"
ALGORITHM = "stratified-random-v1"
ENCODINGS = ("utf-8", "utf-8-sig", "cp932")
FIELDS = {"schema", "algorithm", "input_sha256", "input_encoding",
          "parameters", "columns", "row_count", "plan_sha256"}


def csv_bytes(raw, encoding=None, strict=False, expected_columns=None):
    """Decode one captured byte sequence, optionally enforcing CSV shape."""
    candidates = [encoding] if encoding is not None else ["utf-8-sig", "cp932"]
    for codec in candidates:
        try:
            text = raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        reader = csv.reader(io.StringIO(text, newline=""), strict=strict)
        try:
            header = next(reader, [])
            if strict:
                if not header or any(not h for h in header) or len(set(header)) != len(header):
                    raise ValueError("CSV header must contain unique nonempty columns")
                if expected_columns is not None and set(header) != set(expected_columns):
                    raise ValueError("labelled CSV columns differ from the recorded plan")
            rows = []
            for cells in reader:
                if not cells and not strict:
                    continue
                if strict and len(cells) != len(header):
                    raise ValueError("CSV row width differs from header")
                rows.append(dict(zip(header, cells)))
        except csv.Error as exc:
            raise ValueError("malformed CSV: " + str(exc))
        return header, rows, codecs.lookup(codec).name
    raise ValueError("could not decode input as " + ", ".join(candidates))


def parameters(margin, min_n, max_n, seed):
    if isinstance(margin, bool) or not isinstance(margin, (int, float)):
        raise ValueError("margin must be finite and greater than zero")
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError("margin must be finite and greater than zero")
    # Avoid overflow/underflow in the existing sample-size formula.
    if not math.isfinite(0.9604 / margin / margin):
        raise ValueError("margin is too small")
    for name, value in (("min_n", min_n), ("max_n", max_n), ("seed", seed)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(name + " must be an integer")
    if min_n < 1 or max_n < min_n:
        raise ValueError("sample limits require 1 <= min_n <= max_n")
    return {"margin": float(margin), "min_n": min_n, "max_n": max_n, "seed": seed}


def _canonical_rows(rows, columns):
    seen = set()
    result = []
    for row in rows:
        if set(row) != set(columns):
            raise ValueError("plan row columns differ from the recorded plan")
        if any(not isinstance(row[c], str) for c in columns):
            raise ValueError("plan values must be strings")
        identity = row["plan_id"]
        if not identity or identity in seen:
            raise ValueError("plan_id values must be nonempty and unique")
        seen.add(identity)
        result.append({c: row[c] for c in columns if c != "label"})
    return sorted(result, key=lambda row: row["plan_id"])


def _digest(record, rows):
    value = {k: v for k, v in record.items() if k != "plan_sha256"}
    value["rows"] = _canonical_rows(rows, record["columns"])
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def record_plan(rows, input_sha256, encoding, params, columns):
    if encoding not in ENCODINGS:
        raise ValueError("replay encoding must be utf-8, utf-8-sig or cp932")
    record = {"schema": SCHEMA, "algorithm": ALGORITHM,
              "input_sha256": input_sha256, "input_encoding": encoding,
              "parameters": params, "columns": list(columns), "row_count": len(rows)}
    record["plan_sha256"] = _digest(record, rows)
    return record


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonfinite JSON number: " + value)


def load_snapshot(path, columns):
    with open(path, encoding="utf-8") as stream:
        snapshot = json.load(stream, object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant)
    if not isinstance(snapshot, dict):
        raise ValueError("audit snapshot must be an object")
    record = snapshot.get("review_plan")
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise ValueError("missing or malformed review_plan record")
    if record["schema"] != SCHEMA or record["algorithm"] != ALGORITHM:
        raise ValueError("unknown review_plan schema or sampling algorithm")
    if record["input_encoding"] not in ENCODINGS:
        raise ValueError("unsupported review_plan encoding")
    for key in ("input_sha256", "plan_sha256"):
        if not isinstance(record[key], str) or not re.fullmatch("[0-9a-f]{64}", record[key]):
            raise ValueError("malformed " + key)
    if record["columns"] != list(columns):
        raise ValueError("unknown review_plan columns")
    if type(record["row_count"]) is not int or record["row_count"] < 0:
        raise ValueError("row_count must be a nonnegative integer")
    params = record["parameters"]
    if not isinstance(params, dict) or set(params) != {"margin", "min_n", "max_n", "seed"}:
        raise ValueError("malformed review_plan parameters")
    parameters(**params)
    if snapshot.get("input_sha256") != record["input_sha256"]:
        raise ValueError("audit and review_plan input digests differ")
    if snapshot.get("input_encoding") != record["input_encoding"]:
        raise ValueError("audit and review_plan encodings differ")
    return snapshot


def verify_plan(record, regenerated, labelled, input_sha256):
    if input_sha256 != record["input_sha256"]:
        raise ValueError("export digest differs from audit snapshot")
    if len(regenerated) != record["row_count"] or _digest(record, regenerated) != record["plan_sha256"]:
        raise ValueError("regenerated plan does not match recorded parameters and checksum")
    if _canonical_rows(labelled, record["columns"]) != _canonical_rows(regenerated, record["columns"]):
        raise ValueError("labelled plan membership or metadata differs from regenerated plan")
    return {"state": "PASS", "schema": SCHEMA,
            "input_sha256": input_sha256, "plan_sha256": record["plan_sha256"],
            "row_count": len(regenerated), "parameters": dict(record["parameters"]),
            "scope": "Export bytes and review plan metadata replay consistently; labels are not verified.",
            "source_authenticity": "NO-DATA"}
