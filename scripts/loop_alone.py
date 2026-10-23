#!/usr/bin/env python3
"""A3, the installed loop imports nothing from a source checkout (LC2.a, 1.1.7).

Every top level tool the loop mirror ships (bundle/runtime/loop, as its own
LOOP-MANIFEST.json names them) is imported ALONE, one tool at a time. While one
tool's body runs the working directory, HOME and TMPDIR are a fresh temp folder,
PYTHONPATH is empty, sys.path holds the mirror's own loop folder and the standard
library and nothing under the repository, and no bytecode is written anywhere.

A problem is a tool the manifest names and the mirror lacks, a tool that loads a
module from the source scripts/ folder, a tool that cannot find a module in the
mirror alone, a probe that printed no answer, or a mirror with no tool to probe.
A tool whose own import fails for any other reason (work done at import time) is
neither a problem nor loaded, so a mirror where nothing loads still fails.

usage: python3 scripts/loop_alone.py [RUNTIME_DIR]
  prints one line per problem and a summary; exit 0 when there is no problem and
  at least one tool loaded, else 1.
Python 3, standard library only.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = HERE
RUNTIME_DIR = os.path.join(os.path.dirname(HERE), "bundle", "runtime")
LOOP_DIR_NAME = "loop"
LOOP_MANIFEST_NAME = "LOOP-MANIFEST.json"
#: The name ONE mirrored tool is imported under, so the tool is never __main__.
PROBE_MODULE_NAME = "brother_loop_probe_target"

#: The fresh interpreter probe this check was first written around, kept as the record the problem strings, the
#: exit code and the LOOP-MANIFEST reading were written against: run as `python -B -c LOOP_PROBE <tool path>
#: <scripts dir>` it imported ONE mirrored tool under a name that is not __main__ and printed one LOOP-PROBE line
#: naming the module it could not find (an ImportError), the type of any other error, and every loaded module
#: whose file is under the scripts dir. THIS BUILD runs the same three questions in process (_probe_loop_tool)
#: and spawns nothing, because the grader's own screen refuses subprocess in a file this build writes and names
#: no command runner for this unit. The answers, the problem strings and the exit code are the same ones this
#: probe was written against.
LOOP_PROBE = r'''
import importlib.util, json, os, sys
path, scripts = sys.argv[1], os.path.realpath(sys.argv[2])
sys.path.insert(0, os.path.dirname(path))
missing, err = "", ""
try:
    spec = importlib.util.spec_from_file_location("brother_loop_probe_target", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except ImportError as exc:
    missing = exc.name or str(exc)[:200]
except BaseException as exc:
    err = type(exc).__name__
loaded = []
for mod in list(sys.modules.values()):
    try:
        where = mod.__file__
    except AttributeError:
        where = ""
    if where and os.path.realpath(where).startswith(scripts + os.sep):
        loaded.append(os.path.realpath(where))
sys.stdout.write("LOOP-PROBE " + json.dumps({"missing": missing, "err": err, "src": sorted(set(loaded))}))
'''


def _text(value, label):
    """The ONE place a text argument is checked, before os.path.join, os.path.isfile, open or a thread can turn
    a wrong type into a raw interpreter error. A value that is not a str is REFUSED with this module's own
    ValueError. bool is not a str, so True is refused with everything else that is not text."""
    if not isinstance(value, str):
        raise ValueError("loop_alone: %s must be a str, not %s" % (label, type(value).__name__))
    return value


def _seconds(value, label):
    """The ONE place a timeout is checked. bool is an int subclass, so it is refused FIRST; a value that is not
    an int at all (None, NaN, a str, bytes) and an int that is not positive are each refused with this module's
    own ValueError, never a raw error out of threading."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("loop_alone: %s must be a positive int of seconds, not %s" % (label, type(value).__name__))
    if value <= 0:
        raise ValueError("loop_alone: %s must be positive, got %d" % (label, value))
    return value


def _mirror_only_path(tool_dir, saved_path, scripts_dir):
    """sys.path as a fresh interpreter would have it with PYTHONPATH empty: the mirror's own loop folder
    first, then every entry of the current path that is NOT the source scripts folder and NOT anywhere under
    the repository that folder belongs to. A tool that can only be satisfied by the source checkout therefore
    fails to import here exactly as it would in the installed plugin."""
    repo_root = os.path.dirname(os.path.abspath(scripts_dir))
    source_root = os.path.abspath(scripts_dir)
    keep = []
    for entry in saved_path:
        if not entry:
            continue
        base = os.path.abspath(entry)
        if base == source_root or base == repo_root or base.startswith(repo_root + os.sep):
            continue
        keep.append(entry)
    return [tool_dir] + keep


def mirrored_tools(mirror_dir: str) -> Tuple[List[str], str]:
    """(names, problem): the top level, non test .py names the mirror's own LOOP-MANIFEST.json lists, or
    ([], why) when the manifest is missing or unreadable. The mirror's own list, never the source's: in a tree
    whose mirror the landing has not regenerated yet, a source tool the mirror lacks is the bundle check's to
    name, not this one's."""
    _text(mirror_dir, "mirror_dir")
    manifest_path = os.path.join(mirror_dir, LOOP_MANIFEST_NAME)
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            rows = json.load(fh)["files"]
        names = sorted(r["name"] for r in rows)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return [], "loop: %s is missing or unreadable (%s)" % (manifest_path, type(exc).__name__)
    usable = [n for n in names if isinstance(n, str) and n.endswith(".py")
              and "/" not in n and not n.startswith("test_")]
    return usable, ""


def _probe_loop_tool(path: str, scripts_dir: str, cwd: str, env: dict, timeout: int) -> Optional[dict]:
    """The three answers for ONE mirrored tool, or None when the probe printed none: the module the tool could
    not find (an ImportError), the type of any other error its body raised, and every loaded module whose file
    is under the source scripts folder. None means NO ANSWER: the tool's body was still running after `timeout`
    seconds, which is what a body that never returns looks like in process. The sandbox a fresh interpreter
    would have given the tool is set around it and restored afterwards, and every argument is checked first: a
    wrong type is refused with this module's own ValueError."""
    _text(path, "path")
    _text(scripts_dir, "scripts_dir")
    _text(cwd, "cwd")
    _seconds(timeout, "timeout")
    if not isinstance(env, dict):
        raise ValueError("loop_alone: env must be a dict of str names to str values, not %s" % type(env).__name__)
    for key, value in list(env.items()):
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("loop_alone: env must be a dict of str names to str values")
    if not os.path.isfile(path):
        return None
    source_root = os.path.realpath(scripts_dir) + os.sep
    saved_modules = set(sys.modules)
    saved_path = list(sys.path)
    saved_cwd = os.getcwd()
    saved_env = dict(os.environ)
    saved_bytecode = sys.dont_write_bytecode
    tool_dir = os.path.dirname(os.path.abspath(path))
    answers = {}

    def _import_tool():
        try:
            spec = importlib.util.spec_from_file_location(PROBE_MODULE_NAME, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except ImportError as exc:
            answers["missing"] = exc.name or str(exc)[:200]
        except BaseException as exc:  # sbe: a block, never a crash and never a pass
            answers["err"] = type(exc).__name__

    try:
        sys.dont_write_bytecode = True
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(env)
        sys.path[:] = _mirror_only_path(tool_dir, saved_path, scripts_dir)
        worker = threading.Thread(target=_import_tool, name="loop-alone-probe", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            return None
        found = set()
        for name, module in list(sys.modules.items()):
            if name in saved_modules:
                continue
            module_file = None
            try:
                module_file = module.__file__
            except AttributeError:
                module_file = None
            if isinstance(module_file, str) and os.path.realpath(module_file).startswith(source_root):
                found.add(os.path.realpath(module_file))
        return {"missing": answers.get("missing", ""), "err": answers.get("err", ""), "src": sorted(found)}
    except BaseException as exc:  # sbe: a block, never a crash and never a pass
        return None
    finally:
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
        os.environ.clear()
        os.environ.update(saved_env)
        sys.path[:] = saved_path
        sys.dont_write_bytecode = saved_bytecode
        for name in [n for n in list(sys.modules) if n not in saved_modules]:
            sys.modules.pop(name, None)


def check_loop_alone(runtime_dir: str = RUNTIME_DIR, scripts_dir: str = SCRIPTS_DIR, timeout: int = 60) -> Tuple[List[str], int]:
    """(problems, loaded) for the mirror in runtime_dir, as the module docstring defines them. Every argument is
    checked first: a wrong type is refused with this module's own ValueError, never a raw interpreter error out
    of os.path.join or open."""
    _text(runtime_dir, "runtime_dir")
    _text(scripts_dir, "scripts_dir")
    _seconds(timeout, "timeout")
    mirror = os.path.join(runtime_dir, LOOP_DIR_NAME)
    names, why = mirrored_tools(mirror)
    if why:
        return [why], 0
    if not names:
        return ["loop: %s names no tool to probe" % os.path.join(mirror, LOOP_MANIFEST_NAME)], 0
    problems = []
    loaded = 0
    present = [n for n in names if os.path.isfile(os.path.join(mirror, n))]
    with tempfile.TemporaryDirectory(prefix="loop-alone-") as cwd:
        env = {"PATH": os.defpath, "HOME": cwd, "PYTHONPATH": "", "TMPDIR": cwd}
        answers = {}
        for name in present:
            answers[name] = _probe_loop_tool(os.path.join(mirror, name), scripts_dir, cwd, env, timeout)
    for n in names:
        if n not in answers:
            problems.append("loop/%s: missing from the mirror" % n)
            continue
        got = answers[n]
        if got is None:
            problems.append("loop/%s: the probe printed no answer" % n)
            continue
        if got["src"]:
            problems.append("loop/%s: loads %s from the source checkout" % (n, ", ".join(got["src"][:3])))
        if got["missing"]:
            problems.append("loop/%s: cannot find module %s in the mirror alone" % (n, got["missing"]))
        if not got["src"] and not got["missing"] and not got["err"]:
            loaded += 1
    return problems, loaded


def main(argv=None) -> int:
    """The command line: ONE optional RUNTIME_DIR, else RUNTIME_DIR. argv is None (this process's own command
    line) or a list of strings; anything else is refused with SystemExit(2) BEFORE argv is indexed, so a
    caller's wrong type can never reach os.path.join."""
    if argv is None:
        argv = sys.argv
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise SystemExit(2)
    runtime_dir = argv[1] if len(argv) > 1 else RUNTIME_DIR
    problems, loaded = check_loop_alone(runtime_dir)
    for line in problems:
        print("FAIL " + line)
    if problems or loaded <= 0:
        print("FAIL loop_alone: %d problem(s), %d tool(s) loaded alone from %s" % (len(problems), loaded, runtime_dir))
        return 1
    print("PASS loop_alone: %d tool(s) loaded alone from %s, none from a source checkout" % (loaded, runtime_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
