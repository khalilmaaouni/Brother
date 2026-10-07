#!/usr/bin/env python3
"""The autonomy block: the policy this run will actually obey, on the screen
the person reads before walking away, instead of in a log line nobody opens.

WHAT IS NEW HERE IS THE SCREEN, NOT THE POLICY. Every value printed is read
at call time out of the modules that already own it: scripts/autonomy_dial.py
(the A0 to A3 dial, its ACTIONS map and its seven-item A3 boundary),
products/brothermode/tools/bm_fence_hook.py (whether the single-writer fence
refuses a cross-fence write or merely records it) and scripts/scope_audit.py
(the verdicts the after-the-fact audit can return). Nothing about the policy
is restated here as text: a second copy of the policy would drift from the
first, and a screen that drifts from the enforcement it describes is worse
than no screen, because it is believed.

THE HONESTY REQUIREMENT. bm_fence_hook.enforced_mode() is opt-in and FAILS
OPEN by default, deliberately and on the record in that file. So on a stock
machine the fence RECORDS a write across another session's claim rather than
refusing it, and this block says exactly that, in those words, and names the
variable that changes it. A block that printed "autonomous inside the fence"
over a fail-open fence would be a false claim shipped into the product.

Anything this file cannot read is printed as NO-DATA on its own line. NO-DATA
is never a pass and never hidden; it is the third answer, same as
scripts/scope_audit.py uses it.

Load-by-path for the sibling modules, matching scripts/attempt_hook.py: this
tool is invoked from an arbitrary cwd, and a plain import would resolve
against sys.path and could pick up a different checkout.

Python 3, standard library only. No network. No em or en dashes.
"""

import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FENCE_HOOK = os.path.join(
    ROOT, "products", "brothermode", "tools", "bm_fence_hook.py")

NO_DATA = "NO-DATA"

EXIT_OK = 0
EXIT_NO_DATA = 2

#: The env var the fence reads. Confirmed by reading bm_fence_hook.fence_mode
#: and bm_fence_hook.enforced_mode, which read this key and nothing else.
FENCE_ENV_VAR = "BM_FENCE_MODE"


def _load(name, path):
    """Import one module by absolute path, or return (None, reason). Never
    raises: a module this screen cannot read becomes a NO-DATA line, never a
    traceback in front of the person about to start a run."""
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return None, "no import spec for %s" % path
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:  # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)


def _dial_module():
    return _load("autonomy_dial", os.path.join(HERE, "autonomy_dial.py"))


def _fence_module():
    return _load("bm_fence_hook", FENCE_HOOK)


def _scope_module():
    return _load("scope_audit", os.path.join(HERE, "scope_audit.py"))


def dial_state(dial_mod, env):
    """What the dial is set to right now, and what each risk class resolves
    to at that setting. Computed through the module's own effective_class, so
    the rule that a dial can only ADD ceremony is shown, never asserted."""
    dial = dial_mod.dial_level(env)
    decisions = {}
    for level in dial_mod.ORDER:
        effective = dial_mod.effective_class(level, dial)
        decisions[level] = dial_mod.ACTIONS[effective]
    raw = (env.get(dial_mod.DIAL_ENV_VAR) or "").strip()
    return {
        "dial": dial,
        "dial_default": dial_mod.DEFAULT_DIAL,
        "dial_env_var": dial_mod.DIAL_ENV_VAR,
        "dial_env_value": raw or None,
        "decisions": decisions,
        "always_refused": list(dial_mod.A3_FLAGS),
    }


def _group_by_decision(order, decisions):
    """[(decision, [levels])] keeping the levels' own ranking, so the screen
    shows a few short rows instead of one row per class saying the same
    thing."""
    groups = []
    for level in order:
        decision = decisions[level]
        if groups and groups[-1][0] == decision:
            groups[-1][1].append(level)
        else:
            groups.append((decision, [level]))
    return groups


def fence_state(fence_mod, env):
    """The fence's real state on THIS machine, read from the hook's own
    fence_mode(), which returns (mode, warning). A value it does not
    recognize runs advisory and carries a warning, and both halves are
    reported here for the same reason the hook prints it on every call."""
    try:
        mode, warning = fence_mod.fence_mode(env)
    except Exception as exc:  # noqa: BLE001
        return {"mode": None, "enforcing": None, "env_var": FENCE_ENV_VAR,
                "warning": None,
                "no_data": "fence_mode() failed: %s: %s"
                           % (type(exc).__name__, exc)}
    return {
        "mode": mode,
        "enforcing": mode == fence_mod.MODE_ENFORCED,
        "env_var": FENCE_ENV_VAR,
        "env_value": (env.get(FENCE_ENV_VAR) or "").strip() or None,
        "advisory_name": fence_mod.MODE_ADVISORY,
        "enforced_name": fence_mod.MODE_ENFORCED,
        "warning": warning,
        "no_data": None,
    }


def scope_state(scope_mod):
    """The verdicts the after-the-fact audit can return, read off the module
    rather than typed here, so a renamed verdict shows up as a changed screen
    instead of a stale one."""
    try:
        return {"verdicts": [scope_mod.CLEAN, scope_mod.QUARANTINE,
                             scope_mod.NO_DATA],
                "tool": "scripts/scope_audit.py", "no_data": None}
    except AttributeError as exc:
        return {"verdicts": None, "tool": "scripts/scope_audit.py",
                "no_data": "scope_audit is missing a verdict name: %s" % exc}


def block_data(env=None):
    """Everything the block says, as a plain dict. A top level `no_data` is
    set only when the POLICY ITSELF could not be read, which is the one
    condition that makes the whole block worthless."""
    env = os.environ if env is None else env
    dial_mod, dial_err = _dial_module()
    if dial_mod is None:
        return {"no_data": "could not read the autonomy policy: %s" % dial_err,
                "dial": None, "fence": None, "after_run": None}

    try:
        data = dial_state(dial_mod, env)
    except Exception as exc:  # noqa: BLE001
        return {"no_data": "autonomy policy unreadable: %s: %s"
                           % (type(exc).__name__, exc),
                "dial": None, "fence": None, "after_run": None}

    data["order"] = list(dial_mod.ORDER)
    data["policy_source"] = "scripts/autonomy_dial.py"

    fence_mod, fence_err = _fence_module()
    if fence_mod is None:
        fence = {"mode": None, "enforcing": None, "env_var": FENCE_ENV_VAR,
                 "warning": None,
                 "no_data": "could not read the fence: %s" % fence_err}
    else:
        fence = fence_state(fence_mod, env)

    scope_mod, scope_err = _scope_module()
    if scope_mod is None:
        after = {"verdicts": None, "tool": "scripts/scope_audit.py",
                 "no_data": "could not read the scope audit: %s" % scope_err}
    else:
        after = scope_state(scope_mod)

    return {"no_data": None, "dial": data, "fence": fence, "after_run": after}


def _wrap(items, width=62, indent="    "):
    """One list across as few lines as fit, so seven boundary names do not
    become seven rows on a screen somebody reads standing up."""
    lines, current = [], ""
    for item in items:
        piece = item if not current else current + ", " + item
        if len(piece) > width and current:
            lines.append(indent + current + ",")
            current = item
        else:
            current = piece
    if current:
        lines.append(indent + current)
    return lines


def render(env=None, data=None):
    """THE function the intent screen calls. Returns the block as one string,
    prints nothing, decides nothing."""
    data = block_data(env) if data is None else data
    if data.get("no_data"):
        return ("Autonomy, before this run goes unattended\n"
                "  %s: %s\n"
                "  Nothing here is a statement about what this run will do."
                % (NO_DATA, data["no_data"]))

    dial = data["dial"]
    out = ["Autonomy, before this run goes unattended"]

    if dial["dial_env_value"]:
        origin = "%s=%s" % (dial["dial_env_var"], dial["dial_env_value"])
    else:
        origin = "%s unset, default %s" % (dial["dial_env_var"],
                                           dial["dial_default"])
    out.append("  Dial: %s (%s)" % (dial["dial"], origin))

    for decision, levels in _group_by_decision(dial["order"],
                                               dial["decisions"]):
        out.append("    %-10s %s" % (", ".join(levels), decision))

    out.append("  Refused until you approve, at every dial position:")
    out.extend(_wrap(dial["always_refused"]))

    fence = data["fence"]
    if fence.get("no_data"):
        out.append("  Fence: %s: %s" % (NO_DATA, fence["no_data"]))
    elif fence["enforcing"]:
        out.append("  Fence: REFUSING. A write across another session's "
                   "claim is denied")
        out.append("    before it lands (%s=%s)."
                   % (fence["env_var"], fence["mode"]))
    else:
        out.append("  Fence: RECORDING ONLY. A write across another "
                   "session's claim is")
        out.append("    recorded, not refused. Set %s=%s to refuse it."
                   % (fence["env_var"], fence["enforced_name"]))
    if fence.get("warning"):
        out.append("    %s" % fence["warning"])

    after = data["after_run"]
    if after.get("no_data"):
        out.append("  After the run: %s: %s" % (NO_DATA, after["no_data"]))
    else:
        out.append("  After the run: %s compares what changed" % after["tool"])
        out.append("    against what was declared (%s)."
                   % ", ".join(after["verdicts"]))

    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true",
                    help="the same content as a machine-readable object")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    data = block_data()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(render(data=data))
    return EXIT_NO_DATA if data.get("no_data") else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
