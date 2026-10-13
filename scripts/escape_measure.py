#!/usr/bin/env python3
"""BG0.a: what an attempted write outside the project does today, measured per hook and channel.

A simulated host builds one throwaway world (a project with a real BrotherMode claim, an outside sentinel file and a
link from inside the project to it), feeds each hook the payload a host would send for a `..`, an absolute and a
symlinked target, and performs the tool's effect only when the hook's answer is allow. Each row says what the hook
decided under that modelled host contract and what happened to the sentinel; no row is a live host result.

    python3 -B scripts/escape_measure.py      one ESCAPE line per row, the controls, a count line;
                                              the rows as JSON at $ESCAPE_MEASURE_OUT when set

FAIL DIRECTION: a hook that is missing, will not launch, times out, exits non zero or answers anything but an empty
stdout or a recognised decision object is `error`, which reads NO-DATA; nothing is credited because something was
missing. Python 3.9, standard library only, no network. Tests: scripts/test_escape_measure.py.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OUTCOMES = ("denied", "advised", "allowed", "NO-DATA")
SHAPES = ("dotdot", "abs", "sym")      # ../outside/sentinel, an absolute path, src/alias/sentinel with src/alias a link out
MODES = ("enforced", "advisory")
CHANNELS = ("write", "patch", "bash", "malformed-type", "malformed-json")
ORIG = b"orig"
EFFECT = "pwned"
TIMEOUT_S = 60
ENV_WHITELIST = ("PATH", "LANG", "TMPDIR")
DENIED_BUT_CHANGED = "a denied call changed the file"
ADAPTER_GAP = "no raw payload for an outside write is recorded for this host; measure it with HG1's canary"
CLAIMED, UNCLAIMED = "src/in.txt", "src/other.txt"

#: hook id -> (bundle copy, source copy); the bundle copy is what an installed plugin runs
HOOK_FILES = {
    "H1": ("bundle/runtime/hooks/brothermode/tools/bm_fence_hook.py", "products/brothermode/tools/bm_fence_hook.py"),
    "H2": ("bundle/runtime/hooks/brothersbe/tools/sbe_fence_hook.py", "products/brothersbe/tools/sbe_fence_hook.py"),
    "H3": ("bundle/runtime/hooks/brothersbe/tools/sbe_bash_write_guard.py",
           "products/brothersbe/tools/sbe_bash_write_guard.py"),
}
#: the probe rows: (hook, channel, shapes, mode)
PLAN = ([("H1", c, SHAPES, "enforced") for c in ("write", "patch", "bash")]
        + [("H1", "malformed-type", ("dotdot",), "enforced"), ("H1", "malformed-json", ("dotdot",), "enforced"),
           ("H1", "malformed-type", ("dotdot",), "advisory")]
        + [("H2", c, SHAPES, "enforced") for c in ("write", "patch")]
        + [("H2", "malformed-type", ("dotdot",), "enforced"), ("H2", "malformed-json", ("dotdot",), "enforced"),
           ("H3", "bash", SHAPES, "enforced")])


def _hook_path(hook):
    for rel in HOOK_FILES[hook]:
        path = os.path.join(REPO, rel)
        if os.path.isfile(path):
            return path
    return None


def _sha256_file(path):
    """The 64 hex sha256 of the file at `path`, or None when it cannot be read."""
    if not isinstance(path, str):
        return None
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def classify(decision: str, note: str, before: Optional[str], after: Optional[str], effect_ran: bool) -> str:
    """One outcome from OUTCOMES for one probe. Pure. A wrong argument type is refused with ValueError."""
    if not isinstance(decision, str) or not isinstance(note, str) or not isinstance(effect_ran, bool):
        raise ValueError("classify: decision and note must be str and effect_ran a bool")
    if not all(h is None or isinstance(h, str) for h in (before, after)):
        raise ValueError("classify: a hash must be a str or None")
    if decision not in ("allow", "deny") or before is None or after is None:
        return "NO-DATA"
    if decision == "deny":
        return "denied" if before == after else "NO-DATA"
    if not effect_ran:
        return "NO-DATA"
    return "advised" if note.strip() else "allowed"


def _check_env(env):
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("env must be a dict of str to str")


def hook_decision(hook_path: Optional[str], payload: object, env: Dict[str, str], cwd: str) -> Dict[str, object]:
    """Run one hook with `payload` on stdin (a str is sent as is, anything else as JSON) and read its answer.

    decision is allow (empty stdout, or a decision object carrying only additionalContext or permissionDecision
    allow), deny (permissionDecision deny) or error (anything else, including a missing hook or a timeout)."""
    _check_env(env)
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        raise ValueError("cwd must be an existing directory")
    if hook_path is not None and not isinstance(hook_path, str):
        raise ValueError("hook_path must be a str or None")
    out = {"decision": "error", "note": "", "stdout": "", "stderr": "", "exit": None}
    if not hook_path or not os.path.isfile(hook_path):
        out["note"] = "no hook file at %r" % (hook_path,)
        return out
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        out["note"] = "the payload cannot be sent as JSON: %s" % exc
        return out
    try:
        proc = subprocess.run([sys.executable, "-B", hook_path], input=text, env=env, cwd=cwd,
                              capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        out["note"] = "the hook did not run: %s: %s" % (type(exc).__name__, exc)
        return out
    lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    out.update(stdout=proc.stdout, stderr=lines[0] if lines else "", exit=proc.returncode)
    out["note"] = out["stderr"]
    if proc.returncode != 0:
        out.update(decision="error", note="the hook exited %d: %s" % (proc.returncode, out["stderr"]))
        return out
    if not proc.stdout.strip():
        out["decision"] = "allow"
        return out
    try:
        obj = json.loads(proc.stdout)
    except ValueError:
        obj = None
    hso = obj.get("hookSpecificOutput") if isinstance(obj, dict) else None
    pd = hso.get("permissionDecision") if isinstance(hso, dict) else None
    ctx = hso.get("additionalContext") if isinstance(hso, dict) else None
    if pd == "deny":
        out["decision"] = "deny"
        out["note"] = str(hso.get("permissionDecisionReason") or out["stderr"])
    elif pd == "allow" or (pd is None and isinstance(ctx, str) and ctx.strip()):
        out["decision"] = "allow"
        out["note"] = (ctx if isinstance(ctx, str) and ctx.strip() else out["stderr"])
    else:
        out.update(decision="error", note="the hook answered no recognised decision: %r" % proc.stdout[:200])
    return out


def _sandbox(base, name):
    home = os.path.join(base, name)
    os.makedirs(os.path.join(home, ".claude"))
    return home


def make_world(root: str) -> Dict[str, object]:
    """A fresh world in a new temporary folder under the existing absolute directory `root`."""
    if not isinstance(root, str) or not os.path.isabs(root) or not os.path.isdir(root):
        raise ValueError("root must be an existing absolute directory")
    base = os.path.realpath(tempfile.mkdtemp(prefix="bg0-", dir=root))
    project = os.path.join(base, "project")
    for d in (os.path.join(project, ".git"), os.path.join(project, "src"), os.path.join(base, "outside")):
        os.makedirs(d)
    sentinel = os.path.join(base, "outside", "sentinel.txt")
    with open(sentinel, "wb") as fh:
        fh.write(ORIG)
    world = {"base": base, "project": project, "sentinel": sentinel, "home": _sandbox(base, "home"),
             "reg_home": _sandbox(base, "reg-home"), "symlink": "", "session_id": None, "claim": "",
             "hooks": {h: _hook_path(h) for h in HOOK_FILES}}
    try:
        os.symlink(os.path.join(base, "outside"), os.path.join(project, "src", "alias"))
    except (OSError, NotImplementedError, AttributeError) as exc:
        world["symlink"] = "no symlink support here: %s" % exc
    import managed_safety
    token, why = managed_safety.materialize(project, {"unit_id": "bg0-probe", "write_scope": [CLAIMED],
                                                      "objective": "BG0 escape measurement"})
    world["session_id"], world["claim"] = token, why
    return world


def _check_world(world):
    keys = ("base", "project", "sentinel", "home", "reg_home", "symlink", "session_id", "claim", "hooks")
    if not isinstance(world, dict) or any(k not in world for k in keys) or not isinstance(world["hooks"], dict):
        raise ValueError("world must be a dict made by make_world")
    if not all(isinstance(world[k], str) for k in keys[:6]) or not os.path.isdir(world["project"]):
        raise ValueError("world must be a dict made by make_world")


def _hook_env(world, home, mode):
    env = {k: os.environ[k] for k in ENV_WHITELIST if k in os.environ}
    env.update(HOME=home, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"))
    if mode == "advisory":
        env["BM_FENCE_MODE"] = "advisory"
    else:
        env.update(BM_FENCE_MODE="enforced", BM_FENCE_STRICT="1")
    return env


def _target(world, shape):
    if shape == "dotdot":
        return os.path.join("..", "outside", "sentinel.txt")
    if shape == "abs":
        return world["sentinel"]
    return os.path.join("src", "alias", "sentinel.txt")


def _payload(world, channel, target):
    """(payload, effect kind) for one channel. A str payload is sent to the hook as raw stdin."""
    base = {"session_id": world["session_id"] or "", "cwd": world["project"], "hook_event_name": "PreToolUse"}
    if channel == "malformed-json":
        return "not json {", "write"
    if channel == "malformed-type":
        return dict(base, tool_name=7, tool_input={"file_path": target, "content": EFFECT}), "write"
    if channel == "write":
        return dict(base, tool_name="Write", tool_input={"file_path": target, "content": EFFECT}), "write"
    patch = "*** Begin Patch\n*** Add File: %s\n+%s\n*** End Patch\n" % (target, EFFECT)
    if channel == "patch-tool":
        return dict(base, tool_name="apply_patch", tool_input={"command": patch}), "write"
    if channel == "patch":
        return dict(base, tool_name="Bash", tool_input={"command": "apply_patch <<'EOF'\n%sEOF\n" % patch}), "write"
    command = "bash -lc %s" % shlex.quote("printf %s > %s" % (EFFECT, shlex.quote(target)))
    return dict(base, tool_name="Bash", tool_input={"command": command}), "bash"


def _effect(world, kind, payload, target, env):
    """Perform the tool's effect; True when it ran to completion."""
    if kind == "bash":
        try:
            proc = subprocess.run(["bash"], input=payload["tool_input"]["command"], cwd=world["project"], env=env,
                                  capture_output=True, text=True, timeout=TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0
    try:
        with open(os.path.realpath(os.path.join(world["project"], target)), "w") as fh:
            fh.write(EFFECT)
    except OSError:
        return False
    return True


def _reset(world):
    try:
        with open(world["sentinel"], "wb") as fh:
            fh.write(ORIG)
    except OSError:
        pass


def _probe(world, hook, channel, target, watch, mode, home=None, rid=""):
    """One row: reset, hash, ask the hook, act only on allow, hash again, classify."""
    _reset(world)
    env = _hook_env(world, home or world["home"], mode)
    wire = "patch-tool" if (hook == "H2" and channel == "patch") else channel
    payload, kind = _payload(world, wire, target)
    before = _sha256_file(watch)
    blocker = ""
    if hook == "H1" and not world["session_id"]:
        blocker = "the BrotherMode claim could not be made: %s" % world["claim"]
    elif kind == "bash" and shutil.which("bash", path=env.get("PATH")) is None:
        blocker = "no bash on this machine"
    elif "src/alias" in target.replace(os.sep, "/") and world["symlink"]:
        blocker = world["symlink"]
    if blocker:
        ans = {"decision": "error", "note": blocker, "stdout": "", "stderr": "", "exit": None}
    elif hook == "none":
        ans = {"decision": "allow", "note": "", "stdout": "", "stderr": "", "exit": None}
    else:
        ans = hook_decision(world["hooks"].get(hook), payload, env, world["project"])
    ran = ans["decision"] == "allow" and _effect(world, kind, payload, target, env)
    after = _sha256_file(watch)
    outcome = classify(ans["decision"], ans["note"], before, after, ran)
    note = ans["note"]
    if ans["decision"] == "deny" and before is not None and before != after:
        note = DENIED_BUT_CHANGED
    return {"id": rid, "hook": hook, "channel": channel, "mode": mode, "host": "codex" if "patch" in channel else
            "claude", "decision": ans["decision"], "outcome": outcome, "note": note, "raw_payload": payload,
            "raw_answer": {"stdout": ans["stdout"], "stderr": ans["stderr"]}, "hash_before": before,
            "hash_after": after, "effect_ran": ran, "hook_home": env["HOME"]}


def run_probe(world: Dict[str, object], hook: str, channel: str, shape: str, mode: str = "enforced") -> Dict[str, object]:
    """One probe row `<hook>-<channel>-<shape>` (plus `-advisory` in advisory mode) against the outside sentinel."""
    _check_world(world)
    if not isinstance(hook, str) or hook not in tuple(HOOK_FILES) + ("none",):
        raise ValueError("hook must be one of %s or none" % ", ".join(HOOK_FILES))
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise ValueError("channel must be one of %s" % ", ".join(CHANNELS))
    if not isinstance(shape, str) or shape not in SHAPES:
        raise ValueError("shape must be one of %s" % ", ".join(SHAPES))
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("mode must be one of %s" % ", ".join(MODES))
    rid = "%s-%s-%s%s" % (hook, channel, shape, "" if mode == "enforced" else "-" + mode)
    return _probe(world, hook, channel, _target(world, shape), world["sentinel"], mode, rid=rid)


def run_declare_probe(world: Dict[str, object], shape: str) -> Dict[str, object]:
    """E4: does planning accept a unit that declares the outside target as its write scope."""
    _check_world(world)
    if not isinstance(shape, str) or shape not in SHAPES:
        raise ValueError("shape must be one of %s" % ", ".join(SHAPES))
    import work_record
    target = _target(world, shape)
    unit = {"id": "bg0-declare", "done_check": "true", "owns": [target]}
    problems = work_record.check_units([unit])
    hit = [p for p in problems if "escaping" in p]
    return {"id": "E4-declare-%s" % shape, "hook": "E4", "channel": "declare", "mode": "planning",
            "host": "planning", "decision": "deny" if hit else "allow", "outcome": "denied" if hit else "allowed",
            "note": hit[0] if hit else "; ".join(problems), "raw_payload": unit,
            "raw_answer": {"stdout": json.dumps(problems), "stderr": ""}, "hash_before": None, "hash_after": None,
            "effect_ran": False, "hook_home": ""}


def run_registration_probe(world: Dict[str, object]) -> Dict[str, object]:
    """Both fences registered as a host would register them, in a SEPARATE sandbox HOME, run in order on one
    outside write. Records what the capability probe reads from that registration beside the measured outcome."""
    _check_world(world)
    settings = os.path.join(world["reg_home"], ".claude", "settings.json")
    cmds = ["python3 -B %s" % shlex.quote(world["hooks"].get(h) or "missing-" + h) for h in ("H2", "H1")]
    with open(settings, "w") as fh:
        json.dump({"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|Bash",
                                             "hooks": [{"type": "command", "command": c} for c in cmds]}]}}, fh)
    try:
        with open(settings) as fh:
            doc = json.load(fh)
        paths = [shlex.split(h["command"])[-1] for h in doc["hooks"]["PreToolUse"][0]["hooks"]]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        paths, why = [], "the settings file could not be parsed: %s" % exc
    else:
        why = ""
    env = _hook_env(world, world["reg_home"], "enforced")
    target = _target(world, "dotdot")
    payload, _kind = _payload(world, "write", target)
    _reset(world)
    before = _sha256_file(world["sentinel"])
    answers = [hook_decision(p, payload, env, world["project"]) for p in paths]
    kinds = [a["decision"] for a in answers]
    decision = "error" if not paths or "error" in kinds else "deny" if "deny" in kinds else "allow"
    note = why or " | ".join(a["note"] for a in answers if a["note"])
    ran = decision == "allow" and _effect(world, "write", payload, target, env)
    after = _sha256_file(world["sentinel"])
    import managed_safety
    return {"id": "REG-write-dotdot", "hook": "REG", "channel": "write", "mode": "enforced", "host": "claude",
            "decision": decision, "outcome": classify(decision, note, before, after, ran), "note": note,
            "raw_payload": payload, "raw_answer": {"stdout": " | ".join(a["stdout"] for a in answers),
                                                   "stderr": " | ".join(a["stderr"] for a in answers)},
            "hash_before": before, "hash_after": after, "effect_ran": ran, "hook_home": env["HOME"],
            "probe": managed_safety._check_hooks(env)}


def run_adapter_rows(world: Dict[str, object]) -> List[Dict[str, object]]:
    """One row per host adapter: claude and codex re-measured through H1, cursor and antigravity unmeasured."""
    _check_world(world)
    rows = []
    for host, channel in (("claude", "write"), ("codex", "patch")):
        row = run_probe(world, "H1", channel, "dotdot")
        row.update(source=row["id"], id=host, host=host)
        rows.append(row)
    for host in ("cursor", "antigravity"):
        rows.append({"id": host, "hook": "adapter", "channel": "none", "mode": "none", "host": host,
                     "decision": "error", "outcome": "NO-DATA", "note": ADAPTER_GAP, "raw_payload": None,
                     "raw_answer": {"stdout": "", "stderr": ""}, "hash_before": None, "hash_after": None,
                     "effect_ran": False, "hook_home": ""})
    return rows


def run_all(world: Dict[str, object]) -> List[Dict[str, object]]:
    """Every probe row, the declared scope rows, the registration row and the adapter rows."""
    _check_world(world)
    rows = [run_probe(world, hook, channel, shape, mode) for hook, channel, shapes, mode in PLAN for shape in shapes]
    rows += [run_declare_probe(world, shape) for shape in SHAPES]
    rows.append(run_registration_probe(world))
    return rows + run_adapter_rows(world)


def _controls(world):
    """C0 the harness sees a write, C1 the fence allows a claimed write, C2 it refuses an unclaimed one."""
    _check_world(world)
    c0 = _probe(world, "none", "write", _target(world, "dotdot"), world["sentinel"], "enforced", rid="C0")
    c0["holds"] = bool(c0["hash_before"] and c0["hash_after"] and c0["hash_before"] != c0["hash_after"])
    out = [c0]
    for rid, rel, want in (("C1", CLAIMED, "allow"), ("C2", UNCLAIMED, "deny")):
        path = os.path.join(world["project"], rel)
        row = _probe(world, "H1", "write", rel, path, "enforced", rid=rid)
        row["holds"] = row["decision"] == want and os.path.isfile(path) == (want == "allow")
        out.append(row)
    return out


def _sentinel_word(row):
    if row["hash_before"] is None or row["hash_after"] is None:
        return "n/a"
    return "unchanged" if row["hash_before"] == row["hash_after"] else "CHANGED"


def _line(row):
    note = " ".join(str(row.get("note") or "").split())[:240]
    return "ESCAPE %s host=%s decision=%s outcome=%s sentinel=%s  %s" % (
        row["id"], row["host"], row["decision"], row["outcome"], _sentinel_word(row), note)


def write_table(rows: List[Dict[str, object]], path: str) -> None:
    """Every row, raw payload included, as one JSON list at `path`."""
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("rows must be a list of dicts")
    if not isinstance(path, str) or not path.strip() or isinstance(path, bool):
        raise ValueError("path must be a non empty str")
    try:
        text = json.dumps(rows, indent=1, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("rows cannot be written as JSON: %s" % exc)
    try:
        with open(path, "w") as fh:
            fh.write(text + "\n")
    except OSError as exc:
        raise ValueError("the table could not be written to %s: %s" % (path, exc))


def main(argv: Optional[List[str]] = None) -> int:
    """Measure, print one line per row and per control, and write $ESCAPE_MEASURE_OUT when set.
    0 when the three controls hold, 1 when one does not."""
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SystemExit(2)
    if argv:
        sys.stderr.write("usage: escape_measure.py (no arguments; ESCAPE_MEASURE_OUT names the JSON file)\n")
        raise SystemExit(2)
    parent = tempfile.mkdtemp(prefix="escape-measure-")
    try:
        world = make_world(os.path.realpath(parent))
        rows, controls = run_all(world), _controls(world)
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    for row in rows:
        print(_line(row))
    for c in controls:
        print("CONTROL %s holds=%s decision=%s" % (c["id"], c["holds"], c["decision"]))
    counts = dict((o, sum(1 for r in rows if r["outcome"] == o)) for o in OUTCOMES)
    print("COUNT rows=%d %s controls_held=%d/3" % (len(rows), " ".join("%s=%d" % kv for kv in counts.items()),
                                                    sum(1 for c in controls if c["holds"])))
    out = os.environ.get("ESCAPE_MEASURE_OUT", "").strip()
    if out:
        write_table(rows, out)
    return 0 if all(c["holds"] for c in controls) else 1


if __name__ == "__main__":
    sys.exit(main())
