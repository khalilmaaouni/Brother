"""L5b.6 audit script: inventory skips, run the audit, recheck a report.

Every public function validates its arguments first and raises AuditInputError
(an AuditInputError is a ValueError) rather than letting a raw interpreter
error escape. Nothing here spawns a child process; the three fail-closed
probes are run in process through runpy.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.l5b_audit import report as report_mod
from tools.l5b_audit import scanner as scanner_mod
from tools.l5b_audit import score as score_mod
from tools.l5b_audit import lint_rules


DEFAULT_EXEMPTIONS = os.path.join("tools", "l5b_audit", "exemptions.json")
DEFAULT_FIRE_MAP = os.path.join("tools", "l5b_audit", "fire_map.json")
_MIN_SUBSTRING_CHARS = 8   # verify._MIN_SUBSTRING_CHARS, pinned by test_audit_recheck (verify reaches subprocess)
LOCK_NAME = ".l5b_audit.lock"
PROBE_NAMES = ("empty", "corrupt", "unknown")
MIN_SCORE = 8.5
MIN_FIRED_RATIO = 0.90


def _require_non_empty_str(label, value):
    if not isinstance(value, str) or not value:
        raise report_mod.AuditInputError("%s must be a non-empty string" % label)


def inventory_skips(root):
    """Walk root and count Python files that could not be read as UTF-8."""
    _require_non_empty_str("root", root)
    if not os.path.isdir(root):
        raise report_mod.AuditInputError("root must be a directory")
    seen = 0
    skipped = 0
    reasons = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        for filename in sorted(filenames):
            if not filename.endswith(".py") or scanner_mod.is_test_file(filename):
                continue   # the same population scan_tree measures (owner option A, 2026-10-02)
            seen += 1
            full = os.path.join(dirpath, filename)
            try:
                with open(full, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                skipped += 1
                reasons.append("unreadable: %s (%s)" % (full, exc))
                continue
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped += 1
                reasons.append("not utf-8: %s" % full)
    return report_mod.SkipInventory(
        files_seen=seen,
        files_skipped=skipped,
        skip_reasons=tuple(reasons),
    )


def _failed_thresholds(report):
    """Return every threshold name that the report fails, empty tuple when green."""
    if not isinstance(report, report_mod.AuditReport):
        raise report_mod.AuditInputError("report must be an AuditReport")
    failed = []
    if report.blocked:
        failed.append("blocked")
    if report.reason != report_mod.REASON_COMPLETE_PASS:
        failed.append("reason=%s" % report.reason)
    if report.score_decimal < MIN_SCORE:
        failed.append("score=%r" % report.score_decimal)
    if report.boundary_calls_stated != report.boundary_calls_total:
        failed.append("stated != total")
    denom = max(1, report.boundary_calls_stated)
    if (report.boundary_calls_tested / denom) < MIN_FIRED_RATIO:
        failed.append("fired ratio")
    return tuple(failed)


def _argv_items(argv):
    if argv is None:
        return None
    if not isinstance(argv, (list, tuple)):
        raise report_mod.AuditInputError("argv must be a list of strings or None")
    items = []
    for item in argv:
        if not isinstance(item, str):
            raise report_mod.AuditInputError("argv items must be strings")
        items.append(item)
    return items


def _relative_root(root):
    """The audited root as a working directory relative path, or an AuditInputError:
    an absolute root or one that leaves the working directory is refused before any work."""
    _require_non_empty_str("root", root)
    if os.path.isabs(root):
        raise report_mod.AuditInputError("root must be relative to the working directory")
    cwd = os.path.realpath(os.getcwd())
    real = os.path.realpath(os.path.join(cwd, root))
    if real != cwd and not real.startswith(cwd + os.sep):
        raise report_mod.AuditInputError("root leaves the working directory")
    if not os.path.isdir(real):
        raise report_mod.AuditInputError("root must be a directory")
    return os.path.normpath(root).replace(os.sep, "/")


def _relative_path(path):
    cwd = os.path.realpath(os.getcwd())
    real = os.path.realpath(path)
    if real == cwd or real.startswith(cwd + os.sep):
        return os.path.relpath(real, cwd).replace(os.sep, "/")
    return path


def _sha256_or_empty(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def _fire_map_ids(path):
    """The entry ids load_fire_map (tools/l5b_audit/verify.py) would keep, in its own order of judgement."""
    _require_non_empty_str("fire map path", path)
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise report_mod.AuditInputError("fire map unreadable: %s" % exc)
    if not isinstance(data, list):
        raise report_mod.AuditInputError("fire map must hold a JSON array")
    ids = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise report_mod.AuditInputError("fire map entry %d must be an object" % index)
        for key in ("entry_id", "test_id", "mutation_id", "expect_substring"):
            if key not in item:
                raise report_mod.AuditInputError("fire map entry %d missing %s" % (index, key))
            if not isinstance(item[key], str):
                raise report_mod.AuditInputError("fire map entry %d %s must be a string" % (index, key))
        if len("".join(item["expect_substring"].split())) < _MIN_SUBSTRING_CHARS:
            continue
        if not item["entry_id"] or not item["test_id"] or not item["mutation_id"]:
            raise report_mod.AuditInputError("fire map entry %d has an empty id field" % index)
        ids.add(item["entry_id"])
    return frozenset(ids)


def _admitted_fires(fires, calls, root, fire_map_ids):
    """A fired record counts only when this scan and the fire map both name it, once."""
    if not isinstance(fires, tuple) or not isinstance(calls, tuple) or not isinstance(fire_map_ids, frozenset):
        raise report_mod.AuditInputError("fires and calls must be tuples, fire_map_ids a frozenset")
    _require_non_empty_str("root", root)
    scanned = set(scanner_mod.call_id("%s/%s" % (root, call.file), call.qualname, call.symbol, call.ordinal)
                  for call in calls)
    seen = set()
    for record in fires:
        if not record.fired:
            continue
        if record.entry_id not in scanned:
            raise report_mod.AuditInputError("fired record names no scanned call: %s" % record.entry_id)
        if record.entry_id not in fire_map_ids:
            raise report_mod.AuditInputError("fired record names no fire map entry: %s" % record.entry_id)
        if record.entry_id in seen:
            raise report_mod.AuditInputError("fired record repeated: %s" % record.entry_id)
        seen.add(record.entry_id)
    return tuple(fires)


def _derive(root, fires_path, fire_map_path):
    """The one derivation of an audit record from the tree: the audit emits it, the recheck compares it."""
    root = _relative_root(root)
    _require_non_empty_str("fires_path", fires_path)
    _require_non_empty_str("fire_map_path", fire_map_path)
    skips = inventory_skips(root)
    calls = scanner_mod.scan_tree(root)

    hits = []
    for call in calls:
        full = os.path.join(root, call.file)
        try:
            with open(full, "rb") as handle:
                raw = handle.read()
            source = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            lint_hits = lint_rules.lint_call(source, call)
        except ValueError as exc:
            raise report_mod.AuditInputError(
                "lint_call refused %s: %s" % (call.symbol, exc))
        for hit in lint_hits:
            hits.append(report_mod.AuditHit(
                hit_id=hit.hit_id,
                rule_id=hit.rule_id,
                kind=call.kind,
                severity=hit.severity,
                enclosing_function=hit.enclosing_function,
            ))

    refusal = ""
    try:
        fires, checker_bypass = _read_fires(fires_path)
        fires = _admitted_fires(fires, calls, root, _fire_map_ids(fire_map_path))
    except report_mod.AuditInputError as exc:
        refusal, fires, checker_bypass = str(exc), (), ()

    probes = tuple(_run_probe(name) for name in PROBE_NAMES)

    audit_report = report_mod.assemble(
        scan=calls,
        lint=tuple(hits),
        fires=fires,
        exemptions=(),
        probes=probes,
        skips=skips,
    )
    if refusal:
        # a bad fires file BLOCKS as SCAN_ERROR: never a traceback, never a pass
        audit_report = dataclasses.replace(
            audit_report, blocked=True, reason=report_mod.REASON_SCAN_ERROR,
            skips=dataclasses.replace(skips, skip_reasons=skips.skip_reasons + ("fires: %s" % refusal,)))
    elif checker_bypass:
        audit_report = dataclasses.replace(
            audit_report, blocked=True, reason=report_mod.REASON_CHECKER_MISMATCH,
            skips=dataclasses.replace(skips, skip_reasons=skips.skip_reasons
                                      + ("checker_bypass: %d call(s)" % len(checker_bypass),)))
    return dataclasses.replace(
        audit_report, root=root,
        fires_path=_relative_path(fires_path), fires_sha256=_sha256_or_empty(fires_path),
        fire_map_path=_relative_path(fire_map_path), fire_map_sha256=_sha256_or_empty(fire_map_path))


def _recheck(path):
    _require_non_empty_str("path", path)
    if not os.path.isfile(path):
        return 2
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return 2
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return 2
    try:
        data = json.loads(text)
    except ValueError:
        return 2
    if not isinstance(data, dict):
        return 2
    stored = data.get("report_hash")
    if not isinstance(stored, str):
        return 2
    body = dict(data)
    body.pop("report_hash", None)
    body.pop("produced_at", None)
    payload = json.dumps(body, ensure_ascii=True, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if digest != stored:
        return 1
    # L5b.8: a record that cannot be re-derived is never a pass
    root, fires, fire_map = data.get("root"), data.get("fires"), data.get("fire_map")
    if not all(isinstance(v, str) and v for v in (root, fires, fire_map)):
        return 2
    try:
        _relative_root(root)
    except report_mod.AuditInputError:
        return 2
    for needed in (fires, fire_map):
        if not os.path.isfile(needed) or not os.access(needed, os.R_OK):
            return 2
    try:
        derived = report_mod._report_body(_derive(root, fires, fire_map))
    except report_mod.AuditInputError:
        return 2
    derived.pop("report_hash", None)
    derived.pop("produced_at", None)
    if derived != body:
        return 1
    return 0


def _run_probe(name):
    path = os.path.join(_ROOT, "tools", "l5b_audit", "probes", name + ".py")
    if not os.path.isfile(path):
        return score_mod.FailClosedProbe(name=name, exit_code=1, output="")
    buffer = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buffer):
            runpy.run_path(path, run_name="__main__")
    except SystemExit as exc:
        raw = exc.code
        if raw is None:
            code = 0
        elif isinstance(raw, bool):
            code = 1
        elif isinstance(raw, int):
            code = raw
        else:
            code = 1
    except Exception:
        code = 1
    return score_mod.FailClosedProbe(name=name, exit_code=code, output=buffer.getvalue())


def _read_fires(path):
    _require_non_empty_str("fires path", path)
    if not os.path.isfile(path):
        raise report_mod.AuditInputError("fires file is missing")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise report_mod.AuditInputError("fires file unreadable: %s" % exc)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise report_mod.AuditInputError("fires file is not utf-8")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise report_mod.AuditInputError("fires file is not JSON: %s" % exc)
    if not isinstance(data, dict):
        raise report_mod.AuditInputError("fires file must hold a JSON object")
    if data.get("scheme_version") != "l5b-fires-v1":
        raise report_mod.AuditInputError("fires scheme_version mismatch")
    raw_fires = data.get("fires", ())
    if not isinstance(raw_fires, list):
        raise report_mod.AuditInputError("fires must be a list")
    fires = []
    for index, item in enumerate(raw_fires):
        if not isinstance(item, dict):
            raise report_mod.AuditInputError("fires[%d] must be an object" % index)
        entry_id = item.get("entry_id")
        fired = item.get("fired")
        reason = item.get("reason", "")
        if not isinstance(entry_id, str) or not entry_id:
            raise report_mod.AuditInputError("fires[%d].entry_id must be a non-empty string" % index)
        if not isinstance(fired, bool):
            raise report_mod.AuditInputError("fires[%d].fired must be a boolean" % index)
        if not isinstance(reason, str):
            raise report_mod.AuditInputError("fires[%d].reason must be a string" % index)
        fires.append(report_mod.FireRecord(entry_id=entry_id, fired=fired, reason=reason))
    bypass = data.get("checker_bypass", ())
    if not isinstance(bypass, list):
        raise report_mod.AuditInputError("checker_bypass must be a list")
    for entry in bypass:
        if not isinstance(entry, str):
            raise report_mod.AuditInputError("checker_bypass entries must be strings")
    return tuple(fires), tuple(bypass)


def _audit(root, out, doc, patch, fires_path, exemptions_path, fire_map_path=""):
    _require_non_empty_str("root", root)
    _require_non_empty_str("out", out)
    _require_non_empty_str("doc", doc)
    _require_non_empty_str("patch", patch)
    _require_non_empty_str("fires_path", fires_path)
    _require_non_empty_str("exemptions_path", exemptions_path)
    if not os.path.isdir(root):
        raise report_mod.AuditInputError("root must be a directory")
    audit_report = _derive(root, fires_path, fire_map_path or os.path.join(_ROOT, DEFAULT_FIRE_MAP))
    report_mod.emit(audit_report, doc, out, patch)
    if _failed_thresholds(audit_report):
        return 1
    return 0


def _usage():
    sys.stderr.write("usage: l5b_audit.py --recheck PATH\n")
    sys.stderr.write("   or: l5b_audit.py --root DIR --out JSON --doc MD --patch PATCH --fires FIRES [--fire-map PATH]\n")


def main(argv=None):
    items = _argv_items(argv)
    if items is None:
        items = list(sys.argv[1:])
    if not items:
        _usage()
        return 2
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--recheck")
    parser.add_argument("--root")
    parser.add_argument("--out")
    parser.add_argument("--doc")
    parser.add_argument("--patch")
    parser.add_argument("--fires")
    parser.add_argument("--exemptions")
    parser.add_argument("--fire-map")
    args, extras = parser.parse_known_args(items)
    if extras:
        _usage()
        return 2
    if args.recheck:
        return _recheck(args.recheck)
    if not (args.root and args.out and args.doc and args.patch and args.fires):
        _usage()
        return 2
    exemptions_path = args.exemptions or os.path.join(_ROOT, DEFAULT_EXEMPTIONS)
    lock_path = os.path.join(os.path.dirname(os.path.abspath(args.out)), LOCK_NAME)
    handle = None
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        handle = open(lock_path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.stderr.write("another audit is running\n")
            handle.close()
            return 1
    try:
        return _audit(args.root, args.out, args.doc, args.patch, args.fires, exemptions_path,
                      args.fire_map or "")
    finally:
        if handle is not None:
            try:
                import fcntl as _fcntl
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            except Exception:
                pass
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
