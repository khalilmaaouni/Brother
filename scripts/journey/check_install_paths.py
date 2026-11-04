#!/usr/bin/env python3
"""J1's A.first-screen-paths-exist: every path a stranger's first screen names exists where it says.

Until this existed the row carried only `measured_by: FRUN1.a` and no check, so it read "the row declares no
check, so nothing measured it" however the first screen changed. A row with no check is a unit that cannot land.

WHAT THE FIRST SCREEN IS, read off the INSTALLED plugin rather than this repository, so the subject is the
deliverable and never a fixture:
  1. what the session start hooks print, run the way the host runs them (the installed hooks.json, the installed
     plugin root, a fresh project folder as the working directory), because that is literally the first text a
     stranger sees after install;
  2. the door and its skills (commands/*.md, skills/*/SKILL.md and their references), which is what the first
     /brother answers from.
For 1, every `python3 <file>.py` the text tells the person to run must exist as written from the folder they are
in (absolute, or relative to that folder). For 2, every path written under ${CLAUDE_PLUGIN_ROOT},
${BROTHER_PLUGIN_ROOT} or ${PLUGIN_ROOT} must exist under the installed plugin root.

FAIL DIRECTION. No install, no hooks file, a hook that cannot run: NO-DATA, exit 2, never a pass. A first screen
naming one path that is not there: FAIL, exit 1, naming it.

    python3 -B scripts/journey/check_install_paths.py
    python3 -B scripts/journey/check_install_paths.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

MANIFEST = "RUNTIME-MANIFEST.json"
#: ${CLAUDE_PLUGIN_ROOT}/x, ${BROTHER_PLUGIN_ROOT}/x and ${PLUGIN_ROOT}/x, with or without the braces.
ROOTED = re.compile(r"\$\{?(?:CLAUDE_PLUGIN_ROOT|BROTHER_PLUGIN_ROOT|PLUGIN_ROOT)\}?/([A-Za-z0-9_./-]+)")
#: `python3 <file>.py` (an optional -B or -I first) in text a person is told to run.
COMMANDED = re.compile(r"\bpython3?\s+(?:-[BI]\s+)?([^\s'\"`;|&]+\.py)\b")


def installed_root(config):
    """The installed plugin root under `config` (the folder holding runtime/RUNTIME-MANIFEST.json), or None. Found
    BY NAME, the rule run_journey.install_fresh uses, so this never needs the host's cache layout."""
    for base, dirs, files in os.walk(config or ""):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if os.path.basename(base) == "runtime" and MANIFEST in files:
            return os.path.dirname(base)
    return None


def session_start_lines(root, home, timeout=120):
    """(text the installed SessionStart hooks print, "") or (None, why). Each hook runs as the host runs it: its
    command from the installed hooks.json, CLAUDE_PLUGIN_ROOT set to the installed root, a fresh git project under
    `home` as the working directory, and the SessionStart event on stdin."""
    try:
        with open(os.path.join(root, "hooks", "hooks.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        entries = doc["hooks"]["SessionStart"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, "the installed hooks.json names no SessionStart hook (%s)" % exc
    project = tempfile.mkdtemp(prefix="first-screen-", dir=home if os.path.isdir(home or "") else None)
    subprocess.run(["git", "init", "-q", project], capture_output=True, text=True, timeout=60)
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=root)
    event = json.dumps({"hook_event_name": "SessionStart", "cwd": project, "session_id": "first-screen",
                        "source": "startup"})
    said = []
    for entry in entries:
        for hook in (entry or {}).get("hooks") or []:
            cmd = (hook or {}).get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                continue
            try:
                # shell=True on purpose: the host runs a hook's command string through a shell, which is what
                # expands its "${CLAUDE_PLUGIN_ROOT}". The string is the installed plugin's own, never user input.
                done = subprocess.run(cmd, shell=True, input=event, cwd=project, env=env, capture_output=True,
                                      text=True, timeout=timeout)
            except (OSError, subprocess.SubprocessError) as exc:
                return None, "a SessionStart hook could not run: %s" % exc
            said.append(done.stdout)
    if not said:
        return None, "the installed hooks.json carries no SessionStart command"
    return "\n".join(said), project


def door_texts(root):
    """{relative path: text} for the door and its skills under the installed root."""
    out = {}
    for pattern in ("commands/*.md", "skills/*/SKILL.md", "skills/*/references/*.md"):
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    out[os.path.relpath(path, root)] = fh.read()
            except OSError:
                continue
    return out


def judge(root, printed, cwd, texts):
    """(exit code, one line). Pure over what was read, so the selftest judges THIS and not a copy."""
    if root is None:
        return 2, ("NO-DATA: no installed plugin (no runtime/%s) under the config dir, so no first screen could be "
                   "read" % MANIFEST)
    if printed is None:
        return 2, "NO-DATA: the session start text could not be read: %s" % cwd
    if not texts:
        return 2, "NO-DATA: the installed plugin carries no door or skill text, so there is no first screen to read"
    missing = []
    for rel in sorted(set(COMMANDED.findall(printed))):
        if not os.path.isfile(rel if os.path.isabs(rel) else os.path.join(cwd, rel)):
            missing.append("session start says run %s, which is not there from the folder a stranger is in" % rel)
    named = 0
    for name, text in sorted(texts.items()):
        for rel in sorted(set(ROOTED.findall(text))):
            named += 1
            if not os.path.exists(os.path.join(root, rel.rstrip("."))):
                missing.append("%s names ${PLUGIN_ROOT}/%s, which the install does not carry" % (name, rel))
    if missing:
        return 1, "FAIL: %d first screen path(s) do not exist: %s" % (len(missing), "; ".join(missing[:4]))
    return 0, ("PASS: every path the installed first screen names exists (%d under the plugin root, %d commanded "
               "by the session start text)" % (named, len(set(COMMANDED.findall(printed)))))


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    root = tempfile.mkdtemp(prefix="paths-root-")
    cwd = tempfile.mkdtemp(prefix="paths-cwd-")
    os.makedirs(os.path.join(root, "runtime"))
    open(os.path.join(root, "runtime", "door.py"), "w").close()
    good = {"skills/x/SKILL.md": "run python3 \"${CLAUDE_PLUGIN_ROOT}/runtime/door.py\""}
    bad = {"skills/x/SKILL.md": "run python3 \"${BROTHER_PLUGIN_ROOT}/runtime/gone.py\""}
    absolute = os.path.join(root, "runtime", "door.py")
    case("a door naming only present paths is PASS", judge(root, "", cwd, good)[0], 0)
    case("a door naming an absent path is FAIL", judge(root, "", cwd, bad)[0], 1)
    case("a start line commanding a relative script that is not there is FAIL",
         judge(root, "setup is not complete; run: python3 scripts/setup.py", cwd, good)[0], 1)
    case("a start line commanding an absolute script that is there is PASS",
         judge(root, "run: python3 %s" % absolute, cwd, good)[0], 0)
    case("no install is NO-DATA", judge(None, "", cwd, good)[0], 2)
    case("an unreadable start text is NO-DATA", judge(root, None, "boom", good)[0], 2)
    case("no door text is NO-DATA", judge(root, "", cwd, {})[0], 2)
    bad_count = 0
    for label, want, got, ok in cases:
        bad_count += 0 if ok else 1
        print("%-66s want %d got %d %s" % (label, want, got, "ok" if ok else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad_count else "%d FAILURE(S)" % bad_count))
    return 0 if not bad_count else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    root = installed_root(args.config)
    printed, cwd = session_start_lines(root, os.environ.get("HOME", "")) if root else (None, "")
    code, line = judge(root, printed, cwd, door_texts(root) if root else {})
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
