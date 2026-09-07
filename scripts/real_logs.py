#!/usr/bin/env python3
"""real_logs.py: nothing that runs as a test or a battery may grow a real
machine log (row M3, the 2026-09-07 reflection).

WHAT HAPPENED. On 2026-09-06, hook test suites (PR 458's new tests and,
separately, main's own older suite) reached the founder's actual
~/.claude/hook-outcomes.jsonl through the hook's own default config dir
instead of an isolated one: three test runs wrote 36 fixture rows, then
main's unisolated suite wrote six more. A detector then counted those 42
fixture rows as 18 prevented repeats -- a real machine log grew because a
TEST ran, and nothing was watching the log itself to say so.

THE FIX. This module resolves the three real machine logs the estate's own
hooks write, exactly the way each hook resolves them (its own env var first,
falling back to its own default), snapshots their size/mtime/count, and
compares two snapshots. `check_all.sh` snapshots at battery start and
compares at the end (check "real-logs-unchanged"); every hook test suite
listed below snapshots in setUpModule and asserts unchanged in
tearDownModule, so a test that reaches a real path without redirecting its
own env var fails the suite that let it happen, in the same run, rather than
shipping silently again.

THE THREE PATHS, each resolved from the writing hook's own code rather than
a literal ~/.claude:

  hook_outcomes_path()      products/brothermode/tools/vault_recall_hook.py's
                             OUTCOMES: BM_HOOK_OUTCOMES if set, else
                             brother_paths.config_dir()/hook-outcomes.jsonl.
                             The three-line computation is copied verbatim
                             from that module's own OUTCOMES assignment
                             rather than imported, so importing this module
                             never pulls in the hook's full vault machinery
                             for the sake of one path string.

  repeat_guard_state_dir()  tools/repeat-guard/repeat_guard.py's STATE_DIR:
                             pathlib.Path.home() / ".claude" / "repeat-guard".
                             repeat_guard.py carries no dedicated env
                             override for this; its own test suite isolates
                             by setting HOME for the whole subprocess, which
                             is exactly the seam Path.home() reads
                             (os.path.expanduser("~") consults HOME on
                             POSIX). Re-resolved at call time, never cached,
                             so a HOME override made after this module first
                             imported is still seen.

  attempt_ledger_path()     scripts/attempt_ledger.py's STORE: the
                             ATTEMPT_LEDGER env var if set, else
                             brother_paths.config_path("attempt-ledger",
                             "attempts.jsonl") via that module's own
                             _default_store(). Called fresh every time
                             rather than reading the module's STORE constant,
                             because STORE is a default bound once at import
                             time -- the exact trap a repeat-guard lesson on
                             this machine already names -- and would not see
                             an ATTEMPT_LEDGER set after the first import.

A "grew" path is one whose byte size or file count increased between two
snapshots. A missing path (absent before, after, or both) is NO-DATA, never
a pass: a check that never saw the file cannot vouch for it.

Proving command: python3 scripts/test_real_logs.py
"""
import json
import os
import pathlib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
import attempt_ledger  # noqa: E402

NODATA = "NO-DATA"


def hook_outcomes_path():
    """vault_recall_hook.py's OUTCOMES, replicated (see module docstring)."""
    return os.environ.get("BM_HOOK_OUTCOMES") or os.path.join(
        brother_paths.config_dir(), "hook-outcomes.jsonl")


def repeat_guard_state_dir():
    """repeat_guard.py's STATE_DIR, re-read live so a HOME override is seen."""
    return pathlib.Path.home() / ".claude" / "repeat-guard"


def attempt_ledger_path():
    """attempt_ledger.py's STORE, re-resolved live (see module docstring)."""
    return os.environ.get("ATTEMPT_LEDGER") or attempt_ledger._default_store()


#: name -> zero-arg resolver, each called fresh at snapshot time so an env
#: var changed between two snapshots (a test isolating itself) is honored.
REAL_LOGS = {
    "hook_outcomes": hook_outcomes_path,
    "repeat_guard_state": repeat_guard_state_dir,
    "attempt_ledger": attempt_ledger_path,
}

#: repeat_guard_state is the whole ~/.claude/repeat-guard directory, and
#: EVERY live session's hook appends to it on every tool call -- not only
#: this process's own. On a shared machine it can grow during any battery
#: or suite run with zero tool calls of its own (measured 2026-09-07: a
#: standalone snapshot loop with no tool calls of its own still saw it
#: grow), so its growth is ambient noise, never proof that the run under
#: test touched it. compare() treats growth of a name in this set as
#: advisory (WARN), never the FAIL verdict; only a log outside this set
#: (hook_outcomes, attempt_ledger) can fail the check.
SHARED_BY_LIVE_SESSIONS = frozenset({"repeat_guard_state"})


def _dir_stat(path):
    total = 0
    count = 0
    latest = None
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            total += st.st_size
            count += 1
            if latest is None or st.st_mtime > latest:
                latest = st.st_mtime
    return total, count, latest


def _snapshot_one(path):
    p = str(path)
    if not os.path.exists(p):
        return {"path": p, "exists": False}
    if os.path.isdir(p):
        size, count, mtime = _dir_stat(p)
        return {"path": p, "exists": True, "size": size, "count": count,
                "mtime": mtime}
    st = os.stat(p)
    return {"path": p, "exists": True, "size": st.st_size, "count": 1,
            "mtime": st.st_mtime}


def snapshot():
    """{name: {"path", "exists", "size", "count", "mtime"}} for every real
    log this module knows about, resolved fresh from each hook's own env var
    seam."""
    return {name: _snapshot_one(resolver()) for name, resolver in REAL_LOGS.items()}


#: Alias read more naturally from a test's setUpModule.
snapshot_for_tests = snapshot


def compare(before, after=None):
    """("PASS", []) when nothing grew; ("FAIL", [(name, path, b_size,
    a_size, b_count, a_count), ...]) naming every path that grew, DECISIVE
    entries (outside SHARED_BY_LIVE_SESSIONS) first and advisory ones after,
    when at least one decisive path grew; ("WARN", [...]) with only the
    advisory entries when the sole growth is in SHARED_BY_LIVE_SESSIONS
    (ambient, not proof of anything this run did); (NO-DATA, [name, ...])
    naming every path missing before or after, when nothing grew. Growth of
    either kind takes precedence over NO-DATA, and FAIL takes precedence
    over WARN."""
    after = snapshot() if after is None else after
    grown_decisive = []
    grown_advisory = []
    missing = []
    for name in REAL_LOGS:
        b = before.get(name) or {"exists": False}
        a = after.get(name) or {"exists": False}
        if not b.get("exists") or not a.get("exists"):
            missing.append(name)
            continue
        b_size, a_size = b.get("size", 0), a.get("size", 0)
        b_count, a_count = b.get("count", 0), a.get("count", 0)
        if a_size > b_size or a_count > b_count:
            entry = (name, a.get("path"), b_size, a_size, b_count, a_count)
            if name in SHARED_BY_LIVE_SESSIONS:
                grown_advisory.append(entry)
            else:
                grown_decisive.append(entry)
    if grown_decisive:
        return "FAIL", grown_decisive + grown_advisory
    if grown_advisory:
        return "WARN", grown_advisory
    if missing:
        return NODATA, missing
    return "PASS", []


def assert_unchanged(before, context=""):
    """Raise AssertionError if any real machine log grew since `before` (a
    snapshot() dict), but only for a "FAIL" verdict. A missing path is not a
    failure here: many machines never had a repeat-guard state dir or
    attempt ledger before a hook ran once, and compare()'s own CLI already
    treats NO-DATA as distinct from FAIL. Nor is a "WARN" verdict a failure:
    growth confined to SHARED_BY_LIVE_SESSIONS (repeat_guard_state) is every
    live session's hook writing to a directory this test never touched, so
    it never fails a suite. Call from a test module's tearDownModule,
    paired with snapshot_for_tests() in setUpModule."""
    verdict, detail = compare(before)
    if verdict != "FAIL":
        return
    lines = ["a real machine log grew during %s:" % (context or "this test run")]
    for name, path, b_size, a_size, b_count, a_count in detail:
        lines.append("  %s (%s): size %d -> %d bytes, count %d -> %d"
                      % (name, path, b_size, a_size, b_count, a_count))
    raise AssertionError("\n".join(lines))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("snapshot", "compare"):
        print("usage: real_logs.py snapshot | compare BEFORE.json", file=sys.stderr)
        return 2
    if argv[0] == "snapshot":
        print(json.dumps(snapshot(), indent=2, sort_keys=True))
        return 0
    if len(argv) < 2:
        print("real_logs.py compare: needs a BEFORE.json path", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            before = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: could not read %s: %s" % (NODATA, argv[1], exc), file=sys.stderr)
        return 2
    verdict, detail = compare(before)
    if verdict == "PASS":
        print("PASS: no real machine log grew (%d checked)" % len(REAL_LOGS))
        return 0
    if verdict == "WARN":
        for name, path, b_size, a_size, b_count, a_count in detail:
            print("WARN: %s (%s) grew: size %d -> %d bytes, count %d -> %d, "
                  "written by every live session's hook, not a verdict"
                  % (name, path, b_size, a_size, b_count, a_count))
        print("PASS: no decisive real machine log grew (%d advisory)"
              % len(detail))
        return 0
    if verdict == "FAIL":
        for name, path, b_size, a_size, b_count, a_count in detail:
            if name in SHARED_BY_LIVE_SESSIONS:
                print("WARN: %s (%s) grew: size %d -> %d bytes, count %d -> %d, "
                      "written by every live session's hook, not a verdict"
                      % (name, path, b_size, a_size, b_count, a_count))
            else:
                print("FAIL: %s (%s) grew: size %d -> %d bytes, count %d -> %d"
                      % (name, path, b_size, a_size, b_count, a_count))
        return 1
    for name in detail:
        print("%s: %s does not exist (%s)" % (NODATA, name, REAL_LOGS[name]()))
    return 2


if __name__ == "__main__":
    sys.exit(main())
