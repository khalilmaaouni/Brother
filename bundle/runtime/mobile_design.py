#!/usr/bin/env python3
"""Search a user-curated mobile reference board and inspect creative media.

Original local implementation. No hosted catalog, network, scraping or model
inference. Search is token matching, never semantic similarity or revenue proof.
Every reference keeps its source; missing visual evidence remains NO-DATA.

WBS-30.03 design evidence adapter: evidence_record() and check_staleness()
strengthen one board reference with a stable id, source date, media hash,
journey step, device class, locale, accessibility observation and rationale
for inclusion, feeding visual_reference_ids on mobile-journey-contract-v1.
Evidence and context only, never an automatic UX verdict.
"""
import argparse
import datetime
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse

import native_evidence as N
from mobile_workflow import Refusal, require, read_json, write_new, digest

SCHEMA = "brother-mobile-board-v1"
EVIDENCE_SCHEMA = "brother-design-evidence-v1"
STALENESS_SCHEMA = "brother-design-evidence-staleness-v1"


def words(value):
    return set(re.findall(r"\w+", value.casefold()))


def board(path):
    data = read_json(path)
    require(isinstance(data, dict) and data.get("schema") == SCHEMA, "Unsupported reference board")
    require(isinstance(data.get("screens"), list) and data["screens"], "Board needs curated screens")
    ids, positions = set(), set()
    for screen in data["screens"]:
        require(isinstance(screen, dict), "Screen must be an object")
        required = ("id", "app", "title", "flow", "source", "observed_at", "notes")
        require(all(isinstance(screen.get(k), str) and screen[k].strip() for k in required), "Incomplete screen reference")
        require(screen["id"] not in ids, "Duplicate screen ID")
        ids.add(screen["id"])
        try:
            datetime.date.fromisoformat(screen["observed_at"])
        except ValueError as exc:
            raise Refusal("Reference needs an ISO observation date") from exc
        require(urlparse(screen["source"]).scheme in ("https", "http", "file"), "Source needs an explicit URL")
        require(isinstance(screen.get("step"), int) and not isinstance(screen["step"], bool) and screen["step"] >= 0, "Flow step must be a nonnegative integer")
        position = (screen["app"], screen["flow"], screen["step"])
        require(position not in positions, "Ambiguous flow ordering")
        positions.add(position)
        require(isinstance(screen.get("elements"), list) and all(isinstance(e, str) and e.strip() for e in screen["elements"]), "Elements must be strings")
        for key in ("device_class", "locale", "accessibility_observation", "rationale"):
            if key in screen:
                require(isinstance(screen[key], str) and screen[key].strip(), "%s must be a non-empty string" % key)
        media = screen.get("media")
        if media is not None:
            require(N.valid_file_hash(media) and N.hash_file(media["path"]) == media, "Screen media is missing, changed or malformed")
    return data


def evidence_record(path, screen_id):
    """Design evidence adapter (WBS-30.03): the stable, named-field record for
    ONE design reference already curated on a mobile_design board. Evidence
    and context only -- status reflects whether a media hash was captured
    for this reference, never design correctness, review outcome or a UX
    pass/fail. See check_staleness for the separate, narrowly-scoped hash
    check that catches a reference id whose target changed underneath it."""
    data = board(path)
    matches = [s for s in data["screens"] if s["id"] == screen_id]
    require(len(matches) == 1, "Reference id not found on this board")
    screen = matches[0]
    media = screen.get("media")
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": "PASS" if media else "NO-DATA",
        "reference_id": screen["id"],
        "source_date": screen["observed_at"],
        "journey_step": {"flow": screen["flow"], "step": screen["step"]},
        "device_class": screen.get("device_class", "NO-DATA"),
        "locale": screen.get("locale", "NO-DATA"),
        "accessibility_observation": screen.get("accessibility_observation", "NO-DATA"),
        "rationale_for_inclusion": screen.get("rationale", "NO-DATA"),
        "media": media if media else "NO-DATA",
        "board": digest(path),
        "limits": ["Evidence and context, never an automatic UX verdict: status names only whether a media hash was captured.",
                   "A recorded reference does not certify the design was reviewed, approved, correct or still current -- see check_staleness."],
    }


def check_staleness(evidence_path, board_path):
    """Does a previously recorded evidence reference id still resolve to a
    file whose hash matches what was recorded? Named failure mode (WAVE-2
    Muse hostile review, WBS-30.03): a stable id can keep resolving after
    its target quietly changed or was replaced, so an id-exists check
    alone silently passes against the wrong target. This re-hashes the
    file the id CURRENTLY resolves to on the board and compares it
    against the hash captured in the evidence record, not against the
    board's own (possibly re-synced) inline hash. Scoped to hash identity
    only: never a design correctness or UX verdict."""
    evidence = read_json(evidence_path)
    require(isinstance(evidence, dict) and evidence.get("schema") == EVIDENCE_SCHEMA, "Not a design evidence record")
    reference_id = evidence.get("reference_id")
    require(isinstance(reference_id, str) and reference_id.strip(), "Evidence record has no reference id")
    raw = read_json(board_path)
    require(isinstance(raw, dict) and raw.get("schema") == SCHEMA and isinstance(raw.get("screens"), list), "Unsupported reference board")
    matches = [s for s in raw["screens"] if isinstance(s, dict) and s.get("id") == reference_id]
    resolves = len(matches) == 1
    checks = [{"name": "reference_resolves", "status": "PASS" if resolves else "FAIL"}]
    recorded_media = evidence.get("media")
    recorded_media = recorded_media if isinstance(recorded_media, dict) else None
    if not resolves:
        checks.append({"name": "media_hash_match", "status": "NO-DATA"})
    else:
        current_media = matches[0].get("media")
        current_media = current_media if isinstance(current_media, dict) else None
        if recorded_media is None and current_media is None:
            checks.append({"name": "media_hash_match", "status": "NO-DATA"})
        elif recorded_media is None or current_media is None:
            checks.append({"name": "media_hash_match", "status": "FAIL"})
        else:
            try:
                fresh = digest(current_media["path"])
            except (Refusal, OSError, KeyError):
                fresh = None
            checks.append({"name": "media_hash_match", "status": "PASS" if fresh is not None and fresh["sha256"] == recorded_media.get("sha256") else "FAIL"})
    status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "NO-DATA" if any(c["status"] == "NO-DATA" for c in checks) else "PASS"
    return {
        "schema": STALENESS_SCHEMA,
        "status": status,
        "reference_id": reference_id,
        "checks": checks,
        "board": digest(board_path),
        "limits": ["Hash match confirms the file the id currently resolves to is byte-identical to what evidence_record captured, nothing more.",
                   "Never a design correctness, review or UX verdict; a matched hash is not a review and a mismatch is not a defect."],
    }


def search(path, query="", app=None, flow=None, element=None):
    data = board(path)
    terms = words(query)
    rows = []
    for screen in data["screens"]:
        if app and screen["app"] != app or flow and screen["flow"] != flow or element and element not in screen["elements"]:
            continue
        searchable = " ".join([screen[k] for k in ("app", "title", "flow", "notes")] + screen["elements"])
        if not terms.issubset(words(searchable)):
            continue
        rows.append({**screen, "visual_evidence": "PASS" if screen.get("media") else "NO-DATA"})
    rows.sort(key=lambda s: (s["app"], s["flow"], s["step"], s["id"]))
    return {"status": "PASS", "screens": rows, "count": len(rows), "board": digest(path),
            "limits": ["Local token/filter search only. No live market metrics or semantic similarity.",
                       "Media integrity does not prove a reference was visually reviewed or improved conversion."]}


def brief(path, query, outcome):
    result = search(path, query)
    require(isinstance(outcome, str) and outcome.strip(), "Name the user outcome")
    return {"schema": "brother-mobile-design-brief-v1", "status": "PASS" if result["screens"] else "NO-DATA",
            "outcome": outcome, "research": result,
            "decisions": {k: {"status": "NO-DATA", "reason": "Complete from the selected references and actual app"}
                          for k in ("layout_and_tokens", "navigation_and_state", "motion_purpose", "reduced_motion",
                                    "ipad_adaptation", "accessibility", "asset_provenance", "gamification", "success_measure")},
            "review": ["Compare the complete task flow, including cancellation, errors and return navigation.",
                       "Use native controls and data-driven SwiftUI state; keep research separate from implementation.",
                       "Record the interaction and review interruption, repeated taps and reduced motion.",
                       "Measure the actual user outcome; commercial success is not inferred from a reference app."]}


def inspect_media(path, role, max_bytes=None, max_duration=None):
    require(role in ("runtime", "marketing", "prototype"), "Media role is invalid")
    before = digest(path)
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", before["path"]]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "NO-DATA", "reason": "Media probe unavailable: %s" % exc, "asset": before, "command": command}
    require(proc.returncode == 0, "Media probe refused the asset")
    try:
        value = json.loads(proc.stdout)
    except ValueError as exc:
        raise Refusal("Media probe returned malformed JSON") from exc
    require(isinstance(value, dict) and isinstance(value.get("streams"), list) and value["streams"], "Media has no probeable streams")
    require(digest(path) == before, "Media changed while being inspected")
    streams = []
    for raw in value["streams"]:
        require(isinstance(raw, dict), "Malformed media stream")
        row = {k: raw[k] for k in ("codec_type", "codec_name", "width", "height", "pix_fmt", "sample_rate", "channels") if k in raw}
        if raw.get("avg_frame_rate"):
            try:
                row["fps"] = float(Fraction(raw["avg_frame_rate"]))
            except (ValueError, ZeroDivisionError):
                row["fps"] = "NO-DATA"
        streams.append(row)
    duration = value.get("format", {}).get("duration")
    if duration is not None:
        try:
            duration = float(duration)
            require(math.isfinite(duration) and duration >= 0, "Invalid media duration")
        except (ValueError, TypeError) as exc:
            raise Refusal("Invalid media duration") from exc
    checks = []
    if max_bytes is not None:
        require(max_bytes > 0, "Byte budget must be positive")
        checks.append({"name": "file_bytes", "status": "PASS" if before["size"] <= max_bytes else "FAIL", "actual": before["size"], "maximum": max_bytes})
    if max_duration is not None:
        require(math.isfinite(max_duration) and max_duration > 0, "Duration budget must be positive")
        checks.append({"name": "duration", "status": "NO-DATA" if duration is None else "PASS" if duration <= max_duration else "FAIL", "actual": duration, "maximum": max_duration})
    status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "NO-DATA" if any(c["status"] == "NO-DATA" for c in checks) else "PASS"
    return {"schema": "brother-mobile-media-v1", "status": status, "asset": before, "role": role, "streams": streams,
            "duration_seconds": duration, "checks": checks, "command": command, "exit_code": proc.returncode,
            "probe_sha256": N.sha256_bytes(proc.stdout.encode()),
            "observations": {"runtime_performance": "NO-DATA", "visual_quality": "NO-DATA", "rights": "NO-DATA", "reduced_motion": "NO-DATA"},
            "limits": ["Metadata and explicit file budgets only. File frame rate is not app frame rate.",
                       "Keep generator project/workflow, model revision, rights evidence and native fallback alongside the asset."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    s = sub.add_parser("search")
    s.add_argument("--board", required=True); s.add_argument("--query", default="")
    for key in ("app", "flow", "element"):
        s.add_argument("--" + key)
    b = sub.add_parser("brief")
    b.add_argument("--board", required=True); b.add_argument("--query", default=""); b.add_argument("--outcome", required=True)
    m = sub.add_parser("media")
    m.add_argument("--asset", required=True); m.add_argument("--role", required=True, choices=("runtime", "marketing", "prototype"))
    m.add_argument("--max-bytes", type=int); m.add_argument("--max-duration", type=float)
    e = sub.add_parser("evidence")
    e.add_argument("--board", required=True); e.add_argument("--id", required=True, dest="screen_id")
    c = sub.add_parser("check-staleness")
    c.add_argument("--evidence", required=True); c.add_argument("--board", required=True)
    for p in (s, b, m, e, c):
        p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "search":
            result = search(args.board, args.query, args.app, args.flow, args.element)
        elif args.action == "brief":
            result = brief(args.board, args.query, args.outcome)
        elif args.action == "media":
            result = inspect_media(args.asset, args.role, args.max_bytes, args.max_duration)
        elif args.action == "evidence":
            result = evidence_record(args.board, args.screen_id)
        else:
            result = check_staleness(args.evidence, args.board)
        write_new(args.out, result)
        print("mobile_design: " + result["status"])
        print(args.out)
        return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[result["status"]]
    except (Refusal, OSError, ValueError) as exc:
        print("mobile_design: FAIL: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
