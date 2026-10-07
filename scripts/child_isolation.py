"""Child process isolation for the Jev test suites (R4.3, built 2026-10-03).

Every child a Jev test starts runs through _run_isolated, and nowhere else:
  * its environment comes from _child_env: a fresh HOME made inside the test's own state dir, the test's state dir as
    BROTHER_JEV_STATE_DIR, the repository's scripts/ on PYTHONPATH, and nothing from the live environment but PATH and
    the locale, so no live home and no live config can reach the child through a variable it was never meant to see;
  * its argv is checked against the live paths the suite captured at import (the live home, its state dir, budget and
    canary paths): a live state path on the command line refuses the call before anything starts;
  * its cwd is a temp directory, never the repository, so a stray file cannot land in the tree.
_assert_no_subprocess_run_directly is the AST audit that keeps every other subprocess call site out of the suites.

Why a module and not helpers inside the tests: R4.3 measured 85 grades refused because a test file imported subprocess,
which the build screen allows only in a declared command runner. This file is that runner for R4.
Standard library only, no side effect on import. Every refusal is a ValueError naming its reason, never a crash.
"""
import ast
import os
import subprocess
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS)
SubprocessError = subprocess.SubprocessError     # the suites catch these without importing subprocess themselves
TimeoutExpired = subprocess.TimeoutExpired
#: the only live variables a child keeps: how to find programs, and the locale its output is decoded in
KEEP = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
LIVE_KEYS = ("state_dir", "budget_path", "canary_path")
_SPAWNING = frozenset({"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"})


def _is_dir(value):
    return isinstance(value, str) and bool(value) and "\0" not in value and os.path.isabs(value) and os.path.isdir(value)


def _under(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _child_env(state_dir, home_files=None):
    """The whole environment of a child: PATH and locale from the live one, a fresh HOME inside state_dir (never reused),
    BROTHER_JEV_STATE_DIR set to state_dir, PYTHONPATH set to the repository's scripts/. Nothing else passes through,
    so a live HOME, a live BROTHER_* variable or a credential in the parent never reaches the child. A caller adds
    the variables its child needs (a bridge command, a pid file) to the returned dict; _run_isolated checks them.
    home_files: {path relative to the new HOME: text} written into it, for fixtures a child reads from its HOME (the
    content gate's terms list); a path that is absolute or leaves the home is refused."""
    if not _is_dir(state_dir):
        raise ValueError("state_dir must be an existing absolute directory, got %r" % (state_dir,))
    if home_files is not None and not isinstance(home_files, dict):
        raise ValueError("home_files must be a dict of relative path to text")
    home = tempfile.mkdtemp(prefix="child-home-", dir=state_dir)
    for rel, text in (home_files or {}).items():
        if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel) or not isinstance(text, str):
            raise ValueError("home_files entries must be relative path strings to text, got %r" % (rel,))
        dest = os.path.join(home, rel)
        if not _under(dest, home) or os.path.realpath(dest) == os.path.realpath(home):
            raise ValueError("home_files path %r leaves the child home" % rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
    env = {k: os.environ[k] for k in KEEP if os.environ.get(k)}
    env.update(HOME=home, BROTHER_JEV_STATE_DIR=state_dir, PYTHONPATH=SCRIPTS, PYTHONDONTWRITEBYTECODE="1")
    return env


def _live_paths_or_refuse(live_paths):
    if not isinstance(live_paths, dict):
        raise ValueError("live_paths must be the dict the suite captured at import")
    out = {}
    for key in LIVE_KEYS:
        value = live_paths.get(key)
        if not isinstance(value, str) or not value or not os.path.isabs(value):
            raise ValueError("captured live path %r is missing: a child cannot be checked, so it does not start" % key)
        out[key] = value
    home = live_paths.get("home", "")
    if not isinstance(home, str):
        raise ValueError("captured live home must be a string")
    out["home"] = home
    return out


def _assert_argv_has_no_live_path(argv, live_paths):
    """ValueError when any argv element names a live state path: equal to the live home, or holding the live state dir,
    budget path or canary path anywhere in its text (a `--x=<path>` form included). The repository itself may sit
    under the live home, so a path under the home that is not its Jev state is allowed: the state is what a child
    must never reach through an argument."""
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ValueError("argv must be a non empty list of strings")
    live = _live_paths_or_refuse(live_paths)
    for arg in argv:
        if not isinstance(arg, str) or not arg or "\0" in arg:
            raise ValueError("argv elements must be non empty strings without NUL, got %r" % (arg,))
        if live["home"] and os.path.normpath(arg) == os.path.normpath(live["home"]):
            raise ValueError("argv carries the live home %r" % arg)
        for key in LIVE_KEYS:
            if live[key] in arg or (os.path.isabs(arg) and _under(arg, live[key])):
                raise ValueError("argv carries the live %s: %r" % (key, arg))


def _assert_env_is_isolated(env, live_paths):
    live = _live_paths_or_refuse(live_paths)
    if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
        raise ValueError("env must be a dict of strings built by _child_env")
    home, state = env.get("HOME", ""), env.get("BROTHER_JEV_STATE_DIR", "")
    if not _is_dir(home) or not _is_dir(state) or not _under(home, state) or os.path.realpath(home) == os.path.realpath(state):
        raise ValueError("env HOME must be the fresh home _child_env made inside the state dir, got %r" % home)
    if live["home"] and _under(home, live["home"]) and not _under(home, tempfile.gettempdir()):
        raise ValueError("env HOME %r resolves under the live home" % home)
    for k, v in env.items():
        for key in LIVE_KEYS:
            if live[key] in v:
                raise ValueError("env %s carries the live %s" % (k, key))


def _run_isolated(argv, env, live_paths, cwd, timeout):
    """Run argv once, as a child that can reach no live state: argv and env checked first (a refusal starts nothing),
    cwd a directory outside the repository. Returns the CompletedProcess (stdout and stderr captured as text); a
    timeout or a failure to start raises subprocess's own error, which the suites catch as child_isolation's names."""
    _assert_argv_has_no_live_path(argv, live_paths)
    _assert_env_is_isolated(env, live_paths)
    if not _is_dir(cwd) or _under(cwd, REPO_ROOT):
        raise ValueError("cwd must be an existing temp directory outside the repository, got %r" % (cwd,))
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
        raise ValueError("timeout must be a number of seconds in (0, 600], got %r" % (timeout,))
    return subprocess.run(list(argv), env=dict(env), cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def _assert_no_subprocess_run_directly(module_source):
    """ValueError naming every line that starts a process outside _run_isolated: subprocess.run and its siblings
    (Popen, call, check_call, check_output, getoutput), called through the module or through a name imported from it,
    and os.system or os.popen. Zero exceptions: a suite that needs a child goes through _run_isolated."""
    if not isinstance(module_source, str):
        raise ValueError("module_source must be the text of a Python file")
    try:
        tree = ast.parse(module_source)
    except SyntaxError as exc:
        raise ValueError("module source does not parse: %s" % exc)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            imported.update(a.asname or a.name for a in node.names if a.name in _SPAWNING)
    bad = []

    def visit(node, inside):
        for child in ast.iter_child_nodes(node):
            here = inside or (isinstance(child, ast.FunctionDef) and child.name == "_run_isolated")
            if isinstance(child, ast.Call) and not here:
                f = child.func
                direct = (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                          and ((f.value.id == "subprocess" and f.attr in _SPAWNING)
                               or (f.value.id == "os" and f.attr in ("system", "popen"))))
                if direct or (isinstance(f, ast.Name) and f.id in imported):
                    bad.append(child.lineno)
            visit(child, here)

    visit(tree, False)
    if bad:
        raise ValueError("a process is started outside _run_isolated at line(s) %s" % ", ".join(map(str, sorted(bad))))


#: fixture files a Jev child reads from its HOME: the content gate refuses to run without its terms list
FIXTURE_HOME_FILES = (os.path.join(".claude", "coe-outside-gate-terms.json"),)


def fixture_home_files(live_home):
    """The FIXTURE_HOME_FILES this process's own HOME holds, as home_files for _child_env. Read only from a fixture HOME:
    when HOME is unset or is the live home, nothing is copied (a child then refuses on the missing list, never runs on
    live data). A file that cannot be read is left out the same way."""
    home = os.environ.get("HOME", "")
    if not home or not isinstance(live_home, str) or (live_home and os.path.realpath(home) == os.path.realpath(live_home)):
        return {}
    out = {}
    for rel in FIXTURE_HOME_FILES:
        try:
            with open(os.path.join(home, rel), encoding="utf-8") as fh:
                out[rel] = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
    return out
