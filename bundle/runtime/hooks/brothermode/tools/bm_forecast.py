#!/usr/bin/env python3
"""Record time forecasts and actuals, and compute a correction factor from
real paired history, mechanically.

WHY THIS EXISTS
  On 2026-08-11 this project's overnight plan gave four time estimates.
  Three were judged and every one of them was wrong by four to five times
  in the SAME direction (the work finished far earlier than forecast). The
  one estimate derived from a previously measured run of the same command
  was accurate. Nothing in the system recorded forecast against actual, so
  the bias survived four estimates in one night and would have survived
  forever. The fix is not "estimate better": it is a tool that records
  forecasts, records actuals, computes the correction factor from real
  history, and REFUSES to invent a correction factor when history is too
  thin.

THE CONTRACT
  Storage: <root>/.brothermode/forecasts-log.jsonl, append-only, one JSON
  object per line, UTF-8, created on first write with mode 0600 inside the
  existing .brothermode directory. <root> is resolved by borrowing
  bm_store.resolve_root() the same way bm_progress_check.py does, with a
  --root PATH override, or --log PATH to point at a specific log file
  directly (what the tests use).

  Two record kinds, distinguished by the "kind" key:

    forecast: task, clock (agent|wall), basis (judged|measured),
              likely_minutes, plus optional upper_minutes, assumption,
              source, and recorded_at.
    actual:   task, minutes, plus optional note, and recorded_at.

  calibrate pairs every task that has at least one forecast AND at least
  one actual (matching --clock/--basis filters against the forecast side;
  actuals carry no clock or basis), using the LATEST forecast per task for
  a reforecast. ratio = latest_forecast.likely_minutes / actual.minutes. A
  pair whose actual is zero or negative is DROPPED and named, never
  silently included and never divided by zero.

  With fewer than 3 usable pairs, calibrate is NO-DATA at exit 2 and NEVER
  prints a multiplier: this is the load-bearing honesty property of the
  whole tool. It must never fall back to 1.0 and must never treat an empty
  log as a pass. apply runs calibrate internally and propagates its
  NO-DATA verbatim, printing no estimate, so a caller can never receive an
  uncalibrated number believing it was calibrated.

  check is the gate verb: a forecast open longer than --max-open-hours with
  no actual recorded against it is the exact failure that let the bias
  survive. An absent or empty log is NO-DATA, never PASS. A line that
  cannot be parsed as JSON is counted as malformed rather than crashing the
  tool; an all-malformed log is NO-DATA.

  Exit codes are load bearing everywhere: 0 pass, 1 fail, 2 could not tell.
  NO-DATA output always starts with the literal string "NO-DATA:".

EFFECT CLASS: append_only_log
  This tool owns exactly one file, forecasts-log.jsonl, and writes to it
  through one small helper, _append_record, which opens with
  os.O_WRONLY | os.O_CREAT | os.O_APPEND at mode 0600. It never imports or
  constructs tools/bm_store.py's Store: bm_store is loaded only to borrow
  resolve_root() for --root resolution, exactly like bm_progress_check.py
  does, and this tool never becomes a second writer of the sqlite store.

BE SILENT-SAFE
  `main` catches every unexpected exception anywhere in this module and
  reports it as "NO-DATA: <ExceptionType>: <message>" at exit 2, rather
  than raising: a crashing check must never read as a pass.

Python 3.9, standard library only. No network. No subprocess.
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_forecast.py record --task T --clock agent --basis judged
      --likely-minutes N [--upper-minutes N] [--assumption TEXT]
      [--source TEXT] [--at ISO] [--log PATH] [--root PATH]
  python3 tools/bm_forecast.py actual --task T --minutes N [--note TEXT]
      [--at ISO] [--log PATH] [--root PATH]
  python3 tools/bm_forecast.py calibrate [--clock agent] [--basis judged]
      [--log PATH] [--root PATH]
  python3 tools/bm_forecast.py apply --likely-minutes N [--clock agent]
      [--basis judged] [--log PATH] [--root PATH]
  python3 tools/bm_forecast.py check [--max-open-hours 12] [--now ISO]
      [--log PATH] [--root PATH]
  python3 tools/bm_forecast.py list [--log PATH] [--root PATH]
"""

import datetime
import io
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CLOCKS = ("agent", "wall")
BASES = ("judged", "measured")
MIN_PAIRS = 3


# ---------------------------------------------------------------------------
# Root and log path resolution.
# ---------------------------------------------------------------------------

def _load_bm_store():
    """Load bm_store.py by PATH, mirroring bm_progress_check.py's
    _load_bm_store: this tool can be invoked with an arbitrary cwd, and a
    plain `import bm_store` would depend on the caller's sys.path. Never
    raises: an unimportable module degrades to an explicit NO-DATA reason."""
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_forecast", path)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for bm_store.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def resolve_project_root(explicit_root):
    """Return (root, None) or (None, reason), mirroring
    bm_progress_check.resolve_project_root. --root always wins; otherwise
    bm_store.resolve_root() is borrowed for its (path, source) tuple."""
    if explicit_root:
        candidate = os.path.realpath(os.path.expanduser(explicit_root))
        if not os.path.isdir(candidate):
            return None, "no such directory: %s" % candidate
        return candidate, None
    mod, err = _load_bm_store()
    if mod is None:
        return None, ("could not load bm_store.py to resolve the project "
                      "root (%s); pass --root explicitly" % err)
    root, _source = mod.resolve_root()
    if not root:
        return None, ("nothing anchors a BrotherMode project here (no "
                      "BROTHERMODE_ROOT, no marker directory, no git repo "
                      "found); pass --root explicitly")
    return root, None


def resolve_log_path(explicit_log, explicit_root):
    """Return (log_path, None) or (None, reason). --log always wins over
    root resolution, since the tests point it at an arbitrary tempfile
    path that may not exist yet."""
    if explicit_log:
        return os.path.realpath(os.path.expanduser(explicit_log)), None
    root, reason = resolve_project_root(explicit_root)
    if root is None:
        return None, reason
    return os.path.join(root, ".brothermode", "forecasts-log.jsonl"), None


# ---------------------------------------------------------------------------
# Append-only log: one write helper, one read helper.
# ---------------------------------------------------------------------------

def _append_record(log_path, record):
    """The single write site for this tool. Creates the parent directory
    and the file (mode 0600) on first write, and appends exactly one JSON
    line thereafter. This is the only function in the module that opens
    the log for writing."""
    parent = os.path.dirname(log_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    line = json.dumps(record, sort_keys=True) + "\n"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _read_records(log_path):
    """Return (records, malformed_count). Each unparsable line is counted
    rather than raised; a completely empty or absent log yields ([], 0),
    and the caller decides what that means for its own verdict."""
    if not os.path.exists(log_path):
        return [], 0
    records = []
    malformed = 0
    with io.open(log_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except Exception:
                malformed += 1
    return records, malformed


def _now_iso():
    # timezone.utc rather than utcnow(): utcnow() is deprecated from Python
    # 3.12 and prints a DeprecationWarning to stderr on every call. This tool
    # runs inside gates and hooks whose output is read as a verdict, so a
    # warning printed beside a verdict line is a real defect rather than
    # cosmetic. timezone.utc exists in 3.9, which datetime.UTC does not.
    return datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text):
    """Parse the "...Z" UTC ISO 8601 timestamps this tool writes and
    accepts via --at/--now. Raises ValueError on anything else, which the
    caller turns into a plain usage failure."""
    value = text
    if value.endswith("Z"):
        value = value[:-1]
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# record / actual
# ---------------------------------------------------------------------------

def cmd_record(opts):
    if opts["clock"] not in CLOCKS:
        sys.stdout.write(
            "refused: --clock must be one of %s, got %r\n"
            % (", ".join(CLOCKS), opts["clock"]))
        return 1
    if opts["basis"] not in BASES:
        sys.stdout.write(
            "refused: --basis must be one of %s, got %r\n"
            % (", ".join(BASES), opts["basis"]))
        return 1
    if not opts["task"]:
        sys.stdout.write("refused: --task is required\n")
        return 1
    if opts["likely_minutes"] is None:
        sys.stdout.write("refused: --likely-minutes is required\n")
        return 1

    log_path, reason = resolve_log_path(opts["log"], opts["root"])
    if log_path is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    record = {
        "kind": "forecast",
        "task": opts["task"],
        "clock": opts["clock"],
        "basis": opts["basis"],
        "likely_minutes": float(opts["likely_minutes"]),
        "recorded_at": opts["at"] or _now_iso(),
    }
    if opts["upper_minutes"] is not None:
        record["upper_minutes"] = float(opts["upper_minutes"])
    if opts["assumption"]:
        record["assumption"] = opts["assumption"]
    if opts["source"]:
        record["source"] = opts["source"]

    _append_record(log_path, record)
    sys.stdout.write(
        "recorded forecast task=%s clock=%s basis=%s likely_minutes=%.1f\n"
        % (record["task"], record["clock"], record["basis"],
           record["likely_minutes"]))
    return 0


def cmd_actual(opts):
    if not opts["task"]:
        sys.stdout.write("refused: --task is required\n")
        return 1
    if opts["minutes"] is None:
        sys.stdout.write("refused: --minutes is required\n")
        return 1

    log_path, reason = resolve_log_path(opts["log"], opts["root"])
    if log_path is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    existing, _malformed = _read_records(log_path)
    has_forecast = any(
        r.get("kind") == "forecast" and r.get("task") == opts["task"]
        for r in existing)

    record = {
        "kind": "actual",
        "task": opts["task"],
        "minutes": float(opts["minutes"]),
        "recorded_at": opts["at"] or _now_iso(),
    }
    if opts["note"]:
        record["note"] = opts["note"]

    _append_record(log_path, record)

    if has_forecast:
        sys.stdout.write(
            "recorded actual task=%s minutes=%.1f\n"
            % (record["task"], record["minutes"]))
    else:
        sys.stdout.write(
            "recorded actual task=%s minutes=%.1f (UNFORECAST: no forecast "
            "on record for this task, recorded anyway)\n"
            % (record["task"], record["minutes"]))
    return 0


# ---------------------------------------------------------------------------
# calibrate / apply
# ---------------------------------------------------------------------------

def _latest_forecasts_by_task(records, clock_filter, basis_filter):
    """The latest forecast per task matching the filters, keyed by task.
    "Latest" is by recorded_at string order (ISO 8601 sorts lexically),
    falling back to file order for ties, so a reforecast always wins."""
    latest = {}
    for index, record in enumerate(records):
        if record.get("kind") != "forecast":
            continue
        if clock_filter and record.get("clock") != clock_filter:
            continue
        if basis_filter and record.get("basis") != basis_filter:
            continue
        task = record.get("task")
        if task is None:
            continue
        key = (record.get("recorded_at") or "", index)
        prior = latest.get(task)
        if prior is None or key >= prior[0]:
            latest[task] = (key, record)
    return {task: rec for task, (_key, rec) in latest.items()}


def _earliest_actual_by_task(records):
    """One actual per task is enough to pair; the earliest is used so a
    task is considered closed as soon as any measurement exists."""
    by_task = {}
    for record in records:
        if record.get("kind") != "actual":
            continue
        task = record.get("task")
        if task is None:
            continue
        if task not in by_task:
            by_task[task] = record
    return by_task


def compute_calibration(records, clock_filter, basis_filter):
    """Return a dict describing the calibration attempt:
      {"ratios": [...], "n": int, "dropped": [task, ...],
       "usable_tasks": [...]}
    Never raises on a zero/negative actual: that pair is dropped and named.
    """
    forecasts = _latest_forecasts_by_task(records, clock_filter, basis_filter)
    actuals = _earliest_actual_by_task(records)

    ratios = []
    usable_tasks = []
    dropped = []
    for task, forecast in forecasts.items():
        actual = actuals.get(task)
        if actual is None:
            continue
        minutes = actual.get("minutes")
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            dropped.append(task)
            continue
        if minutes <= 0:
            dropped.append(task)
            continue
        likely = forecast.get("likely_minutes")
        try:
            likely = float(likely)
        except (TypeError, ValueError):
            dropped.append(task)
            continue
        ratios.append(likely / minutes)
        usable_tasks.append(task)

    return {
        "ratios": ratios,
        "n": len(ratios),
        "dropped": sorted(dropped),
        "usable_tasks": sorted(usable_tasks),
    }


def cmd_calibrate(opts):
    log_path, reason = resolve_log_path(opts["log"], opts["root"])
    if log_path is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    records, malformed = _read_records(log_path)
    result = compute_calibration(records, opts["clock"], opts["basis"])

    clock_label = opts["clock"] or "any"
    basis_label = opts["basis"] or "any"

    if result["n"] < MIN_PAIRS:
        dropped_note = ""
        if result["dropped"]:
            dropped_note = " (dropped: %s)" % ", ".join(result["dropped"])
        sys.stdout.write(
            "NO-DATA: %d usable pair%s for clock=%s basis=%s, need %d%s\n"
            % (result["n"], "" if result["n"] == 1 else "s",
               clock_label, basis_label, MIN_PAIRS, dropped_note))
        return 2

    median = statistics.median(result["ratios"])
    dropped_note = ""
    if result["dropped"]:
        dropped_note = " dropped=%s" % ",".join(result["dropped"])
    sys.stdout.write(
        "CALIBRATED clock=%s basis=%s n=%d median=%.2f min=%.2f max=%.2f%s\n"
        % (clock_label, basis_label, result["n"], median,
           min(result["ratios"]), max(result["ratios"]), dropped_note))
    return 0


def cmd_apply(opts):
    if opts["likely_minutes"] is None:
        sys.stdout.write("refused: --likely-minutes is required\n")
        return 1

    log_path, reason = resolve_log_path(opts["log"], opts["root"])
    if log_path is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    records, _malformed = _read_records(log_path)
    result = compute_calibration(records, opts["clock"], opts["basis"])

    clock_label = opts["clock"] or "any"
    basis_label = opts["basis"] or "any"

    if result["n"] < MIN_PAIRS:
        dropped_note = ""
        if result["dropped"]:
            dropped_note = " (dropped: %s)" % ", ".join(result["dropped"])
        sys.stdout.write(
            "NO-DATA: %d usable pair%s for clock=%s basis=%s, need %d%s\n"
            % (result["n"], "" if result["n"] == 1 else "s",
               clock_label, basis_label, MIN_PAIRS, dropped_note))
        return 2

    median = statistics.median(result["ratios"])
    min_ratio = min(result["ratios"])
    max_ratio = max(result["ratios"])
    input_minutes = float(opts["likely_minutes"])

    # Bounds come from the observed extremes of real history, never an
    # invented spread: dividing by the smallest ratio (the forecast that
    # ran relatively LONGEST versus reality) gives the largest corrected
    # estimate, and dividing by the largest ratio gives the smallest.
    likely = input_minutes / median
    low = input_minutes / max_ratio
    high = input_minutes / min_ratio

    sys.stdout.write(
        "CALIBRATED-ESTIMATE input=%.1f likely=%.1f low=%.1f high=%.1f "
        "basis=%s clock=%s n=%d\n"
        % (input_minutes, likely, low, high, basis_label, clock_label,
           result["n"]))
    return 0


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def open_forecasts(log_path, now_iso, max_open_hours):
    """PUBLIC. The tasks whose forecast never got an actual and are now
    older than `max_open_hours`, as a sorted list of task names.

    Returns (tasks, note). `tasks` is None when the question cannot be
    answered at all (no log, an unreadable log, an unparseable `now_iso`),
    and `note` says why. None is NOT an empty list and callers must not
    treat it as one: a missing log means could-not-tell, never all-clear.
    An empty list with a note of None means the real answer is none open.

    This exists so cmd_check below and tools/bm_handover.py's verify-close
    share ONE implementation of what "an open forecast" means. The whole
    reason the original forecasting bias survived four estimates in one
    night is that nothing forced a forecast to be closed, so the rule that
    closes them cannot itself be a second copy that can drift.
    """
    if not log_path or not os.path.exists(log_path):
        return None, "no forecast log at %s" % (log_path or "(unresolved)")
    records, malformed = _read_records(log_path)
    if not records:
        if malformed:
            return None, ("log has %d line(s), all malformed" % malformed)
        return None, "no records in the forecast log"
    try:
        now = _parse_iso(now_iso)
    except Exception:  # sbe: allow-silent already surfaced to the caller via the returned note string, not silent
        return None, "could not parse %r as ISO 8601" % now_iso

    with_forecast, with_actual, ages = set(), set(), {}
    for record in records:
        task = record.get("task")
        if task is None:
            continue
        if record.get("kind") == "forecast":
            with_forecast.add(task)
            try:
                age = (now - _parse_iso(record.get("recorded_at"))
                       ).total_seconds() / 3600.0
            except Exception:
                # A malformed recorded_at must not silently read as age 0.0
                # (never overdue) below: that is exactly the forecasting
                # bias this module exists to catch, so it is surfaced here
                # rather than swallowed, without changing the return shape.
                sys.stderr.write(
                    "bm_forecast: task %r has a forecast record with an "
                    "unparseable recorded_at, excluded from age tracking\n"
                    % (task,))
                continue
            if ages.get(task) is None or age > ages[task]:
                ages[task] = age
        elif record.get("kind") == "actual":
            with_actual.add(task)

    return (sorted(t for t in (with_forecast - with_actual)
                   if ages.get(t, 0.0) > max_open_hours), None)


def cmd_check(opts):
    log_path, reason = resolve_log_path(opts["log"], opts["root"])
    if log_path is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    if not os.path.exists(log_path):
        sys.stdout.write("NO-DATA: no forecast log at %s\n" % log_path)
        return 2

    records, malformed = _read_records(log_path)
    if not records:
        if malformed:
            sys.stdout.write(
                "NO-DATA: log at %s has %d line(s), all malformed\n"
                % (log_path, malformed))
        else:
            sys.stdout.write("NO-DATA: no forecast log at %s\n" % log_path)
        return 2

    now_text = opts["now"] or _now_iso()
    try:
        now = _parse_iso(now_text)
    except ValueError:
        sys.stdout.write(
            "NO-DATA: could not parse --now value %r as ISO 8601\n"
            % now_text)
        return 2

    max_open_hours = opts["max_open_hours"]

    forecast_count = 0
    tasks_with_forecast = set()
    tasks_with_actual = set()
    open_ages = {}
    for record in records:
        kind = record.get("kind")
        task = record.get("task")
        if kind == "forecast":
            forecast_count += 1
            if task is not None:
                tasks_with_forecast.add(task)
                recorded_at = record.get("recorded_at")
                try:
                    recorded = _parse_iso(recorded_at)
                    age_hours = (now - recorded).total_seconds() / 3600.0
                except Exception:
                    age_hours = None
                prior = open_ages.get(task)
                if prior is None or (age_hours is not None and age_hours > prior):
                    if age_hours is not None:
                        open_ages[task] = age_hours
        elif kind == "actual":
            if task is not None:
                tasks_with_actual.add(task)

    if forecast_count == 0:
        note = ""
        if malformed:
            note = " (malformed=%d)" % malformed
        sys.stdout.write(
            "NO-DATA: log at %s has no forecast records%s\n"
            % (log_path, note))
        return 2

    open_tasks = sorted(tasks_with_forecast - tasks_with_actual)
    # Delegated so this verb and tools/bm_handover.py's verify-close cannot
    # drift on what "open past the window" means. The counting above stays
    # local because the REPORT line carries more than the verdict does.
    breaching, _seam_note = open_forecasts(log_path, now_text, max_open_hours)
    if breaching is None:
        breaching = [t for t in open_tasks
                     if open_ages.get(t, 0.0) > max_open_hours]

    oldest_open = max(
        [open_ages.get(t, 0.0) for t in open_tasks], default=0.0)

    malformed_note = " malformed=%d" % malformed if malformed else ""

    if breaching:
        sys.stdout.write(
            "FAIL: %d forecast(s) open longer than %.1fh with no actual: "
            "%s%s\n" % (len(breaching), max_open_hours,
                       ", ".join(breaching), malformed_note))
        return 1

    # The wording says "open" rather than "all closed" on purpose. PASS here
    # means no forecast has been open LONGER THAN the window, which is not the
    # same as every forecast having an actual: work legitimately in flight is
    # open and young. Saying "all closed" while three forecasts sat open would
    # have been exactly the kind of false reassurance this whole tool exists
    # to remove, so the line reports the open count and the oldest age instead.
    sys.stdout.write(
        "PASS: %d forecast(s), %d still open, oldest open %.1fh "
        "within the %.1fh window%s\n"
        % (forecast_count, len(open_tasks), oldest_open, max_open_hours,
           malformed_note))
    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def cmd_list(opts):
    log_path, reason = resolve_log_path(opts["log"], opts["root"])
    if log_path is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    if not os.path.exists(log_path):
        sys.stdout.write("NO-DATA: no records\n")
        return 2

    records, malformed = _read_records(log_path)
    if not records:
        sys.stdout.write("NO-DATA: no records\n")
        return 2

    counts = {}
    for record in records:
        kind = record.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "forecast":
            sys.stdout.write(
                "forecast task=%s clock=%s basis=%s likely_minutes=%s "
                "upper_minutes=%s recorded_at=%s\n"
                % (record.get("task"), record.get("clock"),
                   record.get("basis"), record.get("likely_minutes"),
                   record.get("upper_minutes"), record.get("recorded_at")))
        elif kind == "actual":
            sys.stdout.write(
                "actual task=%s minutes=%s recorded_at=%s\n"
                % (record.get("task"), record.get("minutes"),
                   record.get("recorded_at")))
        else:
            sys.stdout.write("unknown-record kind=%s\n" % kind)

    summary = " ".join(
        "%s=%d" % (kind, count) for kind, count in sorted(counts.items()))
    note = " malformed=%d" % malformed if malformed else ""
    sys.stdout.write("summary: %d record(s) %s%s\n"
                     % (len(records), summary, note))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

VERBS = ("record", "actual", "calibrate", "apply", "check", "list")


def _parse_argv(argv):
    """Return (verb, opts, error). `error` set means stop and report a
    plain usage failure (exit 2, no "NO-DATA:" prefix: that prefix is
    reserved for "could not determine the verdict", not "bad arguments").

    --help/-h is handled the way tools/bm_project.py's own _parse handles
    it: recognized at the top level (bare `--help`, no verb) AND at every
    verb's flag position, never falling through to "unknown argument" or
    "unknown verb". opts["help"] is the signal _run checks before
    dispatching to any cmd_* function."""
    args = list(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        return "help", {"help": True}, None
    verb = args.pop(0)
    if verb not in VERBS:
        return None, None, "bm_forecast: unknown verb: %s" % verb

    opts = {
        "task": None, "clock": None, "basis": None,
        "likely_minutes": None, "upper_minutes": None,
        "assumption": None, "source": None, "at": None,
        "minutes": None, "note": None,
        "max_open_hours": 12.0, "now": None,
        "log": None, "root": None, "help": False,
    }

    flag_to_key = {
        "--task": ("task", str),
        "--clock": ("clock", str),
        "--basis": ("basis", str),
        "--likely-minutes": ("likely_minutes", float),
        "--upper-minutes": ("upper_minutes", float),
        "--assumption": ("assumption", str),
        "--source": ("source", str),
        "--at": ("at", str),
        "--minutes": ("minutes", float),
        "--note": ("note", str),
        "--max-open-hours": ("max_open_hours", float),
        "--now": ("now", str),
        "--log": ("log", str),
        "--root": ("root", str),
    }

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            opts["help"] = True
            return verb, opts, None
        if arg not in flag_to_key:
            return None, None, "bm_forecast: unknown argument: %s" % arg
        if i + 1 >= len(args):
            return None, None, "bm_forecast: %s requires a value" % arg
        key, caster = flag_to_key[arg]
        raw_value = args[i + 1]
        try:
            opts[key] = caster(raw_value)
        except ValueError:  # sbe: allow-silent already surfaced to the caller via the returned err string, printed to stderr by _run
            return None, None, (
                "bm_forecast: %s expects a number, got %r"
                % (arg, raw_value))
        i += 2

    return verb, opts, None


def _run(argv):
    """The real body of main, split out so `main` can wrap the whole thing
    in one try/except and guarantee NO-DATA on any unexpected exception."""
    verb, opts, err = _parse_argv(argv)
    if err:
        sys.stderr.write(err + "\n")
        return 2

    if opts.get("help"):
        sys.stdout.write(__doc__.strip() + "\n")
        return 0

    if verb == "record":
        return cmd_record(opts)
    if verb == "actual":
        return cmd_actual(opts)
    if verb == "calibrate":
        return cmd_calibrate(opts)
    if verb == "apply":
        return cmd_apply(opts)
    if verb == "check":
        return cmd_check(opts)
    if verb == "list":
        return cmd_list(opts)

    sys.stderr.write("bm_forecast: unhandled verb: %s\n" % verb)
    return 2


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
