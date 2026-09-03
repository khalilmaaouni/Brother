#!/usr/bin/env python3
"""Detect whether this project's machine is at risk of doing nothing,
mechanically.

WHY THIS EXISTS
  On 2026-08-11 this project wrote an overnight plan with thirteen tasks.
  They all finished in ninety minutes. Nothing was queued behind them, so
  had the founder actually gone to bed, roughly six of eight hours would
  have produced nothing. His verdict: a plan whose completion leaves the
  machine idle is not finished being written. Depth is a deliverable, and
  running out of queue is a planning defect that must be REPORTED rather
  than quietly experienced. This tool is the mechanical detector for that
  defect. It answers one question: is this machine at risk of doing
  nothing?

THE CONTRACT
  Input is a queue file, default <root>/docs/plan/QUEUE.json, overridable
  with --queue PATH. Required top level keys: schema (integer), min_depth
  (integer), items (list). Optional: window (object with hard_stop ISO
  8601 and boolean unattended), idle_window_minutes (integer, default 30),
  watch_paths (list of paths relative to root, default ["tools", "docs"]).

  Each item has id, title, state. state is one of exactly queued,
  in_flight, done, blocked. Any other value makes the whole file unusable:
  verdict NO-DATA, exit 2, naming the offending id and value.

  DEPTH is the count of items in state queued. Items in state blocked
  NEVER count toward depth: a blocked item, by definition, cannot be
  started by anybody, so it cannot save a night no matter how many of them
  pile up. This is the load-bearing rule of the whole tool; see the
  comment at compute_counts() below for where it is enforced.

  Five verbs:
    depth      report the count, always exit 0 unless the file is unusable.
    next       the first queued item in array order.
    check      the gate verb; see check_queue() for its exact precedence.
    list       one line per item plus a summary.
    reconcile  re-evaluate every done item's done_check against the tree;
               see cmd_reconcile() for the CONFIRMED / STILL OPEN /
               CANNOT VERIFY contract.
  check is also the default when no verb is given.

  Exit codes are load bearing everywhere: 0 nothing wrong, 1 a real defect
  the founder needs to know about, 2 could not tell. NO-DATA output always
  starts with the literal string "NO-DATA:".

EFFECT CLASS: pure_read
  This tool writes NOT ONE BYTE anywhere, ever: no file writes, no
  directories created, no store touched, no "mark" verb. The reason is
  concrete: a tool that writes must be registered in this project's
  reviewed write-site inventory, and a pure reader does not. bm_store is
  loaded only to borrow its resolve_root() for --root resolution, and a
  writable bm_store.Store is never constructed (constructing one is itself
  a write: it probes the filesystem, edits git excludes, opens a WAL, and
  can migrate a schema).

BE SILENT-SAFE
  Any unexpected exception anywhere in this module is caught by `main` and
  reported as NO-DATA at exit 2, carrying the exception's type and
  message, rather than raising: a crashing check must never read as a
  pass.

Python 3.9, standard library only. No network. No subprocess.
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_idle.py depth     [--queue PATH] [--root PATH]
  python3 tools/bm_idle.py next      [--queue PATH] [--root PATH]
  python3 tools/bm_idle.py check     [--queue PATH] [--root PATH] [--now ISO]
  python3 tools/bm_idle.py list      [--queue PATH] [--root PATH]
  python3 tools/bm_idle.py reconcile [--queue PATH] [--root PATH]

`check` is also the default when no verb is given.
"""

import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: The only valid item states. Any other value found in the file makes the
#: whole file unusable (NO-DATA), because a depth count built on top of an
#: unrecognised state cannot be trusted.
VALID_STATES = ("queued", "in_flight", "done", "blocked")

#: The stages of the founder's north-star chain (docs/NORTH-STAR-CHAIN.md,
#: direction of 2026-08-15), in chain order. An item may name the stage it
#: serves; one that names a stage NOT on this list is a hard error, because a
#: typo would file work under a stage that does not exist and nobody would see
#: it again. "release" is deliberately absent: the host does that, both
#: products stop at the pull request, so no queue item can serve it.
CHAIN_STAGES = (
    "intent",
    "method",
    "provenance",
    "passport",
    "behaviour",
    "business-impact",
    "risk",
    "required-proof",
    "evidence-integrity",
    "accountability",
    "release-readiness",
    "production-observation",
    "human-decision",
    "verified-reality",
)

#: Directory names skipped everywhere a watch path is walked. These are
#: generated or vendored trees whose mtimes say nothing about whether a
#: human or agent is actually still working.
_SKIP_DIR_NAMES = (".git", "__pycache__", "node_modules", ".brothermode")

#: Defaults used when the queue file omits the optional keys.
DEFAULT_IDLE_WINDOW_MINUTES = 30
DEFAULT_WATCH_PATHS = ("tools", "docs")


# ---------------------------------------------------------------------------
# Root resolution. Honors --root when given; otherwise borrows bm_store's
# resolve_root() purely for that lookup, never touching the store itself.
# Mirrors tools/bm_progress_check.py's resolve_project_root() exactly, since
# that file is this tool's structural template.
# ---------------------------------------------------------------------------

def _load_bm_store():
    """Load bm_store.py by PATH: this tool is invoked with an arbitrary
    cwd, and a plain `import bm_store` would depend on whichever sys.path
    the caller happened to have. Never raises: an unimportable module
    degrades to an explicit NO-DATA reason, not a crash."""
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_idle", path)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for bm_store.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def resolve_project_root(explicit_root):
    """Return (root, None) or (None, reason). `explicit_root` (--root)
    always wins and is checked directly against the filesystem; nothing
    else here reads bm_store at all in that case. Otherwise
    bm_store.resolve_root() is borrowed, which returns a (path, source)
    TUPLE that must be unpacked."""
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


# ---------------------------------------------------------------------------
# Queue loading and validation. Pure read: json.load plus type checks.
# ---------------------------------------------------------------------------

def _validate_queue(data):
    """Return None if `data` is a usable queue, or a plain-English reason
    naming the offending key/id/value otherwise. Deliberately strict: a
    depth count or a next-item pick built on top of a file that half
    matches the contract is worse than refusing to answer."""
    if not isinstance(data, dict):
        return "queue root is not a JSON object"
    for key in ("schema", "min_depth", "items"):
        if key not in data:
            return "missing required key: %s" % key
    if not isinstance(data["schema"], int) or isinstance(data["schema"], bool):
        return "schema is not an integer"
    if (not isinstance(data["min_depth"], int)
            or isinstance(data["min_depth"], bool)):
        return "min_depth is not an integer"
    if not isinstance(data["items"], list):
        return "items is not a list"

    for index, entry in enumerate(data["items"]):
        if not isinstance(entry, dict):
            return "item at index %d is not an object" % index
        if "id" not in entry:
            return "item at index %d is missing an id" % index
        if "state" not in entry:
            return "item %s is missing a state" % entry["id"]
        if entry["state"] not in VALID_STATES:
            return "item %s has an unknown state: %s" % (
                entry["id"], entry["state"])
        stage = entry.get("stage")
        if stage is not None and stage not in CHAIN_STAGES:
            return "item %s names a stage that is not on the chain: %s" % (
                entry["id"], stage)
    return None


def unstaged_items(items):
    """Ids of QUEUED items that name no stage of the north-star chain.

    Reported, never refused. The founder's chain (docs/NORTH-STAR-CHAIN.md,
    2026-08-15) says an item that cannot name the stage it serves belongs in
    the parking lot rather than the backlog. Making that a hard error would
    make the whole file unusable for the 82 items written before the chain
    existed, and an unusable queue file means NO-DATA for every verb, which
    would take a reporting rule and turn it into an outage. So a missing
    stage is a named finding and an UNRECOGNISED stage is a hard error: one
    is work nobody has classified yet, the other is a typo that would quietly
    file work under a stage that does not exist.
    """
    return [it["id"] for it in items
            if it["state"] == "queued" and not str(it.get("stage") or "").strip()]


def load_queue(path):
    """Return (data, None) or (None, reason). The only place this module
    opens the queue file; every verb goes through here first so an
    unusable file gives NO-DATA no matter which verb was asked for."""
    if not os.path.isfile(path):
        return None, "missing queue file: %s" % path
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, "not valid JSON: %s (%s: %s)" % (
            path, type(exc).__name__, exc)

    reason = _validate_queue(data)
    if reason:
        return None, reason
    return data, None


def compute_counts(items):
    """(queued, blocked, in_flight, done). Blocked is counted here for
    reporting only; DEPTH itself (see cmd_depth and check_queue) is always
    the queued count alone. A blocked item cannot be started by anybody,
    by definition of the state, so no number of them can substitute for an
    actually startable item; counting them toward depth would let a queue
    full of unstartable work read as a healthy night."""
    queued = sum(1 for it in items if it["state"] == "queued")
    blocked = sum(1 for it in items if it["state"] == "blocked")
    in_flight = sum(1 for it in items if it["state"] == "in_flight")
    done = sum(1 for it in items if it["state"] == "done")
    return queued, blocked, in_flight, done


# ---------------------------------------------------------------------------
# ISO 8601 parsing and watch-path inspection, used only by `check`.
# ---------------------------------------------------------------------------

def parse_iso(value):
    """Parse an ISO 8601 timestamp, standard library only. Python 3.9's
    datetime.fromisoformat handles offsets of the form +09:00 natively but
    not a trailing Z, so that one case is rewritten by hand first. A
    result with no offset at all is treated as UTC rather than left naive,
    so every comparison in this module stays timezone aware."""
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _iter_watch_files(root, relpath):
    """Yield every file's absolute path under root/relpath. Skips
    directories named in _SKIP_DIR_NAMES at any depth. If root/relpath
    does not exist, yields nothing (the caller records that separately so
    it can be named rather than silently ignored)."""
    abs_path = os.path.join(root, relpath)
    if not os.path.exists(abs_path):
        return
    if os.path.isfile(abs_path):
        yield abs_path
        return
    for dirpath, dirnames, filenames in os.walk(abs_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for name in filenames:
            yield os.path.join(dirpath, name)


def newest_watch_mtime(root, watch_paths):
    """(newest_mtime_or_None, missing_paths). Walks every entry in
    `watch_paths` (each relative to `root`) recursively and returns the
    single newest mtime found across all of them, plus the list of entries
    that did not exist on disk at all. A watch path that does not exist is
    skipped rather than raising, and its name is returned so the caller
    can put it in the verdict line instead of failing silently."""
    newest = None
    missing = []
    for rel in watch_paths:
        if not os.path.exists(os.path.join(root, rel)):
            missing.append(rel)
            continue
        for file_path in _iter_watch_files(root, rel):
            mtime = os.path.getmtime(file_path)
            if newest is None or mtime > newest:
                newest = mtime
    return newest, missing


def _missing_paths_suffix(missing):
    if not missing:
        return ""
    label = "path" if len(missing) == 1 else "paths"
    return " (missing watch %s: %s)" % (label, ", ".join(missing))


# ---------------------------------------------------------------------------
# The four verbs.
# ---------------------------------------------------------------------------

def cmd_depth(data):
    queued, blocked, in_flight, done = compute_counts(data["items"])
    line = "DEPTH %d min_depth=%d blocked=%d in_flight=%d done=%d" % (
        queued, data["min_depth"], blocked, in_flight, done)
    return line, 0


def cmd_next(data):
    for entry in data["items"]:
        if entry["state"] == "queued":
            title = entry.get("title", "")
            done_check = entry.get("done_check", "")
            return ("NEXT %s %s\nCHECK %s" % (
                entry["id"], title, done_check)), 0
    return "NO-DATA: nothing queued", 2


def cmd_list(data):
    items = data["items"]
    if not items:
        return "NO-DATA: no items in queue", 2
    lines = []
    counts = {"queued": 0, "in_flight": 0, "done": 0, "blocked": 0}
    for entry in items:
        counts[entry["state"]] += 1
        lines.append("%s %s %s" % (
            entry["state"], entry["id"], entry.get("title", "")))
    lines.append("TOTAL %d items: queued=%d in_flight=%d done=%d "
                 "blocked=%d" % (
                     len(items), counts["queued"], counts["in_flight"],
                     counts["done"], counts["blocked"]))
    return "\n".join(lines), 0


def check_queue(data, root, now_ts):
    """The gate verb. Evaluates the six rules from the module docstring's
    contract in exactly this order and returns the FIRST that applies, as
    (line, exit_code). Called only after load_queue() has already ruled
    out the NO-DATA cases (rule 1), so this function assumes `data` is
    already a validated queue."""
    items = data["items"]
    queued, blocked, _in_flight, _done = compute_counts(items)
    depth = queued
    min_depth = data["min_depth"]

    # Rule 2: zero queued items. Blocked items are named here on purpose
    # so the reader sees, in the same line, that they were not counted.
    if depth == 0:
        return ("QUEUE-EMPTY: 0 items queued, the machine has nothing to "
                "start (%d blocked, not counted)" % blocked), 1

    # Rule 3: depth is healthy in the sense of "nonzero" but still under
    # the founder's declared floor for the night.
    if depth < min_depth:
        return "DEPTH-LOW: %d queued, min_depth %d" % (depth, min_depth), 1

    window = data.get("window")
    unattended = isinstance(window, dict) and window.get("unattended") is True

    # If window is absent entirely, or window.unattended is false, rules 4
    # and 5 are skipped outright: an attended session is allowed to be
    # quiet because a human is thinking, and idleness is only a defect
    # when nobody is watching to notice it.
    if not unattended:
        return "OK: depth %d, attended session" % depth, 0

    # Rule 4: the window has a declared hard stop and it has already
    # passed. Only reachable once depth is healthy (rules 2 and 3 passed),
    # because a closed window makes idling legitimate, not a healthy
    # queue's absence of idling.
    hard_stop_raw = window.get("hard_stop")
    if hard_stop_raw:
        hard_stop_dt = parse_iso(hard_stop_raw)
        if now_ts >= hard_stop_dt.timestamp():
            return ("WINDOW-CLOSED: depth %d, hard stop %s has passed" % (
                depth, hard_stop_raw)), 0

    # Rule 5: idle detection. Reached only when depth is healthy, the
    # window is open, and the session is unattended.
    idle_minutes = data.get("idle_window_minutes", DEFAULT_IDLE_WINDOW_MINUTES)
    watch_paths = data.get("watch_paths", list(DEFAULT_WATCH_PATHS))
    newest, missing = newest_watch_mtime(root, watch_paths)
    suffix = _missing_paths_suffix(missing)
    threshold = now_ts - (idle_minutes * 60)

    if newest is None or newest < threshold:
        return ("IDLE: depth %d but no watched path changed in %d "
                "minutes%s" % (depth, idle_minutes, suffix)), 1

    # Rule 6: everything is fine.
    minutes_ago = (now_ts - newest) / 60.0
    return ("OK: depth %d, last change %.1f minutes ago%s" % (
        depth, minutes_ago, suffix)), 0


# ---------------------------------------------------------------------------
# reconcile: M31's done_check rot detector. A done_check is English prose
# that REFERENCES concrete artifacts (a file path, a test function or class
# name). Re-evaluating it means extracting those references and checking
# they still resolve in the tree, which needs nothing but file reads: no
# subprocess (this file is not on tools/test_bm.py's allowlist), no shell.
# ---------------------------------------------------------------------------

#: Path-like token, with or without a directory: "tools/bm_toolkit.py" or
#: bare "SECURITY.md". The extension list is exactly the one named by M31.
_PATH_REF_RE = re.compile(
    r"[A-Za-z0-9_./-]+\.(?:py|sh|json|md|html|yml|yaml)\b")

#: Test function and test class names: test_foo, TestFoo. Matched against
#: done_check text with every already-extracted path span masked out first
#: (see extract_references), so a filename like tools/test_bm_idle.py never
#: also yields a spurious symbol reference "test_bm_idle" that means nothing
#: on its own.
_SYMBOL_REF_RE = re.compile(r"\b(?:test_[A-Za-z0-9_]+|Test[A-Za-z0-9_]+)\b")

#: The tail of a "files" entry that makes it path shaped: a dot then 1 to 5
#: alphanumeric characters, anchored to the end of the string.
_FILES_ENTRY_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def _is_path_shaped(entry):
    """True if a "files" list entry looks like a path inside this tree
    rather than a bare name. Path shaped means it contains a "/" separator
    or ends in a dot-extension. A bare word like "BrotherSBE" is neither:
    it is the name of a sibling repository, not a path in this tree, and
    treating it as one is a false STILL OPEN finding."""
    return "/" in entry or bool(_FILES_ENTRY_EXTENSION_RE.search(entry))


def extract_references(done_check, files):
    """Return an ordered, de-duplicated list of ("path", ref) / ("symbol",
    ref) tuples pulled from `done_check` text and from `files` (an item's
    own "files" list). A "files" entry is taken as a reference only when it
    is path shaped (see _is_path_shaped); a bare name that is neither a
    directory-qualified path nor a dot-extension is not resolvable in this
    tree and contributes nothing, per _is_path_shaped's contract."""
    text = done_check or ""
    refs = []
    seen = set()

    for match in _PATH_REF_RE.finditer(text):
        key = ("path", match.group(0))
        if key not in seen:
            seen.add(key)
            refs.append(key)

    # Mask out the path spans before hunting for symbols, so a symbol name
    # that only exists as a substring of a filename is never extracted as
    # its own reference.
    masked = _PATH_REF_RE.sub(lambda m: " " * len(m.group(0)), text)
    for match in _SYMBOL_REF_RE.finditer(masked):
        key = ("symbol", match.group(0))
        if key not in seen:
            seen.add(key)
            refs.append(key)

    for entry in files or []:
        if not isinstance(entry, str) or not entry:
            continue
        if not _is_path_shaped(entry):
            continue
        key = ("path", entry)
        if key not in seen:
            seen.add(key)
            refs.append(key)

    return refs


def _collect_py_sources(root):
    """(list_of_file_contents, undecodable_count) for every .py file under
    root/tools and root/scripts. Never raises: a file that cannot be opened
    or decoded is skipped and counted rather than crashing the reconcile
    run, per the module's BE SILENT-SAFE contract."""
    contents = []
    undecodable = 0
    for rel_base in ("tools", "scripts"):
        abs_base = os.path.join(root, rel_base)
        if not os.path.isdir(abs_base):
            continue
        for dirpath, dirnames, filenames in os.walk(abs_base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        contents.append(handle.read())
                except (OSError, UnicodeDecodeError):
                    undecodable += 1
    return contents, undecodable


def _resolve_reference(kind, ref, root, py_sources):
    if kind == "path":
        return os.path.exists(os.path.join(root, ref))
    return any(ref in src for src in py_sources)


def cmd_reconcile(data, root, queue_path):
    """The gate verb for M31: re-evaluate every done item's done_check (and
    its files list) against the tree, without executing anything. Returns
    exactly one of CONFIRMED, STILL OPEN or CANNOT VERIFY per done item; the
    CANNOT VERIFY branch below is the refusal clause and is checked FIRST,
    as its own branch, not folded into the resolution loop, so an item
    whose done_check yielded no reference can never fall through to
    CONFIRMED. Exit code is always 0: this reports findings, it never fails
    the command, because absent evidence is a finding, not a crash. A queue
    the tool cannot even read is already NO-DATA/exit 2 before this
    function is reached (see _run)."""
    done_items = [it for it in data["items"] if it["state"] == "done"]
    py_sources, undecodable = _collect_py_sources(root)

    lines = []
    confirmed = still_open = cannot_verify = 0

    for it in done_items:
        refs = extract_references(it.get("done_check", ""), it.get("files"))

        # Refusal clause (the heart of M31): zero extracted references means
        # this done_check could not be re-evaluated at all, and that must
        # NEVER read as CONFIRMED.
        if not refs:
            cannot_verify += 1
            lines.append(
                "%s CANNOT VERIFY: no path or test-symbol reference could "
                "be extracted from its done_check or files" % it["id"])
            continue

        unresolved = [ref for kind, ref in refs
                      if not _resolve_reference(kind, ref, root, py_sources)]
        if unresolved:
            still_open += 1
            lines.append("%s STILL OPEN: unresolved reference(s): %s" % (
                it["id"], ", ".join(unresolved)))
        else:
            confirmed += 1

    undecodable_suffix = ""
    if undecodable:
        undecodable_suffix = (
            " (%d file(s) under tools/scripts could not be decoded and "
            "were skipped)" % undecodable)

    out = ["RECONCILE %s: %d done item(s) examined%s" % (
        queue_path, len(done_items), undecodable_suffix)]
    out.extend(lines)
    out.append(
        "reconcile: %d done items, %d confirmed, %d still open, %d cannot "
        "verify" % (len(done_items), confirmed, still_open, cannot_verify))
    out.append("This command proposes and does not dispose. It wrote nothing.")
    return "\n".join(out), 0


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

_VERBS = ("depth", "next", "check", "list", "reconcile")


def _parse_argv(argv):
    """Return (verb, queue, root, now, error). `error` set means stop and
    report a plain usage failure (exit 2, no "NO-DATA:" prefix: that
    prefix is reserved for "could not determine the verdict", not "bad
    arguments")."""
    args = list(argv)
    verb = "check"
    if args and not args[0].startswith("--"):
        verb = args.pop(0)
    if verb not in _VERBS:
        return None, None, None, None, "bm_idle: unknown verb: %s" % verb

    queue = None
    root = None
    now = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--queue":
            if i + 1 >= len(args):
                return None, None, None, None, "bm_idle: --queue requires a value"
            queue = args[i + 1]
            i += 2
        elif arg == "--root":
            if i + 1 >= len(args):
                return None, None, None, None, "bm_idle: --root requires a value"
            root = args[i + 1]
            i += 2
        elif arg == "--now":
            if i + 1 >= len(args):
                return None, None, None, None, "bm_idle: --now requires a value"
            now = args[i + 1]
            i += 2
        else:
            return None, None, None, None, "bm_idle: unknown argument: %s" % arg
    return verb, queue, root, now, None


def _run(argv):
    """The real body of main, split out so `main` can wrap the whole thing
    in one try/except and guarantee NO-DATA on any unexpected exception."""
    verb, queue_arg, root_arg, now_arg, err = _parse_argv(argv)
    if err:
        sys.stderr.write(err + "\n")
        return 2

    root, reason = resolve_project_root(root_arg)
    if root is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    queue_path = queue_arg or os.path.join(root, "docs/plan/QUEUE.json")
    data, reason = load_queue(queue_path)
    if data is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    if verb == "depth":
        line, exit_code = cmd_depth(data)
    elif verb == "next":
        line, exit_code = cmd_next(data)
    elif verb == "list":
        line, exit_code = cmd_list(data)
    elif verb == "reconcile":
        line, exit_code = cmd_reconcile(data, root, queue_path)
    else:
        if now_arg:
            now_ts = parse_iso(now_arg).timestamp()
        else:
            now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        line, exit_code = check_queue(data, root, now_ts)

    sys.stdout.write(line + "\n")
    # The chain finding rides on every verb and changes no exit code, which is
    # deliberate: it reports which queued work nobody has placed on the
    # north-star chain, and unplaced work is a planning fact rather than a
    # reason to fail a machine check. It prints AFTER the verdict so the
    # verdict stays the first line every caller already parses.
    unstaged = unstaged_items(data["items"])
    if unstaged:
        sys.stdout.write(
            "CHAIN: %d queued item(s) name no stage of the north-star chain "
            "(docs/NORTH-STAR-CHAIN.md): %s%s\n"
            % (len(unstaged), ", ".join(unstaged[:12]),
               "" if len(unstaged) <= 12 else ", and %d more" % (len(unstaged) - 12)))
    return exit_code


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
