#!/usr/bin/env python3
"""GM1: the owner's 1.1.4 mandate measured from the loop's own ledger and the cut driver's own state, never a report.

  python3 scripts/donecheck_114.py --goal G1 [--plan docs/plan/BROTHER-1.1.4-WBS.json] [--runs DIR] [--run-tag TAG]

G1 (the loop finishes and is intuitive) prints one row per bar: landed share of admitted sub units (at least 0.7),
seat waits (0), plan defect refusals (0), timeouts (at most 1), bare refusals (0), the one next action (PARKED while
RD2 is DEFERRED), and the intuitive rows read from the tree. Each row names the file it came from. Exit 0 only when
every row that is not PARKED is PASS; 1 on any FAIL; 2 on any NO-DATA (a missing ledger is never zero, never a pass).
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))

G1_BARS = {"landed_share": 0.7, "seat_waits": 0, "plan_defect_refusals": 0, "timeouts": 1, "bare_refusals": 0}
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
WBS = os.path.join("docs", "plan", "BROTHER-1.1.4-WBS.json")
RUNBOOK = os.path.join("docs", "plan", "specs", "RUNBOOK-1.1.4.md")
REFUSAL_WORDS = ("WITHHELD", "BLOCKED", "REFUSED")
DEFECT_RE = re.compile(r"scripts/check_all\.sh|\bbundle/|\bSYSTEM\.md\b|no file changed")
STATUS_WORDS = ("READY", "EXHAUSTED", "WITHHELD", "LANDED", "QUARANTINE", "STALLED", "PAUSED", "UNFUNDED",
                "WITHDRAWN", "CONFIG_WAIT", "DRAINED")


def plan_subs(plan: dict) -> List[str]:
    subs = []
    for u in plan.get("units", []) if isinstance(plan, dict) else []:
        for s in u.get("sub_units", []) if isinstance(u, dict) else []:
            sid = s if isinstance(s, str) else (s.get("id") if isinstance(s, dict) else None)
            if isinstance(sid, str) and sid:
                subs.append(sid)
    return subs


def tag_epoch(tag: str) -> Optional[int]:
    """The start time a RUN-TAG carries after its '@' ('<sub>-<HHMMSS>@<epoch>'), or None when it carries none."""
    _, at, tail = tag.strip().rpartition("@")
    return int(tail) if at and tail.isdigit() else None


def run_dirs(runs_dir: str, subs: List[str], run_tag: Optional[str] = None, since: Optional[int] = None) -> List[tuple]:
    """(sub, path) for every <sub>-<digits> directory of a plan sub unit; an unreadable runs dir raises OSError.
    THE ONE run selection every G1 counter reads (2026-10-14: run_tag reached the attempt count but not the refusal
    counts, so one reading mixed two selections). run_tag keeps a directory whose RUN-TAG equals it; since keeps one
    whose RUN-TAG start time is at or after it. A directory the filter cannot read (no RUN-TAG, no time) is left out."""
    wanted = set(subs)
    out = []
    for name in sorted(os.listdir(runs_dir)):
        sub, dash, stamp = name.rpartition("-")
        path = os.path.join(runs_dir, name)
        if not dash or sub not in wanted or not stamp.isdigit() or not os.path.isdir(path):
            continue
        if run_tag is not None or since is not None:
            try:
                with open(os.path.join(path, "RUN-TAG"), encoding="utf-8") as fh:
                    tag = fh.read().strip()
            except OSError:
                continue
            if run_tag is not None and tag != run_tag:
                continue
            if since is not None and (tag_epoch(tag) is None or tag_epoch(tag) < since):
                continue
        out.append((sub, path))
    return out


def loop_attempts(runs_dir: str, plan_subs: List[str], run_tag: Optional[str] = None, since: Optional[int] = None) -> Dict[str, object]:
    """Every results.json row under a run directory of a plan sub unit: counts of attempts, built (ok), seat waits
    (seat_wait true or the SEAT_WAIT text in error), timeouts (TimeoutExpired in error), plus each run's STATUS word;
    an unreadable file is one NO-DATA entry naming it, never a skipped row."""
    out = {"attempts": 0, "built": 0, "seat_waits": 0, "timeouts": 0, "status": {}, "no_data": [], "runs": 0,
           "admitted": []}
    try:
        dirs = run_dirs(runs_dir, plan_subs, run_tag, since)
    except OSError as exc:
        out["no_data"].append("%s: %s" % (runs_dir, exc))
        return out
    for sub, path in dirs:
        out["runs"] += 1
        if sub not in out["admitted"]:
            out["admitted"].append(sub)
        for dirpath, dirnames, files in os.walk(path):
            dirnames.sort()
            if "results.json" not in files:
                continue
            f = os.path.join(dirpath, "results.json")
            try:
                with open(f, encoding="utf-8") as fh:
                    rows = json.load(fh)
                if not isinstance(rows, list):
                    raise ValueError("not a list")
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                out["no_data"].append("%s: %s" % (f, exc))
                continue
            for row in rows:
                if not isinstance(row, dict):
                    out["no_data"].append("%s: a row is not a record" % f)
                    continue
                err = str(row.get("error") or "")
                out["attempts"] += 1
                out["built"] += 1 if row.get("ok") is True else 0
                out["seat_waits"] += 1 if (row.get("seat_wait") is True or "SEAT_WAIT" in err) else 0
                out["timeouts"] += 1 if "TimeoutExpired" in err else 0
        st = os.path.join(path, "STATUS")
        if os.path.exists(st):
            try:
                with open(st, encoding="utf-8") as fh:
                    word = (fh.readline().split() or ["(empty)"])[0]
            except (OSError, UnicodeDecodeError) as exc:
                out["no_data"].append("%s: %s" % (st, exc))
                continue
            word = word if word in STATUS_WORDS else "unknown:" + word
            out["status"][word] = out["status"].get(word, 0) + 1
    return out


def _refusal_lines(runs_dir: str, dirs: List[tuple]) -> List[str]:
    lines = []
    for _sub, path in dirs:
        for name in ("STATUS", "lane.log"):
            try:
                with open(os.path.join(path, name), encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        s = line.strip()
                        if s.startswith(REFUSAL_WORDS):
                            lines.append(s)
            except OSError:
                continue
    return lines


def plan_defect_refusals(runs_dir: str, plan_subs: Optional[List[str]] = None, run_tag: Optional[str] = None,
                         since: Optional[int] = None) -> List[str]:
    """The STATUS lines whose text names a landing refusal of a protected or generated path (check_all.sh, bundle/,
    SYSTEM.md) or 'no file changed': each is a refusal the lint should have made before the spend."""
    out = []
    for _sub, path in run_dirs(runs_dir, plan_subs if plan_subs is not None else _all_subs(runs_dir), run_tag, since):
        try:
            with open(os.path.join(path, "STATUS"), encoding="utf-8", errors="replace") as fh:
                text = fh.read().strip()
        except OSError:
            continue
        if DEFECT_RE.search(text):
            out.append(text)
    return out


def bare_refusals(runs_dir: str, plan_subs: Optional[List[str]] = None, run_tag: Optional[str] = None,
                  since: Optional[int] = None) -> List[str]:
    """Every WITHHELD, BLOCKED or REFUSED line in a run's lane.log and STATUS that does not carry both a cause and a
    remedy (the LD1 shape: '<WORD> ...: <cause>; remedy: <remedy>'); the line itself is returned."""
    dirs = run_dirs(runs_dir, plan_subs if plan_subs is not None else _all_subs(runs_dir), run_tag, since)
    out = []
    for line in _refusal_lines(runs_dir, dirs):
        head, colon, rest = line.partition(":")
        remedy = re.search(r"remedy:\s*\S", line)
        if not colon or not rest.strip() or not remedy:
            out.append(line)
    return out


def _all_subs(runs_dir):
    return [n.rpartition("-")[0] for n in os.listdir(runs_dir)]


def landed_share(plan: dict, admitted: List[str]) -> Optional[float]:
    """landed over admitted for the sub units the loop admitted (a run directory exists), landed read through
    plan_store.sub_landed on the unit's evidence text; None when admitted is empty (NO-DATA, never 1.0)."""
    import plan_store
    if not admitted:
        return None
    evidence = {}
    for u in plan.get("units", []):
        for s in u.get("sub_units", []):
            evidence[s if isinstance(s, str) else s.get("id")] = u.get("evidence") or ""
    landed = sum(1 for s in admitted if plan_store.sub_landed(s, evidence.get(s, "")))
    return landed / float(len(admitted))


def _verdict_max(value, bar):
    return "PASS" if value <= bar else "FAIL"


def _tree_row(name, test, root):
    path = os.path.join(root, test)
    if not os.path.exists(path):
        return (name, None, "NO-DATA", "%s is not in this tree" % test)
    try:
        p = subprocess.run([sys.executable, "-B", test], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return (name, None, "NO-DATA", "%s could not run: %s" % (test, exc))
    return (name, "exit %d" % p.returncode, "PASS" if p.returncode == 0 else "FAIL", test)


def g1_table(plan: dict, runs_dir: str, root: str = ROOT, run_tag: Optional[str] = None, run_tree: bool = True,
             since: Optional[int] = None) -> List[tuple]:
    """(bar, value, verdict, source) rows for G1_BARS; verdict PASS, FAIL or NO-DATA; the next action bar reads
    PARKED while RD2 is DEFERRED in the plan."""
    subs = plan_subs(plan)
    att = loop_attempts(runs_dir, subs, run_tag, since)
    rows = []
    nodata = att["no_data"]
    src = runs_dir
    share = landed_share(plan, att["admitted"])
    if nodata:
        why = "unreadable: %s" % "; ".join(nodata[:3])
        for bar in ("landed_share", "seat_waits", "timeouts"):
            rows.append((bar, None, "NO-DATA", why))
    else:
        if share is None:
            rows.append(("landed_share", None, "NO-DATA", "no plan sub unit was admitted (no run directory) | " + src))
        else:
            rows.append(("landed_share", round(share, 3), "PASS" if share >= G1_BARS["landed_share"] else "FAIL",
                         "%d admitted | %s" % (len(att["admitted"]), src)))
        if att["runs"] == 0:
            rows.append(("seat_waits", None, "NO-DATA", "no run directory | " + src))
            rows.append(("timeouts", None, "NO-DATA", "no run directory | " + src))
        else:
            rows.append(("seat_waits", att["seat_waits"], _verdict_max(att["seat_waits"], G1_BARS["seat_waits"]),
                         "%d attempts | %s" % (att["attempts"], src)))
            rows.append(("timeouts", att["timeouts"], _verdict_max(att["timeouts"], G1_BARS["timeouts"]),
                         "%d attempts | %s" % (att["attempts"], src)))
    if att["runs"] == 0:
        rows.append(("plan_defect_refusals", None, "NO-DATA", "no run directory | " + src))
        rows.append(("bare_refusals", None, "NO-DATA", "no run directory | " + src))
    else:
        d = plan_defect_refusals(runs_dir, subs, run_tag, since)
        b = bare_refusals(runs_dir, subs, run_tag, since)
        rows.append(("plan_defect_refusals", len(d), _verdict_max(len(d), G1_BARS["plan_defect_refusals"]),
                     (d[0][:120] if d else "STATUS files") + " | " + src))
        rows.append(("bare_refusals", len(b), _verdict_max(len(b), G1_BARS["bare_refusals"]),
                     (b[0][:120] if b else "STATUS and lane.log") + " | " + src))
    rd2 = [u for u in plan.get("units", []) if u.get("id") == "RD2"]
    if rd2 and rd2[0].get("state") == "DEFERRED":
        rows.append(("one_next_action", None, "PARKED", "RD2 is DEFERRED in the plan"))
    else:
        rows.append(("one_next_action", None, "NO-DATA", "RD2 is not DEFERRED and no reader of its bar is built"))
    if run_tree:
        rows.append(_tree_row("status_read_only", os.path.join("scripts", "test_brother_run_unfinished.py"), root))
        rows.append(_tree_row("refusals_name_remedy", os.path.join("scripts", "loop", "test_unit_runner_withheld_reason.py"), root))
        rows.append(one_start_command(root))
    return rows


def one_start_command(root: str) -> tuple:
    try:
        with open(os.path.join(root, RUNBOOK), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ("one_start_command", None, "NO-DATA", "%s is not readable" % RUNBOOK)
    launches = set(re.findall(r"scripts/loop/(start_run\.sh|loop_until\.sh|runner_pool\.py)", text))
    ok = launches == {"start_run.sh"}
    return ("one_start_command", ", ".join(sorted(launches)) or "none", "PASS" if ok else "FAIL", RUNBOOK)


G2_BARS = {"driver_runs": 2, "late_refusal_seconds": 600, "unnamed_refusals": 0, "rehearsal_seconds": 3600,
           "events_cost_a_run": 0}
EXPORT_GATE = "export-real-home"
FUTURE_SLACK_S = 3600
DRIVER_VERDICTS = ("PASS", "REUSED", "FAIL", "NO-DATA")
READY_VERDICTS = ("PASS", "FAIL", "NO-DATA")
EVENT_RE = re.compile(r"\bsleep\b|network timeout|network read|timed out|TimeoutExpired|could not resolve host"
                      r"|connection (reset|timed out)", re.I)
TREE_RE = re.compile(r"[0-9a-f]{40}")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _json_files(cut_dir: str, pattern: str) -> List[tuple]:
    """(path, parsed or None, reason) for every file under cut_dir whose name matches `pattern`, name order; an
    unlistable directory is one entry with reason; a missing directory is no entries."""
    if not os.path.isdir(cut_dir):
        return []
    try:
        names = sorted(n for n in os.listdir(cut_dir) if re.fullmatch(pattern, n))
    except OSError as exc:
        return [(cut_dir, None, str(exc))]
    out = []
    for name in names:
        path = os.path.join(cut_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                out.append((path, json.load(fh), ""))
        except (OSError, ValueError) as exc:
            out.append((path, None, "unreadable (%s)" % exc))
    return out


def _state_problem(data, version: str, now: float) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("phases"), dict):
        return "not a driver state"
    if not _num(data.get("started_at")) or data["started_at"] - now > FUTURE_SLACK_S:
        return "started_at is not a past time"
    if not isinstance(data.get("candidate_tree"), str):
        return "no candidate_tree"
    for name, ph in data["phases"].items():
        if not isinstance(ph, dict):
            return "phase %s is not a record" % name
        for key in ("seconds", "started"):
            if key in ph and not _num(ph[key]):
                return "phase %s has a %s that is not a number" % (name, key)
    return ""


def driver_states(cut_dir: str, version: str) -> List[dict]:
    """Every cv1.driver.1 state of `version` under cut_dir, oldest first by started_at, each the parsed state plus
    its "path"; a file that does not parse is one {"no_data": "<path>: <why>"} entry after them (the driver moves it
    aside on its own next run; this reader only reports). A state of another version is not read."""
    out, bad, now = [], [], time.time()
    for path, data, why in _json_files(cut_dir, r"driver-%s-[0-9a-f]{12}\.json" % re.escape(version)):
        why = why or _state_problem(data, version, now)
        if why:
            bad.append({"no_data": "%s: %s" % (path, why), "path": path})
        elif data.get("version") == version:
            data["path"] = path
            out.append(data)
    out.sort(key=lambda d: d["started_at"])
    return out + bad


def ready_records(cut_dir: str, version: str) -> List[dict]:
    """Every cv1.ready.1 record of `version` under cut_dir, oldest first by epoch; an unparseable or malformed one
    is one {"no_data": ...} entry after them. None at all is an empty list (the table names it NO-DATA)."""
    out, bad = [], []
    for path, data, why in _json_files(cut_dir, r"cut-ready-%s-[^/]*\.json" % re.escape(version)):
        if not why:
            if not isinstance(data, dict) or data.get("schema") != "cv1.ready.1":
                why = "not a cv1.ready.1 record"
            elif data.get("version") != version:
                continue
            elif not isinstance(data.get("candidate_tree"), str) or not TREE_RE.fullmatch(data["candidate_tree"]):
                why = "candidate_tree is not 40 hex"
            elif data.get("verdict") not in READY_VERDICTS or not _num(data.get("epoch")):
                why = "verdict or epoch is not valid"
            elif not isinstance(data.get("rows"), list) or any(
                    not isinstance(r, dict) or r.get("verdict") not in READY_VERDICTS for r in data["rows"]):
                why = "a row has no known verdict"
        if why:
            bad.append({"no_data": "%s: %s" % (path, why), "path": path})
        else:
            data["path"] = path
            out.append(data)
    out.sort(key=lambda d: d["epoch"])
    return out + bad


def refusal_rows(states: List[dict], ready: List[dict]) -> List[dict]:
    """One row per driver phase whose verdict is FAIL: "seconds" into the run when it surfaced, "event" (its detail
    names a Sleep line or a network timeout), "exempt" (the export gate measurement), and "named": True when a
    cut-ready record of the same tree has a FAIL row holding the phase's detail text, False when records of that tree
    exist and none does, None when no record of that tree exists (NO-DATA, never named)."""
    out = []
    for st in states:
        if "no_data" in st:
            continue
        tree = st["candidate_tree"]
        records = [r for r in ready if "no_data" not in r and r["candidate_tree"] == tree]
        for name, ph in st["phases"].items():
            if ph.get("verdict") != "FAIL":
                continue
            detail = _text(ph.get("detail"))
            named = None
            if records:
                named = bool(detail) and any(
                    detail in _text(row.get("detail")) for r in records for row in r["rows"] if row["verdict"] == "FAIL")
            out.append({"path": st["path"], "phase": name, "tree": tree, "detail": detail,
                        "seconds": ph.get("started", st["started_at"]) + ph.get("seconds", 0) - st["started_at"],
                        "event": bool(EVENT_RE.search(detail)), "exempt": EXPORT_GATE in detail, "named": named})
    return out


def rehearsal_seconds(states: List[dict]) -> Dict[str, float]:
    """{candidate tree: seconds} of the rehearsal phase of the newest state whose rehearsal verdict is PASS or READY;
    empty when none (a REUSED rehearsal measured nothing)."""
    for st in reversed([s for s in states if "no_data" not in s]):
        ph = st["phases"].get("rehearsal")
        if isinstance(ph, dict) and ph.get("verdict") in ("PASS", "READY") and "seconds" in ph:
            return {st["candidate_tree"]: float(ph["seconds"])}
    return {}


def _day(epoch: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def g2_table(cut_dir: str, version: str) -> List[tuple]:
    """(bar, value, verdict, source) rows for G2_BARS, every figure beside the file it came from; verdict PASS, FAIL
    or NO-DATA. No readable state, or a state that does not parse or names a verdict outside the four, is NO-DATA."""
    states = driver_states(cut_dir, version)
    ready = ready_records(cut_dir, version)
    good = [s for s in states if "no_data" not in s]
    problems = [s["no_data"] for s in states if "no_data" in s]
    for s in good:
        for name, ph in s["phases"].items():
            if ph.get("verdict", "") not in DRIVER_VERDICTS + ("READY", ""):
                problems.append("%s: phase %s has the verdict %r" % (s["path"], name, ph.get("verdict")))
    src = "%s | driver-%s-*.json" % (cut_dir, version)
    rows = []
    if problems:
        why = "unreadable: %s" % "; ".join(problems[:3])
    else:
        why = "" if good else "no driver state for %s | %s" % (version, src)
    refusals = refusal_rows(good, ready)
    real = [r for r in refusals if not r["event"]]
    events = [r for r in refusals if r["event"]]
    for bar in ("driver_runs", "late_refusal_seconds", "unnamed_refusals", "events_cost_a_run"):
        if why:
            rows.append((bar, None, "NO-DATA", why))
            continue
        if bar == "driver_runs":
            day = _day(good[-1]["started_at"])
            n = sum(1 for s in good if _day(s["started_at"]) == day)
            flight = [s for s in good[-1:] if any(not p.get("verdict") for p in s["phases"].values())]
            rows.append((bar, n, _verdict_max(n, G2_BARS[bar]), "%d on %s, %d total%s | %s" % (
                n, day, len(good), ", in flight" if flight else "", src)))
        elif bar == "late_refusal_seconds":
            late = [r for r in real if not r["exempt"]]
            top = max(late, key=lambda r: r["seconds"]) if late else None
            v = int(round(top["seconds"])) if top else 0
            rows.append((bar, v, _verdict_max(v, G2_BARS[bar]), "%s phase %s | %s" % (
                os.path.basename(top["path"]), top["phase"], src) if top else "no late refusal | " + src))
        elif bar == "events_cost_a_run":
            rows.append((bar, len(events), _verdict_max(len(events), G2_BARS[bar]), "%s | %s" % (
                os.path.basename(events[0]["path"]) + " phase " + events[0]["phase"] if events else "no event FAIL", src)))
        else:
            unreadable = [r["no_data"] for r in ready if "no_data" in r]
            missing = [r for r in real if r["named"] is None]
            unnamed = [r for r in real if r["named"] is False]
            if real and (unreadable or missing):
                rows.append((bar, None, "NO-DATA", "no readable cut-ready record for the tree of %s phase %s | %s" % (
                    os.path.basename((missing or real)[0]["path"]), (missing or real)[0]["phase"],
                    (unreadable or [src])[0])))
            else:
                rows.append((bar, len(unnamed), _verdict_max(len(unnamed), G2_BARS[bar]), "%d refusals, %d known | %s" % (
                    len(real), len(real) - len(unnamed), (os.path.basename(unnamed[0]["path"]) + " phase " +
                                                          unnamed[0]["phase"]) if unnamed else "cut-ready-%s-*.json" % version)))
    reh = rehearsal_seconds(states)
    if reh:
        tree, secs = list(reh.items())[0]
        rows.append(("rehearsal_seconds", int(round(secs)), _verdict_max(secs, G2_BARS["rehearsal_seconds"]),
                     "tree %s | %s" % (tree[:12], src)))
    else:
        rows.append(("rehearsal_seconds", None, "NO-DATA", "no PASS or READY rehearsal phase | " + src))
    return rows


def exit_code(rows) -> int:
    kinds = [r[2] for r in rows if r[2] != "PARKED"]
    if not kinds or "NO-DATA" in kinds:
        return 2
    return 1 if "FAIL" in kinds else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="donecheck_114.py")
    ap.add_argument("--goal", required=True, choices=("G1", "G2"))
    ap.add_argument("--plan", default=os.path.join(ROOT, WBS))
    ap.add_argument("--runs", default=RUNS)
    ap.add_argument("--run-tag")
    ap.add_argument("--since", type=int, help="count only attempts whose RUN-TAG start time (epoch seconds) is at or after this")
    ap.add_argument("--version", default="1.1.4")
    ap.add_argument("--cut", default=os.path.expanduser("~/.claude/evidence/cut"))
    a = ap.parse_args(argv)
    if a.goal == "G2":
        rows = g2_table(a.cut, a.version)
        return _print(a, rows)
    try:
        with open(a.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
        if not isinstance(plan, dict) or not isinstance(plan.get("units"), list):
            raise ValueError("the units are not a list")
    except (OSError, ValueError) as exc:
        print("NO-DATA: the plan %s is unreadable (%s)" % (a.plan, exc))
        return 2
    return _print(a, g1_table(plan, a.runs, run_tag=a.run_tag, since=a.since))


def _print(a, rows) -> int:
    where = ("runs %s | tag %s | since %s" % (a.runs, a.run_tag or "all", getattr(a, "since", None) or "all")) if a.goal == "G1" else ("cut %s | version %s" % (a.cut, a.version))
    print("DONECHECK %s | read %s | %s" % (a.goal, time.strftime("%Y-%m-%dT%H%M%S"), where))
    for bar, value, verdict, source in rows:
        print("%-7s %-22s %-10s %s" % (verdict, bar, "-" if value is None else value, source))
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())
