#!/usr/bin/env python3
"""cut_ready.py: every premise a cut can refuse on, one collect all table before the driver.

FR0.a. Reads the cheap premises, the preflight rows and the hub gate in one
pass over injected seams (runner, free, run_all, gate), never stopping at a
refusal; prints one table and writes exactly one
cut-ready-<version>-<tree12>-<epoch>.json record at the end. Every child
process goes through the injected runner; no runner means NO-DATA, never a
pass.

Python 3.9, standard library only. No em or en dashes.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

Row = Dict[str, object]
READY_SCHEMA = "cv1.ready.1"
PASS = "PASS"
FAIL = "FAIL"
NODATA = "NO-DATA"
EXIT_FOR = {"PASS": 0, "FAIL": 1, "NO-DATA": 2}
HUB_TIMEOUT_S = 1800
HUB_ROW = r"^(PASS|FAIL|NO-DATA)\s+exit\s+(\d+)\s+(\S+)\s+(\d+)s(\s|$)"
GATE_SUMMARY = re.compile(r"^pass\s+(\d+)\s+fail\s+(\d+)\s+no-data\s+(\d+)\s*$")
VERDICTS = (PASS, FAIL, NODATA)
ROW_KEYS = ("group", "name", "verdict", "seconds", "detail", "log_path", "log_sha256")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOOP_DIR = os.path.join(ROOT, "scripts", "loop")
if _LOOP_DIR not in sys.path:
    sys.path.append(_LOOP_DIR)
try:
    import power_rule  # noqa: E402  the one power rule, shared with the loop launcher and the driver
except ImportError:   # a missing power rule refuses the source and the lid, it never stands in as AC
    power_rule = None
HORIZON_DEFAULT = (6.0, "floor constant, no measurement read")
FLOOR_HOURS = 6.0
CAP_HOURS = 18.0
NO_STATE_MESSAGE = "floor 6 h, no driver state with a verdict"
VERDICT_WORDS = ("PASS", "FAIL", "NO-DATA", "REUSED")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _need_str(value, what):
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % what)
    return value


def _need_int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % what)
    return value


def _need_num(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a finite number" % what)
    if not math.isfinite(value):
        raise ValueError("%s must be a finite number" % what)
    return value


def _need_verdict(value, what):
    if not isinstance(value, str) or value not in VERDICTS:
        raise ValueError("%s must be one of %r" % (what, list(VERDICTS)))
    return value


def _need_optional_callable(value, what):
    if value is None:
        return value
    if not callable(value):
        raise ValueError("%s must be callable or None" % what)
    return value


def _need_horizon(value, what="horizon"):
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("%s must be a (hours, source) pair" % what)
    _need_num(value[0], "%s hours" % what)
    _need_str(value[1], "%s source" % what)
    return value


def state_seconds(state):
    """Sum the seconds of every phase that carries a verdict word, or None."""
    if not isinstance(state, dict):
        raise ValueError("state must be a dict")
    phases = state.get("phases")
    if not isinstance(phases, dict):
        return None
    total = 0.0
    any_verdict = False
    for phase in phases.values():
        if not isinstance(phase, dict):
            continue
        verdict = phase.get("verdict")
        if verdict not in VERDICT_WORDS:
            continue
        any_verdict = True
        seconds = phase.get("seconds")
        if isinstance(seconds, bool):
            continue
        if not isinstance(seconds, (int, float)):
            continue
        if not math.isfinite(seconds):
            continue
        if seconds < 0:
            continue
        total += float(seconds)
    if not any_verdict:
        return None
    return total


def measured_horizon(directory=""):
    """Newest driver record with a verdict gives the horizon, floor 6 h, cap 18 h."""
    if not isinstance(directory, str):
        raise ValueError("directory must be a string")
    candidates = []
    if directory and os.path.isdir(directory):
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        for name in names:
            if not (name.startswith("driver-") and name.endswith(".json")):
                continue
            path = os.path.join(directory, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            candidates.append((mtime, path, name))
    candidates.sort(key=lambda item: item[0], reverse=True)
    skipped = []
    for _mtime, path, name in candidates:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            skipped.append(name)
            continue
        try:
            record = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            skipped.append(name)
            continue
        if not isinstance(record, dict):
            skipped.append(name)
            continue
        if record.get("schema") != "cv1.driver.1":
            skipped.append(name)
            continue
        raw_state = record.get("state")
        if not isinstance(raw_state, dict):
            skipped.append(name)
            continue
        seconds = state_seconds(raw_state)
        if seconds is None:
            skipped.append(name)
            continue
        hours = seconds / 3600.0
        if hours < FLOOR_HOURS:
            result_hours = FLOOR_HOURS
            source = "floor %g h (measured %.1f h in %s is below the floor)" % (
                FLOOR_HOURS, hours, name)
        elif hours > CAP_HOURS:
            result_hours = CAP_HOURS
            source = "capped %g h from %s (measured %.1f h)" % (CAP_HOURS, name, hours)
        else:
            result_hours = hours
            source = "measured %.1f h from %s" % (hours, name)
        if skipped:
            source = source + "; skipped " + ", ".join(skipped)
        return (result_hours, source)
    return (FLOOR_HOURS, NO_STATE_MESSAGE)


def _need_row(row, what="row"):
    if not isinstance(row, dict):
        raise ValueError("%s must be a row dict" % what)
    for key in ROW_KEYS:
        if key not in row:
            raise ValueError("%s lacks key %r" % (what, key))
    _need_str(row["group"], "%s group" % what)
    _need_str(row["name"], "%s name" % what)
    _need_verdict(row["verdict"], "%s verdict" % what)
    _need_num(row["seconds"], "%s seconds" % what)
    _need_str(row["detail"], "%s detail" % what)
    _need_str(row["log_path"], "%s log_path" % what)
    _need_str(row["log_sha256"], "%s log_sha256" % what)
    return row


def _need_rows(rows, what="rows"):
    if not isinstance(rows, list):
        raise ValueError("%s must be a list of rows" % what)
    for row in rows:
        _need_row(row, what)
    return rows


def _invoke(runner, argv, cwd, timeout=None, env=None):
    try:
        return runner(list(argv), cwd, timeout=timeout, env=env)
    except Exception:
        return None


def _code_of(result):
    try:
        value = result.returncode
    except AttributeError:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _text_of(result):
    try:
        text = result.stdout
    except AttributeError:
        return ""
    if isinstance(text, str):
        return text
    return ""


def _scratch_root():
    base = os.environ.get("TMPDIR", "") or tempfile.gettempdir()
    return base


def _default_free(path):
    target = path
    while target and not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent == target:
            break
        target = parent
    usage = shutil.disk_usage(target or "/")
    return usage.free / (1024.0 ** 3)


def _remove_worktree(path, root, runner):
    if runner is not None:
        try:
            _invoke(runner, ["git", "worktree", "remove", "--force", path], cwd=root, timeout=120)
            _invoke(runner, ["git", "worktree", "prune"], cwd=root, timeout=120)
        except Exception:
            pass
    shutil.rmtree(path, ignore_errors=True)


def make_row(group, name, verdict, seconds, detail, log_path="", log_sha256=""):
    _need_str(group, "group")
    _need_str(name, "name")
    _need_verdict(verdict, "verdict")
    _need_num(seconds, "seconds")
    _need_str(detail, "detail")
    _need_str(log_path, "log_path")
    _need_str(log_sha256, "log_sha256")
    return {
        "group": group,
        "name": name,
        "verdict": verdict,
        "seconds": float(seconds),
        "detail": detail,
        "log_path": log_path,
        "log_sha256": log_sha256,
    }


def overall(rows):
    _need_rows(rows)
    verdicts = [row["verdict"] for row in rows]
    if any(v == FAIL for v in verdicts):
        return FAIL
    if not verdicts or any(v == NODATA for v in verdicts):
        return NODATA
    return PASS


def counts_line(rows):
    _need_rows(rows)
    passed = sum(1 for row in rows if row["verdict"] == PASS)
    failed = sum(1 for row in rows if row["verdict"] == FAIL)
    nodata = sum(1 for row in rows if row["verdict"] == NODATA)
    return "pass %d   fail %d   no-data %d" % (passed, failed, nodata)


def disk_row(path, floor_gib=15, free=None):
    _need_str(path, "path")
    _need_num(floor_gib, "floor_gib")
    _need_optional_callable(free, "free")
    reader = free if free is not None else _default_free
    try:
        value = reader(path)
    except Exception:
        return make_row("premise", "disk-floor", NODATA, 0.0, "the disk reading raised")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return make_row("premise", "disk-floor", NODATA, 0.0, "the disk reading is not a finite number")
    if value < floor_gib:
        return make_row("premise", "disk-floor", FAIL, 0.0,
                        "free %.2f GiB under the floor of %.2f GiB; run python3 scripts/worktree_prune.py --dry"
                        % (float(value), float(floor_gib)))
    return make_row("premise", "disk-floor", PASS, 0.0,
                    "free %.2f GiB at or above the floor of %.2f GiB" % (float(value), float(floor_gib)))


def tree_row(root, runner=None):
    _need_str(root, "root")
    _need_optional_callable(runner, "runner")
    if runner is None:
        return make_row("premise", "tree-clean", NODATA, 0.0, "no runner available to read git status")
    result = _invoke(runner, ["git", "status", "--porcelain"], cwd=root, timeout=120)
    if result is None:
        return make_row("premise", "tree-clean", NODATA, 0.0, "git status could not run")
    code = _code_of(result)
    if code != 0:
        return make_row("premise", "tree-clean", NODATA, 0.0, "git status exited %s" % (code,))
    text = _text_of(result)
    dirty = [line for line in text.splitlines() if line.strip()]
    if not dirty:
        return make_row("premise", "tree-clean", PASS, 0.0, "git status --porcelain is empty")
    return make_row("premise", "tree-clean", FAIL, 0.0,
                    "git status --porcelain names %d changed path(s)" % len(dirty))


LID_CMD = ["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"]
LID_RE = re.compile(r'"AppleClamshellState"\s*=\s*(Yes|No)\b')
#: FR0.f: the four re-runnable, side effect free phases a sleep inside the window corrupts.
SLEEP_SENSITIVE = ("ready", "freeze", "preflight", "rehearsal")
SLEEP_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) ([+-]\d{4}) Sleep\s+Entering Sleep state")


def _default_lid_reader(cmd):
    """The real ioreg reading through the power rule's one process seam, or None."""
    if power_rule is None:
        return None
    return power_rule.command_stdout(cmd)


def lid_state(run=None):
    """'closed' for AppleClamshellState = Yes, 'open' for = No, else 'unreadable'.

    A missing command, a desktop with no such key, a timeout and a runner that
    is not callable all answer 'unreadable', never 'open': only a reading that
    actually says No lets a cut start.
    """
    if run is None:
        run = _default_lid_reader
    if not callable(run):
        return "unreadable"
    try:
        text = run(LID_CMD)
    except Exception:
        return "unreadable"
    if not isinstance(text, str):
        return "unreadable"
    match = LID_RE.search(text)
    if match is None:
        return "unreadable"
    return "closed" if match.group(1).lower() == "yes" else "open"


def _state_reader(reader, default):
    """The state word an injected reader answers, or a word that refuses."""
    if reader is None:
        return default()
    if not callable(reader):
        return "unreadable"
    try:
        got = reader()
    except Exception:
        return "unreadable"
    return got if isinstance(got, str) else "unreadable"


def _default_source():
    if power_rule is None:
        return "unreadable"
    try:
        got = power_rule.power_source()
    except Exception:
        return "unreadable"
    return got if isinstance(got, str) else "unreadable"


def _default_lid():
    return lid_state()


def power_rows(source=None, lid=None):
    """Two premise rows: power-source (only AC is known to last) and lid (only an open lid is known to last)."""
    value = _state_reader(source, _default_source)
    if value == "ac":
        power = make_row("premise", "power-source", PASS, 0.0, "the machine draws from AC Power")
    elif value == "no-data":
        power = make_row("premise", "power-source", NODATA, 0.0,
                         "this host has no pmset reading, so the power source is NO-DATA")
    elif value == "battery":
        power = make_row("premise", "power-source", FAIL, 0.0,
                         "the machine draws from battery: plug in the charger, only AC is known to last")
    else:
        power = make_row("premise", "power-source", FAIL, 0.0,
                         "the power source is unreadable: plug in the charger, only AC is known to last")
    state = _state_reader(lid, _default_lid)
    if state == "open":
        lid_row = make_row("premise", "lid", PASS, 0.0, "the lid is open")
    elif state == "closed":
        lid_row = make_row("premise", "lid", FAIL, 0.0,
                           "the lid is closed and a clamshell sleep ends the run: open the lid")
    else:
        lid_row = make_row("premise", "lid", NODATA, 0.0,
                           "the lid state could not be read (a desktop has no lid): NO-DATA")
    return [power, lid_row]


def sleep_times(log_text):
    """Epoch seconds of every pmset Sleep line, in file order.

    Wake, DarkWake, Wake Requests and any line that does not parse are ignored,
    never guessed. A log_text that is not a str refuses with ValueError.
    """
    if not isinstance(log_text, str):
        raise ValueError("log_text must be a string")
    found = []
    for line in log_text.splitlines():
        match = SLEEP_LINE_RE.match(line)
        if match is None:
            continue
        try:
            when = datetime.datetime.strptime(
                "%s %s %s" % (match.group(1), match.group(2), match.group(3)),
                "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            continue
        try:
            found.append(when.timestamp())
        except (OSError, OverflowError, ValueError):
            continue
    return found


def first_sleep_in(log_text, start, end):
    """The first sleep time t with start <= t <= end, or None. Both edges are inclusive."""
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
        raise ValueError("start must be a finite number")
    if isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end):
        raise ValueError("end must be a finite number")
    for when in sleep_times(log_text):
        if start <= when <= end:
            return when
    return None


def _doc_row(root, name, script, runner):
    full = os.path.join(root, script)
    if not os.path.isfile(full):
        return make_row("premise", name, NODATA, 0.0, "%s is not a regular file" % script)
    if runner is None:
        return make_row("premise", name, NODATA, 0.0, "no runner available to run %s" % script)
    started = time.time()
    result = _invoke(runner, ["python3", script, "--check"], cwd=root, timeout=900)
    seconds = time.time() - started
    if result is None:
        return make_row("premise", name, NODATA, seconds, "the child for %s could not run" % script)
    code = _code_of(result)
    text = _text_of(result)
    lines = [line for line in text.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    if code == 0:
        return make_row("premise", name, PASS, seconds, last or "exit 0")
    if code == 1:
        return make_row("premise", name, FAIL, seconds, last or "exit 1")
    return make_row("premise", name, NODATA, seconds, "exit %s: %s" % (code, last))


def doc_rows(root, runner=None):
    _need_str(root, "root")
    _need_optional_callable(runner, "runner")
    return [
        _doc_row(root, "system-doc", os.path.join("scripts", "system_doc.py"), runner),
        _doc_row(root, "bundle-runtime", os.path.join("scripts", "bundle_runtime.py"), runner),
    ]


def preflight_rows(root, version, horizon_hours, run_all=None, horizon_source=""):
    _need_str(root, "root")
    _need_str(version, "version")
    _need_num(horizon_hours, "horizon_hours")
    _need_optional_callable(run_all, "run_all")
    _need_str(horizon_source, "horizon_source")
    if run_all is None:
        return [make_row("preflight", "preflight", NODATA, 0.0,
                         "cut_preflight.run_all was not injected")]
    try:
        result = run_all(root, version, horizon_hours=horizon_hours, virgin=False)
    except ValueError:
        return [make_row("preflight", "preflight", NODATA, 0.0, "run_all refused its input")]
    except Exception:
        return [make_row("preflight", "preflight", NODATA, 0.0, "run_all could not run")]
    if not isinstance(result, list):
        return [make_row("preflight", "preflight", NODATA, 0.0, "run_all returned no row list")]
    rows = []
    for item in result:
        if not isinstance(item, tuple) or len(item) < 3:
            continue
        word = item[0]
        if word == "OK":
            verdict = PASS
        elif word == "REFUSED":
            verdict = FAIL
        else:
            verdict = NODATA
        name = item[1] if isinstance(item[1], str) else str(item[1])
        detail = item[2] if isinstance(item[2], str) else str(item[2])
        if name == "pack-clock" and horizon_source:
            detail = "horizon %g h (%s): %s" % (horizon_hours, horizon_source, detail)
        rows.append(make_row("preflight", name, verdict, 0.0, detail))
    if len(rows) < 2:
        rows.append(make_row("preflight", "preflight-rest", NODATA, 0.0,
                             "run_all returned %d row(s): a refusal there ends the run" % len(rows)))
    return rows


def parse_hub_rows(text, code):
    _need_str(text, "text")
    _need_int(code, "code")
    if code == 124 or code == 127:
        return [make_row("hub", "hub-gate", NODATA, 0.0,
                         "the hub gate did not complete: exit %d" % code)]
    rows = []
    summary = None
    for line in text.splitlines():
        match = re.match(HUB_ROW, line)
        if match:
            rows.append(make_row("hub", "hub:" + match.group(3), match.group(1),
                                 float(match.group(4)), line.strip()))
            continue
        counts = GATE_SUMMARY.match(line)
        if counts:
            summary = tuple(int(x) for x in counts.groups())
    if not rows or summary is None:
        rows.append(make_row("hub", "hub-gate-summary", NODATA, 0.0,
                             "the gate printed no rows or no summary line"))
    else:
        got = (sum(1 for r in rows if r["verdict"] == PASS),
               sum(1 for r in rows if r["verdict"] == FAIL),
               sum(1 for r in rows if r["verdict"] == NODATA))
        if got != summary:
            rows.append(make_row("hub", "hub-gate-summary", NODATA, 0.0,
                                 "gate rows %r do not match the summary %r" % (got, summary)))
    if code != 0 and not any(r["verdict"] == FAIL for r in rows):
        rows.append(make_row("hub", "hub-gate-exit", FAIL, 0.0,
                             "the hub gate exited %d with no FAIL row" % code))
    return rows


def _save_hub_log(directory, sha, epoch, text):
    folder = directory or _scratch_root()
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return "", ""
    name = "cut-ready-hub-%s-%d.log" % (sha[:12], epoch)
    path = os.path.join(folder, name)
    tmp = path + ".tmp"
    data = text.encode("utf-8", "replace")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError:
        return "", ""
    return path, hashlib.sha256(data).hexdigest()


def hub_rows(root, sha, runner=None, directory=""):
    _need_str(root, "root")
    _need_str(sha, "sha")
    if not SHA_RE.match(sha):
        raise ValueError("sha must be a hex commit id")
    _need_optional_callable(runner, "runner")
    _need_str(directory, "directory")
    if runner is None:
        return [make_row("hub", "hub-gate", NODATA, 0.0,
                         "no runner available to launch the hub gate")]
    scratch = _scratch_root()
    base = os.path.join(scratch, "cut-ready-wt")
    try:
        os.makedirs(base, exist_ok=True)
        worktree = tempfile.mkdtemp(prefix="wt-", dir=base)
    except OSError:
        return [make_row("hub", "hub-gate", NODATA, 0.0,
                         "no scratch worktree folder could be made")]
    epoch = int(time.time())
    try:
        added = _invoke(runner, ["git", "worktree", "add", "--detach", worktree, sha],
                        cwd=root, timeout=300)
        if added is None or _code_of(added) != 0:
            return [make_row("hub", "hub-gate", NODATA, 0.0, "git worktree add failed")]
        env = dict(os.environ)
        tmpdir = os.path.join(scratch, "cut-ready-tmp")
        try:
            os.makedirs(tmpdir, exist_ok=True)
        except OSError:
            tmpdir = scratch
        env["TMPDIR"] = tmpdir
        # required_fast.sh appends every row to the gate ledger and refuses a record with no 32 hex CUT_ID (a cut driver
        # normally sets it). This run is no cut: a fresh id, and the ledger it writes lives and dies inside the scratch worktree.
        env["CUT_ID"] = uuid.uuid4().hex
        result = _invoke(runner, ["sh", "scripts/required_fast.sh"], cwd=worktree,
                         timeout=HUB_TIMEOUT_S, env=env)
        if result is None:
            return [make_row("hub", "hub-gate", NODATA, 0.0,
                             "the hub gate child could not run")]
        text = _text_of(result)
        code = _code_of(result)
        log_path, log_sha256 = _save_hub_log(directory, sha, epoch, text)
        rows = parse_hub_rows(text, code)
        for row in rows:
            row["log_path"] = log_path
            row["log_sha256"] = log_sha256
        return rows
    finally:
        _remove_worktree(worktree, root, runner)


def _rev_parse(runner, root, rev):
    if runner is None:
        return None
    result = _invoke(runner, ["git", "rev-parse", rev], cwd=root, timeout=120)
    if result is None:
        return None
    if _code_of(result) != 0:
        return None
    text = _text_of(result).strip()
    return text or None


def collect(root, version, runner=None, free=None, run_all=None, gate=None,
            horizon=None, directory=""):
    _need_str(root, "root")
    _need_str(version, "version")
    _need_optional_callable(runner, "runner")
    _need_optional_callable(free, "free")
    _need_optional_callable(run_all, "run_all")
    _need_optional_callable(gate, "gate")
    _need_str(directory, "directory")
    if horizon is None:
        horizon = HORIZON_DEFAULT
    _need_horizon(horizon)
    head = _rev_parse(runner, root, "HEAD")
    tree = _rev_parse(runner, root, "HEAD^{tree}")
    if head is None or tree is None:
        return [make_row("candidate", "candidate", NODATA, 0.0,
                         "git rev-parse HEAD or HEAD^{tree} is unreadable")], "", ""
    rows = []
    rows.append(tree_row(root, runner=runner))
    rows.append(disk_row(_scratch_root(), free=free))
    rows.extend(power_rows())
    rows.extend(doc_rows(root, runner=runner))
    rows.extend(preflight_rows(root, version, horizon[0], run_all=run_all,
                               horizon_source=horizon[1]))
    if gate is not None:
        rows.extend(gate(root, head, runner=runner, directory=directory))
    else:
        rows.extend(hub_rows(root, head, runner=runner, directory=directory))
    again = _rev_parse(runner, root, "HEAD^{tree}")
    if again is not None and again != tree:
        rows.append(make_row("premise", "tree-stable", FAIL, 0.0,
                             "HEAD^{tree} moved during the run: %s to %s" % (tree, again)))
    return rows, head, tree


def format_table(rows):
    _need_rows(rows)
    lines = []
    for row in rows:
        if row["verdict"] == PASS:
            continue
        lines.append("%-8s %-10s %-24s %8.2f  %s" % (
            row["verdict"], row["group"], row["name"], float(row["seconds"]), row["detail"]))
    seen = []
    for row in rows:
        if row["group"] not in seen:
            seen.append(row["group"])
    for group in seen:
        in_group = [row for row in rows if row["group"] == group]
        passed = sum(1 for row in in_group if row["verdict"] == PASS)
        lines.append("PASS %d of %d %s" % (passed, len(in_group), group))
    lines.append(counts_line(rows))
    lines.append(overall(rows))
    return "\n".join(lines)


def write_record(directory, version, sha, tree, rows, epoch, horizon):
    _need_str(directory, "directory")
    if not directory:
        raise ValueError("directory must be a non-empty string")
    _need_str(version, "version")
    _need_str(sha, "sha")
    _need_str(tree, "tree")
    _need_rows(rows)
    _need_int(epoch, "epoch")
    _need_horizon(horizon)
    record = {
        "schema": READY_SCHEMA,
        "version": version,
        "candidate_sha": sha,
        "candidate_tree": tree,
        "verdict": overall(rows),
        "epoch": int(epoch),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch))),
        "summary": counts_line(rows),
        "rows": [dict(row) for row in rows],
        "horizon_hours": float(horizon[0]),
        "horizon_source": horizon[1],
    }
    os.makedirs(directory, exist_ok=True)
    tree12 = tree[:12] if len(tree) >= 12 else tree
    path = os.path.join(directory, "cut-ready-%s-%s-%d.json" % (version, tree12, int(epoch)))
    tmp = os.path.join(directory, ".cut-ready-%d-%d.tmp" % (os.getpid(), int(epoch)))
    payload = json.dumps(record, indent=2, sort_keys=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def real_runner(argv, cwd, timeout=None, env=None):
    """The runner a command line run uses: cut_rehearsal._run_tree, which runs each child in its own session and
    kills its whole process tree on a timeout (CF1.b). Before 2026-10-14 main() had no default, so every command line
    run answered NO-DATA on the candidate row and checked nothing; only injected test runners ever ran."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cut_rehearsal
    return cut_rehearsal._run_tree(list(argv), cwd, env, timeout)


def real_run_all(root, version, horizon_hours=HORIZON_DEFAULT[0], virgin=False):
    """The preflight a command line run uses: cut_preflight.run_all, the cheap rows only (the minutes long export rows
    belong to the preflight phase). Before 2026-10-15 main() never supplied it, so the preflight row read NO-DATA."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cut_preflight
    return cut_preflight.run_all(root, version, horizon_hours=horizon_hours, virgin=virgin)


def default_directory():
    """Where the record lives when --directory is absent: cut_rehearsal.evidence_dir(), the folder cut_driver,
    rc_handover and every ready_record reader look in. It used to be the system temp folder, which no reader reads."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cut_rehearsal
    return cut_rehearsal.evidence_dir()


def main(argv=None, root=None, runner=None):
    if argv is None:
        argv_list = None
    else:
        if isinstance(argv, bool) or not isinstance(argv, list):
            raise SystemExit(2)
        for item in argv:
            if not isinstance(item, str):
                raise SystemExit(2)
        argv_list = list(argv)
    if root is None:
        root = ROOT
    if not isinstance(root, str) or not root:
        raise SystemExit(2)
    if runner is not None and not callable(runner):
        raise SystemExit(2)
    runner = runner if runner is not None else real_runner
    parser = argparse.ArgumentParser(description="cut_ready")
    parser.add_argument("--version", required=True, metavar="X.Y.Z")
    parser.add_argument("--root", default="")
    parser.add_argument("--directory", default="")
    args = parser.parse_args(argv_list)
    if not VERSION_RE.match(args.version):
        sys.stderr.write("NO-DATA: --version must be X.Y.Z\n")
        return 2
    run_root = args.root or root
    directory = args.directory or default_directory()
    horizon = measured_horizon(directory)
    rows, head, tree = collect(run_root, args.version, runner=runner, directory=directory,
                               run_all=real_run_all, horizon=horizon)
    sys.stdout.write(format_table(rows) + "\n")
    if not head or not tree:
        sys.stderr.write("NO-DATA: no record written, the candidate commit or tree is unreadable\n")
        return 2
    folder = directory
    try:
        path = write_record(folder, args.version, head, tree, rows, int(time.time()), horizon)
    except (OSError, ValueError):
        sys.stderr.write("NO-DATA: the cut-ready record could not be written\n")
        return 2
    sys.stdout.write("record %s\n" % path)
    return EXIT_FOR[overall(rows)]


if __name__ == "__main__":
    sys.exit(main())
