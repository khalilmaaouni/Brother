#!/usr/bin/env python3
"""PG1.a: the closing ceremony's seed for the next release plan (schema brother-plan-seed/1).

A seed is a DRAFT the planner edits in minutes, never a plan: it lists the units the closing plan left
unfinished (DEFERRED, PARKED, or OPEN with work remaining), the open findings of the decisions file, the
failure notes written since the plan's date, and the loop and cut evidence of WITHHELD, EXHAUSTED or FAIL
runs, every row naming its origin, with the quoted lines of every symbol a unit's reads list names so a
writer skips discovery. Reader: scripts/plan_gen.py (PG1.b). Driven by scripts/handover_ceremony.py
--emit-seed.

FAIL DIRECTION: an input that cannot be read is NO-DATA, as a source inside the seed or as a ValueError that
the ceremony turns into exit 2. No branch presents an absent source as a present one, and no cached read is
silently dropped. Python 3.9, standard library only. Tests: scripts/test_plan_seed.py.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
from typing import List, Optional

SEED_SCHEMA = "brother-plan-seed/1"
NO_DATA = "NO-DATA"
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
CACHE_LINES = 40  # lines quoted from each read entry's line number
LINE_CAP = 400  # characters kept of one quoted line (a minified file is one huge line)
TEXT_CAP = 600  # characters kept of one finding or failure row
_LINE_NO = re.compile(r"\blines?\s+(\d+)")
_FAIL_LINE = re.compile(r"^-\s+\[\[?([^\]\n]+)\]\]?(?:\([^)\n]*\))?\s+(\d{4}-\d{2}-\d{2})\b[.:]?\s*(.*)$")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _cap(text: str) -> str:
    return text if len(text) <= TEXT_CAP else text[:TEXT_CAP] + " [truncated]"


def _str_list(unit: dict, key: str, required: bool) -> List[str]:
    value = unit.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("unit %r: %s must be a list of text, got %r" % (unit.get("id"), key, value))
    return list(value)


def _safe_path(root: str, rel: str) -> Optional[str]:
    """The absolute path of `rel` when it is relative and stays inside `root`, else None."""
    if not rel or os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        return None
    full = os.path.realpath(os.path.join(root, rel))
    base = os.path.realpath(root)
    return full if full == base or full.startswith(base + os.sep) else None


def cached_reads(reads: List[str], root: str) -> List[dict]:
    """For every 'path symbol (line N)' entry: {path, line, sha256, text} with the 40 lines from N, read now; a
    path or line that cannot be read gives {path, text: 'NO-DATA: <why>'}, never a silent skip. The sha256 is of
    the quoted lines, so a changed region is detectable later. An entry may hold several parts split on ';' and
    several 'line N' marks; each mark is one row."""
    if not isinstance(reads, list):
        raise ValueError("reads must be a list, got %s" % type(reads).__name__)
    rows = []
    for entry in reads:
        if not isinstance(entry, str):
            rows.append({"path": repr(entry), "text": "NO-DATA: a reads entry that is not text"})
            continue
        for part in entry.split(";"):
            tokens = part.split()
            if not tokens:
                continue
            path = tokens[0]
            nums = [int(n) for n in _LINE_NO.findall(part)]
            full = _safe_path(root, path)
            if full is None:
                rows.append({"path": path, "text": "NO-DATA: path is absolute or outside the root"})
            elif not nums:
                rows.append({"path": path, "text": "NO-DATA: the entry names no line number"})
            else:
                rows.extend(_quote(full, path, n) for n in nums)
    return rows


def _quote(full: str, path: str, line: int) -> dict:
    try:
        with open(full, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except (OSError, UnicodeDecodeError) as exc:
        return {"path": path, "line": line, "text": "NO-DATA: %s" % type(exc).__name__}
    if line < 1 or line > len(lines):
        return {"path": path, "line": line, "text": "NO-DATA: line %d is past the end (%d lines)" % (line, len(lines))}
    chunk = lines[line - 1:line - 1 + CACHE_LINES]
    text = "\n".join(chunk)
    row = {"path": path, "line": line, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    if any(len(c) > LINE_CAP for c in chunk):
        text = "\n".join(c[:LINE_CAP] for c in chunk)
        row["truncated"] = True
    row["text"] = text
    return row


def parked_units(plan: dict, root: Optional[str] = None, from_plan: Optional[str] = None) -> List[dict]:
    """Every unit of `plan` whose state is DEFERRED or PARKED, or OPEN with remains other than 'nothing', as seed
    rows (id, title, objective, owns, after, source, reads, spec_text when the spec scored 9, reason). A state
    outside OPEN, DEFERRED, PARKED, DONE is carried with reason 'unknown state <x>', never dropped. Not a record:
    ValueError. spec_text is the spec file's text when the unit carries spec_score >= 9 and `root` is given (the
    plan holds no score today, so it is None until PG1.d makes one readable); reads are cached against `root`.
    Every row is a CARRIED unit: it keeps its tracked id and its existing spec, so the row also carries carried
    true, sub_units, worker, spec, done_check, checker and from_plan (`from_plan`, the closing plan's path, or
    NO-DATA when none is given), which plan_gen reads to exempt the id from its collision check."""
    if not isinstance(plan, dict) or not isinstance(plan.get("units"), list):
        raise ValueError("the plan is not a record with a units list")
    rows = []
    for unit in plan["units"]:
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str) or not unit["id"].strip():
            raise ValueError("a unit without a text id: %r" % (unit,))
        state = unit.get("state")
        remains = str(unit.get("remains", "everything")).strip().lower()
        if state == "DONE":
            continue
        if state in ("DEFERRED", "PARKED"):
            reason = "state %s" % state
        elif state == "OPEN":
            if remains == "nothing":
                continue
            reason = "state OPEN, remains %s" % (unit.get("remains", "everything"))
        else:
            reason = "unknown state %s" % (state,)
        reads = _str_list(unit, "reads", False)
        rows.append({
            "id": unit["id"], "title": str(unit.get("title", "")), "objective": str(unit.get("objective", "")),
            "owns": _str_list(unit, "owns", True), "after": _str_list(unit, "after", False),
            "source": str(unit.get("source", "")), "reads": reads,
            "reads_cache": cached_reads(reads, root) if root else [],
            "spec_text": _spec_text(unit, root), "reason": reason, "origin": "plan:%s" % unit["id"],
            "carried": True, "sub_units": _str_list(unit, "sub_units", False), "worker": str(unit.get("worker", "")),
            "spec": str(unit.get("spec", "")), "done_check": str(unit.get("done_check", "")),
            "checker": str(unit.get("checker", "")),
            # 2026-10-14: the landing evidence travels with the unit, so a sub unit landed in N stays landed in N+1
            # (plan_store.sub_landed) and its files are not read as stale NEW labels by the next plan lint.
            "evidence": str(unit.get("evidence") or ""),
            "from_plan": from_plan if isinstance(from_plan, str) and from_plan.strip() else NO_DATA})
    return rows


def _spec_text(unit: dict, root: Optional[str]) -> Optional[str]:
    score = unit.get("spec_score")
    if root is None or isinstance(score, bool) or not isinstance(score, (int, float)) or score < 9:
        return None
    full = _safe_path(root, str(unit.get("spec", "")))
    try:
        with open(full, encoding="utf-8") as f:
            return f.read()
    except (OSError, TypeError, UnicodeDecodeError):
        return None


def open_findings(decisions_text: str) -> List[dict]:
    """The bullets under a heading whose text contains 'Parked' or 'could not verify' (any case) in a
    PLAN-DECISIONS file, one row each (text, heading). Empty text gives []; non text raises ValueError. A bullet
    cut by the end of a file that has no final newline is kept and marked truncated."""
    if not isinstance(decisions_text, str):
        raise ValueError("the decisions text is %s, not text" % type(decisions_text).__name__)
    rows, heading, hit = [], "", False
    lines = decisions_text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            hit = "parked" in heading.lower() or "could not verify" in heading.lower()
        elif hit and re.match(r"^\s*[-*]\s+\S", line):
            row = {"text": _cap(re.sub(r"^\s*[-*]\s+", "", line).strip()), "heading": heading}
            if i == len(lines) - 1:
                row["truncated"] = True
            rows.append(row)
    return rows


def failure_rows(index_text: str, since: str) -> List[dict]:
    """The Failures-Index lines dated `since` (YYYY-MM-DD) or later: {slug, date, text}. Non text raises."""
    if not isinstance(index_text, str):
        raise ValueError("the failure index is %s, not text" % type(index_text).__name__)
    rows = []
    for line in index_text.split("\n"):
        m = _FAIL_LINE.match(line)
        if m and m.group(2) >= since:
            rows.append({"slug": m.group(1), "date": m.group(2), "text": _cap(m.group(3).strip())})
    return rows


def _no_data(kind: str, where: str, why: str) -> dict:
    return {"kind": kind, "file": where, "status": NO_DATA, "text": why}


def evidence_findings(runs_dir: str, cut_dir: str, since: float = 0.0) -> List[dict]:
    """One row per loop run directory whose STATUS is WITHHELD or EXHAUSTED and per cut driver state whose newest
    phase is FAIL, each naming its file, newer than epoch `since`, sorted by file; an unreadable directory, STATUS
    or state is one NO-DATA row, never []."""
    rows = []
    try:
        for name in sorted(os.listdir(runs_dir)):
            path = os.path.join(runs_dir, name, "STATUS")
            if not os.path.isfile(path):
                continue
            try:
                if os.path.getmtime(path) < since:
                    continue
                with open(path, encoding="utf-8") as f:
                    first = f.readline().strip()
            except (OSError, UnicodeDecodeError) as exc:
                rows.append(_no_data("loop-run", path, "STATUS unreadable: %s" % type(exc).__name__))
                continue
            if first.split(" ")[0] in ("WITHHELD", "EXHAUSTED"):
                rows.append({"kind": "loop-run", "file": path, "status": first.split(" ")[0], "text": _cap(first)})
    except OSError as exc:
        rows.append(_no_data("loop-run", runs_dir, "runs directory unreadable: %s" % type(exc).__name__))
    try:
        for name in sorted(os.listdir(cut_dir)):
            path = os.path.join(cut_dir, name)
            if not (name.startswith("driver-") and name.endswith(".json")) or os.path.getmtime(path) < since:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    phases = json.load(f)["phases"]
                newest = max(phases, key=lambda p: phases[p]["started"])
                verdict = phases[newest]["verdict"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                rows.append(_no_data("cut-state", path, "driver state unreadable: %s" % type(exc).__name__))
                continue
            if verdict == "FAIL":
                rows.append({"kind": "cut-state", "file": path, "status": "FAIL",
                             "text": _cap("phase %s FAIL: %s" % (newest, phases[newest].get("detail", "")))})
    except OSError as exc:
        rows.append(_no_data("cut-state", cut_dir, "cut directory unreadable: %s" % type(exc).__name__))
    return sorted(rows, key=lambda r: r["file"])


def read_grant(path: str) -> dict:
    """The owner's words from a baseline record: lines 'owner | model | "<words>"' (also budget, deadline), fields
    split on ' | ', '#' lines and others ignored. Words verbatim without their quotes; a key with no line,
    unbalanced quotes, NO-DATA or conflicting repeats reads NO-DATA. An unreadable file raises OSError."""
    seen = {"model": [], "budget": [], "deadline": []}
    with open(path, encoding="utf-8") as f:
        for line in f.read().split("\n"):
            parts = [p.strip() for p in line.split(" | ", 2)]
            if len(parts) == 3 and parts[0] == "owner" and parts[1] in seen:
                w = parts[2]
                quoted = len(w) >= 2 and w[0] == '"' and w[-1] == '"'
                seen[parts[1]].append(w[1:-1] if quoted else NO_DATA)
    return {k: (v[0] if v and len(set(v)) == 1 else NO_DATA) for k, v in seen.items()}


def _proposed(n: int, title: str, text: str, origin: str, source: str) -> dict:
    return {"id": "PROPOSED-%02d" % n, "title": title[:80], "objective": text, "owns": [], "after": [],
            "source": source, "reads": [], "reads_cache": [], "spec_text": None,
            "reason": "proposed from %s" % origin.split(":")[0], "origin": origin}


def _grant(grant: dict) -> dict:
    """model, budget and deadline: the owner's words verbatim, or NO-DATA each."""
    out = {}
    for key in ("model", "budget", "deadline"):
        v = grant.get(key) if isinstance(grant, dict) else None
        out[key] = v if isinstance(v, str) and v.strip() and v.strip() != NO_DATA else NO_DATA
    return out


def build_seed(version: str, plan: dict, decisions_text: Optional[str], index_text: Optional[str],
               evidence: List[dict], grant: dict, now: float, root: Optional[str] = None,
               from_plan: Optional[str] = None) -> dict:
    """The seed record: schema, version, generated_at, grant (model, budget, deadline: words or NO-DATA), sources
    (each PRESENT or NO-DATA with why), units (parked rows first, then PROPOSED-NN rows per finding, failure note
    and evidence row, every row with its origin), findings, failures. A version that is not N.N.N, a plan that is
    not a record or a malformed evidence row raises ValueError. None for decisions_text or index_text is NO-DATA.
    `root` (the tree the reads entries are relative to) is where the cached reads are taken; None caches nothing.
    `from_plan` is the closing plan's path for the carried rows; None with a root asks the one plan selector
    (scripts/loop/plan_path.py, BROTHER_WBS), and a selector that refuses leaves NO-DATA (plan_gen then refuses)."""
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ValueError("version %r is not N.N.N" % (version,))
    if not isinstance(evidence, list) or not all(
            isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("kind", "file", "status", "text"))
            for r in evidence):
        raise ValueError("evidence must be a list of rows with text kind, file, status and text")
    if from_plan is None and root is not None:
        from_plan = _selected_plan()
    units = parked_units(plan, root, from_plan)
    as_of = _DATE.match(str(plan.get("as_of", "")))
    findings = open_findings(decisions_text) if decisions_text is not None else []
    fail_rows, why_fail = [], ""
    if index_text is None:
        why_fail = "no Failures-Index was given"
    elif not as_of:
        why_fail = "the plan has no readable as_of date to cut the index at"
    else:
        fail_rows = failure_rows(index_text, as_of.group(1))
    bad_ev = [r for r in evidence if r["status"] == NO_DATA]
    g = _grant(grant)
    miss = [k for k in g if g[k] == NO_DATA]
    sources = [
        {"name": "plan", "status": "PRESENT", "why": "", "as_of": str(plan.get("as_of", ""))},
        _source("decisions", decisions_text is not None, "no PLAN-DECISIONS file was found"),
        _source("failures_index", not why_fail, why_fail),
        _source("evidence", not bad_ev, "; ".join("%s: %s" % (r.get("file"), r.get("text")) for r in bad_ev)),
        _source("grant", not miss, "no owner words for " + ", ".join(miss))]
    n = 0
    for f in findings:
        n += 1
        units.append(_proposed(n, f["text"], f["text"], "finding:" + f["heading"], f["heading"]))
    for f in fail_rows:
        n += 1
        units.append(_proposed(n, f["slug"], f["text"], "failure:" + f["slug"], "Failures-Index " + f["date"]))
    for r in evidence:
        if r["status"] != NO_DATA:
            n += 1
            units.append(_proposed(n, "%s %s" % (r["kind"], r["status"]), r["text"], "evidence:" + r["file"], r["file"]))
    return {"schema": SEED_SCHEMA, "version": version,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "grant": g, "sources": sources, "units": units,
            "findings": findings + evidence,
            "failures": {"status": "PRESENT" if not why_fail else NO_DATA, "why": why_fail, "rows": fail_rows}}


def _selected_plan() -> Optional[str]:
    """The plan path the one selector names for this process, or None when it refuses."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import plan_path
        return plan_path.selected()
    except (ImportError, ValueError):
        return None


def _source(name: str, present: bool, why: str) -> dict:
    return {"name": name, "status": "PRESENT" if present else NO_DATA, "why": "" if present else why}


def write_seed(path: str, seed: dict) -> str:
    """Writes the seed as JSON (indent 1, newline) through a temporary name and a rename, refusing to overwrite a
    file whose schema differs (or that is not a seed record at all), and returns the path. The parent directory
    must exist (no silent mkdir). A seed that is not a brother-plan-seed/1 record raises ValueError."""
    if not isinstance(seed, dict) or seed.get("schema") != SEED_SCHEMA:
        raise ValueError("not a %s record" % SEED_SCHEMA)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                held = json.load(f).get("schema")
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError("%s exists and is not a readable seed (%s): not overwritten" % (path, type(exc).__name__))
        if held != SEED_SCHEMA:
            raise ValueError("%s holds schema %r, not %s: not overwritten" % (path, held, SEED_SCHEMA))
    tmp = "%s.tmp-%d-%d" % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(seed, indent=1) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path
