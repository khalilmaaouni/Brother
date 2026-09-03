#!/usr/bin/env python3
"""Drive tools/bm_fence_hook.py in BOTH modes against real fixtures and prove
the difference between them.

WHY THIS EXISTS
  bm_fence_hook.py carries two modes. Unset or BM_FENCE_MODE=advisory is the
  default and fails OPEN on every condition where the fence cannot be checked.
  BM_FENCE_MODE=enforced is meant to fail CLOSED on those same conditions.
  The enforced path was written for C-01 and reviewed, and until this file
  nothing had ever DRIVEN it: no test spawned the hook with the variable set
  and read what came back. A mode nobody has driven is a claim, not a control,
  which is the same failure class as a checklist nobody runs.

  Every condition below is taken from bm_fence_hook.py itself: the codes in
  _FAIL_REASONS, the raise sites that carry them, the unconditional denials,
  and the one code in _ALLOW_EVEN_WHEN_ENFORCED. Nothing here is invented from
  the prose that describes the hook.

HOW THE VERDICT IS READ, WHICH IS THE TRAP THIS FILE AVOIDS
  bm_fence_hook.deny_payload() returns a JSON object on STDOUT and exits 0.
  An ALLOW is the ABSENCE of stdout, also at exit 0. So the exit code is 0 in
  both directions and reading it alone would score every single refusal as an
  allow. This drill reads stdout, parses it as JSON, and looks at
  hookSpecificOutput.permissionDecision. Exit code and stderr are captured too,
  and a nonzero exit or unparseable stdout is reported as PROTOCOL, never
  silently folded into either verdict.

SAFETY
  BM_FENCE_MODE is set ONLY in the environment of the subprocesses this file
  spawns, and only for the enforced leg. Nothing here writes to any settings
  file, exports anything, or touches any checkout: every fixture is a fresh
  temporary directory that is removed at the end. Running this while other
  sessions are live changes nothing for them.

Python 3, standard library only. No em or en dashes anywhere in this file or
its output.
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# The drill lives beside the estate's other drills in scripts/, NOT inside the
# product it exercises. It was written in products/brothermode/tools/ and moved
# here 2026-09-02, because a shipping module in that tree may not import
# subprocess (SECURITY.md's no-network-calls claim is gated on exactly that) and
# a drill is a harness rather than a shipped tool. The tools it drives still live
# in the product, so the paths are resolved from the repository root.
PRODUCT_TOOLS = os.path.join(os.path.dirname(HERE), "products", "brothermode", "tools")
HOOK = os.path.join(PRODUCT_TOOLS, "bm_fence_hook.py")
STORE = os.path.join(PRODUCT_TOOLS, "bm_store.py")

#: Verdicts a leg can produce.
ALLOW = "ALLOW"
DENY = "DENY"
PROTOCOL = "PROTOCOL"

#: Environment keys that steer the hook. Every child starts with all of them
#: cleared, so an inherited value from the operator's shell can never decide a
#: leg's result, and each case sets back exactly what it means to test.
STEERING = ("BM_FENCE_MODE", "BM_FENCE_STRICT", "BM_FENCE_BATTERY",
            "BM_FENCE_SESSION_ID", "BROTHERMODE_ROOT")

MY_SESSION = "drill-session-owner"
PEER_SESSION = "drill-session-peer"


# ---------------------------------------------------------------------------
# Running the hook.
# ---------------------------------------------------------------------------

def run_hook(payload, enforced, cwd, env=None, hook=None):
    """One leg. Returns (verdict, exit_code, stdout, stderr).

    `payload` is either a dict (encoded as JSON) or raw bytes/str, so a case
    can feed stdin that is not JSON at all. `enforced` decides whether
    BM_FENCE_MODE=enforced is placed in the CHILD's environment; the advisory
    leg leaves the variable UNSET, which is the real default a machine runs in.
    """
    child = dict(os.environ)
    for k in STEERING:
        child.pop(k, None)
    if env:
        child.update(env)
    if enforced:
        child["BM_FENCE_MODE"] = "enforced"
    if isinstance(payload, (dict, list)):
        data = json.dumps(payload)
    else:
        data = payload
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        r = subprocess.run([sys.executable, hook or HOOK, "hook"],
                           input=data, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, cwd=cwd, env=child,
                           timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return PROTOCOL, -1, "", "%s: %s" % (type(e).__name__, e)
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    return read_verdict(out, r.returncode), r.returncode, out, err


def read_verdict(stdout, code):
    """The hook's decision, read the way the hook expresses it.

    Contract, from bm_fence_hook.deny_payload and cmd_hook: a DENY is a JSON
    object on stdout carrying hookSpecificOutput.permissionDecision == "deny",
    printed at exit 0. An ALLOW is empty stdout, also at exit 0. Anything else
    is a protocol break and is reported as one rather than guessed at."""
    if code != 0:
        return PROTOCOL
    if not stdout.strip():
        return ALLOW
    try:
        obj = json.loads(stdout)
    except ValueError:
        return PROTOCOL
    try:
        decision = obj["hookSpecificOutput"]["permissionDecision"]
    except (KeyError, TypeError):
        return PROTOCOL
    if decision == "deny":
        return DENY
    if decision in ("allow", "ask"):
        return ALLOW
    return PROTOCOL


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

def _tool(script, args, root, cwd):
    child = dict(os.environ)
    for k in STEERING:
        child.pop(k, None)
    child["BROTHERMODE_ROOT"] = root
    return subprocess.run([sys.executable, script] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=cwd, env=child, timeout=120)


def new_project(base, name):
    """An initialized BrotherMode project root with one file to write."""
    root = os.path.join(base, name)
    os.makedirs(root)
    with io.open(os.path.join(root, "target.txt"), "w", encoding="utf-8") as f:
        f.write("fixture\n")
    r = _tool(STORE, ["init"], root, root)
    if r.returncode != 0:
        raise RuntimeError("store init failed: %s"
                           % r.stderr.decode("utf-8", "replace")[-400:])
    return root


def label_for(root, session_id):
    """The PUBLIC fence label for a session, asked of the hook's own
    session-label verb rather than recomputed here. Minting it also creates
    the session's token file, which the identity case then corrupts."""
    r = _tool(HOOK, ["session-label", "--session-id", session_id], root, root)
    if r.returncode != 0:
        raise RuntimeError("session-label failed: %s"
                           % r.stderr.decode("utf-8", "replace")[-400:])
    return r.stdout.decode("utf-8").strip()


def claim(root, name, rel, label):
    r = _tool(STORE, ["claim", name, "--lifetime", "ephemeral",
                      "--objective", "fence drill fixture",
                      "--files", rel, "--session", label], root, root)
    if r.returncode != 0:
        raise RuntimeError("claim failed: %s"
                           % r.stderr.decode("utf-8", "replace")[-400:])


def store_file(root):
    return os.path.join(root, ".brothermode", "store.sqlite3")


def write_payload(root, session_id=MY_SESSION, rel="target.txt"):
    return {"session_id": session_id, "cwd": root,
            "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": os.path.join(root, rel)}}


# ---------------------------------------------------------------------------
# The cases. Each returns (payload, cwd, env, hook_path) or raises Skip.
# ---------------------------------------------------------------------------

class Skip(Exception):
    """A condition that could not be constructed. Reported as NO-DATA with
    what it would need. NO-DATA is never a pass."""


class Case(object):
    def __init__(self, name, condition, advisory, enforced, build, note=""):
        self.name = name
        self.condition = condition
        self.expect_advisory = advisory
        self.expect_enforced = enforced
        self.build = build
        self.note = note


def build_cases(base):
    cases = []
    add = cases.append

    # --- bad-payload, raised in cmd_hook before decide() is ever called -----
    def stdin_not_json(_):
        root = new_project(base, "p-notjson")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-notjson", "target.txt", label)
        return "this is not json at all", root, {"BROTHERMODE_ROOT": root}, None
    add(Case("stdin-not-json", "bad-payload", ALLOW, DENY, stdin_not_json))

    def stdin_empty(_):
        root = new_project(base, "p-empty")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-empty", "target.txt", label)
        return "", root, {"BROTHERMODE_ROOT": root}, None
    add(Case("stdin-empty", "bad-payload", ALLOW, DENY, stdin_empty))

    # --- bad-payload, raised inside decide() -------------------------------
    def payload_not_object(_):
        root = new_project(base, "p-array")
        return ["not", "an", "object"], root, {"BROTHERMODE_ROOT": root}, None
    add(Case("payload-not-an-object", "bad-payload", ALLOW, DENY,
             payload_not_object))

    def tool_input_not_object(_):
        root = new_project(base, "p-badinput")
        p = write_payload(root)
        p["tool_input"] = "a string, not an object"
        return p, root, {"BROTHERMODE_ROOT": root}, None
    add(Case("tool-input-not-an-object", "bad-payload", ALLOW, DENY,
             tool_input_not_object))

    def no_session_id(_):
        root = new_project(base, "p-nosid")
        p = write_payload(root)
        del p["session_id"]
        return p, root, {"BROTHERMODE_ROOT": root}, None
    add(Case("no-session-id", "no-session-id", ALLOW, DENY, no_session_id))

    def unknown_path_key(_):
        root = new_project(base, "p-nokey")
        p = write_payload(root)
        p["tool_input"] = {"destination_file": os.path.join(root, "target.txt")}
        return p, root, {"BROTHERMODE_ROOT": root}, None
    add(Case("unrecognized-path-key", "no-target-path", ALLOW, DENY,
             unknown_path_key))

    # --- store conditions ---------------------------------------------------
    def store_missing(_):
        root = os.path.join(base, "p-nostore")
        os.makedirs(os.path.join(root, ".brothermode"))
        with io.open(os.path.join(root, "target.txt"), "w",
                     encoding="utf-8") as f:
            f.write("fixture\n")
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("store-missing", "no-store", ALLOW, DENY, store_missing))

    def store_zero_bytes(_):
        root = new_project(base, "p-zero")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-zero", "target.txt", label)
        with io.open(store_file(root), "w", encoding="utf-8"):
            pass
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("store-zero-bytes", "store-unreadable/unqueryable", ALLOW, DENY,
             store_zero_bytes))

    def store_corrupt(_):
        root = new_project(base, "p-corrupt")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-corrupt", "target.txt", label)
        with open(store_file(root), "wb") as f:
            f.write(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 512)
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("store-corrupt", "store-unreadable", ALLOW, DENY, store_corrupt))

    def store_wrong_schema(_):
        root = new_project(base, "p-schema")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-schema", "target.txt", label)
        os.remove(store_file(root))
        for extra in ("-wal", "-shm"):
            side = store_file(root) + extra
            if os.path.exists(side):
                os.remove(side)
        con = sqlite3.connect(store_file(root))
        con.execute("CREATE TABLE unrelated (a INTEGER)")
        con.commit()
        con.close()
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("store-wrong-schema", "store-unreadable/unqueryable", ALLOW,
             DENY, store_wrong_schema))

    def zero_active_claims(_):
        root = new_project(base, "p-noclaims")
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("store-zero-active-claims", "no-active-claims", ALLOW, DENY,
             zero_active_claims))

    def store_unimportable(_):
        root = new_project(base, "p-noimport")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-noimport", "target.txt", label)
        lonely = os.path.join(base, "lonely-tools")
        os.makedirs(lonely)
        copy = os.path.join(lonely, "bm_fence_hook.py")
        shutil.copyfile(HOOK, copy)
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, copy)
    add(Case("store-unimportable", "store-unimportable", ALLOW, DENY,
             store_unimportable))

    # --- identity -----------------------------------------------------------
    def no_identity(_):
        root = new_project(base, "p-noid")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-noid", "target.txt", label)
        fence = os.path.join(root, ".brothermode", "fence")
        tokens = [f for f in os.listdir(fence) if f.endswith(".token")]
        if not tokens:
            raise Skip("no token file was minted under %s, so there is "
                       "nothing to corrupt; needs the session-label verb to "
                       "have created one" % fence)
        with io.open(os.path.join(fence, tokens[0]), "w",
                     encoding="utf-8") as f:
            f.write("not-a-64-character-hex-token\n")
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("session-identity-underivable", "no-identity", ALLOW, DENY,
             no_identity))

    # --- internal error -----------------------------------------------------
    def internal_error(_):
        root = new_project(base, "p-internal")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-internal", "target.txt", label)
        p = write_payload(root)
        # A cwd carrying a NUL byte. It passes the payload's own isinstance and
        # strip() checks, then blows up inside os.path.realpath under
        # resolve_root, which is an unforeseen exception rather than a
        # _FailOpen: exactly the blanket-catch path.
        p["cwd"] = root + "\x00bad"
        return (p, root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("internal-error-mid-decision", "internal-error", ALLOW, DENY,
             internal_error))

    # --- battery fence ------------------------------------------------------
    def battery_unreadable(_):
        root = new_project(base, "p-battery")
        tools_dir = os.path.join(root, "tools")
        os.makedirs(tools_dir)
        gate = load_gate()
        if gate is None:
            raise Skip("tools/test_all.py could not be imported, so the gate "
                       "lock path cannot be asked for; needs a complete tools "
                       "directory")
        lock = gate.lock_path(tools_dir)
        with io.open(lock, "w", encoding="utf-8") as f:
            f.write("holder\n")
        os.chmod(lock, 0)
        if os.access(lock, os.R_OK):
            os.remove(lock)
            raise Skip("the lock file stayed readable after chmod 000 (root, "
                       "or a filesystem without POSIX modes); needs a mode "
                       "honouring filesystem and a non-root user")
        cleanup_paths.append(lock)
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("battery-lock-unreadable", "battery-unreadable", ALLOW, DENY,
             battery_unreadable))

    # --- unconditional denials: the fence working, in BOTH modes ------------
    def foreign_fence(_):
        root = new_project(base, "p-foreign")
        label_for(root, MY_SESSION)
        peer = label_for(root, PEER_SESSION)
        claim(root, "drill-foreign", "target.txt", peer)
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("foreign-fence-conflict", "real ownership conflict", DENY, DENY,
             foreign_fence,
             "negative control: the fence's own rule, mode independent"))

    def unreadable_patch(_):
        root = new_project(base, "p-patch")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-patch", "target.txt", label)
        cmd = ("apply_patch <<'PATCH'\n"
               "*** Begin Patch\n"
               "*** End Patch\n"
               "PATCH\n")
        p = {"session_id": MY_SESSION, "cwd": root,
             "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": cmd}}
        return (p, root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("apply-patch-unreadable-envelope", "unreadable patch envelope",
             DENY, DENY, unreadable_patch,
             "unconditional deny by design, mode independent"))

    # --- positive controls: enforced mode must still ALLOW ------------------
    def own_fence(_):
        root = new_project(base, "p-own")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-own", "target.txt", label)
        return (write_payload(root), root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("own-fence-claimed-write", "legitimate claimed write", ALLOW,
             ALLOW, own_fence,
             "POSITIVE CONTROL: if this denies, enforced mode refuses "
             "everything and the whole run is NO-DATA"))

    def outside_project(_):
        outside = os.path.join(base, "outside")
        os.makedirs(outside)
        with io.open(os.path.join(outside, "target.txt"), "w",
                     encoding="utf-8") as f:
            f.write("fixture\n")
        p = {"session_id": MY_SESSION, "cwd": outside,
             "hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": os.path.join(outside, "target.txt")}}
        return (p, outside, {}, None)
    add(Case("no-root-outside-project", "no-root", ALLOW, ALLOW,
             outside_project,
             "the one code in _ALLOW_EVEN_WHEN_ENFORCED, by design"))

    def tool_name_not_string(_):
        root = new_project(base, "p-toolname")
        label = label_for(root, MY_SESSION)
        claim(root, "drill-toolname", "target.txt", label)
        p = write_payload(root)
        p["tool_name"] = 1234
        return (p, root, {"BROTHERMODE_ROOT": root}, None)
    add(Case("tool-name-not-a-string", "classified as a non-write tool",
             ALLOW, ALLOW, tool_name_not_string,
             "OBSERVED GAP: a malformed payload whose tool_name is not a "
             "string leaves decide() before any mode check, so enforced mode "
             "cannot refuse it"))

    return cases


cleanup_paths = []
_gate = []


def load_gate():
    """tools/test_all.py, imported by path the way the hook imports it, so the
    lock path this drill writes is the one the hook will look for. Never
    re-derives the lock path itself: the format has one owner."""
    if _gate:
        return _gate[0]
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "drill_test_all", os.path.join(HERE, "test_all.py"))
        if spec is None or spec.loader is None:
            _gate.append(None)
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _gate.append(mod)
        return mod
    except Exception:
        _gate.append(None)
        return None


# ---------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------

def stderr_reason(text):
    """The hook's own one-line explanation, for the detail column."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("bm_fence_hook:"):
            return s[len("bm_fence_hook:"):].strip()
    return ""


def main(argv=None):
    base = tempfile.mkdtemp(prefix="bm-fence-drill-")
    rows = []
    try:
        cases = build_cases(base)
        for case in cases:
            try:
                payload, cwd, env, hook = case.build(base)
            except Skip as e:
                rows.append((case, "NO-DATA", "NO-DATA", str(e)))
                continue
            except Exception as e:
                rows.append((case, "NO-DATA", "NO-DATA",
                             "fixture could not be built (%s: %s)"
                             % (type(e).__name__, e)))
                continue
            adv, adv_code, _adv_out, adv_err = run_hook(
                payload, False, cwd, env, hook)
            enf, enf_code, _enf_out, enf_err = run_hook(
                payload, True, cwd, env, hook)
            detail = stderr_reason(enf_err) or stderr_reason(adv_err)
            if adv_code != 0 or enf_code != 0:
                detail = ("nonzero exit (advisory %s, enforced %s) %s"
                          % (adv_code, enf_code, detail))
            rows.append((case, adv, enf, detail))
    finally:
        for p in cleanup_paths:
            try:
                os.chmod(p, 0o600)
                os.remove(p)
            except OSError:  # sbe: allow-silent best-effort unlock before teardown; shutil.rmtree(ignore_errors=True) right below still removes whatever is left
                pass
        shutil.rmtree(base, ignore_errors=True)

    print("BrotherMode fence: enforced-mode drill")
    print("hook: %s" % HOOK)
    print("advisory leg runs with BM_FENCE_MODE UNSET (the machine default); "
          "enforced leg sets it in the CHILD process only.")
    print("verdict read from stdout JSON "
          "(hookSpecificOutput.permissionDecision), never from the exit code, "
          "which is 0 for allow AND deny.")
    print("")
    head = "%-32s %-34s %-9s %-9s %s" % (
        "CONDITION", "FENCE CODE", "ADVISORY", "ENFORCED", "RESULT")
    print(head)
    print("-" * len(head))

    passes = failures = nodata = 0
    positive_control_ok = None
    for case, adv, enf, detail in rows:
        if adv == "NO-DATA":
            result = "NO-DATA"
            nodata += 1
        elif adv == case.expect_advisory and enf == case.expect_enforced:
            result = "PASS"
            passes += 1
        else:
            result = "FAIL (wanted %s/%s)" % (case.expect_advisory,
                                              case.expect_enforced)
            failures += 1
        if case.name == "own-fence-claimed-write":
            positive_control_ok = (enf == ALLOW)
        print("%-32s %-34s %-9s %-9s %s"
              % (case.name, case.condition, adv, enf, result))
        if detail:
            print("%-32s   reason: %s" % ("", detail[:160]))
        if case.note:
            print("%-32s   note: %s" % ("", case.note))

    print("")
    print("summary: %d conditions, %d PASS, %d FAIL, %d NO-DATA"
          % (len(rows), passes, failures, nodata))
    if positive_control_ok is None:
        print("VERDICT: NO-DATA. The positive control did not run, so nothing "
              "here distinguishes correct enforcement from refusing "
              "everything.")
        return 2
    if not positive_control_ok:
        print("VERDICT: NO-DATA. The positive control DENIED a properly "
              "claimed write under enforcement, so either the fixture is "
              "wrong or enforced mode refuses everything. Every other row "
              "above is uninterpretable until that is fixed.")
        return 2
    print("positive control: a properly claimed write ALLOWS under "
          "BM_FENCE_MODE=enforced, so enforcement is selective, not blanket.")
    if failures:
        print("VERDICT: FAIL. %d condition(s) did not behave as the source "
              "says they should." % failures)
        return 1
    if nodata:
        print("VERDICT: PASS with %d NO-DATA. A NO-DATA is not a pass; each "
              "one names above what it would need." % nodata)
        return 0
    print("VERDICT: PASS. Every enumerated condition fails OPEN with the mode "
          "unset and CLOSED with BM_FENCE_MODE=enforced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
