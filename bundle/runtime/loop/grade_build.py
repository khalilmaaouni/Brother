#!/usr/bin/env python3
"""Grade one model-written build (edits + tests + mutations) in a scratch copy. Never touches the real tree.

usage (from the repo root): grade_build.py <build.json> [--keep]
Verdict lines: APPLY, RED-WITHOUT-CODE, GREEN-WITH-CODE, MUTATIONS n/m caught, then PASS or FAIL with reasons.
A build PASSES only if every edit applies on a unique match, its tests fail without its code, pass with it, and
at least 3 mutations apply and are all caught. Anything unparseable or unsafe is FAIL, never a pass.
screen_rules() is the one text rendering of the safety screen for a builder, built from the constants unsafe() and
preflight() read; with BROTHER_BRIEF_SCREEN=on (brief_screen_mode) build_brief puts it in the brief and the hand typed
sentences in unit_runner.py and build_plan.py defer to it.
"""
import ast  # module level: _bindings and _dotted below are helpers of unsafe(), which used to hold the only import
import contextlib
import hashlib, json, os, re, shutil, subprocess, sys, tempfile

BLOCK = re.compile(r"or_ask|urllib|http\.client|import requests|requests\.(?:get|post|put|patch|delete|request|Session)\(|import socket|socket\.socket|os\.system|\beval\(|\bexec\(|ftplib|smtplib|"
                   r"security find-generic-password|\.ssh|keychain", re.I)
#: judged by PARSING when the text is Python: a real import or call counts, a mention inside a string literal or an import of
#: urllib.parse does not. Twice on 2026-09-20 a bare word match refused honest code (a scanner naming network symbols, a URL parser).
#: Modules a build may not import. subprocess and pty were absent, so "no network" could be
#: defeated by shelling out to a network binary instead of importing a network library. A screen
#: that blocks the library and not the shell is a screen against the careless, not the hostile.
NET_MODULES = ("urllib.request", "urllib.error", "http.client", "http.server", "requests",
               "socket", "ftplib", "smtplib", "telnetlib", "xmlrpc", "httpx", "aiohttp",
               "subprocess", "pty", "multiprocessing", "asyncio.subprocess", "ctypes")
ALWAYS_TEXT = re.compile(r"or_ask|security find-generic-password|\.ssh/|keychain", re.I)
#: Call names that execute something. "popen" was here and "Popen" was NOT, which is the whole
#: story: this set is matched case sensitively, so subprocess.Popen sailed past a screen that
#: blocked os.popen. Proved 2026-09-21 by an adversarial review: a build importing subprocess and
#: shelling out to curl returned unsafe() = None, so arbitrary command execution and network
#: exfiltration cleared a screen whose entire purpose is "no network".
#: A METHOD named run or call on an ordinary object is not a process (review 2026-09-27: a test's case.run(result) and a
#: TestSuite().run(r) were refused, 10 of D14's refusals): it passes unless its target resolves into a denied module. A bare
#: run(...) stays refused, and subprocess itself cannot be imported by a build, nor a repo module that uses it.
ORDINARY_METHODS = frozenset({"run", "call"})
BAD_CALLS = {"eval", "exec", "system", "popen", "__import__", "import_module",
             # the subprocess family, every entry point it exposes
             "Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
             # os exec and spawn, which need no subprocess import at all
             "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
             "spawnv", "spawnve", "spawnl", "spawnlp", "posix_spawn", "fork", "forkpty"}


#: Attributes of os that start a process or a shell. os ITSELF cannot be forbidden, since an honest build
#: needs os.path, so these are named one by one AND resolved through whatever name they were BOUND to.
OS_EXEC_ATTRS = {"system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
                 "spawnv", "spawnve", "spawnl", "spawnlp", "posix_spawn", "fork", "forkpty"}

#: Dynamic attribute access, REFUSED OUTRIGHT in worker written code. Decided 2026-09-21 rather than left
#: implicit: getattr(os, "sys" + "tem")("true") cleared this screen, and no name list can ever catch a name
#: that is assembled at runtime. A build is code written to a spec and has no honest need to reach an
#: attribute it cannot spell, so the whole mechanism goes. The cost of refusing is that an unusual honest
#: build is rewritten; the cost of allowing is arbitrary command execution on this machine.
DYNAMIC_CALLS = {"getattr", "setattr", "delattr", "vars", "globals", "locals"}


def _is_net(name):
    return any(name == m or name.startswith(m + ".") for m in NET_MODULES) or name == "urllib"


#: Process modules sit in NET_MODULES because, unconfined, a process can reach the network. Under the proven sandbox the
#: network is closed at the kernel (test_sandbox_first proves a started child cannot write out or connect), so contained()
#: relaxes exactly these. ctypes stays refused in every mode: no honest build needs it.
PROCESS_MODULES = ("subprocess", "pty", "multiprocessing", "asyncio.subprocess")


def _is_wire(name):
    """A module that reaches the network itself: NET_MODULES minus the process modules the sandbox confines."""
    return _is_net(name) and not any(name == m or name.startswith(m + ".") for m in PROCESS_MODULES)


#: COMMAND RUNNERS, owner ruling 2026-09-22 (docs/decisions/grader-subprocess-allow-list-2026-09-22.json, option A):
#: a deliverable whose job is to run a command (a git location guard, an audit verifier, a canary) may import
#: subprocess ONLY when its unit names its PATH under "command_runners" in the plan. For such a file every call into
#: subprocess must pass a LITERAL argv (a list or tuple of string constants): a command composed at runtime is
#: refused, because a literal screen cannot see it. Network binaries, git verbs that reach a remote, shells and
#: interpreters given code on the command line, and shell=True are refused inside a literal argv too. Nothing else
#: in the screen moves: an unlisted path is refused exactly as before, and NET_MODULES other than subprocess stay
#: refused for listed paths as well. The plan is the only source of the list: an unreadable plan allows NOTHING.
PLAN_PATH = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
NET_BINARIES = {"curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "netcat", "telnet", "ftp", "rsync", "socat",
                "openssl", "pip", "pip3", "npm", "npx", "brew", "gh", "codex", "claude"}
NET_GIT_VERBS = {"fetch", "push", "pull", "clone", "ls-remote", "remote", "submodule"}
INTERPRETERS = {"python", "python3", "sh", "bash", "zsh", "perl", "ruby", "node", "osascript"}


def allowed_runners(plan_path=PLAN_PATH):
    """Paths some unit names under command_runners. Unreadable or malformed plan: the empty set, never a guess."""
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        units = plan.get("units") if isinstance(plan, dict) else None
        out = set()
        for u in units or []:
            for path in (u.get("command_runners") or []) if isinstance(u, dict) else []:
                if isinstance(path, str) and path.strip():
                    out.add(path.strip())
        return out
    except (OSError, ValueError, AttributeError):
        return set()


def _argv_reason(node, target):
    """Why this subprocess call is refused even in a command runner, or None when its argv is a clean literal."""
    tail = target.rsplit(".", 1)[-1]
    if tail in ("getoutput", "getstatusoutput"):
        return "%s() takes a shell string" % tail
    for kw in node.keywords:
        if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
            return "%s() with shell not literally False" % tail
    argv = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "args"), None)
    if argv is None:
        return "%s() with no argv" % tail
    if not isinstance(argv, (ast.List, ast.Tuple)):
        return "%s() argv is not a literal list" % tail
    words = []
    for el in argv.elts:
        if not (isinstance(el, ast.Constant) and isinstance(el.value, str)):
            return "%s() argv element is not a string literal" % tail
        words.append(el.value)
    if not words:
        return "%s() with an empty argv" % tail
    names = [os.path.basename(w) for w in words]
    hit = next((w for w in names if w in NET_BINARIES), None)
    if hit:
        return "%s() runs %s, a network binary" % (tail, hit)
    if names[0] == "git" and any(w in NET_GIT_VERBS for w in words[1:]):
        return "%s() runs git with a verb that reaches a remote" % tail
    if names[0] in INTERPRETERS and any(w in ("-c", "-e") for w in words[1:]):
        return "%s() hands %s code on the command line" % (tail, names[0])
    return None


def _bindings(tree):
    """Every name an import BOUND, mapped to the dotted thing it was bound to.

    THE HOLE THIS CLOSES, measured 2026-09-21 against the four call shapes an auditor fired at it:
    `from os import system as launch` returned None, because the screen matched what a call was CALLED
    (`system`) and never what the name was BOUND to. Renaming the import defeated the entire screen.
    A binding shape this does not recognise is simply not recorded, so the name resolves to itself and
    the literal name check downstream still applies: an unknown shape never becomes a safe one.
    """
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name          # import os as o  ->  o is os
                else:
                    top = a.name.split(".")[0]      # import os.path  ->  binds the NAME os
                    out[top] = top
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                out[a.asname or a.name] = (mod + "." + a.name) if mod else a.name
    return out


def _dotted(fn, binds):
    """The dotted target a call resolves to once import aliases are followed, or "" when it cannot be named.

    An unnameable target (a call on a call, an attribute of a local object) returns "" and falls through to
    the literal name check, which is what stood here before: this ADDS a net, it never replaces one.
    """
    parts = []
    while isinstance(fn, ast.Attribute):
        parts.append(fn.attr)
        fn = fn.value
    if not isinstance(fn, ast.Name):
        return ""
    parts.append(binds.get(fn.id, fn.id))
    return ".".join(reversed(parts))


# Explicit offline import surface. Extensions name exact modules, never prefixes.
SAFE_IMPORTS = frozenset("abc argparse ast base64 binascii bisect calendar collections collections.abc contextlib copy csv dataclasses datetime decimal difflib enum errno fnmatch fractions functools glob hashlib heapq hmac importlib importlib.util io itertools json keyword logging math operator os os.path pathlib pprint random re shlex shutil stat string struct sys tempfile textwrap time traceback types typing unittest unittest.mock urllib.parse uuid warnings weakref".split())


# THE EXECUTION CONTRACT, NOT A SHORT LIST (2026-09-27, Codex and Opus): 8 of 11 run 6 refusals came before any test ran,
# for ordinary imports (threading, __future__) and for repository modules the build did not edit. Admitted now: every
# standard library module outside the deny set below, and a repository module whose own imports, followed through the
# repository, reach nothing denied and nothing outside the standard library. Safe by construction against network,
# process and native code, with no hand kept list to lag the code.
#: No credential reaches a build's tests through the environment (review 2026-09-27): a variable named like a secret is
#: dropped before any command runs in the sandbox.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_switches import SECRET_ENV, suite_env  # noqa: E402  the one credential-name rule lives in loop_switches (2026-10-02)
DENY_TOP = frozenset({"subprocess", "ctypes", "multiprocessing", "pty", "webbrowser", "socket", "ssl", "asyncio", "getpass"})
STDLIB = frozenset(getattr(sys, "stdlib_module_names", ())) or (frozenset(m.split(".")[0] for m in SAFE_IMPORTS) | frozenset(
    "__future__ threading queue concurrent array codecs configparser gzip zipfile tarfile inspect locale numbers "
    "secrets sqlite3 statistics unicodedata html xml email platform".split()))
_REPO_SAFE = {}


def _denied(name):
    return _is_net(name) or name.split(".")[0] in DENY_TOP


def _stdlib(name):
    return name.split(".")[0] in STDLIB


def _repo_module_path(name, root="."):
    """The repository file behind an import name, or None: the dotted path itself, or a module beside the scripts the
    tests put on sys.path (scripts/, scripts/loop/)."""
    if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", name or ""):
        return None
    rel = name.replace(".", "/")
    for cand in (rel + ".py", rel + "/__init__.py", "scripts/" + rel + ".py", "scripts/loop/" + rel + ".py"):
        path = os.path.join(root, cand)
        if os.path.isfile(path):
            return path
    return None


def _repo_module_safe(name, root=".", _stack=()):
    """True when `name` is a module file in this repository whose imports reach, through the repository, only the
    standard library outside the deny set. A cycle is judged by the rest of the walk; an unreadable, relative or
    dynamically built import is refused (it cannot be followed)."""
    key = (os.path.abspath(root), name)
    if key in _REPO_SAFE:
        return _REPO_SAFE[key]
    if name in _stack:
        return True
    path = _repo_module_path(name, root)
    ok = path is not None and len(_stack) < 40
    if ok:
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError, ValueError):
            tree, ok = None, False
    if ok:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (getattr(node.func, "id", "") == "__import__" or getattr(node.func, "attr", "") == "import_module"):
                ok = False; break
            # A MODULE IS SAFE BY WHAT IT CALLS, NOT ONLY BY WHAT IT IMPORTS (review 2026-09-27): os is admitted, so a
            # module that calls os.system, an exec, a spawn, a fork or a popen would hand that power to a build that
            # imports it. Refused whether reached as os.system(...) or as `from os import system`, then system(...).
            if isinstance(node, ast.Call) and (getattr(node.func, "attr", "") in OS_EXEC_ATTRS or getattr(node.func, "id", "") in OS_EXEC_ATTRS):
                ok = False; break
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    ok = False; break
                mods = [node.module or ""] + ["%s.%s" % (node.module, a.name) for a in node.names
                                               if node.module and _repo_module_path("%s.%s" % (node.module, a.name), root)]
            for m in mods:
                if _denied(m) or not (_stdlib(m) or _repo_module_safe(m, root, _stack + (name,))):
                    ok = False; break
            if not ok:
                break
    _REPO_SAFE[key] = ok
    return ok


def _import_allowed(name, allowed_imports, runner=False):
    """An explicit import name, with subprocess reserved for declared runners."""
    if name == "subprocess":
        return runner
    if name in SAFE_IMPORTS or (name in allowed_imports and not _is_net(name)):
        return True
    if _denied(name):
        return False
    return _stdlib(name) or _repo_module_safe(name)


def _build_imports(build):
    """Local test subjects explicitly named by this build's edited Python paths.

    The contents are screened in the same call. This does not discover arbitrary
    installed modules or allow a directory prefix. Package ancestors are exact
    import names so `from pkg import module` can test `pkg/module.py`.
    """
    names = set()
    for item in build.get("edits") or []:
        path = item.get("path", "") if isinstance(item, dict) else ""
        if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*\.py", path):
            continue
        parts = path[:-3].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        names.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
        # THE BARE NAME TOO (Codex and Fable, 2026-09-28): tests put scripts/ and scripts/loop/ on sys.path and import
        # the sibling by its stem (61 test files do), so `import coe_p0_driver` was refused for the module this very
        # build writes while `from scripts import coe_p0_driver` passed; 18 of 54 safety refusals in one run were only
        # this. The module's own content is screened in the same call, and a stem naming a denied module (ctypes,
        # subprocess, a network module) is still refused by _import_allowed's own checks.
        if len(parts) == 2 and parts[0] == "scripts" or parts[:2] == ["scripts", "loop"] and len(parts) == 3:
            names.add(parts[-1])
    return names


def _read_target(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _base_literals(path, read):
    """The protected literals (ALWAYS_TEXT matches) the BASE file already holds. 2026-10-02: a build rewriting an existing
    file whole was judged on every string in it, so D2's tests, which name the bridge they test, refused every D2 build
    whatever it wrote (5 of 6 safety refusals that night). A literal already in the base passed every landing gate before
    this build existed; the build is judged on what it adds. Unreadable or unparseable base: an empty set, so every
    literal is judged, never fewer."""
    try:
        tree = ast.parse(read(path))
    except (OSError, ValueError, SyntaxError, TypeError):
        return frozenset()
    return frozenset(n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str) and ALWAYS_TEXT.search(n.value))


def _in_context(it, prior, read, later=()):
    """((tree, first, last), "") for a find/replace fragment that does not parse alone, judged WHERE IT LANDS; else
    (None, reason). Codex B5 rehearsal 2026-09-26: the canary's recorded D6.6 build appends module level code after an
    indented anchor, and its mutations replace an if header with `if False:`; none parses alone, all are valid in place.
    The target is read from the grader's root after this build's own earlier items for the same path (a mutation sees
    the edited file); the anchor must occur exactly once; the whole file must parse once the fragment is applied. Every
    unknown refuses: no anchor, a missing or ambiguous anchor, an unreadable target, invalid Python once applied."""
    path, find, rep = it.get("path"), it.get("find"), it.get("replace")
    if not isinstance(find, str) or not find or not isinstance(rep, str):
        return None, "%s Python fragment cannot be parsed and names no anchor to place it" % path
    body = None
    try:
        for p in prior:
            if isinstance(p.get("new_file_content"), str):
                body = p["new_file_content"]; continue
            pf = p.get("find")
            if body is None:
                body = read(path)
            placed = replace_once(body, pf, p.get("replace") if isinstance(p.get("replace"), str) else "") if isinstance(pf, str) and pf else None
            if placed is None:
                return None, "%s Python fragment cannot be parsed and an earlier item for this path cannot be placed" % path
            body = placed
        if body is None:
            body = read(path)
    except (OSError, UnicodeDecodeError):
        return None, "%s Python fragment cannot be parsed and its target is unreadable" % path
    n = len(occurrences(body, find))
    if n != 1:
        return None, "%s Python fragment cannot be parsed and its anchor occurs %d times, needs exactly once" % (path, n)
    at = body.index(find)
    body = body[:at] + rep + body[at + len(find):]
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError):
        tree = None
    # D2.6 round 4, 2026-10-03: a diff can open a call in one hunk and close it in a later hunk of the same file, so the
    # file parses only once the build's LATER items for this path are applied too. Apply them, following this fragment's
    # span; a later item that rewrites the whole file or overlaps the span cannot be placed and refuses.
    end = at + len(rep)
    for q in (later if tree is None else ()):
        if isinstance(q.get("new_file_content"), str):
            return None, "%s is not valid Python once its fragment is applied, and a later item rewrites the whole file" % path
        qf, qr = q.get("find"), q.get("replace") if isinstance(q.get("replace"), str) else ""
        hits = occurrences(body, qf) if isinstance(qf, str) and qf else []
        if len(hits) != 1:
            return None, "%s is not valid Python once its fragment is applied, and a later item for this path cannot be placed" % path
        if hits[0] + len(qf) <= at:
            at, end = at + len(qr) - len(qf), end + len(qr) - len(qf)
        elif hits[0] < end:
            return None, "%s is not valid Python once its fragment is applied, and a later item overlaps it" % path
        body = body[:hits[0]] + qr + body[hits[0] + len(qf):]
    if tree is None:
        try:
            tree = ast.parse(body)
        except (SyntaxError, ValueError):
            return None, "%s is not valid Python once its fragment is applied at its anchor" % path
    first = body[:at].count("\n") + 1
    return (tree, first, first + body[at:end].count("\n")), ""


def unsafe(build, runners=None, *, allowed_imports=frozenset(), read=None, contained=False):
    """The first reason this build is unsafe to run, or None. A Python fragment that does not parse alone is judged in
    context (_in_context): the whole file must parse where it lands, and the replaced lines meet the same screen;
    anything that cannot be placed is refused. `read`: the target reader, relative to the grader's root by default.
    `runners`: legacy positional command runner paths; read from the plan when omitted.
    `allowed_imports`: exact additional module names authorized by the caller.
    `contained`: True only when the code will run under the proven sandbox (contained()). SANDBOX FIRST (plan E, 2026-10-01):
    over every grade on this machine the screen refused 1,452 of 5,165 builds, the largest single loss, mostly for starting
    a process, dynamic attributes or a computed import, all of which the sandbox already confines (no network, no write
    outside the build, no secret read). Contained, those pass; credential literals, network modules and code that cannot be
    parsed are refused in every mode."""
    import textwrap
    if not isinstance(build, dict):
        return "build must be a dict, got %s" % type(build).__name__
    if not isinstance(allowed_imports, (frozenset, set, list, tuple)):
        return "allowed_imports must be a frozenset of module names, got %s" % type(allowed_imports).__name__
    if any(not isinstance(x, str) for x in allowed_imports):
        return "allowed_imports must hold module name strings only"
    if runners is not None and (isinstance(runners, str)
                                or not isinstance(runners, (list, tuple, set, frozenset))):
        return "runners must be a collection of path strings, got %s" % type(runners).__name__
    if runners is None:
        runners = allowed_runners()
    read = read or _read_target
    placed = [x for x in (build.get("edits") or []) + (build.get("tests") or []) if isinstance(x, dict)]
    for it in (build.get("edits") or []) + (build.get("tests") or []) + (build.get("mutations") or []):
        if not isinstance(it, dict):
            continue
        for key in ("new_file_content", "replace"):
            text = it.get(key)
            if not isinstance(text, str) or not text.strip():
                continue
            if not str(it.get("path", "")).endswith(".py"):
                hit = BLOCK.search(text)
                if hit:
                    return "%s (not Python) contains %r" % (it.get("path"), hit.group(0))
                continue
            tree = None
            for candidate in (text, textwrap.dedent(text), "if 1:\n" + textwrap.indent(textwrap.dedent(text), "    ")):
                try:
                    tree = ast.parse(candidate); break
                except (SyntaxError, ValueError):
                    continue
            span = None
            if tree is None:
                if key != "replace":
                    return "%s Python fragment cannot be parsed" % it.get("path")
                # the build's own earlier items for this path: before it for an edit or test, all of them for a mutation
                upto = next((i for i, x in enumerate(placed) if x is it), len(placed))
                prior = [x for x in placed[:upto] if x.get("path") == it.get("path")]
                later = [x for x in placed[upto + 1:] if x.get("path") == it.get("path")]
                ctx, why = _in_context(it, prior, read, later)
                if why:
                    return why
                tree, first, last = ctx
                span = (first, last)
            binds = _bindings(tree)
            runner = str(it.get("path", "")) in runners
            kept = _base_literals(it.get("path"), read)   # whole file or fragment: a literal the base holds is not the build's own
            for node in ast.walk(tree):
                if span and not (span[0] <= getattr(node, "lineno", 0) <= span[1]):
                    continue   # judged in context: only the replaced lines are this build's code
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and ALWAYS_TEXT.search(node.value) \
                        and node.value not in kept:
                    return "%s contains a protected path or credential command literal" % it.get("path")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if contained and not _is_wire(alias.name):
                            continue   # contained: a process or exec module is confined by the sandbox; the network is not
                        if not _import_allowed(alias.name, allowed_imports, runner):
                            return "%s imports %s" % (it.get("path"), alias.name)
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if contained and mod and not node.level and not _is_wire(mod):
                        continue
                    if node.level or not (mod == "urllib" and all(a.name == "parse" for a in node.names)) and not _import_allowed(mod, allowed_imports, runner):
                        return "%s imports from %s" % (it.get("path"), mod)
                    # FROM A PACKAGE, EACH NAMED SUBMODULE IS AN IMPORT TOO: `from scripts.loop import or_ask` names a module
                    # whose own imports must pass, not only the package's __init__
                    sub = next((a.name for a in node.names if mod and not node.level and _repo_module_path("%s.%s" % (mod, a.name))
                                and "%s.%s" % (mod, a.name) not in allowed_imports and not _import_allowed("%s.%s" % (mod, a.name), allowed_imports, runner)), None)
                    if sub:
                        return "%s imports %s.%s" % (it.get("path"), mod, sub)
                    taken = [a.name for a in node.names if a.name in OS_EXEC_ATTRS]
                    if mod == "os" and taken:
                        # refuse the BINDING, not only the call: the two can live in different items of one
                        # build, and an item is scanned alone, so a call site alone would never see the import
                        return "%s imports os.%s" % (it.get("path"), ", os.".join(taken))
                    if mod == "urllib" and any(a.name != "parse" for a in node.names):
                        return "%s imports urllib.%s" % (it.get("path"), [a.name for a in node.names])
                elif isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                    if name in DYNAMIC_CALLS and not contained:
                        return "%s calls %s(): dynamic attribute access is refused in a build" % (it.get("path"), name)
                    target = _dotted(fn, binds)
                    if contained:
                        if target and _is_wire(target):
                            return "%s calls %s(), which is bound to %s" % (it.get("path"), name, target)
                        arg = node.args[0] if node.args else None
                        if name in ("import_module", "__import__") and isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _is_wire(arg.value):
                            return "%s calls %s() on the network module %s" % (it.get("path"), name, arg.value)
                        continue   # contained: process starts, exec and dynamic access are confined by the sandbox
                    if runner and target.split(".")[0] == "subprocess":
                        reason = _argv_reason(node, target)
                        if reason:
                            return "%s (a command runner) %s" % (it.get("path"), reason)
                        continue
                    if target and (_is_net(target)
                                   or (target.split(".")[0] == "os" and target.rsplit(".", 1)[-1] in OS_EXEC_ATTRS)):
                        return "%s calls %s(), which is bound to %s" % (it.get("path"), name, target)
                    if name in ("import_module", "__import__"):
                        # importing the product's own modules by a LITERAL name is honest test code; a computed name or a network module is not
                        arg = node.args[0] if node.args else None
                        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _import_allowed(arg.value, allowed_imports, runner)):
                            return "%s calls %s() on a name that is not a plain local module literal" % (it.get("path"), name)
                    elif name in BAD_CALLS and not (name in ORDINARY_METHODS and isinstance(fn, ast.Attribute) and not _denied(target or "")):
                        return "%s calls %s()" % (it.get("path"), name)
    return None


#: How many mutations must APPLY and all be caught before a build counts as tested. Three, because one or two
#: mutations are satisfied by a single assertion and say nothing about the rest of the diff; the estate's own sweep
#: of 2026-09-21 found 39 survivors across seven modules whose suites were green, and every one of those modules
#: carried fewer than three real mutations. It lives HERE, as a constant with its reason, and NOT in a caller flag:
#: a floor a caller can lower from the command line is not a property the recorded verdict implies.
MIN_MUTATIONS = 3

# A done_check is ONE unittest invocation the sandbox runs as a list with no shell: a module, or a scripts/ test file,
# optionally followed by class or method filters and -v. 2026-09-24: 32 of 280 spec sections wrote real invocations
# such as `python3 scripts/test_supply_chain_gate.py LicenseTests -v`, workers copied them, and this regex refused every
# one as "runs no test". Still refused: two commands joined by && or ;, unittest discover, -c one liners, selftests.
# 2026-09-25: H.md puts every NEW test beside its module, so scripts/loop/test_*.py is one level deeper; this regex refused
# it in seven of H3.a's grades and H3.a exhausted. Exactly one optional loop/ level, nothing deeper, no dots in a path part.
OK_CMD = re.compile(r"^python3 (-B )?(-m unittest [\w.]+( [\w.]+)*( -v)?|scripts/(loop/)?test_\w+\.py( [A-Za-z_][\w.]*)*( -v)?)$")


def load(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read().strip()
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


SLOT = [None]            # the machine slot this process holds (local_slot), or None outside one
SANDBOXES = os.path.expanduser(os.environ.get("BROTHER_GRADE_SANDBOXES") or "~/.claude/brother-scratch/grade")
PERSISTENT = set()       # sandbox roots that are reused, never removed at the end of a grade


def _slot_sandbox(tag):
    """The persistent sandbox of THIS slot for this tag (r0, r1, probe), reset to HEAD plus the working tree's changed
    files, or None when there is no slot or git refuses (the caller then makes a fresh clone, as before).
    WHY (owner law 2026-09-24, resource footprint): a fresh clone per scratch() and a copytree per mutation wrote and
    deleted about 33,000 files and 555 MB per grade, 3.3 GB per round, all of it traffic for fseventsd, which grew to a
    39 GB footprint. A git worktree shares the object store and a reset rewrites only what differs; the lock held by
    local_slot for the whole grade is what makes one sandbox safe to reuse (never two graders in one tree)."""
    if SLOT[0] is None: return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    g = lambda *a, **k: subprocess.run(["git"] + list(a), capture_output=True, text=True, env=env, **k)
    sha = g("rev-parse", "HEAD").stdout.strip(); common = g("rev-parse", "--git-common-dir").stdout.strip()
    if not sha or not common: return None
    # keyed by the repository too: a sandbox of another repository would refuse the checkout and fall back to a clone
    key = hashlib.sha256(os.path.realpath(common).encode()).hexdigest()[:8]
    root = os.path.join(SANDBOXES, "slot-%d-%s-%s" % (SLOT[0], key, tag))
    if not os.path.isdir(os.path.join(root, ".git")) and not os.path.isfile(os.path.join(root, ".git")):
        os.makedirs(SANDBOXES, exist_ok=True)
        g("worktree", "prune")
        if g("worktree", "add", "-q", "--detach", "-f", root, sha).returncode != 0: return None
    # the sandbox must be OURS and a worktree of THIS repository before anything destructive runs in it
    top = g("-C", root, "rev-parse", "--show-toplevel").stdout.strip()
    if os.path.realpath(top) != os.path.realpath(root): return None
    if g("-C", root, "checkout", "-q", "--detach", "-f", sha).returncode != 0: return None
    if g("-C", root, "clean", "-q", "-f", "-d", "-x").returncode != 0: return None
    PERSISTENT.add(root)
    return root


def scratch(tag="r"):
    root = _slot_sandbox(tag)
    if root:
        changed = subprocess.run(["git", "diff", "--name-only"], capture_output=True, text=True, check=True).stdout.split()
        for rel in changed:  # the working tree is what the model was shown
            if os.path.isfile(rel):
                os.makedirs(os.path.dirname(os.path.join(root, rel)) or root, exist_ok=True); shutil.copy2(rel, os.path.join(root, rel))
        return root
    return _fresh_scratch()


def _fresh_scratch():
    """A REAL git repository at HEAD (clone --local shares the object store, so the cost is a checkout). A plain `git archive`
    tree is not a valid copy: suites that ask git anything are red in it with no build applied (measured 2026-09-20:
    scripts/test_release_note_from_tree.py 14 failures and 2 errors on a pristine export). Falls back to the archive only when
    the clone cannot be made, and says so."""
    root = tempfile.mkdtemp(prefix="grade-build-")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, env=env).stdout.strip()
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=True, env=env).stdout.strip()
    ok = subprocess.run(["git", "clone", "-q", "--local", "--no-checkout", common, root], capture_output=True, text=True, env=env).returncode == 0 \
        and subprocess.run(["git", "-C", root, "checkout", "-q", "--detach", sha], capture_output=True, text=True, env=env).returncode == 0
    if not ok:
        print("SANDBOX            git clone refused, using a plain archive tree: suites that need git will be dropped as red at base")
        DEGRADED.append("sandbox fell back to a git-less archive tree, so git-dependent suites were dropped")
        shutil.rmtree(root, ignore_errors=True); os.makedirs(root)
        tar = subprocess.run(["git", "archive", "HEAD"], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", root], input=tar, check=True)
    changed = subprocess.run(["git", "diff", "--name-only"], capture_output=True, text=True, check=True).stdout.split()
    for rel in changed:  # the working tree is what the model was shown
        if os.path.isfile(rel):
            shutil.copy2(rel, os.path.join(root, rel))
    return root


def tree_state(root):
    """The sandbox's git status lines: what the build left changed or new. Read after the green apply, so a mutation's
    run can be undone to exactly that state."""
    r = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True)
    return set(r.stdout.splitlines()) if r.returncode == 0 else None


class RestoreRefused(ValueError):
    """A restore pass whose argument or tree state is hostile: refused, never run.

    Raised instead of returning a normal value, so a wrong type, an empty
    string, a NUL in a path, a before set with a non string element, a tree
    state that is not a set of strings and a line that would escape the root
    are each a DELIBERATE refusal at the module boundary rather than a crash
    or a silent accept.
    """


#: The paths whose git checkout exited non zero during the current restore
#: pass. Reset at the start of restore_tree(); read back by
#: restore_tree_report() so the caller can NAME them on the refusal line
#: (H4.c REQ-H-RESTORE).
RESTORE_FAILED = []


def _checkout_failed(rel):
    """Record the path a git checkout refused, and answer None for the restore pass.

    None keeps restore_tree's existing contract: a checkout that exits non
    zero ends the pass. The path lands in RESTORE_FAILED so the caller can
    NAME it rather than print a bare "could not be undone".
    """
    if rel not in RESTORE_FAILED:
        RESTORE_FAILED.append(rel)
    return None


def _state_line_reason(line):
    """Why this git status line may not be restored, else None.

    The path a status line carries is RELATIVE to the sandbox root. A line
    whose path is absolute or carries a .. component would make os.path.join
    escape the root, and the restore would then remove or overwrite a file
    outside the sandbox: that is a refusal, never a silent accept.
    """
    if not isinstance(line, str) or not line:
        return "empty line"
    rel = line[3:].strip().strip('"')
    if not rel:
        return "no path in the line"
    if os.path.isabs(rel) or rel.startswith("/") or rel.startswith("\\"):
        return "absolute path"
    if os.path.splitdrive(rel)[0]:
        return "absolute path"
    for part in rel.replace("\\", "/").split("/"):
        if part == "..":
            return "escapes the root"
    return None


def _states_checked(now, before):
    """(now, before) when both are sets of safe relative path lines, else RestoreRefused.

    The tree state and the recorded state are both read as SETS OF STRINGS. A
    corrupt value (a bool, an int, a list, a bare string returned by a mocked
    or hostile tree_state) is refused here, before the set difference below
    can raise a raw TypeError.
    """
    for label, value in (("now", now), ("before", before)):
        if value is None:
            continue
        if not isinstance(value, (set, frozenset)):
            raise RestoreRefused("%s must be a set of strings or None, got %s" % (label, type(value).__name__))
        for item in value:
            if not isinstance(item, str):
                raise RestoreRefused("%s elements must be strings, got %s" % (label, type(item).__name__))
            why = _state_line_reason(item)
            if why is not None:
                raise RestoreRefused("%s line may not be restored (%s): %r" % (label, why, item))
    return (now, before)


def restore_tree_report(root, before=None):
    """(undone, failed) for one restore pass: H4.c REQ-H-RESTORE.

    `failed` names every path whose git checkout exited non zero; a non empty
    `failed` refuses the next mutation with the paths named. A path that is
    neither tracked nor present counts as undone. An unreadable tree state and
    no state to restore to are failures too, never the safe case.

    A hostile argument raises RestoreRefused, a deliberate refusal. Every
    caller routes through this function, so this one validation guards them
    all: restore_tree() reads the paths back out of RESTORE_FAILED.
    """
    if not isinstance(root, str) or not root or chr(0) in root:
        raise RestoreRefused("root must be a non empty string without NUL")
    _states_checked(None, before)
    if before is None:
        return (0, ["<no state to restore to>"])
    undone = restore_tree(root, before)
    failed = list(RESTORE_FAILED)
    if undone is None and not failed:
        failed = ["<tree state unreadable>"]
    return (0 if undone is None else undone, failed)


def restore_tree(root, before):
    """Undo what a mutation's test run left in the tree beyond the build's own state: a new file is removed, a tracked
    file the run modified is checked out again. The one mutated file is restored by bytes by the caller. Returns the
    number of entries undone, or None when the state cannot be read (the caller then ends the mutations)."""
    del RESTORE_FAILED[:]
    now = tree_state(root)
    _states_checked(now, before)
    if before is None or now is None: return None
    undone = 0
    for line in now - before:
        rel = line[3:].strip().strip('"')
        full = os.path.join(root, rel)
        if line.startswith("??"):
            if os.path.isdir(full) and not os.path.islink(full): shutil.rmtree(full, ignore_errors=True)
            elif os.path.lexists(full): os.remove(full)
        else:
            # a checkout that fails leaves the tree dirty: the state cannot be trusted, so the caller ends the mutations
            # (H4.c REQ-H-RESTORE; found by the BrotherSBE silent failure lint 2026-09-25)
            if subprocess.run(["git", "-C", root, "checkout", "-q", "--", rel], capture_output=True, timeout=60).returncode != 0: return _checkout_failed(rel)
        undone += 1
    return undone


#: Paths a build never writes (eighth review 2026-10-02, reproduced: a graded build could land a GitHub workflow, which
#: runs on the hub with Actions on and nothing enforcing the cost law on this path, and the landing's own trust anchors,
#: which the next landing or push then executes). Owner and session changes to them land through a reviewed branch
#: (land_branch.sh is a git merge and never reads this rule). Compared case folded: this volume is case insensitive.
PROTECTED_FIRST = (".github", ".claude")
PROTECTED_DIRS = ("docs/plan/specs/",)   # case folded prefixes
PROTECTED_ANY = {".gitattributes", ".gitmodules", ".gitignore"}   # .gitignore: ignored paths are invisible to every landing check (eleventh review)
PROTECTED_FILES = ("scripts/pre_push_hook.sh", "scripts/check_all.sh", "scripts/hermetic_test_check.py",
                   "scripts/loop/sandbox.sb",
                   # the landing gates and the push gate run from the tree with no sandbox (land_batch GATES, the hook)
                   "scripts/test_battery_registration.py", "scripts/system_doc.py", "scripts/bundle_runtime.py",
                   "scripts/pre_push_gate.py",
                   # run unsandboxed by the loop from the tree (tenth review 2026-10-02): the pre-commit check at every
                   # landing commit, the closer, the pass's sizing eval, the sentry and the diagnostician
                   "scripts/self_check_staged.py", "scripts/close_unit.py", "scripts/adaptive_sizing.py",
                   "scripts/worktree_sentry.py", "scripts/diag_round.py",
                   # the plan and its specs: the landing writes evidence there itself, and close_unit runs done checks
                   "docs/plan/brother-1.1.0-launch-wbs.json",
                   # EVERY MODULE THE FROZEN CHECKS IMPORT (review 14 finding 1, 2026-10-02): the lander runs the pre-commit
                   # check and the push gate from a frozen copy of the base commit, and the base commit is whatever landed
                   # last, so a build that edits a module those checks import changes the NEXT landing's checks. From an AST
                   # walk of self_check_staged.py, pre_push_gate.py, hermetic_test_check.py and cut_preflight.py (and the
                   # bundle gate's codex_skills import), transitively (export_public.py, exempt then for unit T1,
                   # is protected below since T1 closed).
                   "scripts/private_terms_scan.py", "scripts/loop/secret_scan.py", "scripts/edition_guard.py",
                   "scripts/codex_skills.py", "scripts/cut_preflight.py", "scripts/plugin_bump_gate.py",
                   "scripts/tmp_sandbox.py", "scripts/git_location_guard.py", "scripts/loop/grade_build.py",
                   "scripts/loop/loop_switches.py",
                   # THE REST OF THAT CLOSURE, COMPUTED (review 15 finding 3, 2026-10-03): the hand written slice above missed
                   # 28 modules the same four checks reach. scripts/test_grade_build_guard.py walks the closure with the
                   # hermetic check's import reader and goes red on any member missing here; a new import in a check lands
                   # with its protection or not at all.
                   "plugin/runtime/brother/core/dispatch_semaphore.py", "plugin/runtime/brother/core/model_capability_profile.py",
                   "plugin/runtime/brother/core/openrouter_dispatch.py", "plugin/runtime/brother/core/openrouter_ledger.py",
                   "plugin/runtime/brother/core/openrouter_prices.py", "plugin/runtime/brother/core/openrouter_strict.py",
                   "plugin/runtime/brother/core/repo_paths.py",
                   "scripts/bridge_content_gate.py", "scripts/brother_paths.py", "scripts/client_parity.py",
                   "scripts/coe_outside_gate.py", "scripts/jev_calibration.py",
                   "scripts/jev_cascade.py", "scripts/jev_checks.py", "scripts/jev_decide.py", "scripts/jev_g1_seam_cache.py",
                   "scripts/jev_registry.py", "scripts/jev_seam.py", "scripts/loop/breaker.py", "scripts/loop/claude_ledger.py",
                   "scripts/loop/model_call.py", "scripts/loop/model_reachability.py", "scripts/loop/model_router.py",
                   "scripts/loop/or_ask.py", "scripts/loop/proof_ledger.py", "scripts/reproduce_export.py",
                   "scripts/retire_catalogs.py", "scripts/version_source.py",
                   # imported by retire_catalogs.py since OP1.e (6c02d3cc6, 2026-10-06) without its protection; found red
                   # on main by scripts/test_grade_build_guard.py on 2026-10-10 (unit U0c)
                   "scripts/donecheck_u8.py",
                   # what the frozen builder exports is the landing tree's own allowlist (cut_preflight.export_tree_builder)
                   "docs/plan/export-allowlist.txt",
                   # EVERY MODULE THE LANDER ITSELF RUNS OR IMPORTS FROM THE FROZEN COPY OUTSIDE boxed() (review 16, 2026-10-03):
                   # the closer and the failure ledger run there as plain processes, and gate_order and record_gate import the
                   # D13 policy and dream_gate in process. The same computed walk, its entries widened to close_unit.py,
                   # failure_ledger.py, land_batch.py, dream_gate.py and dream_gate_policy.py, adds these. export_public.py
                   # joins too: unit T1, the one ruling that kept it writable, is DONE (review 16 finding 3).
                   "scripts/export_public.py",
                   # what the push gate RUNS as a child, unboxed (review 17, found by scripts/test_lander_box_tripwire.py: an
                   # import walk never sees a script run by path); system_doc.py is protected above
                   "scripts/record_drift.py",
                   # the one reader of the loop's stop controls, which land_batch.main asks first (D13 hold, 2026-10-03)
                   "scripts/loop/loop_hold.py",
                   "plugin/runtime/brother/core/dream_gate.py", "plugin/runtime/brother/core/dream_gate_policy.py",
                   "scripts/failure_ledger.py", "scripts/gate_order.py", "scripts/grade_build.py", "scripts/prediction_ledger.py",
                   "scripts/loop/ev_gate.py", "scripts/loop/freeze_manifest.py", "scripts/loop/land_batch.py",
                   "scripts/loop/loop_receipt.py", "scripts/loop/plan_lint.py", "scripts/loop/plan_store.py",
                   "scripts/loop/proof_accept.py", "scripts/loop/proof_launch.py", "scripts/loop/provenance.py",
                   "scripts/loop/spec_check.py", "scripts/loop/unit_ledger.py", "scripts/loop/worker_mix.py")


#: (D) DEFENCE IN DEPTH, NOT THE CONTROL (review 17, 2026-10-03, three escapes executed around the list above): a protected
#: module is still shadowed by a file the list never names. The lander's control is that it runs no tree code outside the
#: sandbox except isolated, pinned or protected entries (land_batch.isolated, CLOSER, boxed); these refusals only narrow
#: what a build can stage for a later reader. Three shapes: an __init__ on the package path of a protected module (that
#: package's code runs on every import below it); anything in a protected module's folder whose name stem is the module's
#: (plan_store/, plan_store.so, plan_store.pyc: a package directory and an extension module both win over the .py); and a
#: module named like the standard library at the top of a folder the loop puts on sys.path (scripts/json.py), a file or a
#: package __init__ (a bare directory is a namespace portion, which never beats the real module).
SHADOW_DIRS = ("scripts", "scripts/loop", "plugin/runtime/brother/core")
#: names the standard library had on 3.9 or gained by 3.13, so a tree read by either interpreter is covered whichever one
#: grades it (sys.stdlib_module_names exists from 3.10; on 3.9 the library folder is listed instead)
STDLIB_EXTRA = {"aifc", "asynchat", "asyncore", "audioop", "binhex", "cgi", "cgitb", "chunk", "crypt", "distutils", "formatter",
                "imghdr", "imp", "lib2to3", "mailcap", "msilib", "nis", "nntplib", "ossaudiodev", "parser", "pipes", "smtpd",
                "sndhdr", "spwd", "sunau", "symbol", "telnetlib", "uu", "xdrlib", "tomllib", "graphlib", "zoneinfo", "_bootlocale",
                # the other platform's modules, which a 3.9 library folder listing never shows and the stdlib still probes for
                "_winapi", "msvcrt", "winreg", "winsound", "nt", "_overlapped", "_msi", "_wmi"}


def _stdlib_names():
    names = set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names) | STDLIB_EXTRA
    if not hasattr(sys, "stdlib_module_names"):
        import sysconfig
        for folder in (sysconfig.get_path("stdlib"), os.path.join(sysconfig.get_path("platstdlib"), "lib-dynload")):
            for name in (os.listdir(folder) if os.path.isdir(folder) else ()):
                names.add(name.split(".")[0])
    return frozenset(n.casefold() for n in names if n and n != "site-packages")


STDLIB_NAMES = _stdlib_names()


def shadow_reason(rel):
    """"" when rel (normalised, relative) shadows nothing the lander's isolated or pinned entries read, else why."""
    parts = [p.casefold() for p in rel.replace(os.sep, "/").split("/")]
    last = parts[-1]
    for prot in PROTECTED_FILES:
        if not prot.endswith(".py"):
            continue
        pparts = prot.casefold().split("/")
        folder, stem = pparts[:-1], pparts[-1][:-3]
        if last.split(".")[0] == "__init__" and parts[:-1] == pparts[:len(parts) - 1] and len(parts) - 1 <= len(folder):
            return "an __init__ on the package path of the protected %s" % prot
        if len(parts) > len(folder) and parts[:len(folder)] == folder and parts[len(folder)].split(".")[0] == stem \
                and parts != pparts:
            return "a name that shadows the protected %s" % prot
    for d in SHADOW_DIRS:
        dparts = d.split("/")
        if len(parts) > len(dparts) and parts[:len(dparts)] == dparts and parts[len(dparts)].split(".")[0] in STDLIB_NAMES:
            if len(parts) == len(dparts) + 1 or (len(parts) == len(dparts) + 2 and last.split(".")[0] == "__init__"):
                return "a standard library name at the top of %s/" % d
    # THE REPOSITORY ROOT IS AN IMPORT FOLDER TOO (D13 follow-up, 2026-10-03, Astra option A): the driver's inline
    # `python3 -c` and stdin launches run with the landing tree as their working directory, and for those Python puts the
    # working directory first on sys.path (Python 3.9's -I does not remove it). A standard library name at the root
    # would load in place of the real module there, outside the sandbox.
    module_file = os.path.splitext(last)[1] in (".py", ".pyc", ".pyo", ".pyw", ".so", ".pyd")   # a text file imports nothing
    if parts[0].split(".")[0] in STDLIB_NAMES and module_file and (len(parts) == 1 or (len(parts) == 2 and last.split(".")[0] == "__init__")):
        return "a standard library name at the repository root"
    # startup hooks Python itself looks for by name on the import path, at the root or the top of an import folder
    top = len(parts) == 1 or any(len(parts) == len(d.split("/")) + 1 and parts[:-1] == d.split("/") for d in SHADOW_DIRS)
    if top and (last.split(".")[0] in ("sitecustomize", "usercustomize") or last.endswith(".pth")):
        return "a Python startup hook name (%s) on an import folder" % last
    return ""


def dest(root, path):
    """(full path, "") for a build path whose write stays inside root, else (None, why). ONE check for every writer.

    Lexical first (absolute, .., any .git part, NUL). Then RESOLVED (Codex audit F5, 2026-09-27): a directory symlink
    already in the tree turned `link/x` into a write outside it, and the lexical check alone passed it. The parent is
    resolved and must stay under the resolved root; the destination itself may not be a symlink, even a dangling one,
    because a write through it lands wherever it points."""
    if not isinstance(path, str) or not path or "\0" in path:
        return None, "path is empty or not a plain string"
    rel = os.path.normpath(path)
    # case folded (seventh review 2026-10-02): this volume is case insensitive, so ".GIT/hooks/pre-commit" passed a
    # case sensitive test and landed as a real git hook that ran on the loop's next commit
    if rel.startswith(("..", "/")) or ".git" in [part.casefold() for part in rel.split(os.sep)]:
        return None, "path escapes the tree: %s" % rel
    parts = [part.casefold() for part in rel.split(os.sep)]
    if parts[0] in PROTECTED_FIRST or set(parts) & PROTECTED_ANY or rel.casefold() in PROTECTED_FILES \
            or rel.casefold().startswith(PROTECTED_DIRS):
        return None, "path is one the landing trusts, never written by a build: %s" % rel
    shadow = shadow_reason(rel)
    if shadow:
        return None, "path is one the landing trusts, never written by a build: %s (%s)" % (rel, shadow)
    full = os.path.join(root, rel)
    if os.path.islink(full):
        return None, "path is a symlink, never written through: %s" % rel
    top = os.path.realpath(root)
    parent = os.path.realpath(os.path.dirname(full))
    if parent != top and not parent.startswith(top + os.sep):
        return None, "path resolves outside the tree through a symlink: %s" % rel
    return full, ""


def defined_after_main(src):
    """The line of the first definition after a module's `if __name__ == "__main__":` block, or None. Run as a
    script, the block executes before anything below it exists: measured 2026-09-28, scripts/supply_chain_gate.py
    carried its guard above five functions the gate calls, died with NameError on every run and printed NO-DATA for
    days, and a test file's guard sat above three test classes that a script run never saw. Builds append to the
    end of a file, so the next append lands below the guard. Unparseable text is None: the syntax screen owns it."""
    try:
        body = ast.parse(src).body
    except (SyntaxError, ValueError):
        return None
    for i, node in enumerate(body):
        test = ast.dump(node.test) if isinstance(node, ast.If) else ""
        if "'__name__'" in test and "'__main__'" in test:
            later = [x for x in body[i + 1:] if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                                                ast.Assign, ast.AnnAssign))]
            return later[0].lineno if later else None
    return None


def occurrences(text, find):
    """Every start position of find in text, OVERLAPPING ones included. str.count counts non overlapping matches, so a
    periodic find ("x\nx\n" inside "x\nx\nx\n") read as unique and str.replace applied it at the first match, which may
    not be the place the build meant (found 2026-10-02 replaying native builds). The one uniqueness test every apply
    site routes through: a find is applied only when this returns exactly one position."""
    if not isinstance(text, str) or not isinstance(find, str) or not find:
        return []
    out, i = [], text.find(find)
    while i != -1:
        out.append(i)
        i = text.find(find, i + 1)
    return out


def replace_once(text, find, rep):
    """text with its ONE occurrence of find replaced, or None when find does not occur exactly once (overlaps counted)."""
    at = occurrences(text, find)
    return text[:at[0]] + rep + text[at[0] + len(find):] if len(at) == 1 else None


def apply(root, items, problems, label):
    n = 0
    for it in items or []:
        if not isinstance(it, dict) or not isinstance(it.get("path"), str):
            problems.append("%s: malformed item" % label); continue
        rel = os.path.normpath(it["path"])
        full, why = dest(root, it["path"])
        if why:
            problems.append("%s: %s" % (label, why)); continue
        if "new_file_content" in it:
            if os.path.exists(full):
                problems.append("%s: NEW file already exists: %s" % (label, rel)); continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as fh:
                fh.write(it["new_file_content"])
            n += 1; continue
        if not os.path.isfile(full):
            problems.append("%s: no such file: %s" % (label, rel)); continue
        with open(full, encoding="utf-8", newline="") as fh:   # exact bytes: text mode rewrote carriage returns
            src = fh.read()
        find, rep = it.get("find"), it.get("replace")
        if not isinstance(find, str) or not isinstance(rep, str) or not find:
            problems.append("%s: find/replace missing for %s" % (label, rel)); continue
        out = replace_once(src, find, rep)
        if out is None:
            problems.append("%s: find matches %d times in %s: %r" % (label, len(occurrences(src, find)), rel, find[:60])); continue
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
        n += 1
    for rel in sorted({str(it.get("path", "")) for it in items or [] if isinstance(it, dict)}):
        full = dest(root, rel)[0] if rel.endswith(".py") else None
        if full and os.path.isfile(full):
            try:
                with open(full, encoding="utf-8") as fh:
                    line = defined_after_main(fh.read())
            except (OSError, UnicodeDecodeError) as exc:
                problems.append("%s: %s cannot be read back after the build: %s" % (label, rel, type(exc).__name__))
                continue
            if line:
                problems.append("%s: %s defines something at line %d below its `if __name__ == \"__main__\":` block, "
                                "which a script run executes first; move the block to the end" % (label, rel, line))
    return n


OLD_PYTHON = "/usr/bin/python3"

def interpreter_version(path):
    """The "major.minor" an interpreter reports, or None when it cannot be run.

    AN INTERPRETER IS ITS VERSION, NOT ITS PATH. Measured 2026-09-21 at this grader's entry point:
    launched as /usr/bin/python3, sys.executable resolved to the Xcode shim behind it, so the two
    PATHS differed, the second leg ran, and the grade printed "GREEN-ON-bin/python3 yes" for a run
    that had exercised Python 3.9 TWICE and 3.13 never. A path comparison answered a question nobody
    asked. Asking the interpreter what it is costs one subprocess and cannot be fooled by a shim.
    """
    try:
        r = subprocess.run([path, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


#: Why a run was WEAKER than a full grade, appended by whoever weakened it. A degraded run still prints every line it
#: can, but it may never end in PASS: measured 2026-09-21, the archive fallback below silently drops every suite that
#: needs git as "red at base", so the build is graded against FEWER checks and the PASS line looks identical to a full
#: one. A loosened run that cannot be told apart from a strict one is the defect, not the loosening.
DEGRADED = []


#: The sandbox profile this module runs commands under on macOS. A missing
#: profile refuses to grade, never runs unsandboxed silently.
SANDBOX_PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox.sb")


class SandboxRefused(ValueError):
    """A command may not run because the sandbox is missing or the arguments are hostile."""


def _opted_out():
    """True only when BROTHER_SANDBOX is exactly 'off'."""
    return os.environ.get("BROTHER_SANDBOX") == "off"


def _sandbox_present():
    """True on macOS when sandbox-exec is on PATH."""
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def contained():
    """True only when the sandbox will confine the build's code: sandbox-exec is present, its profile is on disk, and nobody
    opted out. The screen relaxes only under this answer; anything else keeps the strict screen."""
    return _sandbox_present() and not _opted_out() and os.path.isfile(SANDBOX_PROFILE)


#: Extra processes a sandboxed command may start beyond the user's count at launch. A fork bomb meets this ceiling instead
#: of the machine's. macOS offers no per tree memory limit through ulimit; the command's own timeout bounds the rest.
PROC_HEADROOM = 256


def _proc_cap():
    try:
        r = subprocess.run(["ps", "-U", str(os.getuid()), "-o", "pid="], capture_output=True, text=True, timeout=10)
        n = len(r.stdout.split()) if r.returncode == 0 else 0
    except (OSError, subprocess.SubprocessError):
        n = 0
    return (n or 3840) + PROC_HEADROOM   # an unreadable count still gets a fixed ceiling, never none


_APPLIES = []


def _sandbox_applies():
    """'' when sandbox-exec can apply a profile from this process, else why; probed once per process. Measured 2026-10-03:
    inside the hermetic push check, itself running under sandbox.sb, every sandboxed run died with exit 71
    "sandbox_apply: Operation not permitted" (the kernel refuses a sandbox inside this one), and the grader read that as
    tests failing for no stated reason. A sandbox that cannot apply is a sandbox that is not ready, said up front."""
    if not _APPLIES:
        try:
            r = subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                               capture_output=True, text=True, timeout=30)
            _APPLIES.append("" if r.returncode == 0 else
                            "sandbox-exec cannot apply a profile here (exit %s: %s): a sandbox inside a sandbox"
                            % (r.returncode, (r.stderr or "").strip()[:160]))
        except (OSError, subprocess.SubprocessError) as exc:
            _APPLIES.append("sandbox-exec could not be started: %s" % exc)
    return _APPLIES[0]


def sandbox_ready():
    """'' when the sandbox may run, else a reason naming the missing profile or why a profile cannot be applied here."""
    if _opted_out():
        return ""
    if not os.path.isfile(SANDBOX_PROFILE):
        return "sandbox profile is missing: %s" % SANDBOX_PROFILE
    # ONE PROBE, ONE ANSWER (2026-10-03): an absent sandbox-exec used to read '' here (ready) and sandboxed() asked
    # _sandbox_present() a second time to refuse; two questions for one fact let a test's mocked answers drift by one
    # call. Absent is now a reason here, the one place every caller (sandboxed, grade main, land_apply) asks.
    if not _sandbox_present():
        return "no sandbox-exec on this host; nothing runs unconfined (BROTHER_SANDBOX=off is the owner's explicit opt out)"
    return _sandbox_applies()


def sandboxed(cmd, root, tmp):
    """Wrap cmd so it runs under the sandbox profile, or refuse.

    The guard is type-explicit so a hostile argument raises SandboxRefused
    instead of a TypeError from a later operation.
    """
    if not isinstance(cmd, list) or not cmd:
        raise SandboxRefused("cmd must be a non-empty list")
    for item in cmd:
        if not isinstance(item, str) or not item or "\0" in item:
            raise SandboxRefused("cmd elements must be non-empty strings without NUL")
    if not isinstance(root, str) or not root or "\0" in root:
        raise SandboxRefused("root must be a non-empty string without NUL")
    if not isinstance(tmp, str) or not tmp or "\0" in tmp:
        raise SandboxRefused("tmp must be a non-empty string without NUL")
    if _opted_out():
        print("SANDBOX OFF: BROTHER_SANDBOX=off, running unsandboxed")
        return cmd
    # ISOLATION IS MANDATORY (plan E, 2026-10-01): a host without sandbox-exec, a missing profile, or a profile that cannot
    # apply here (a sandbox inside a sandbox) all refuse through sandbox_ready(); nothing runs bare. Grading reads NO-DATA.
    missing = sandbox_ready()
    if missing:
        raise SandboxRefused(missing)
    return ["sandbox-exec", "-f", SANDBOX_PROFILE, "-D", "ROOT=" + os.path.realpath(root),
            "-D", "TMP=" + os.path.realpath(tmp), "/bin/sh", "-c", 'ulimit -u %d && exec "$@"' % _proc_cap(), "sh"] + cmd


#: Every character some reader of a grade file ends a line on: grep splits on \n, Python's splitlines also on \r, \x0b,
#: \x0c, \x1c to \x1e, \x85, U+2028 and U+2029. Codex audit F3 (2026-09-27): the worker's own `unknowns` field printed
#: "worker text\nPASS", a standalone PASS line above the real FAIL, and grade_lane.sh took it as the verdict.
_CTRL = re.compile("[\x00-\x1f\x7f\x85\u2028\u2029]")


def one_line(text):
    """text with every line break and control character escaped, so one print is one line whoever wrote the text."""
    return _CTRL.sub(lambda m: "\\x%02x" % ord(m.group(0)) if ord(m.group(0)) < 0x100 else "\\u%04x" % ord(m.group(0)),
                     str(text))


def say(text):
    """The grader's ONLY way to print a line of its verdict. Worker text (unknowns, a path, a mutation name, a test's
    own output inside a tail) can never start a line of its own, so no worker can print a PASS the grader did not."""
    print(one_line(text))


#: unittest's own closing summary, exactly as the runner writes it ("Ran %d test%s in %.3fs"). ONE per run: a second
#: one is a forged line or a nested run, and neither says what THIS run executed.
_RAN = re.compile(r"^Ran (\d+) tests? in \d+\.\d+s$")
_IMPORT = re.compile(r"^(?:ModuleNotFoundError|ImportError): (?:No module named '([\w.]+)'"
                     r"|cannot import name '(\w+)' from (?:partially initialized module )?'([\w.]+)')", re.M)


def test_verdict(code, out):
    """(kind, detail) for ONE test command, read from the unittest runner's own summary, never from the exit alone.

    Codex audit F1 (2026-09-27): a unittest file with no unittest.main() exits 0 having run nothing, so it graded green,
    and an import failure satisfied both the run without the code and every mutation. The kinds:
      PASSED  exit 0, one summary saying OK, and at least one test executed that was not skipped
      FAILED  one summary saying FAILED with at least one failing or erroring TEST; the loader's stand in for a module
              that did not import is not a test
      IMPORT  the test module itself did not import (the loader's stand in, or a traceback and no summary); detail is
              the dotted name the LAST import error names, the one that ended the run, or ""
      NONE    anything else: no summary, two summaries, zero tests, a timeout, an exit that contradicts an OK summary.
    An unknown shape is NONE, never PASSED."""
    out = out or ""
    lines = out.splitlines()
    at = [i for i, line in enumerate(lines) if _RAN.match(line.rstrip())]
    names = [m.group(1) or "%s.%s" % (m.group(3), m.group(2)) for m in _IMPORT.finditer(out)]
    last = names[-1] if names else ""
    if len(at) > 1:
        return "NONE", "%d runner summaries in one run; one run prints one" % len(at)
    if not at:
        if code != 0 and names:
            return "IMPORT", last
        return "NONE", "exit %s and no unittest summary, so no test is known to have run" % code
    ran = int(_RAN.match(lines[at[0]].rstrip()).group(1))
    verdict = next((line.strip() for line in lines[at[0] + 1:] if line.strip()), "")
    if verdict.startswith("OK"):
        skipped = re.search(r"skipped=(\d+)", verdict)
        executed = ran - (int(skipped.group(1)) if skipped else 0)
        if code == 0 and executed > 0:
            return "PASSED", "%d test(s) ran" % executed
        return "NONE", "exit %s with %d test(s) executed" % (code, executed)
    if verdict.startswith("FAILED"):
        heads = [line for line in lines if line.startswith(("FAIL: ", "ERROR: "))]
        real = [line for line in heads if "_FailedTest" not in line]
        if real:
            return "FAILED", "%d of %d test(s) failed" % (len(real), ran)
        if heads:
            return "IMPORT", last
    return "NONE", "exit %s after the summary %r" % (code, verdict[:40])


def supplied(build):
    """The dotted modules this build's EDITS write: a/b/c.py is a.b.c, a/b/__init__.py is a.b."""
    out = set()
    for it in build.get("edits") or []:
        path = it.get("path") if isinstance(it, dict) else None
        if isinstance(path, str) and path.endswith(".py"):
            parts = [p for p in os.path.normpath(path)[:-3].split(os.sep) if p]
            if parts and parts[-1] == "__init__":
                parts.pop()
            if parts:
                out.add(".".join(parts))
    return out


def _names_the_build(name, mods):
    """True when an import error's dotted name is a module this build writes, reached by its full name, by its last
    parts (a test beside it imports it short), as a package the build creates it in, or as the module a missing NAME
    was imported from (`cannot import name 'f' from 'm'` is reported as m.f)."""
    parent = name.rsplit(".", 1)[0] if "." in name else name
    return any(s == c or s.endswith("." + c) or s.startswith(c + ".") for s in mods for c in (name, parent))


def red_reason(code, out, mods):
    """Why this run WITHOUT the build's code is red for the right reason, or "" when it is not. Right: a test ran and
    failed, or the test module could not import a module this build itself writes (a new module's test cannot load
    without it). Anything else, a crash, a timeout, a missing unrelated module, a script that ran no test, is not
    evidence the tests need the code."""
    kind, detail = test_verdict(code, out)
    if kind == "FAILED":
        return detail
    if kind == "IMPORT" and detail and _names_the_build(detail, mods):
        return "the tests cannot import %s, which this build writes" % detail
    return ""


def fingerprint(root, paths):
    """{path: sha256 of its bytes} for the build's own files as they stand in root. A path that is now a symlink, sits
    under one that leads out of the tree, is missing or unreadable carries its own marker, so swapping a file for a link
    to the same bytes still reads as changed (Codex audit F2, 2026-09-27)."""
    out = {}
    for rel in paths:
        full, why = dest(root, rel)
        if why:
            out[rel] = "refused: " + why
            continue
        try:
            with open(full, "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            out[rel] = "missing or unreadable"
    return out


def changed(root, before):
    """The paths of `before` whose fingerprint in root is no longer the one recorded: a test run rewrote them."""
    now = fingerprint(root, list(before))
    return sorted(p for p in before if now[p] != before[p])


def own_paths(root, items):
    """The normalized paths of the build items that are writable inside root: the files whose bytes are the build."""
    return sorted({os.path.normpath(it["path"]) for it in items or []
                   if isinstance(it, dict) and not dest(root, it.get("path"))[1]})


TAIL_START = ("FAIL", "ERROR", "Ran", "FAILED", "Traceback", "ImportError", "ModuleNotFound", "SyntaxError", "NameError")
EXC_LINE = re.compile(r"^\s*[A-Za-z_][\w.]*(Error|Exception|Failure)\b")


def failure_tail(out):
    """The grade line's summary of a failing command. THE ASSERTION IS THE LESSON (owner 2026-10-01, "reason of rejection
    should be captured ... reused as input for re brief"): the old filter kept "FAIL: test_x" and a bare "Traceback" header
    and dropped the exception line under it, so ACC3.c was re-briefed three rounds running with no reason in hand. Every
    exception line is kept (240 chars), the rest as before (110), 1500 chars in all; no match keeps the last 300 chars."""
    keep = [l.strip()[:240] if EXC_LINE.match(l) else l[:110] for l in (out or "").splitlines()
            if l.startswith(TAIL_START) or EXC_LINE.match(l)]
    return " | ".join(keep)[:1500] or (out or "")[-300:]


#: ONE TEST COMMAND'S LIMIT, seconds. 300 until 2026-10-05: PR1.c's suite (test_precut_review.py) ran 244 s alone at load
#: 3 to 4 on that day's measurement, and its original grade read GREEN-WITH-CODE "exit 124: timeout" at load 2 to 3, a false
#: FAIL. 600 is about 2.5 times that measured 244 s run; the whole grade stays inside unit_runner.GRADE_TIMEOUT_S.
COMMAND_TIMEOUT_S = 600


def run(root, cmds, python=None, guard=None):
    """(worst exit, tail, [(cmd, exit, output)], moved) over cmds, each run sandboxed with its own HOME and TMPDIR.

    `guard`: the fingerprint of the build's own files. It is compared after EVERY command, not once per call: a test
    that rewrote the code in one command and put it back in the next graded other bytes in between, and a check after
    the whole call saw the file as submitted. `moved` names what changed; the commands after it are not run."""
    sandbox = os.path.join(root, ".grade-sandbox"); os.makedirs(sandbox, exist_ok=True)
    os.makedirs(os.path.join(sandbox, "tmp"), exist_ok=True)   # test temp stays in the sandbox (332 modules leaked to the global temp folder)
    # the landing rerun's ONE definition (2026-10-03: this copy kept the run's BROTHER_TRANSPORTS=claude, so a bridge
    # selftest refused itself here while the same suite passed at landing; L5a-7 graded red 7 of 241 on it)
    env = suite_env(os.environ, sandbox)
    worst, tail, each, moved = 0, "", [], []
    for cmd in cmds:
        try:
            r = subprocess.run(sandboxed(cmd.replace("python3", python or sys.executable, 1).split(), root, os.path.join(sandbox, "tmp")), cwd=root, env=env,
                               capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S)
            code, out = r.returncode, (r.stderr + r.stdout)
        except SandboxRefused as exc:
            code, out = 126, "SANDBOX REFUSED: %s" % exc
        except subprocess.TimeoutExpired:
            code, out = 124, "timeout"
        each.append((cmd, code, out))
        if code != 0:
            worst = code
            tail = failure_tail(out)
        if guard is not None:
            moved = changed(root, guard)
            if moved:
                break   # every later command would run on other bytes
    return worst, tail, each, moved


def unproven(each, own):
    """"" when every OWN command in a green run PASSED by the runner's own summary, else what the first one lacked.
    Neighbour suites keep their exit code rule: they guard against regressions, not for this build's tests."""
    for cmd, code, out in each:
        if cmd in own:
            kind, detail = test_verdict(code, out)
            if kind != "PASSED":
                return "%s: %s" % (cmd, detail)
    return ""


def preflight(build, exists=os.path.exists, read=None):
    """'' when the build may enter a sandbox, else the FIRST contract violation, in about a millisecond. Owner
    approved unit E1 (2026-09-22): 108 of the ledger's 145 failures (probe-crash, red-without-code, module-shadowed)
    were contract violations a brief line forbids, and each cost a 187 s grade and a 575 s probe to discover.
    Checks run cheapest first: every item names a path; every Python fragment parses; a test lives beside a module
    in a folder that exists (never an invented tests/ folder); done_check runs more than zero tests; every edit's
    find occurs exactly once in the target as it is now; at least three mutations. Unreadable target: violation."""
    import textwrap
    def rd(p):
        if read: return read(p)
        with open(p, encoding="utf-8") as f: return f.read()
    edits, tests, muts = build.get("edits") or [], build.get("tests") or [], build.get("mutations") or []
    for kind, items in (("edit", edits), ("test", tests), ("mutation", muts)):
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("path"), str) or not it["path"].strip():
                return "a %s item names no path" % kind
    for it in edits + tests:
        # Only a WHOLE new file must parse. A replace fragment is legitimately partial (a def header plus its first
        # lines), and refusing it would reject honest builds: the safety screen already tolerates that shape.
        text = it.get("new_file_content")
        if it["path"].endswith(".py") and isinstance(text, str) and text.strip():
            try: ast.parse(text)
            except (SyntaxError, ValueError): return "%s: the new file does not parse" % it["path"]
    for it in tests:
        folder = os.path.dirname(os.path.normpath(it["path"]))
        if folder and not exists(folder): return "%s: tests live beside the module; folder %r does not exist" % (it["path"], folder)
    dc = (build.get("done_check") or "").strip()
    if not OK_CMD.match(dc): return "done_check %r runs no test (python3 scripts/test_*.py or -m unittest)" % dc[:60]
    created = {it["path"]: it["new_file_content"] for it in edits + tests if isinstance(it.get("new_file_content"), str)}
    for it in edits:
        if isinstance(it.get("find"), str):
            try: body = created[it["path"]] if it["path"] in created else rd(it["path"])   # a file this build creates is readable
            except OSError: return "%s: target unreadable for a find/replace edit" % it["path"]
            n = len(occurrences(body, it["find"]))
            if n != 1: return "%s: find occurs %d times, needs exactly once" % (it["path"], n)
    if len(muts) < 3: return "only %d mutation(s), needs 3 or more" % len(muts)
    return ""


def test_cmds(build):
    cmds = []
    for it in build.get("tests") or []:
        rel = os.path.normpath(it.get("path", ""))
        cmd = "python3 -B " + rel if rel.startswith("scripts/") else "python3 -B -m unittest " + rel[:-3].replace("/", ".")
        if rel.endswith(".py") and cmd not in cmds:
            cmds.append(cmd)
    dc = (build.get("done_check") or "").strip()
    if OK_CMD.match(dc) and dc not in cmds:
        cmds.append(dc)
    # neighbours: every existing suite of a module the build edits (dir/stem.py -> dir/test_stem*.py). A fix that is
    # green in its own tests and breaks a sibling contract was only caught at landing before this (D9.f, 2026-09-20).
    import glob
    for it in build.get("edits") or []:
        rel = os.path.normpath(it.get("path", "")) if isinstance(it, dict) and isinstance(it.get("path"), str) else ""
        stem = os.path.basename(rel)[:-3]
        if not rel.endswith(".py") or stem.startswith("test_"):
            continue
        for hit in sorted(glob.glob(os.path.join(os.path.dirname(rel), "test_%s*.py" % stem))):
            cmd = "python3 -B " + hit if hit.startswith("scripts/") else "python3 -B -m unittest " + hit[:-3].replace("/", ".")
            if cmd not in cmds:
                cmds.append(cmd)
    return [c for c in cmds if OK_CMD.match(c)]


# THE SCREEN AS TEXT FOR A BUILDER (FX-09, 2026-09-29): 52 percent of one day's attempts (12 of 23 grades) were refused
# here before any test ran, for rules no brief ever stated; the only model facing statement of this screen was a hand
# typed sentence in unit_runner.py and build_plan.py that had drifted from it (it forbade shutil, which SAFE_IMPORTS
# allows). screen_rules renders the screen FROM THE CONSTANTS AND FUNCTIONS ABOVE, never from a retyped list, so the brief
# cannot drift from what unsafe() and preflight() enforce. It is advisory text: unsafe() still decides every build.
SCREEN_HEAD = ("THE GRADER'S SCREEN (generated from scripts/loop/grade_build.py at brief time; the grader refuses the "
               "WHOLE build on any line below)")
SCREEN_CAP = 8000   # bytes; the rule lines always fit, module lines past the cap are counted, never silently dropped


def screen_rules(paths, runners=None):
    """The screen as text for a builder, from the same constants and functions unsafe() and preflight() use.
    paths: list or tuple of repo relative path strings the brief shows (anything else: ValueError).
    runners: command runner paths; allowed_runners() when None. Pure: reads files only through
    _repo_module_safe, starts no process, reads no environment, writes nothing."""
    if not isinstance(paths, (list, tuple)) or not all(isinstance(p, str) for p in paths):
        raise ValueError("screen_rules: paths must be a list or tuple of path strings")
    if runners is None:
        runners = allowed_runners()
    elif isinstance(runners, str) or not isinstance(runners, (list, tuple, set, frozenset)) \
            or not all(isinstance(r, str) for r in runners):
        raise ValueError("screen_rules: runners must be a collection of path strings")
    shown_runners = [p for p in dict.fromkeys(paths) if p in runners]
    rules = [
        SCREEN_HEAD,
        "- IMPORTS REFUSED in any file you write: %s; a bare `import urllib` is refused and `from urllib import parse` "
        "is allowed; any relative import (`from . import x`) is refused." % ", ".join(sorted(set(NET_MODULES) | DENY_TOP)),
        "- IMPORTS ALLOWED: the standard library outside that list; a repository module only when its own imports, "
        "followed through the repository, reach nothing refused, no relative import and no import_module or __import__, "
        "and it calls none of os.%s; a module THIS build edits or creates may be imported by its dotted path, and a "
        "scripts/ or scripts/loop/ module also by its bare file stem." % ", os.".join(sorted(OS_EXEC_ATTRS)),
        "- CALLS REFUSED: %s; EXCEPT A METHOD NAMED: %s; such a method on an ordinary object (`obj.run(...)`) passes, "
        "and a bare function of that name is refused even when you defined it yourself." % (
            ", ".join(n + "()" for n in sorted(BAD_CALLS)), ", ".join(sorted(ORDINARY_METHODS))),
        "- OS CALLS REFUSED however they are imported or renamed: %s; and `from os import` any of them is refused."
        % ", ".join("os." + a for a in sorted(OS_EXEC_ATTRS)),
        "- ALWAYS REFUSED (dynamic attribute access): %s; import_module() and __import__() pass only with a string "
        "literal naming a module the IMPORTS rules allow." % ", ".join(n + "()" for n in sorted(DYNAMIC_CALLS)),
        "- TEXT REFUSED: a Python string literal matching the case insensitive regular expression %s; in a file that "
        "is not Python, any match of %s." % (ALWAYS_TEXT.pattern, BLOCK.pattern),
        "- COMMAND RUNNERS shown in this brief: %s; only there may subprocess be imported, and only with a literal "
        "list argv of string constants and shell never set. NETWORK BINARIES refused in an argv: %s; GIT VERBS "
        "refused: %s; INTERPRETERS refused when given code with -c or -e: %s; getoutput() and getstatusoutput() "
        "are refused." % (", ".join(shown_runners) or "none shown", ", ".join(sorted(NET_BINARIES)),
                          ", ".join(sorted(NET_GIT_VERBS)), ", ".join(sorted(INTERPRETERS))),
        "- PREFLIGHT AND APPLY: done_check must match %s; at least %d mutations, each applied and caught; a test lives "
        "beside its module in a folder that exists; every find occurs exactly once in its target; a NEW Python file "
        "parses; a NEW path must not already exist; nothing is defined below `if __name__ == \"__main__\":`."
        % (OK_CMD.pattern, MIN_MUTATIONS),
    ]
    mods = []
    for p in dict.fromkeys(paths):
        base = os.path.basename(p)
        if not p.endswith(".py") or base.startswith("test_"):
            continue
        parts = p[:-3].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        names = [".".join(parts)]
        if (len(parts) == 2 and parts[0] == "scripts") or (len(parts) == 3 and parts[:2] == ["scripts", "loop"]):
            names.append(parts[-1])
        ok = all(_import_allowed(n, frozenset()) for n in names)   # the grader's own function, never a copy
        mods.append("  %s: %s%s" % (p, "importable" if ok else "NOT importable unless your build edits it",
                                    " (command runner)" if p in shown_runners else ""))
    if mods:
        rules.append("- SHOWN MODULES, the grader's verdict on importing each when your build does not edit it:")

    def render(kept):
        more = len(mods) - len(kept)
        tail = ["%d more shown modules are not listed here; each is judged by the rules above" % more] if more else []
        return "\n".join(rules + kept + tail) + "\n"

    kept = list(mods)
    while kept and len(render(kept).encode("utf-8")) > SCREEN_CAP:
        kept.pop()
    return render(kept)


def brief_screen_mode(environ=None):
    """'on' or 'off' from BROTHER_BRIEF_SCREEN. Unset or empty: 'off' (today's brief). Exactly 'on' or 'off'
    after strip() and lower(); any other value: ValueError naming the variable and the value."""
    env = os.environ if environ is None else environ
    raw = env.get("BROTHER_BRIEF_SCREEN")
    if raw is None:
        return "off"
    value = raw.strip().lower() if isinstance(raw, str) else None
    if value == "":
        return "off"
    if value in ("on", "off"):
        return value
    raise ValueError("BROTHER_BRIEF_SCREEN=%r is not on or off" % (raw,))


def private_hits(text):
    """How many private terms an outgoing text carries. ONE function for every gate. Client terms and the first target app's name match
    anywhere. Names from the private list match as WHOLE WORDS only: a four letter name inside the word serialization held an honest
    brief on 2026-09-20. Unreadable list: every text counts as a hit (fails closed)."""
    try:
        names = [l.strip().lower() for l in open(os.path.expanduser("~/.brothersbe-private-names"), encoding="utf-8") if l.strip() and not l.startswith("#")]
    except OSError:
        return 1
    low = text.lower()
    return (text.count("\u0055RRY") + text.count("\u0043CBJI") + low.count("\u0074onari")
            + sum(len(re.findall(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", low)) for n in names))


@contextlib.contextmanager
def local_slot():
    """One of LOCAL_SLOTS (default 5) machine slots, shared by EVERY grader and probe run on this machine through file locks.
    Model calls are free and parallel; sandboxes and test suites are not. Many runners may exist; only this many may use the CPU at once.
    A crashed holder frees its slot by itself: the lock dies with the process."""
    import fcntl, time
    n = max(1, int(os.environ.get("LOCAL_SLOTS", "5")))
    d = os.path.expanduser("~/.claude/evidence/local-slots"); os.makedirs(d, exist_ok=True)
    held = None
    deadline = time.monotonic() + max(60, int(os.environ.get("SLOT_WAIT", "3600") or 3600))   # a bound, never forever
    while held is None:
        if time.monotonic() > deadline:
            raise TimeoutError("no machine slot free within SLOT_WAIT; NO-DATA for this build, not a pass")
        for i in range(n):
            f = open(os.path.join(d, "slot-%d" % i), "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); held = f; SLOT[0] = i; break
            except OSError:
                f.close()
        if held is None:
            time.sleep(2)
    try:
        yield
    finally:
        SLOT[0] = None; held.close()


def main():
    try:
        with local_slot():
            return _main()
    except TimeoutError as exc:
        say("FAIL %s" % exc); return 3
    except Exception as exc:   # a crash of the GRADER judged nothing (review 2026-10-03, A1): exit 3 NO-DATA, never exit 1
        say("NO-DATA grader crashed: %s: %s" % (type(exc).__name__, str(exc)[:300])); return 3


def _main():
    path, keep = sys.argv[1], "--keep" in sys.argv
    reasons = []
    nodata = []   # reasons that say a leg never ran or the run was loosened: no judgment of the build (2026-10-03)
    try:
        build = load(path)
    except (ValueError, OSError) as exc:
        say("FAIL unparseable build: %s" % exc); return 1
    why = unsafe(build, allowed_imports=_build_imports(build), contained=contained())
    if why:
        say("FAIL safety screen: %s; read it by hand before anything else" % why); return 1
    why = preflight(build)
    if why:
        say("FAIL preflight: %s" % why); return 1
    refused = sandbox_ready()
    if refused:
        # NOTHING RAN, SO NOTHING WAS JUDGED (review 2026-10-03): exit 3, the grader's own NO-DATA, never a FAIL that
        # unit_runner would read as a rejection and pay a repair round for.
        say("NO-DATA sandbox: %s" % refused)
        return 3
    cmds = test_cmds(build)
    if not cmds:
        say("FAIL no runnable, allow-listed test command"); return 1
    roots = []
    try:
        # 1. tests alone must fail FOR A REASON A TEST STATES: a test ran and failed, or the test cannot import a module
        #    this build writes. Codex audit F1 (2026-09-27): any nonzero exit counted, so a file that ran no test at all
        #    went "red" on an import error and "green" on an exit 0 that executed nothing.
        mods = supplied(build)
        r0 = scratch("r0"); roots.append(r0); p0 = []
        apply(r0, build.get("tests"), p0, "tests")
        # THE BYTES THE VERDICT IS ABOUT (Codex audit F2, 2026-09-27): a test that rewrote the implementation during
        # its own green run graded the corrected file, not the submitted one. Every run is checked against these.
        fp0 = fingerprint(r0, own_paths(r0, build.get("tests")))
        tampered = []
        # a neighbour suite may only count against the build if it is GREEN on the base: a suite already red proves nothing
        own = set(test_cmds({"tests": build.get("tests"), "done_check": build.get("done_check")}))
        for c in [c for c in cmds if c not in own]:
            code, _, _, moved = run(r0, [c], guard=fp0)
            tampered += moved
            if code != 0:
                cmds.remove(c); say("NEIGHBOUR          red at base, dropped: %s" % c)
        _, _, each, moved = run(r0, [c for c in cmds if c in own], guard=fp0)
        tampered += moved
        red = next((r for r in (red_reason(code, out, mods) for _, code, out in each) if r), "")
        vacuous = all(code == 0 for _, code, _ in each)
        if red:
            say("RED-WITHOUT-CODE   yes (%s)" % red)
        elif vacuous:
            say("RED-WITHOUT-CODE   NO: tests pass without the code")
            reasons.append("tests pass without the code")
        else:
            why = next("%s: %s" % (c, test_verdict(code, out)[1]) for c, code, out in each if code != 0)
            say("RED-WITHOUT-CODE   NO: it failed, but no test ran and failed and no import of this build's own module "
                "failed (%s)" % why)
            reasons.append("tests fail without the code for no reason a test states")
        # 2. code + tests must pass
        r1 = scratch("r1"); roots.append(r1); p1 = []
        ne = apply(r1, build.get("edits"), p1, "edit"); nt = apply(r1, build.get("tests"), p1, "tests")
        say("APPLY              %d edits, %d test items, %d problems" % (ne, nt, len(p1)))
        for p in p1:
            say("   - " + p)
        if p1:
            reasons.append("%d patch problems" % len(p1))
        green_state = tree_state(r1)   # what the build itself put in the tree, before any run: every mutation is undone to this
        fp1 = fingerprint(r1, own_paths(r1, (build.get("edits") or []) + (build.get("tests") or [])))
        green = 1
        if not tampered:
            green, tail, each, moved = run(r1, cmds, guard=fp1)
            tampered += moved
            short = "" if green else unproven(each, own)
            if short:
                green = 5   # unittest's own code for a run that executed no test
            say("GREEN-WITH-CODE    %s" % ("yes" if green == 0 else "NO: exit 0 but %s" % short if short else
                                           "NO exit %s: %s" % (green, tail)))
            if green != 0:
                reasons.append("suite not green with the code" if not short else "a test command of this build executed no test")
        else:
            say("GREEN-WITH-CODE    NOT RUN: the run without the code already rewrote this build's own bytes")
        # TWO INTERPRETERS, AND WHICHEVER ONE LAUNCHED THE GRADER DOES NOT GET TO DECIDE. The product ships on the
        # system Python (3.9 on this estate) and on the modern one (3.13), so a build green on one alone is not green.
        # Measured 2026-09-21: this leg ran ONLY when sys.executable differed from OLD_PYTHON and that file existed,
        # so launching the grader AS /usr/bin/python3 skipped it in silence and a 3.9 only build could be recorded
        # PASS. That made the verdict a statement about the caller rather than about the build. Both absences now
        # REFUSE: an interpreter that was never run proves nothing, and an unrun leg is NO-DATA, never a pass.
        if green == 0 and not tampered:
            other = interpreter_version(OLD_PYTHON)
            mine = "%d.%d" % sys.version_info[:2]
            if other is None:
                say("GREEN-ON-OLD       NO-DATA: %s could not be run, so that leg was never exercised" % OLD_PYTHON)
                reasons.append("cannot prove the build on %s: that interpreter did not run here" % OLD_PYTHON); nodata.append(reasons[-1])
            elif other == mine:
                say("GREEN-ON-OLD       NO-DATA: %s is Python %s and so is this grader, so only ONE version was ever exercised"
                    % (OLD_PYTHON, other))
                reasons.append("grader runs Python %s and so does %s: one version was exercised, not two" % (mine, OLD_PYTHON)); nodata.append(reasons[-1])
            else:
                old, otail, oeach, moved = run(r1, cmds, python=OLD_PYTHON, guard=fp1)
                tampered += moved
                oshort = "" if old else unproven(oeach, own)
                if oshort:
                    old, otail = 5, "exit 0 but " + oshort
                say("GREEN-ON-PY%-6s %s" % (other, "yes (this grader proved %s)" % mine if old == 0 else "NO exit %s: %s" % (old, otail)))
                if old != 0:
                    reasons.append("suite not green on %s" % OLD_PYTHON)
        # 3. each mutation on top of the patched tree must turn it red.
        #
        # SHORT CIRCUITED WHEN THE TESTS ALREADY PASS WITHOUT THE CODE, and the argument is about MEANING before
        # it is about cost. A mutation asks "do these tests notice a change to the code". If the tests did not
        # notice the code being REMOVED ENTIRELY, they cannot notice a mutation of it, so every mutation result
        # here is known in advance and tells nobody anything. Running them is not a cheaper answer to a harder
        # question; it is no answer at all.
        #
        # The cost it removes is the largest single item in the grade. Each mutation is a FULL COPYTREE of the
        # patched sandbox plus a test run. Measured 2026-09-21 over 25.25 machine hours: grade averaged 195 s
        # and 102 of 166 builds failed it, and 25 of 51 sampled failures were exactly this verdict, each one
        # paying for its whole mutation suite after the verdict was already decided.
        #
        # GREEN-WITH-CODE above is deliberately still run: it is ONE test run, not N tree copies, and it tells
        # the repair round whether the implementation itself works, which is the evidence that makes the next
        # round different. The estate's own law is to cut the static rules before the specific failure evidence,
        # and this cuts neither: it cuts work whose result is already implied.
        caught = applied = 0
        mutations = build.get("mutations") or []
        if not red and mutations:
            say("MUTATIONS          SKIPPED, %d not run: %s" % (len(mutations),
                "the tests already pass WITHOUT the code, so they cannot catch a mutation OF it and the result is known"
                if vacuous else "no test ran and failed without the code, so a mutation's red could not be told from that one"))
            mutations = []
        elif green != 0 and mutations:
            # NOT GREEN WITH THE CODE, THE VERDICT IS ALREADY FAIL (2026-10-05): a mutation of a tree that is already red is
            # red whatever the tests check, and the mutation reason below counts only when green == 0. Measured alone, the
            # PR1.c grade spent 1693 of its 2186 s on six such mutations, and MG1.e and PR1.c hit the 1800 s grade limit.
            say("MUTATIONS          SKIPPED, %d not run: the suite is not green with the code, so the verdict is already "
                "FAIL and a mutation of a red tree is red whatever the tests check" % len(mutations))
            mutations = []
        if tampered:
            mutations = []
        for m in mutations:
            # IN PLACE on the green tree, the touched file's bytes restored after (a copytree per mutation was 6,621
            # files written and deleted each time); the restore is checked, and a failed restore ends the mutations.
            # The target is RESOLVED first (Codex audit F5): a mutation whose path leads out of the tree is never read
            # or written, and nothing is written back when it did not apply (it wrote nothing).
            target, bad = dest(r1, m.get("path"))
            if bad:
                say("   mutation %-38s DID NOT APPLY %s" % (str(m.get("name"))[:38], ["mutation: " + bad]))
                continue
            saved = open(target, "rb").read() if os.path.isfile(target) else None
            pm = []
            if apply(r1, [m], pm, "mutation") != 1:
                say("   mutation %-38s DID NOT APPLY %s" % (str(m.get("name"))[:38], pm[:1]))
                continue
            applied += 1
            expect = dict(fp1)
            expect.update(fingerprint(r1, [os.path.normpath(m["path"])]))   # the mutated bytes, as this grader wrote them
            code, _, each, moved = run(r1, cmds, guard=expect)
            tampered += moved
            if tampered:
                break   # never write back through what the run left: the sandbox is reset at its next use
            if saved is None:
                if os.path.isfile(target): os.remove(target)
            else:
                open(target, "wb").write(saved)
            # the run's own leftovers (a file a test wrote into the tree, a tracked file it changed) go too, so the next
            # mutation starts from the green tree and not from this one's side effects (auditor finding 2026-09-24)
            _undone, _failed = restore_tree_report(r1, green_state)
            if (open(target, "rb").read() if os.path.isfile(target) else None) != saved or _failed:
                reasons.append("a mutation could not be undone in the sandbox, so later mutations were not run: "
                               + (", ".join(_failed) if _failed else "the one mutated file was not restored byte for byte")); break
            # CAUGHT MEANS A TEST FAILED (Codex audit F1): a mutation that only stops a module importing is not one
            hit = any(test_verdict(c, o)[0] == "FAILED" for _, c, o in each)
            caught += 1 if hit else 0
            say("   mutation %-38s %s" % (str(m.get("name"))[:38], "caught" if hit else "SURVIVED" if not code else
                                          "SURVIVED: exit %s but no test ran and failed (a module that does not import is not a catch)" % code))
        if red or not (build.get("mutations") or []):
            say("MUTATIONS          %d of %d applied were caught" % (caught, applied))
        # A build that never went red without its code is ALREADY failing on that reason; adding a mutation
        # complaint on top would be a second reason for a verdict the first already decided, and would teach the
        # repair round to chase mutation counts instead of writing a test that can fail. The same holds for a run
        # that rewrote the build's own bytes: its counts are about other bytes, so that is the ONE reason given.
        if tampered:
            say("BYTES              CHANGED by a test run: %s" % ", ".join(sorted(set(tampered))))
            reasons.append("a test run rewrote this build's own file(s) %s, so the verdict would belong to other bytes"
                           % ", ".join(sorted(set(tampered))))
        elif red and green == 0 and (applied < MIN_MUTATIONS or caught < applied):
            reasons.append("mutations: %d applied, %d caught (need %d or more, all caught)" % (applied, caught, MIN_MUTATIONS))
        for d in dict.fromkeys(DEGRADED):   # scratch() runs per sandbox, so one degradation is recorded twice
            say("DEGRADED           %s" % d)
            reasons.append("run was loosened: %s, so it is not usable as evidence" % d); nodata.append(reasons[-1])
        say("COMMANDS           %s" % cmds)
        say("UNKNOWNS           %s" % (build.get("unknowns") or "none"))
        record_claim("grade", str(path), "this build passes, so it is worth probing", 1.0 if not reasons else 0.0)
        if reasons and all(r in nodata for r in reasons):
            # every reason is a leg that never ran or a loosened run: the build was not judged, exit 3 (NO-DATA)
            say("NO-DATA: " + "; ".join(reasons))
            return 3
        say("PASS" if not reasons else "FAIL: " + "; ".join(reasons))
        return 0 if not reasons else 1
    finally:
        if not keep:
            for r in roots:
                if r not in PERSISTENT:   # a slot sandbox is reset at its next use, never removed and recloned
                    shutil.rmtree(r, ignore_errors=True)


from typing import Sequence

#: The five judging tools and the predictor name each of them writes under (P1.c R5). The name is
#: not derivable from the file name, so it is written down here rather than guessed at read time.
PREDICTOR_FILES = {
    "spec_score.py": "spec_score",
    "grade_build.py": "grade",
    "probe_build.py": "probe",
    "spec_council.py": "council",
    "diag_brief.py": "diagnostic",
}


def wired_predictors(paths: Sequence) -> dict:
    """{predictor: call site path or empty string}: which judging tools write a claim.

    Each path is read as BYTES and decoded as UTF-8, and is wired when its own source calls the ledger:
    predict( directly, or the record_claim( wrapper that every call site here uses. A source that cannot be
    read or decoded is NO-DATA and is reported with the empty call site, because a source this reader cannot
    read may never read as wired. A value that is not a list or tuple, or an element that is not a non empty
    string, is refused with ValueError, never a raw TypeError. The predictor name is the table above, or the
    file's stem when the source is not one of the five.
    """
    if isinstance(paths, (str, bytes)) or not isinstance(paths, (list, tuple)):
        raise ValueError("paths must be a list or tuple of file paths, not %s" % type(paths).__name__)
    out = {}
    for path in paths:
        if not isinstance(path, str) or not path:
            raise ValueError("every path must be a non empty string, not %r" % (path,))
        name = PREDICTOR_FILES.get(os.path.basename(path), os.path.splitext(os.path.basename(path))[0])
        out[name] = ""
        try:
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "predict(" in text or "record_claim(" in text:
            out[name] = path
    return out


def mirror_matches(tracked_dir: str, installed_dir: str) -> list:
    """Relative names of every file whose bytes differ between the tracked and the installed copy.

    Empty list means the mirror is honest. Bytes are compared, never sizes and never mtimes. A file present
    in only one of the two directories is named too: a missing twin is drift and may never read as honest. A
    directory that is not a non empty string, or is not a directory on disk, is refused with ValueError,
    because an unreadable mirror is NO-DATA and never a pass.
    """
    for label, where in (("tracked_dir", tracked_dir), ("installed_dir", installed_dir)):
        if not isinstance(where, str) or not where:
            raise ValueError("%s must be a non empty string" % label)
        if not os.path.isdir(where):
            raise ValueError("%s is not a directory: %s" % (label, where))

    def listing(root):
        found = {}
        for base, _dirs, files in os.walk(root):
            for name in files:
                full = os.path.join(base, name)
                found[os.path.relpath(full, root)] = full
        return found

    left, right = listing(tracked_dir), listing(installed_dir)
    out = []
    for rel in sorted(set(left) | set(right)):
        a, b = left.get(rel), right.get(rel)
        if a is None or b is None:
            out.append(rel)
            continue
        try:
            with open(a, "rb") as fa, open(b, "rb") as fb:
                if fa.read() != fb.read():
                    out.append(rel)
        except OSError:
            out.append(rel)
    return out


# THE LEDGER IS IMPORTED BY NAME (2026-09-28): it was loaded by a computed file path, which the deploy freeze
# cannot trace, so every deploy after P1.c landed was refused, and in the deployed layout that path pointed outside
# the tool folder, so no claim was ever written. By name, the freeze ships prediction_ledger.py beside this file
# and the import finds it there; in the repository it is found on scripts/. A ledger that cannot be imported is
# None here, a NO-DATA, and never an import error that takes the judging tool down with it (P1.c behaviour 2).
try:
    import prediction_ledger as _PREDICTION_LEDGER
except (ImportError, SyntaxError, ValueError, TypeError, KeyError, IndexError, AttributeError, NameError,
        AssertionError, RuntimeError, OSError):  # sbe: allow-silent an absent or broken ledger is NO-DATA; record_claim returns False and says so on stderr for every claim it loses
    _PREDICTION_LEDGER = None

_DEFAULT_LEDGER = object()


def record_claim(predictor, subject, claim, confidence=None, ledger=_DEFAULT_LEDGER):
    """Record one judgement as a claim about the future, before that future arrives (P1.c R5).

    True when the ledger accepted the row, False when the ledger is missing, unreadable or refusing. A ledger
    failure NEVER raises out of here and never changes the calling tool's exit code (P1.c behaviour 2): an
    instrument may not break the tool it measures. A hostile ARGUMENT is refused with ValueError, never a raw
    TypeError from deeper in, and a confidence that is not a real number between 0.0 and 1.0 (a bool, a
    string, NaN) is refused rather than scored.
    """
    if not isinstance(predictor, str) or not predictor.strip():
        raise ValueError("predictor must be a non empty string")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("subject must be a non empty string")
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("claim must be a non empty string")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be None or a real number, not %s" % type(confidence).__name__)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
    module = _PREDICTION_LEDGER if ledger is _DEFAULT_LEDGER else ledger
    if module is None or not hasattr(module, "predict"):
        return _not_recorded(predictor, subject, "the prediction ledger could not be imported")
    try:
        if confidence is None:
            module.predict(predictor=predictor, subject=subject, claim=claim)
        else:
            module.predict(predictor=predictor, subject=subject, claim=claim, confidence=confidence)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, NameError, OSError, RuntimeError,
            AssertionError) as exc:
        return _not_recorded(predictor, subject, "%s: %s" % (type(exc).__name__, exc))
    return True


def _not_recorded(predictor, subject, why):
    """The one place a lost claim is surfaced (2026-09-30): every caller discards record_claim's False, so the
    NO-DATA line is written here, on stderr, and never changes the calling tool's exit code."""
    sys.stderr.write("NO-DATA: %s claim on %s was not recorded (%s)\n" % (predictor, subject, str(why)[:200]))
    return False


if __name__ == "__main__":
    sys.exit(main())
