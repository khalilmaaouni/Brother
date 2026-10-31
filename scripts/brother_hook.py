#!/usr/bin/env python3
"""brother_hook.py: one process runs every handler of a hook event (HD1.c).

Usage (as the generator will write it into bundle/hooks/hooks.json, HD1.e):

    python3 "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/brother_hook.py" <Event>

with the hook payload on stdin. Until HD1 a Bash call started eight hook
processes (558 ms of interpreter starts, V-main); this file reads the dispatch
table bundle_runtime writes into HOOKS-MANIFEST.json and runs the event's
handlers one after another in THIS process: each handler script is executed as
__main__ with its own argv, stdin, stdout and stderr, exactly as its own
process would have, and the process-wide state it could disturb (argv, path,
cwd, modules it imported from the mirror, the std streams) is put back after.

THE THREE ANSWERS, merged:
  * a handler that DENIES (exit 2, or a hookSpecificOutput permissionDecision
    "deny" object, or a "decision": "block" object) denies the event, wherever
    it sits in the table and whatever the others said. Every handler still
    runs: the recorders have side effects the audit reads later, and Claude
    Code ran them all in parallel before this file existed.
  * everything else is allowed, and the handlers' stdout is merged (JSON
    objects are merged, plain text is joined, one output passes untouched).
  * a handler that RAISES is a different thing from a handler that denies. A
    deny class handler that raises refuses the call (a guard that fails must
    never fail open); an advisory one never blocks.

WHERE THE FAILURE DIRECTION POINTS, by cause:
  * a handler that cannot be LOADED (ImportError, SyntaxError, a script that
    will not compile) is an environmental fault of this process, not a verdict,
    so that handler alone is spawned as its own process, exactly as it ran
    before. A benign call is never denied because a module would not import here.
  * a manifest that cannot be read or has no table, a missing HOME, or an event
    the table does not hold: every handler of the event is found again from the
    product hooks.json files the generator mirrors under
    runtime/hooks/<product>/hooks/hooks.json and SPAWNED (one process each, in
    parallel), so no guard goes unrun. Only an event nothing wires exits 0
    silently.
  * DEADLINES. Deny class rows run first, so a hanging recorder can never keep a
    guard from answering. Every row has a deadline: the timeout its row carries, or
    the event's budget when it carries none. The budget is 0.8 of the largest row
    timeout of the event (the host's own timeout on the one command that replaces
    them is that number, and a host that times out fails OPEN, so this file answers
    first). A row past its deadline is interrupted; a deny row is then run again as
    its own process under a timeout, an advisory row is dropped with a visible line.
    If the budget is used up before every deny row has answered, the event exits 2
    naming the row that used it up. The deadline is a SIGALRM interval timer, so it
    needs the main thread of a platform that has one; otherwise every row is spawned.
  * BROTHER_HOOK_DISPATCH=spawn forces the spawn path for every handler: the
    rollback switch.
  * a mirror that exists but cannot be parsed is an unknown guard: it counts as
    a deny class handler that raised.

The double fire guard (hook_guard.should_yield) is asked per handler on both
paths, so the dispatcher yields exactly where the wrapper did.

Python 3, standard library only, no network. Runs on 3.9 and newer.
"""
import getpass
import hashlib
import importlib.machinery
import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import types

#: Where this file sits once installed: <plugin>/runtime/hooks/. The manifest
#: and every mirrored product tree are beside it.
HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_NAME = "HOOKS-MANIFEST.json"
#: This file's own name once mirrored beside the manifest; the one command per event
#: bundle_runtime writes into hooks.json names it.
DISPATCHER_NAME = "brother_hook.py"
#: The manifest key that holds {event: [row, ...]}.
TABLE_KEY = "dispatch"
#: Where a product's own hooks.json is mirrored, relative to <HOOKS_DIR>/<product>.
MIRROR_TAIL = ("hooks", "hooks.json")
SPAWN_ENV = "BROTHER_HOOK_DISPATCH"
SPAWN = "spawn"
DENY = "deny"
ADVISORY = "advisory"
#: Events whose matcher names a tool; for every other event a matcher is not
#: compared and the handler runs (a duplicate run is safe, a skipped guard is not).
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure",
               "PermissionRequest", "PermissionDenied")
#: Events whose exit 2 stops the action; a dispatcher that cannot judge refuses these.
BLOCKING_EVENTS = ("PreToolUse", "Stop")
#: What a row may run for when it names no timeout, and so the base of an event's budget.
DEFAULT_TIMEOUT_S = 60
#: The share of the largest row timeout the event spends before it answers for itself.
BUDGET_FRACTION = 0.8
_EVENT_RE = re.compile(r"[A-Za-z]+\Z")
_TOOL_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/tools/([A-Za-z0-9_.\-]+\.py)\Z")


def handler_class(entry):
    """deny or advisory. Only a class spelled advisory (any case) reads advisory;
    a missing, empty, unknown or non-string class reads deny, and so does
    anything that is not an entry at all: a guard nobody classified is a guard."""
    cls = entry.get("class") if isinstance(entry, dict) else None
    if isinstance(cls, str) and cls.lower() == ADVISORY:
        return ADVISORY
    return DENY


def entry_for(product, matcher, hook):
    """The dispatch row for one authored handler of a product hooks.json. Its
    `tool` is None when the command is not the shape the generator writes
    (`python3 "${CLAUDE_PLUGIN_ROOT}/tools/<name>.py" args...`): such a row cannot
    be run, the generator refuses it and the dispatcher counts it as a raise."""
    command = hook.get("command", "") if isinstance(hook, dict) else ""
    tool, args = None, []
    try:
        tokens = shlex.split(command) if isinstance(command, str) else []
    except ValueError:
        tokens = []
    for i, token in enumerate(tokens):
        m = _TOOL_RE.match(token)
        if m:
            tool, args = "%s/tools/%s" % (product, m.group(1)), tokens[i + 1:]
            break
    row = {"product": product, "matcher": matcher if isinstance(matcher, str) else "",
           "class": handler_class(hook), "tool": tool, "args": args}
    timeout = hook.get("timeout") if isinstance(hook, dict) else None
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
        row["timeout"] = timeout
    return row


def rows_from_doc(doc, product):
    """{event: [row, ...]} for one product hooks.json, in file order. A document
    that is not the hooks.json shape contributes nothing."""
    table = {}
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        rows = table.setdefault(event, [])
        for group in groups if isinstance(groups, list) else ():
            members = group.get("hooks") if isinstance(group, dict) else None
            for hook in members if isinstance(members, list) else ():
                rows.append(entry_for(product, group.get("matcher", ""), hook))
    return table


def load_table(manifest_path):
    """{event: [row, ...]} from the `dispatch` table of HOOKS-MANIFEST.json, each
    row carrying `path`, the absolute script. RAISES (OSError, ValueError) when
    the file cannot be read, is not JSON, holds no table, or a row is malformed:
    an unreadable table is never an empty one, because an empty table is what
    lets an event through unjudged."""
    with open(manifest_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    table = doc.get(TABLE_KEY) if isinstance(doc, dict) else None
    if not isinstance(table, dict):
        raise ValueError("%s holds no %r table" % (manifest_path, TABLE_KEY))
    base = os.path.dirname(os.path.abspath(manifest_path))
    out = {}
    for event, rows in table.items():
        if not isinstance(rows, list):
            raise ValueError("%s: %s is not a list" % (manifest_path, event))
        out[event] = []
        for row in rows:
            tool = row.get("tool") if isinstance(row, dict) else None
            parts = tool.split("/") if isinstance(tool, str) else []
            if not parts or any(p in ("", ".", "..") for p in parts) \
                    or not isinstance(row.get("args", []), list):
                raise ValueError("%s: a %s row names no runnable tool" % (manifest_path, event))
            out[event].append(dict(row, path=os.path.join(base, *parts)))
    return out


def mirror_rows(event, hooks_dir=None):
    """The event's rows read from the mirrored product hooks.json files, product
    folders in sorted order (brothermode before brothersbe, the generator's own
    order). A mirror that exists and cannot be read is one deny class row with no
    tool: it raises when run, which refuses the call."""
    hooks_dir = hooks_dir or HOOKS_DIR
    rows = []
    try:
        products = sorted(os.listdir(hooks_dir))
    except OSError:
        return rows
    for product in products:
        path = os.path.join(hooks_dir, product, *MIRROR_TAIL)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError, RecursionError):
            rows.append({"product": product, "matcher": "", "class": DENY, "tool": None,
                         "args": [], "unreadable": path})
            continue
        for row in rows_from_doc(doc, product).get(event, []):
            row["path"] = os.path.join(hooks_dir, *row["tool"].split("/")) if row["tool"] else None
            rows.append(row)
    return rows


def _matches(row, event, payload):
    matcher = row.get("matcher", "")
    if event not in TOOL_EVENTS or matcher in ("", "*"):
        return True
    tool = payload.get("tool_name") if isinstance(payload, dict) else None
    if not isinstance(tool, str) or not isinstance(matcher, str):
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return True


# ---------------------------------------------------------------------------
# Judging one handler's answer.
# ---------------------------------------------------------------------------

def is_denied(rc, out):
    """A refusal as Claude Code reads one: exit 2, or a permissionDecision deny
    object, or a decision block object, on any line of stdout."""
    if rc == 2:
        return True
    for blob in [out] + out.splitlines():
        try:
            doc = json.loads(blob)
        except ValueError:
            continue
        if isinstance(doc, dict):
            spec = doc.get("hookSpecificOutput")
            if isinstance(spec, dict) and spec.get("permissionDecision") == DENY:
                return True
            if doc.get("decision") == "block":
                return True
    return False


def _guard_name(row):
    return os.path.basename(row.get("tool") or row.get("unreadable") or "?")


def _raised(row, why):
    """What a handler that raised answers. A deny class guard refuses the call; an
    advisory one answers nothing and never blocks."""
    guard = _guard_name(row)
    if handler_class(row) == DENY:
        msg = ("brother_hook: guard %s raised while judging this call (%s), so the call is "
               "refused: a guard that fails must not fail open. %s\n" % (guard, why, _off_switch(guard)))
        return {"rc": 2, "out": "", "err": msg, "raised": True, "denied": True, "guard": guard}
    return {"rc": 0, "out": "", "err": "", "raised": True, "denied": False, "guard": guard}


def _answer(row, rc, out, err):
    """An exit status that is neither allow (0) nor deny (2) is a crash: a hook
    process that dies on a traceback exits 1, which Claude Code reads as allow."""
    if rc not in (0, 2):
        return _raised(row, "exit status %s" % rc)
    return {"rc": rc, "out": out, "err": err, "raised": False,
            "denied": is_denied(rc, out), "guard": _guard_name(row)}


def _should_yield(row, event):
    """hook_guard's double fire rule, asked per handler. Any doubt is RUN."""
    try:
        import hook_guard as guard  # beside this file once installed
        command = ["python3", row["path"]] + list(row.get("args", []))
        return bool(guard.should_yield(row.get("product", ""), event, row.get("matcher", ""),
                                       command, os.environ.get("CLAUDE_PLUGIN_ROOT"),
                                       guard.config_dir(), guard.project_dir()))
    except BaseException:  # noqa: BLE001 - failing to ask means RUN
        return False


# ---------------------------------------------------------------------------
# Running a handler: in this process, or as its own.
# ---------------------------------------------------------------------------

class RowDeadline(BaseException):
    """Raised inside a handler by the interval timer. A BaseException so a handler's own
    `except Exception` cannot swallow the deadline."""


class LoadError(Exception):
    """The handler could not be loaded in this process: an environmental fault
    that sends this one handler to the spawn path."""


def _text_stream(data=b""):
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace",
                            newline="", write_through=True)


def _exit_status(code):
    if code is None:
        return 0, ""
    if isinstance(code, int):
        return code, ""
    return 1, "%s\n" % (code,)


def run_in_process(path, args, raw, seconds=None):
    """Execute the script at `path` as __main__ with `args` and `raw` on stdin;
    returns (rc, stdout, stderr). Raises LoadError for anything that stops the
    script from loading or importing, RowDeadline when it is still running after
    `seconds` (None: no deadline), and lets any other exception through (the
    handler raised)."""
    name = "brother_hook_handler"
    try:
        code = importlib.machinery.SourceFileLoader(name, path).get_code(name)
    except (ImportError, SyntaxError, OSError, ValueError) as exc:
        raise LoadError("%s: %s" % (type(exc).__name__, exc))
    stdin, out, err = _text_stream(raw.encode("utf-8")), _text_stream(), _text_stream()
    module = types.ModuleType("__main__")
    module.__file__ = path
    saved = (sys.argv, sys.path[:], sys.stdin, sys.stdout, sys.stderr, os.getcwd(),
             sys.modules.get("__main__"), set(sys.modules))
    sys.argv, sys.stdin, sys.stdout, sys.stderr = [path] + list(args), stdin, out, err
    sys.path.insert(0, os.path.dirname(path))
    sys.modules["__main__"] = module
    rc, tail, failure = 0, "", None
    live, old_alarm = [False], None

    def fire(signum, frame):
        if live[0]:  # a timer that fires after the handler returned does nothing
            raise RowDeadline()
    try:
        if seconds is not None:
            old_alarm = signal.signal(signal.SIGALRM, fire)
            signal.setitimer(signal.ITIMER_REAL, seconds)
        live[0] = True
        try:
            exec(code, module.__dict__)
        finally:
            live[0] = False
    except SystemExit as exc:
        rc, tail = _exit_status(exc.code)
    except (ImportError, SyntaxError) as exc:
        failure = LoadError("%s: %s" % (type(exc).__name__, exc))
    except RowDeadline as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - the handler raised
        failure = exc
    finally:
        if seconds is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_alarm)
        out.flush()
        err.flush()
        got_out = out.buffer.getvalue().decode("utf-8", "replace")
        got_err = err.buffer.getvalue().decode("utf-8", "replace")
        sys.argv, _path, sys.stdin, sys.stdout, sys.stderr, cwd, was_main, before = saved
        sys.path[:] = _path
        try:
            os.chdir(cwd)
        except OSError:
            pass
        if was_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = was_main
        # Modules the handler imported from the mirror are dropped, so two products
        # that each ship a module of the same name never see each other's.
        for mod_name in set(sys.modules) - before:
            origin = getattr(sys.modules.get(mod_name), "__file__", None)
            if isinstance(origin, str) and os.path.abspath(origin).startswith(HOOKS_DIR + os.sep):
                sys.modules.pop(mod_name, None)
    if failure is not None:
        raise failure
    return rc, got_out, got_err + tail


def _valid_timeout(row):
    t = row.get("timeout")
    return t if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0 else None


def spawn_all(rows, raw, until=None):
    """Every row as its own process, started together (Claude Code ran them in
    parallel) and collected in table order. Each is waited for at most its own
    timeout (DEFAULT_TIMEOUT_S when it names none) and never past `until`, a
    time.monotonic() value; a process still running then is killed and the row has
    raised."""
    started = []
    for row in rows:
        if not row.get("path") or not os.path.isfile(row["path"]):
            started.append((row, None, "the script is missing"))
            continue
        try:
            proc = subprocess.Popen([sys.executable, row["path"]] + [str(a) for a in row.get("args", [])],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True)
            started.append((row, proc, ""))
        except OSError as exc:
            started.append((row, None, type(exc).__name__))
    results = []
    for row, proc, why in started:
        if proc is None:
            results.append(_raised(row, why))
            continue
        timeout = _valid_timeout(row) or DEFAULT_TIMEOUT_S
        if until is not None:
            timeout = max(0.05, min(timeout, until - time.monotonic()))
        try:
            out, err = proc.communicate(raw, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            results.append(_raised(row, "timed out after %.1fs" % timeout))
            continue
        except (OSError, ValueError) as exc:
            results.append(_raised(row, type(exc).__name__))
            continue
        results.append(_answer(row, proc.returncode, out, err))
    return results


def run_row(row, raw, in_process, until=None):
    """One handler's answer. In process when it loads; as its own process when it
    does not; a raise by the handler itself is judged by its class. `until` is the
    time.monotonic() value the event's budget ends at. In process the row's deadline
    is its own timeout or what is left of the budget, whichever is sooner; a row that
    misses it is interrupted, then a deny row is run again as its own process under a
    timeout (an advisory row is dropped: running it again can only cost time), and a
    row cut by the budget itself answers with cut="budget"."""
    if not row.get("path") or not os.path.isfile(row["path"]):
        return _raised(row, "its hooks.json is unreadable" if row.get("unreadable") else
                       "the script is missing or its command is not a runnable tool")
    if in_process:
        own = _valid_timeout(row)
        left = None if until is None else until - time.monotonic()
        seconds = own if left is None else (left if own is None else min(own, left))
        try:
            rc, out, err = run_in_process(row["path"], row.get("args", []), raw, seconds)
            return _answer(row, rc, out, err)
        except LoadError:
            pass  # environmental: this one handler goes to the spawn path
        except RowDeadline:
            if own is None or (left is not None and left <= own):
                answer = _raised(row, "it used up the event budget")
                answer["cut"] = "budget"
                return answer
            if handler_class(row) != DENY:
                answer = _raised(row, "it missed its %.1fs deadline" % own)
                answer["err"] = "brother_hook: advisory %s skipped, it missed its deadline\n" % answer["guard"]
                return answer
            # fall through: a deny row that missed its own deadline gets one more try, as a process
        except Exception as exc:  # noqa: BLE001 - the handler raised
            return _raised(row, type(exc).__name__)
    return spawn_all([row], raw, until)[0]


# ---------------------------------------------------------------------------
# Merging the answers of one event.
# ---------------------------------------------------------------------------

_RANK = {"deny": 3, "ask": 2, "allow": 1}


def _merge_json(objs):
    merged = {}
    for obj in objs:
        for key, value in obj.items():
            have = merged.get(key)
            if key == "hookSpecificOutput" and isinstance(value, dict) and isinstance(have, dict):
                for k2, v2 in value.items():
                    if k2 == "additionalContext" and isinstance(v2, str) and isinstance(have.get(k2), str):
                        have[k2] = have[k2] + "\n" + v2
                    elif k2 == "permissionDecision" and k2 in have:
                        if _RANK.get(v2, 0) > _RANK.get(have[k2], 0):
                            have[k2] = v2
                    else:
                        have.setdefault(k2, v2)
            elif key == "systemMessage" and isinstance(value, str) and isinstance(have, str):
                merged[key] = have + "\n" + value
            else:
                merged.setdefault(key, value)
    return merged


def _merge_out(outs):
    outs = [o for o in outs if o.strip()]
    if len(outs) <= 1:
        return outs[0] if outs else ""
    objs = []
    for o in outs:
        try:
            doc = json.loads(o)
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            return "\n".join(x.rstrip("\n") for x in outs) + "\n"
        objs.append(doc)
    return json.dumps(_merge_json(objs)) + "\n"


def merge(results):
    """(rc, stdout, stderr) for the event: deny wins, otherwise the outputs merge."""
    results = [r for r in results if r]
    denied = [r for r in results if r["denied"]]
    if denied:
        err = "".join(r["err"] for r in denied)
        if any(r["rc"] == 2 for r in denied):
            return 2, "", err
        return 0, denied[0]["out"], err
    return 0, _merge_out([r["out"] for r in results]), "".join(r["err"] for r in results)


def event_budget(rows):
    """Seconds an event may spend before it answers for itself: BUDGET_FRACTION of the
    largest timeout any of its rows names (DEFAULT_TIMEOUT_S when none does)."""
    named = [_valid_timeout(r) for r in rows if _valid_timeout(r)]
    return BUDGET_FRACTION * (max(named) if named else DEFAULT_TIMEOUT_S)


def _can_time_out():
    return (hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")
            and threading.current_thread() is threading.main_thread())


def run_event(event, payload, raw, rows, in_process):
    """Every matching row's answer merged into (rc, stdout, stderr). Deny class rows
    run first; the merge still reads the answers in table order."""
    rows = [r for r in rows if _matches(r, event, payload)]
    live = [r for r in rows if not (r.get("path") and _should_yield(r, event))
            and not _advisory_skipped(r, payload)]
    until = time.monotonic() + event_budget(rows)
    if not (in_process and _can_time_out()):
        return merge(settle(event, payload, spawn_all(live, raw, until)))
    order = sorted(range(len(live)), key=lambda i: handler_class(live[i]) != DENY)
    results, eater = [None] * len(live), None
    for pos, i in enumerate(order):
        if until - time.monotonic() <= 0:
            if any(handler_class(live[j]) == DENY for j in order[pos:]):
                guard = _guard_name(eater or live[i])
                results.append({"rc": 2, "out": "", "raised": True, "denied": True, "guard": guard,
                                "err": "brother_hook: the event budget ran out before every deny guard had "
                                       "answered (it was used up by %s), so the call is refused\n" % guard})
            break
        results[i] = run_row(live[i], raw, True, until)
        if results[i].get("cut") == "budget":
            eater = live[i]
    return merge(settle(event, payload, results))


def dispatch(event, payload, table):
    """(exit code, output) for one event judged in this process from `table` (a
    load_table result). An event the table does not hold has no handler here and
    answers (0, ""); main() is what finds such an event's guards again. Handler
    stderr goes to this process's stderr, since the pair carries stdout only."""
    rc, out, err = run_event(event, payload, json.dumps(payload), table.get(event, []), True)
    if err:
        sys.stderr.write(err)
    return rc, out


def _select(event, spawn_forced):
    """(rows, in_process): the table's rows, run in this process, when the table
    is readable and holds the event; otherwise the mirrored product hooks.json
    files, always spawned."""
    rows = None
    if os.environ.get("HOME"):
        try:
            rows = load_table(os.path.join(HOOKS_DIR, MANIFEST_NAME)).get(event)
        except (OSError, ValueError, RecursionError):
            rows = None
    if rows is None:
        return mirror_rows(event), False
    return rows, not spawn_forced


# ---------------------------------------------------------------------------
# HD1.d: two raises park the call; only a person outside the session answers.
#
# A deny class guard that RAISES is counted per (session, guard, sha256 of the
# input). The first raise refuses naming the guard and its off switch; the
# second writes one park file under ~/.brother/park/ (O_EXCL) and refuses with
# its absolute path and the one answer command; every later raise on that input
# refuses pointing at the same park. Nothing here ever allows a call: a park is
# exit 2, and an answered park stays a refusal while the guard still raises.
# An advisory guard that raises twice in a session is skipped for the rest of
# that session, with one visible line. Every deny and every park appends one
# row (ts, action, guard, rule, never the input) to ~/.brother/guard-ledger.jsonl,
# the ledger proof_card.guard_row counts.
#
# THE BOUNDARY `answered` rests on: the answer file is written only by
# `--answer`, which refuses unless stdin AND stdout are a terminal, and the
# session can write neither ~/.brother/park/ by Bash (sbe_bash_write_guard
# refuses it) nor by Write (the fence). A session that reaches a pseudo terminal
# through a launcher the guard does not see is outside this boundary.
# ---------------------------------------------------------------------------

ANSWER_FLAG = "--answer"
LEDGER_NAME = "guard-ledger.jsonl"
#: docs/limits.md "How to switch each guard off", by guard script.
OFF_SWITCHES = {"sbe_bash_write_guard.py": "BROTHERSBE_BASH_GUARD_OFF=1",
                "sbe_fence_hook.py": "BROTHERSBE_FENCE_HOOK_OFF=1",
                "sbe_authority_hook.py": "BROTHERSBE_AUTHORITY_HOOK_OFF=1"}
_ADVISORY_KEY = "advisory-session"


def _off_switch(guard):
    switch = OFF_SWITCHES.get(guard)
    if switch:
        return "Its off switch is %s (docs/limits.md)." % switch
    return "Its off switch is the one docs/limits.md names for it (none is listed for %s)." % guard


def _sha(data):
    """A digest of any text, lone surrogates included: a payload the host could not decode must still be counted,
    never crash the bookkeeping that keeps a refusal refused."""
    if not isinstance(data, bytes):
        data = str(data).encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()


def _brother_dir():
    home = os.environ.get("HOME")
    return os.path.join(home, ".brother") if home else None


def _park_dir():
    base = _brother_dir()
    return os.path.join(base, "park") if base else None


def _input_text(payload):
    """The judged input: a Bash command, else the whole tool_input."""
    tin = payload.get("tool_input") if isinstance(payload, dict) else None
    cmd = tin.get("command") if isinstance(tin, dict) else None
    return cmd if isinstance(cmd, str) else json.dumps(tin, sort_keys=True, default=str)


def _session(payload):
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    return sid if isinstance(sid, str) else ""


def _count_file(session, guard, digest, park_dir):
    """The raise counter: beside the park folder, never in it, so a count is not a park."""
    key = _sha("\0".join((session, guard, digest)))
    return os.path.join(os.path.dirname(os.path.abspath(park_dir)), "raises", key)


def raise_count(session, guard, digest, park_dir, bump=True):
    """Records one raise (unless bump is False) and answers the count so far. One byte
    is appended per raise (O_APPEND, so two concurrent raises both count). A counter
    that cannot be written answers 1: the call is refused, it is only not parked."""
    path = _count_file(session, guard, digest, park_dir)
    try:
        if bump:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, b"x")
            finally:
                os.close(fd)
        return os.path.getsize(path)
    except OSError:
        return 1 if bump else 0


def answer_command(park_file):
    return "python3 %s %s %s" % (shlex.quote(os.path.abspath(__file__)), ANSWER_FLAG,
                                 shlex.quote(park_file))


def park(session, guard, command, park_dir):
    """The absolute path of the park file for (session, guard, input), written once with
    O_EXCL; a park that already exists is returned as it is (it stays parked)."""
    park_dir = os.path.abspath(park_dir)
    digest = _sha(command)
    name = "%s-%s-%s.park" % (re.sub(r"[^A-Za-z0-9_.\-]", "_", guard)[:60] or "guard",
                              digest[:16], _sha(session)[:8])
    path = os.path.join(park_dir, name)
    os.makedirs(park_dir, mode=0o700, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    doc = {"version": 1, "ts": time.time(), "session": session, "guard": guard,
           "input_sha256": digest, "input": command, "answer": answer_command(path)}
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=1) + "\n")
    return path


def _answer_file(park_file):
    return os.path.abspath(park_file) + ".answer.json"


def answered(park_file):
    """True only for the answer `--answer` writes: a JSON object naming this park, the
    sha256 of the park file as it reads NOW, a /dev/ terminal and an answerer. Anything
    else, including any file the session writes beside the park, reads False."""
    try:
        with open(park_file, "rb") as fh:
            digest = _sha(fh.read())
        with open(_answer_file(park_file), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError):
        return False
    return (isinstance(doc, dict) and doc.get("park") == os.path.abspath(park_file)
            and doc.get("park_sha256") == digest
            and isinstance(doc.get("tty"), str) and doc["tty"].startswith("/dev/")
            and isinstance(doc.get("answerer"), str) and bool(doc["answerer"].strip()))


def answer_main(park_file):
    """`--answer <park file>`: a person, at a terminal, answers a park."""
    if not (os.isatty(0) and os.isatty(1)):
        sys.stderr.write("brother_hook: refused, a park is answered by a person at a terminal, "
                         "not by a session (stdin and stdout must both be a terminal)\n")
        return 2
    park_file = os.path.abspath(park_file)
    try:
        with open(park_file, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        sys.stderr.write("brother_hook: cannot read %s (%s)\n" % (park_file, type(exc).__name__))
        return 2
    sys.stdout.write(body.decode("utf-8", "replace") + "\nType yes to answer this park: ")
    sys.stdout.flush()
    if sys.stdin.readline().strip().lower() != "yes":
        sys.stdout.write("not answered\n")
        return 1
    doc = {"park": park_file, "park_sha256": _sha(body), "tty": os.ttyname(0),
           "answerer": getpass.getuser(), "ts": time.time()}
    try:
        fd = os.open(_answer_file(park_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        sys.stderr.write("brother_hook: cannot write the answer (%s)\n" % type(exc).__name__)
        return 2
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc) + "\n")
    sys.stdout.write("answered: %s\n" % _answer_file(park_file))
    return 0


def _ledger(event, action, guard, rule):
    """One row, never the input. A ledger that cannot be written changes no verdict."""
    base = _brother_dir()
    if not base:
        return
    row = {"ts": time.time(), "event": event, "action": action, "guard": guard, "rule": rule}
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(base, LEDGER_NAME), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass


def _advisory_skipped(row, payload):
    park_dir = _park_dir()
    return bool(park_dir) and handler_class(row) == ADVISORY and raise_count(
        _session(payload), _guard_name(row), _ADVISORY_KEY, park_dir, bump=False) >= 2


def settle(event, payload, results):
    """Counts the raises among `results`, parks a deny class guard's second raise on
    one input, skips an advisory guard after its second raise in the session, and
    ledgers every deny and park. A guard counts once per call (the budget's own
    refusal names the row that ate it, a second result for the same guard). Returns
    `results`, edited in place.

    BOOKKEEPING NEVER CHANGES A VERDICT (security review of the HD1.d merge, 2026-10-30): a crash
    in the park or ledger code must not turn a deny into a dispatcher crash the host reads as
    allow. Any exception here leaves every result as the guards answered it and says so on the
    denied ones."""
    try:
        return _settle(event, payload, results)
    except Exception as exc:  # noqa: BLE001 - the verdicts stand, the bookkeeping failed
        for r in results:
            if r and r.get("denied"):
                r["err"] = (r.get("err") or "") + ("brother_hook: park bookkeeping failed (%s); the call stays "
                                                   "refused\n" % type(exc).__name__)
        return results


def _settle(event, payload, results):
    park_dir, session, counted = _park_dir(), _session(payload), set()
    for r in results:
        if not r:
            continue
        if r.get("raised") and park_dir and r.get("guard") not in counted:
            counted.add(r.get("guard"))
            if r.get("denied"):
                text = _input_text(payload)
                if raise_count(session, r["guard"], _sha(text), park_dir) >= 2:
                    try:
                        path = park(session, r["guard"], text, park_dir)
                    except OSError as exc:
                        r["err"] += "brother_hook: the park could not be written (%s)\n" % type(exc).__name__
                    else:
                        r["parked"] = True
                        state = ("it was answered, and the guard still raises, so it stays refused"
                                 if answered(path) else "it waits for a person")
                        r["err"] = ("brother_hook: guard %s raised twice on this input, so the call is "
                                    "PARKED (exit 2, never allowed); %s. Park file: %s\n"
                                    "A person at a terminal answers it with:\n  %s\n"
                                    % (r["guard"], state, path, answer_command(path)))
            elif raise_count(session, r["guard"], _ADVISORY_KEY, park_dir) == 2:
                r["err"] += ("brother_hook: advisory %s raised twice in this session, so it is "
                             "skipped for the rest of the session\n" % r["guard"])
        if r.get("denied"):
            rule = ("raise.twice" if r.get("parked") else "raise" if r.get("raised")
                    else "exit2" if r.get("rc") == 2 else "permission.deny")
            _ledger(event, "park" if r.get("parked") else "deny", r.get("guard", "?"), rule)
    return results


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == ANSWER_FLAG:
        return answer_main(argv[1])
    if len(argv) != 1 or not _EVENT_RE.match(argv[0]):
        sys.stderr.write("brother_hook: usage: brother_hook.py <Event>, payload on stdin\n")
        return 2
    event = argv[0]
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    try:
        rows, in_process = _select(event, os.environ.get(SPAWN_ENV) == SPAWN)
        rc, out, err = run_event(event, payload, raw, rows, in_process)
    except Exception as exc:  # noqa: BLE001 - a dispatcher fault must not become silence
        try:
            rc, out, err = run_event(event, payload, raw, mirror_rows(event), False)
        except Exception:  # noqa: BLE001
            sys.stderr.write("brother_hook: could not judge %s (%s)\n" % (event, type(exc).__name__))
            return 2 if event in BLOCKING_EVENTS else 1
    if out:
        sys.stdout.write(out)
        sys.stdout.flush()
    if err:
        sys.stderr.write(err)
    return rc


if __name__ == "__main__":
    sys.exit(main())
