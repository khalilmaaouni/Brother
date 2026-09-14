"""Durable sidecar for parked plan units."""

import json
import os
import tempfile
import time


def _empty_state():
    return {"withheld": {}, "parked_until": {}}


def _problem(path, reason):
    return "{}: {}".format(path, reason)


def save(path, state):
    withheld = state.get("withheld") or {}
    if not withheld:
        try:
            os.remove(path)
        except FileNotFoundError:  # sbe: allow-silent target already absent, removal's goal is already met
            pass
        return

    payload = {
        "version": 1,
        "saved_at": time.time(),
        "withheld": withheld,
        "parked_until": state.get("parked_until") or {},
    }

    directory = os.path.dirname(path) or "."
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=".park_sidecar.", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except FileNotFoundError:  # sbe: allow-silent temp file already replaced or cleaned up, nothing left to remove
                pass


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return _empty_state(), None
    except OSError as exc:
        return _empty_state(), _problem(path, "cannot read: {}".format(exc))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _empty_state(), _problem(path, "invalid JSON: {}".format(exc.msg))

    if not isinstance(data, dict):
        return _empty_state(), _problem(path, "top level is not an object")

    version = data.get("version")
    if version != 1:
        return _empty_state(), _problem(
            path, "unsupported version: {!r}".format(version)
        )

    withheld = data.get("withheld")
    parked_until = data.get("parked_until")
    if not isinstance(withheld, dict) or not isinstance(parked_until, dict):
        return _empty_state(), _problem(
            path, "withheld or parked_until is not an object"
        )

    return data, None


def restore_into(rows, state):
    new_rows = list(rows)
    existing = set()
    for row in new_rows:
        if isinstance(row, dict) and "id" in row:
            existing.add(row["id"])

    withheld = {}
    if isinstance(state, dict):
        maybe = state.get("withheld")
        if isinstance(maybe, dict):
            withheld = maybe

    restored_ids = []
    for unit_id in sorted(withheld.keys()):
        if unit_id in existing:
            continue
        new_rows.append(withheld[unit_id])
        existing.add(unit_id)
        restored_ids.append(unit_id)

    return new_rows, restored_ids
