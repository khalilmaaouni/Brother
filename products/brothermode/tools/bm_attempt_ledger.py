"""J1.c compaction capsule: write down what compaction loses, delete nothing.

Pure: no file reads, no writes, no subprocess, no network. This module
accepts already-parsed transcript lines, pairs each tool call with its
result by the call id, records every failed attempt deduplicated by a
stable fingerprint, bounds the ledger by a budget and counts what was cut.
It never deletes anything from any transcript; the caller decides where
the rendered section lands.
"""
import hashlib
import json
import re

MAX_ATTEMPTS = 40
MAX_DETAIL_CHARS = 300
FAILURE_MARKERS = (
    "Traceback", "REFUSED", "FAILED", "fatal:", "denied",
    "exit code 1", "Exit code",
)

_WS_RE = re.compile(r"\s+")
# Temp directories in the two common Unix layouts. The path body stops at
# the first whitespace or quote so a following argument is not swallowed.
_TEMP_RE = re.compile(
    r"(?:/tmp/|/private/tmp/|/var/tmp/|/private/var/folders/|/var/folders/)"
    r"[^\s\"']*"
)
# Both pids and epoch second or millisecond counters land in this band.
_NUM_RE = re.compile(r"\b\d{4,13}\b")


def _text_or_empty(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _mask(text):
    text = _WS_RE.sub(" ", text)
    text = _TEMP_RE.sub("<TEMP>", text)
    text = _NUM_RE.sub("<NUM>", text)
    return text


def _result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def pair_calls(lines):
    """One dict per tool call, joined to its result by the call's id.

    Each returned dict has keys id, name, input, result_text, is_error and
    index. A call with no result keeps result_text None; a result with no
    call is ignored and counted by the caller. Pure. A non list input is
    refused with ValueError rather than scored.
    """
    if not isinstance(lines, list):
        raise ValueError("pair_calls: lines must be a list")
    calls = []
    by_id = {}
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        msg = line.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            cid = block.get("id")
            if not isinstance(cid, str):
                continue
            name = block.get("name")
            if not isinstance(name, str):
                name = ""
            rec = {
                "id": cid,
                "name": name,
                "input": block.get("input"),
                "result_text": None,
                "is_error": False,
                "index": index,
            }
            calls.append(rec)
            by_id[cid] = rec
    for line in lines:
        if not isinstance(line, dict):
            continue
        msg = line.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            cid = block.get("tool_use_id")
            if not isinstance(cid, str):
                continue
            rec = by_id.get(cid)
            if rec is None:
                continue
            if rec["result_text"] is not None:
                continue
            rec["result_text"] = _result_text(block)
            rec["is_error"] = block.get("is_error") is True
    return calls


def is_failed(attempt):
    """True when is_error is true or result_text carries a failure marker.

    A None result is NOT a failure (unknown is not evidence); it is listed
    as unfinished by build_ledger. Pure. A non dict input is refused with
    ValueError.
    """
    if not isinstance(attempt, dict):
        raise ValueError("is_failed: attempt must be a dict")
    if attempt.get("is_error") is True:
        return True
    text = attempt.get("result_text")
    if text is None:
        return False
    if not isinstance(text, str):
        text = _text_or_empty(text)
    for marker in FAILURE_MARKERS:
        if marker in text:
            return True
    return False


def fingerprint(attempt):
    """Stable sha256 of the tool name and its input, whitespace folded and
    temp paths, pids and epoch numbers masked, so the same command that
    failed five times is ONE row with count 5. Pure. A non dict input is
    refused with ValueError.
    """
    if not isinstance(attempt, dict):
        raise ValueError("fingerprint: attempt must be a dict")
    name = attempt.get("name")
    if not isinstance(name, str):
        name = ""
    name = _WS_RE.sub(" ", name).strip()
    inp = attempt.get("input")
    try:
        inp_str = json.dumps(inp, sort_keys=True, separators=(",", ":"),
                             default=str)
    except (TypeError, ValueError):
        inp_str = _text_or_empty(inp)
    masked = _mask(inp_str)
    payload = (name + "\n" + masked).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _what(attempt):
    name = attempt.get("name")
    if not isinstance(name, str) or not name:
        name = "?"
    inp = attempt.get("input")
    if name == "Bash" and isinstance(inp, dict):
        return ("Bash: " + _text_or_empty(inp.get("command")))[:MAX_DETAIL_CHARS]
    if name in ("Edit", "Write", "Read") and isinstance(inp, dict):
        return (name + ": " + _text_or_empty(inp.get("file_path")))[:MAX_DETAIL_CHARS]
    return name[:MAX_DETAIL_CHARS]


def _why(attempt):
    text = attempt.get("result_text")
    if text is None:
        return ""
    if not isinstance(text, str):
        text = _text_or_empty(text)
    last = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            last = stripped
    return last[:MAX_DETAIL_CHARS]


def build_ledger(lines, max_attempts=MAX_ATTEMPTS):
    """Build the attempt ledger. Returns {"failed", "unfinished", "cut"}.

    Newest first. Over the budget the OLDEST rows are cut and cut says how
    many: never silently. Pure. A non list input, a bool where an int is
    expected, a negative budget or a non int budget is refused with
    ValueError.
    """
    if not isinstance(lines, list):
        raise ValueError("build_ledger: lines must be a list")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise ValueError("build_ledger: max_attempts must be an int")
    if max_attempts < 0:
        raise ValueError("build_ledger: max_attempts must be non-negative")
    attempts = pair_calls(lines)
    failed_groups = {}
    unfinished = []
    for att in attempts:
        if att["result_text"] is None:
            unfinished.append(att)
            continue
        if is_failed(att):
            fp = fingerprint(att)
            grp = failed_groups.get(fp)
            if grp is None:
                grp = {
                    "fingerprint": fp,
                    "what": _what(att),
                    "why": _why(att),
                    "count": 0,
                    "last_index": att["index"],
                    "later_succeeded": False,
                }
                failed_groups[fp] = grp
            grp["count"] += 1
            if att["index"] >= grp["last_index"]:
                grp["last_index"] = att["index"]
                grp["what"] = _what(att)
                grp["why"] = _why(att)
    for fp, grp in failed_groups.items():
        for att in attempts:
            if att["index"] <= grp["last_index"]:
                continue
            if att["result_text"] is None:
                continue
            if is_failed(att):
                continue
            if fingerprint(att) != fp:
                continue
            grp["later_succeeded"] = True
            break
    failed = sorted(failed_groups.values(),
                    key=lambda row: row["last_index"], reverse=True)
    cut = 0
    if len(failed) > max_attempts:
        cut = len(failed) - max_attempts
        failed = failed[:max_attempts]
    unfinished_rows = []
    for att in unfinished:
        unfinished_rows.append({
            "fingerprint": fingerprint(att),
            "what": _what(att),
            "index": att["index"],
        })
    unfinished_rows.sort(key=lambda row: row["index"], reverse=True)
    return {"failed": failed, "unfinished": unfinished_rows, "cut": cut}


def render_ledger(ledger):
    """Render the markdown section.

    An empty ledger renders one line saying none were recorded: the section
    is never absent, so a reader can tell "nothing failed" from "the ledger
    did not run". Pure. A non dict input is refused with ValueError.
    """
    if not isinstance(ledger, dict):
        raise ValueError("render_ledger: ledger must be a dict")
    out = ["## Already tried and failed (do not repeat without a new reason)", ""]
    failed = ledger.get("failed")
    if not isinstance(failed, list) or not failed:
        out.append("(none were recorded)")
        return "\n".join(out)
    for row in failed:
        if not isinstance(row, dict):
            continue
        what = _text_or_empty(row.get("what"))
        why = _text_or_empty(row.get("why"))
        count = row.get("count")
        if isinstance(count, bool) or not isinstance(count, int):
            count = 0
        later = " (later succeeded)" if row.get("later_succeeded") else ""
        out.append("- %s (failed %d time(s))%s" % (what, count, later))
        if why:
            out.append("  why: %s" % why)
    return "\n".join(out)


def repeat_rate(ledger, later_lines):
    """How many fingerprints that had failed were run again after the
    compaction. THE measure of whether this works. Pure. Non dict or non
    list inputs are refused with ValueError.
    """
    if not isinstance(ledger, dict):
        raise ValueError("repeat_rate: ledger must be a dict")
    if not isinstance(later_lines, list):
        raise ValueError("repeat_rate: later_lines must be a list")
    failed = ledger.get("failed")
    if not isinstance(failed, list):
        failed = []
    failed_fps = set()
    for row in failed:
        if isinstance(row, dict):
            fp = row.get("fingerprint")
            if isinstance(fp, str):
                failed_fps.add(fp)
    failed_before = len(failed_fps)
    if failed_before == 0:
        return {"failed_before": 0, "repeated_after": 0, "rate": 0.0}
    attempts = pair_calls(later_lines)
    repeated = set()
    for att in attempts:
        fp = fingerprint(att)
        if fp in failed_fps:
            repeated.add(fp)
    repeated_after = len(repeated)
    return {
        "failed_before": failed_before,
        "repeated_after": repeated_after,
        "rate": float(repeated_after) / float(failed_before),
    }
