"""hermetic_test_check.py: a changed test is run where the public runner runs it.

WHY THIS EXISTS. Three of the six refusals of the 1.0.21 cut on 2026-09-20
were tests that passed only on their author's machine: two needed a folder
the public tree does not ship, four needed a private file in the operator's
home. Each was green in its own branch, green in review, green on main, and
red only at the cut or on the public runner. cut_preflight.py catches that
class when a cut starts; this catches it BEFORE THE PUSH, when the author
still has the change in front of them.

For every scripts/ file changed in a range, the test that covers it (the file
itself when it is a test, scripts/test_<name>.py otherwise) is run on an
export shaped tree with HOME empty and no git identity, through
cut_preflight's own builder and environment so the two cannot disagree.

Exit 0 when every such test passed or nothing changed. Exit 1 when one
failed. Exit 2 when one could not be run (NO-DATA), a timeout included.

A test that fails there is run ONCE more, in a disposable detached worktree
of the checkout's HEAD under a fresh empty HOME (never in the checkout
itself: review 14, 2026-10-02), and only to choose the refusal's words: red
there too is not a hermeticity defect. The verdict is REFUSED either way.

BOTH RUNS ARE CONFINED (decision D12, 2026-10-02). A touched test imports the
build's own modules, and the landing pushes after every landing, so until
then a landed build's code ran here on the open host: real network, writes
anywhere. Each run now goes through the grader's sandbox (grade_build
.sandboxed: writes only under the tree it runs in and its throwaway HOME, no
network, the tree's own .git shut) with the landing suite environment
(loop_switches.suite_env: HOME and TMPDIR under that home, no run knobs, no
GIT_ variables). The export run and the checkout rerun share one wrap, so a
red the sandbox causes reads the same on both sides and is named "red in the
checkout too", never a hermeticity defect. A host that cannot confine the run
reads NO-DATA and runs nothing bare; the pre-push hook stops on NO-DATA.

The hermetic gate covers scripts/ only. --reach is a separate, read-only
selection mode over scripts/, scripts/loop/ and plugin/runtime/brother/core/;
it prints impacted test paths and selftest commands without running them.
"""
import argparse
import ast
import contextlib
import importlib.util
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import cut_preflight as P  # noqa: E402
import evidence_obligation as EO  # noqa: E402
import export_public as E  # noqa: E402
import timing_hunt as TH  # noqa: E402


def _loop_module(name):
    """scripts/loop/<name>.py loaded by EXPLICIT PATH, the way pre_push_gate
    loads secret_scan: scripts/ carries an unrelated grade_build.py of its
    own, so a bare import, or scripts/loop at the front of sys.path, would
    resolve the wrong module for this file or shadow the right one for every
    other consumer. A load failure propagates: the gate then exits non zero,
    a refusal, never a clean pass."""
    path = os.path.join(HERE, "loop", name + ".py")
    spec = importlib.util.spec_from_file_location("brother_loop_" + name, path)
    if spec is None or spec.loader is None:
        raise ImportError("the loop module could not be loaded: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _loop_module("grade_build")
LS = _loop_module("loop_switches")

OK, REFUSED, NODATA = P.OK, P.REFUSED, P.NODATA
#: test_brother_run.py, the longest suite in scripts/, measured 200s.
TIMEOUT_S = 900
#: A suite that waits in real time (the proof pair rehearsal: 473.7 s alone in a checkout, over 900 s on the export
#: tree inside the pre-push gate, 2026-09-27) declares its own deadline in its first lines as
#: "# hermetic-budget-seconds: N". The run is still complete; only its deadline moves, and never past this cap.
BUDGET_CAP_S = 3600
_BUDGET_RE = re.compile(r"^#\s*hermetic-budget-seconds:\s*(\d+)\b", re.M)   # a trailing reason is allowed


def declared_budget(path, default=TIMEOUT_S):
    """The deadline a test file declares in its first 4 KB, capped at BUDGET_CAP_S; the default when it declares none
    or the file cannot be read (an unreadable file then fails its own run, never gains time)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return default
    m = _BUDGET_RE.search(head)
    return min(int(m.group(1)), BUDGET_CAP_S) if m else default


def _checked_root(root, who):
    """Refuse a root that is not a plain directory path, for every caller
    that owns one: not a string, or not a directory. One validation, routed
    through by tests_for, changed_paths and check, so hostile input stops
    here with ValueError and never reaches os.path or P._run as a raw
    interpreter exception."""
    if not isinstance(root, str):
        raise ValueError("%s: root must be a directory path" % who)
    if not os.path.isdir(root):
        raise ValueError("NO-DATA: %s root is not a directory: %s" % (who, root))
    return root


def tests_for(paths, root):
    """The test files covering `paths`, sorted, each named once. A path
    outside scripts/, a non Python file and a script with no test are not
    this check's business.

    Hostile input is REFUSED with ValueError, never a raw interpreter
    exception and never a silent empty list: a paths that is not a list and
    a path that is not a string stop here, and the root is read through
    _checked_root so it must be a directory."""
    if not isinstance(paths, (list, tuple)):
        raise ValueError("tests_for: paths must be a list of changed paths")
    for path in paths:
        if not isinstance(path, str):
            raise ValueError("tests_for: every changed path must be a string")
    _checked_root(root, "tests_for")
    out = set()
    for path in paths:
        d, name = os.path.split(path.strip())
        if d != "scripts" or not name.endswith(".py"):
            continue
        test = name if name.startswith("test_") else "test_" + name
        if os.path.isfile(os.path.join(root, "scripts", test)):
            out.add("scripts/" + test)
    return sorted(out)


def callers_of(name, bodies):
    """Both spellings: the FILE name (a subprocess call) and the MODULE name (an import).

    Hostile input is refused with ValueError, never a raw interpreter exception: a name that is
    not a non empty string, a bodies that is not a dict, and a path or a body that is not a
    string all stop here, before the file name reader or the regex engine sees them. A bodies
    whose own items cannot be read, the unhashable key shape red team round 0 fired, is refused
    the same way rather than raising TypeError into the caller.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("callers_of: name must be a non empty string")
    if not isinstance(bodies, dict):
        raise ValueError("callers_of: bodies must be a dict of path to text")
    rows = []
    try:
        for path, body in bodies.items():
            rows.append((path, body))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("callers_of: bodies could not be read as a dict (%s)" % type(exc).__name__)
    for path, body in rows:
        if not isinstance(path, str) or not isinstance(body, str):
            raise ValueError("callers_of: every path and body must be a string")
    stem = name[:-3] if name.endswith(".py") else name[:-3] if name.endswith(".sh") else name
    file_re = re.compile(re.escape(name))
    mod_re = re.compile(r"(?:^|\W)(?:import|from)\s+%s(?:\W|$)" % re.escape(stem), re.M)
    hits = []
    for path, body in rows:
        if os.path.basename(path) == name:
            continue                      # a file referring to itself is not a caller
        # THIS FILE IS A REGISTER, NEVER A CALL SITE. Measured 2026-09-21: sources() scans
        # scripts/ too, so the moment a tool is named in ENTRY_POINTS or RECORDED_DEAD the grep
        # below found it HERE and reported the tool WIRED. That is why this check has printed
        # "0 declared entry point(s)" since the day it was written while nine entry points sat
        # in the table: every declaration was quietly reclassified as a call. The failure
        # direction is the dangerous one, since declaring an orphan made the finding vanish
        # instead of becoming reviewable, which is the exact opposite of why it is declared BY
        # NAME. Skipped by basename so the installed twin in ~/.claude/bin cannot do it either.
        if os.path.basename(path) == "test_loop_wiring.py":
            continue
        if file_re.search(body) or (name.endswith(".py") and mod_re.search(body)):
            hits.append(os.path.basename(path))
    return sorted(set(hits))


REACH_DIRS = ("scripts", "scripts/loop", "plugin/runtime/brother/core")


def _reach_imports(path, body, modules, packages, stdlib):
    """Return (local imported paths, unresolved) without importing target code."""
    found = set()
    unresolved = False

    def resolve(name):
        hits = modules.get(name, set())
        found.update(hits)
        return bool(hits or name in packages or name.split(".")[0] in stdlib)

    try:
        tree = ast.parse(body, filename=path)
    except SyntaxError:
        return found, True
    aliases = {alias.asname: alias.name for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) for alias in node.names if alias.asname}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not resolve(alias.name):
                    unresolved = True
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = path.split("/")[:-node.level]
                base = ".".join(parts + ([base] if base else []))
            parent_known = resolve(base)
            children_known = [resolve(base + "." + alias.name) for alias in node.names]
            if not parent_known and not all(children_known):
                unresolved = True
            if base in packages and base not in modules and not all(children_known):
                # A namespace directory cannot supply a missing member as an
                # attribute of its own module body.
                unresolved = True
        elif isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else node.func.id if isinstance(node.func, ast.Name) else "")
            name = aliases.get(name, name)
            if name in ("import_module", "__import__"):
                if (not node.args or not isinstance(node.args[0], ast.Constant)
                        or not isinstance(node.args[0].value, str)
                        or not resolve(node.args[0].value)):
                    unresolved = True
            elif name in ("spec_from_file_location", "SourceFileLoader", "SourcelessFileLoader"):
                # Computed loader paths cannot prove a dependency absent.
                if (len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)
                        or not isinstance(node.args[1].value, str)
                        or not resolve(node.args[1].value.removesuffix(".py").replace("/", "."))):
                    unresolved = True
    return found, unresolved


def _has_selftest(path, body):
    """Recognize a module's flag handler, not a command it runs on another file."""
    if path.endswith(".sh"):
        return bool(re.search(r'''(?m)^\s*(?:["']?--selftest["']?\s*\)|(?:if\s+)?\[\[?[^\n]*--selftest)''', body))
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) or (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            if any(isinstance(value, ast.Constant) and value.value == "--selftest"
                   for value in ast.walk(node)):
                return True
    return False


def tests_reaching(paths, root):
    """Return a sorted, unique list[str] of impacted checks, without running them.

    Test entries are repository-relative test_*.py or test_*.sh paths. Selftest
    entries are shell commands, `python3 <quoted-relative-path> --selftest` or
    `sh <quoted-relative-path> --selftest`, for reached sources handling that flag
    in a Python comparison/argument parser or a shell case/conditional.
    Search the immediate files of REACH_DIRS (scripts/loop is included once).

    Match callers_of's file and import spellings plus qualified, relative and
    literal dynamic imports. Include changed tests, named tests of changed
    modules and direct production callers, and tests importing or naming either.
    Only test-to-test IMPORT edges extend that bounded selection transitively.
    Ambiguous module names retain all candidates. A test whose imports cannot all
    be resolved (an unresolved import, an unresolvable dynamic loader path, or a
    Python parse failure) has unknown reach, so it is always selected; its
    siblings keep their own known reach. Widening its whole directory instead
    selected 633 of 633 tests on hub main for a one-file change, because 46 of
    632 tests are unresolved and every directory holds one. A dynamic load that
    names the file literally is still found through callers_of's file spelling.
    Standard library names are known from interpreter files; target code is
    never loaded.

    A root that is not a directory, a path outside it, or an unreadable changed
    file raises ValueError naming the NO-DATA input. Unreadable scanned sources
    likewise refuse rather than silently deleting edges. Empty paths selects no
    checks. This API does not alter tests_for's pre-push coverage contract.
    """
    root = os.fspath(root)
    if not os.path.isdir(root):
        raise ValueError("NO-DATA: root is not a directory: %s" % root)

    def read(path):
        try:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError as exc:
            raise ValueError("NO-DATA: cannot read %s: %s" % (path, exc)) from exc

    changed = set()
    bodies = {}
    for path in paths:
        normalized = os.path.normpath(path)
        if os.path.isabs(path) or normalized == ".." or normalized.startswith("../"):
            raise ValueError("NO-DATA: changed path is not repository-relative: %s" % path)
        bodies[normalized] = read(normalized)
        changed.add(normalized)
    if not changed:
        return []
    for folder in REACH_DIRS:
        directory = os.path.join(root, folder)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if name.endswith((".py", ".sh")):
                path = folder + "/" + name
                bodies[path] = read(path)

    tests = {p for p in bodies if os.path.dirname(p) in REACH_DIRS
             and os.path.basename(p).startswith("test_")
             and p.endswith((".py", ".sh"))}
    modules, packages = {}, set()
    for path in bodies:
        if not path.endswith(".py"):
            continue
        parts = path[:-3].split("/")
        for start in range(len(parts)):
            modules.setdefault(".".join(parts[start:]), set()).add(path)
            for end in range(start + 1, len(parts)):
                packages.add(".".join(parts[start:end]))
    # No find_spec on repository imports: even looking one up can execute its
    # parent package. Disk names cover the stdlib on both supported interpreters.
    stdlib = set(sys.builtin_module_names)
    for directory in (sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib")):
        for folder in (directory, os.path.join(directory, "lib-dynload")):
            if os.path.isdir(folder):
                stdlib.update(name.split(".")[0] for name in os.listdir(folder))

    imports, unknown_reach = {}, set()
    for path, body in bodies.items():
        if path.endswith(".py"):
            imports[path], unknown = _reach_imports(path, body, modules, packages, stdlib)
            if unknown and path in tests:
                unknown_reach.add(path)

    caller_cache = {}

    def callers(target):
        if target not in caller_cache:
            name = os.path.basename(target)
            stem = os.path.splitext(name)[0]
            # Literal prefilter only: every match of either legacy spelling
            # contains the stem. Avoid regex scans of unrelated large fixtures.
            candidates = {p: body for p, body in bodies.items() if stem in body}
            names = set(callers_of(name, candidates))
            caller_cache[target] = {p for p in bodies if os.path.basename(p) in names
                                    or target in imports.get(p, ())}
        return caller_cache[target]

    direct = set().union(*(callers(p) for p in changed))
    modules_reached = changed | (direct - tests)
    selected = (changed | direct) & tests
    for path in modules_reached:
        selected.update(callers(path) & tests)
        own = "test_" + os.path.splitext(os.path.basename(path))[0] + ".py"
        selected.update(p for p in tests if os.path.basename(p) == own)
    selected.update(unknown_reach)   # unknown reach runs; its directory keeps its known reach
    pending = list(selected)
    while pending:
        target = pending.pop()
        for test in tests - selected:
            if target in imports.get(test, ()):
                selected.add(test)
                pending.append(test)
    for path in modules_reached - tests:
        if os.path.dirname(path) in REACH_DIRS and _has_selftest(path, bodies[path]):
            interpreter = "python3" if path.endswith(".py") else "sh"
            selected.add("%s %s --selftest" % (interpreter, shlex.quote(path)))
    return sorted(selected)


def changed_paths(root, rng, runner=None):
    """(paths, problem): files added or modified in `rng` (a list of git
    revision arguments, either a..b or sha --not --remotes).

    Hostile input is REFUSED with ValueError, never a raw interpreter
    exception and never a silent list: a root that is not a directory, an
    rng that is not a list and a revision argument that is not a string all
    stop here, before git is asked. A git call that returns no result at all
    is a problem, never an AttributeError into the caller."""
    _checked_root(root, "changed_paths")
    if not isinstance(rng, (list, tuple)):
        raise ValueError("changed_paths: rng must be a list of revision arguments")
    for rev in rng:
        if not isinstance(rev, str):
            raise ValueError("changed_paths: every revision argument must be a string")
    proc = P._run(["git", "log", "--format=", "--name-only", "--diff-filter=AM"]
                  + list(rng), root, runner)
    if proc is None or not hasattr(proc, "returncode"):
        return None, "git log %s produced no result" % " ".join(rng)
    if proc.returncode != 0:
        return None, "git log %s failed: %s" % (" ".join(rng), P._last(proc))
    return sorted({l for l in (proc.stdout or "").splitlines() if l.strip()}), None


def _failing_names(proc):
    """' FAIL/ERROR: <first three test names>' or ''. The last line of a
    unittest run says how many failed, never which; measured 2026-09-20, two
    sessions each met the same one failure refusal and neither could name
    the test, because the tree it ran in was already deleted."""
    text = (proc.stdout or "") + (proc.stderr or "")
    names = [l.split(" ", 1)[1].split(" (")[0] for l in text.splitlines()
             if l.startswith(("FAIL: ", "ERROR: "))]
    if not names:
        return ""
    more = " and %d more" % (len(names) - 3) if len(names) > 3 else ""
    return ", failing: %s%s" % (", ".join(names[:3]), more)


def _scratch_root():
    return os.path.expanduser(os.environ.get("BROTHER_SCRATCH")
                              or "~/.claude/brother-scratch")


def _export_pair(kind):
    """(tree, home): two fresh folders under the scratch root (export_public.scratch_dir, C9), the export tree and the
    HOME its tests run under. Raises OSError having removed whichever one it made; never the system temp folder."""
    made = []
    try:
        for what in ("export", "home"):
            made.append(E.scratch_dir("hermetic-%s%s-" % (kind, what)))
    except OSError:
        for path in made:
            shutil.rmtree(path, ignore_errors=True)
        raise
    return made[0], made[1]


def _keep(test, proc):
    """The refused run's whole output, kept outside the tree that is about
    to be deleted. Returns the path, or why it could not be written."""
    scratch = _scratch_root()
    path = os.path.join(scratch, "run-hermetic-refused-%s-%d.txt"
                        % (os.path.basename(test), os.getpid()))
    try:
        os.makedirs(scratch, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write((proc.stdout or "") + (proc.stderr or ""))
    except OSError as exc:
        return "(could not save: %s)" % exc
    return path


def _confined(cmd, root, home):
    """(argv, env): `cmd` wrapped so nothing it does reaches the open host.
    The argv is the grader's sandbox (grade_build.sandboxed: writes only under
    `root` and `home`, no network, `root`/.git shut); the env is the landing
    suite's (loop_switches.suite_env over the public runner's conditions:
    HOME and TMPDIR under `home`, no run knobs, no GIT_ variables, no
    bytecode). ONE definition for the export run and the checkout rerun, so a
    red the sandbox causes reads the same on both sides. Raises
    grade_build.SandboxRefused when this host cannot confine the run: the
    caller reads NO-DATA and nothing runs bare."""
    env = LS.suite_env(P.runner_env(home), home)
    # runner_env above empties `home`; the TMPDIR suite_env names is made after it, or every tempfile call in the test
    # would fall back to the host's temp folder, which the sandbox denies.
    os.makedirs(os.path.join(home, "tmp"), exist_ok=True)
    with contextlib.redirect_stdout(sys.stderr):   # an opt out narrates on stdout; this stream carries verdicts only
        return G.sandboxed(cmd, root, home), env


def _checkout_rerun(root, test, runner, timeout):
    """(proc, problem): `test` run once more on the checkout's HEAD, in a
    DISPOSABLE detached worktree of it under the run scoped scratch root (one
    at a time, removed before this returns) with a fresh empty HOME beside
    it, so only the tree differs from the export run. proc is None when no
    HOME or worktree could be made, the test is not in HEAD, or the host
    cannot confine the run.

    NEVER IN THE CHECKOUT ITSELF (review 14 finding 2, 2026-10-02): the rerun
    was confined to a sandbox rooted at the checkout, which is the landing
    tree when the lander runs this gate, so a red test that writes beside
    itself dirtied that tree; the lander then could not unwind ("tree not
    clean") and every later landing refused."""
    scratch = _scratch_root()
    try:
        os.makedirs(scratch, exist_ok=True)
        home = tempfile.mkdtemp(prefix="run-hermetic-checkout-", dir=scratch)
        parent = tempfile.mkdtemp(prefix="run-hermetic-worktree-", dir=scratch)
    except OSError as exc:
        return None, "no empty HOME could be made: %s" % exc
    tree = os.path.join(parent, "tree")
    git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        made = P._run(["git", "-C", root, "worktree", "add", "--quiet", "--detach", tree, "HEAD"], root, None, env=git_env, timeout=300)
        if made.returncode != 0:
            return None, ("no disposable worktree of the checkout's HEAD could be made: %s"
                          % P._last(made))
        if not os.path.isfile(os.path.join(tree, test)):
            return None, "the test is not in the checkout's HEAD, so there is nothing to rerun"
        cmd, env = _confined([sys.executable, os.path.join(tree, test)], tree, home)
        return P._run(cmd, tree, runner, env=env, timeout=timeout), None
    except G.SandboxRefused as exc:
        return None, "the sandbox refused: %s" % exc
    finally:
        P._run(["git", "-C", root, "worktree", "remove", "--force", tree], root, None, env=git_env, timeout=300)
        shutil.rmtree(parent, ignore_errors=True)
        P._run(["git", "-C", root, "worktree", "prune"], root, None, env=git_env, timeout=300)
        shutil.rmtree(home, ignore_errors=True)


def _checks_running(tree, test):
    """The names of the required_fast.sh checks whose whole command is this
    exact test file (an optional python3, an optional -B, the path, nothing
    else), read from the export tree's own copy. [] when the file is missing,
    unreadable or names no such check."""
    try:
        with open(os.path.join(tree, "scripts", "required_fast.sh"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    names = []
    for line in lines:
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        if len(tokens) < 3 or tokens[0] != "run_check":
            continue
        rest = tokens[2:]
        if rest and os.path.basename(rest[0]).startswith("python"):
            rest = rest[1:]
        if rest and rest[0] == "-B":
            rest = rest[1:]
        if rest == [test]:
            names.append(tokens[1])
    return names

def declared_no_data(tree, test, proc):
    """(inputs, why): `inputs` is the list of declared absent inputs when a test's exit 2 is the estate's own declared
    NO-DATA on this export tree, else None with `why`. THE ONE RULE required_fast.sh's obligation step applies
    (scripts/evidence_obligation.py transition: an exit 2 whose check declares an expected_absent_input that is absent
    from the repo is allowed), asked here of the tree the public runner would see, so a check that honestly says NO-DATA
    is not forced into a false exit 0. All must hold, else None (the refusal stands, fail closed): the exit is exactly 2;
    gate_obligations.json reads; every check that runs this file declares an input that stays inside the tree, is absent
    (a dangling link counts as present), and is named in the output."""
    if proc.returncode != 2:
        return None, "exit %d is not NO-DATA" % proc.returncode
    names = _checks_running(tree, test)
    if not names:
        return None, "no required_fast.sh check runs this file"
    try:
        checks = EO.load_obligations(tree).get("checks", {})
    except EO.ObligationError as exc:
        return None, "gate_obligations.json: %s" % exc
    text = (proc.stdout or "") + (proc.stderr or "")
    inputs = []
    for name in names:
        expected = (checks.get(name) or {}).get("expected_absent_input")
        if not expected or EO.expected_absent_escapes(tree, expected):
            return None, "check %s declares no usable expected_absent_input" % name
        if os.path.lexists(os.path.join(tree, expected)):
            return None, "check %s declares %s absent but it is present" % (name, expected)
        if expected not in text:
            return None, "the output never names %s" % expected
        inputs.append(expected)
    return inputs, ""

def declared_red(root, test):
    """The reason the battery's EXEMPT register (scripts/test_battery_registration.py in the checkout, read as a literal,
    never imported) gives `test`, or ''. 1.1.8 H3, 2026-10-23: a harness written before its code is red by design while
    its unit is OPEN, the battery already declares it so, and this gate refused every push that reached it (the night's
    landings included). Only the register's own literal counts; an unreadable register declares nothing."""
    import ast
    try:
        with open(os.path.join(root, "scripts", "test_battery_registration.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "EXEMPT" for t in node.targets):
            try:
                exempt = ast.literal_eval(node.value)
            except ValueError:
                return ""
            if not isinstance(exempt, dict):
                return ""
            rel = test[len("scripts/"):] if test.startswith("scripts/") else None
            reason = exempt.get(rel) if rel else None
            return reason.strip() if isinstance(reason, str) else ""
    return ""


def _declared_red_in_checkout(root, test, runner, timeout):
    """'' unless `test` is declared red by design AND is red in the checkout too, else the OK detail to report. A
    declared test that passes in the checkout fails only on the export tree: that IS a hermeticity defect and is still
    refused, so the declaration never hides what this gate exists to catch."""
    reason = declared_red(root, test)
    if not reason:
        return ""
    again, problem = _checkout_rerun(root, test, runner, timeout)
    if problem or again is None or again.returncode in (0, 124, 127):
        return ""
    return ("RED BY DESIGN as declared in scripts/test_battery_registration.py EXEMPT (%s), and red in the checkout "
            "too, so not a hermeticity defect" % reason[:160])

def _refusal(root, test, proc, runner, timeout):
    """The refusal's words, chosen by a rerun in the checkout. Measured
    2026-09-26: the export only wording was false for 2 of 3 refusals, both
    red in the full checkout too (an implementation missing from the branch,
    a real lint hit), and it sent the fixer toward fixtures."""
    head = ("FAILS on the export tree with an empty HOME (exit %d: %s)%s"
            % (proc.returncode, P._last(proc), _failing_names(proc)))
    again, problem = _checkout_rerun(root, test, runner, timeout)
    if again is not None and again.returncode in (124, 127):
        problem = P._last(again)
    if problem:
        why = ("; the rerun in the checkout could not run (%s), so whether it "
               "is red there too is unknown" % problem)
    elif again.returncode != 0:
        why = ("; red in the checkout too: not a hermeticity defect, fix the "
               "code or the test")
    else:
        why = (". It depends on something only this checkout or this home "
               "folder has; give the test its own fixture")
    return "%s%s  [full: %s]" % (head, why, _keep(test, proc))


def check(root, tests, runner=None, build=None, timeout=TIMEOUT_S):
    """[(verdict, test, detail)] for each test, run in one export tree.

    Hostile input is REFUSED with ValueError, never a raw interpreter
    exception and never a silent empty result: a root that is not a
    directory, a tests that is not a list, a test path that is not a string,
    and a timeout that is not a positive number (a bool or a NaN included)
    all stop here. An empty list is still the honest "nothing changed"
    answer; None and a bare str are not."""
    _checked_root(root, "check")
    if not isinstance(tests, (list, tuple)):
        raise ValueError("check: tests must be a list of test paths")
    for test in tests:
        if not isinstance(test, str):
            raise ValueError("check: every test path must be a string")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("check: timeout must be a number of seconds")
    if timeout != timeout or timeout <= 0:
        raise ValueError("check: timeout must be a positive number of seconds")
    if not tests:
        return [(OK, "hermetic-tests", "no scripts/ test is touched by this change")]
    # SP4 (1.1.5): no caller build means the tree is LEASED from export_tree_cache (one build per HEAD tree, exporter and
    # allowlist; a dirty root, an unreadable key or an entry that fails verification builds as before). The bytes are
    # cached, never a verdict: each test below still runs, in this worktree, under the sandbox (which cannot write into
    # the cache entry, outside `tree` and `home`). A caller's own build is used exactly as passed.
    build = build or P.export_tree_builder(root, cache=True)
    try:
        tree, home = _export_pair("")
    except OSError as exc:
        return [(NODATA, "hermetic-tests", "no scratch folder for the export tree could be made: %s" % exc)]
    try:
        try:
            build(tree)
        except Exception as exc:  # sbe: allow-silent reported as NO-DATA below, never swallowed
            return [(NODATA, "hermetic-tests",
                     "the export tree could not be built: %s" % exc)]
        results = []
        for test in tests:
            if not os.path.isfile(os.path.join(tree, test)):
                results.append((OK, test, "not shipped in the export tree, so "
                                          "the public runner never runs it"))
                continue
            # ONE HOME PER TEST (2026-09-28): a test may leave files under its HOME (the straggler harness leaves
            # .claude/brother-scratch, the grader suite .claude/evidence), and one shared HOME cleaned with rmdir then
            # refused the NEXT test, so the whole pre-push gate crashed on a tree with no finding in it.
            test_home = tempfile.mkdtemp(prefix="t-", dir=home)
            try:
                cmd, env = _confined([sys.executable, os.path.join(tree, test)], tree, test_home)
            except G.SandboxRefused as exc:
                results.append((NODATA, test, "could not be run: %s" % exc))
                continue
            proc = P._run(cmd, tree, runner, env=env,
                          timeout=declared_budget(os.path.join(tree, test), timeout))
            if proc.returncode == 0:
                results.append((OK, test, "passes on the export tree with an "
                                          "empty HOME: %s" % P._last(proc)))
            elif proc.returncode in (124, 127):
                results.append((NODATA, test, "could not be run: %s" % P._last(proc)))
            else:
                inputs, _why = declared_no_data(tree, test, proc)
                declared = "" if inputs else _declared_red_in_checkout(root, test, runner, timeout)
                if inputs:
                    results.append((OK, test, "NO-DATA on the export tree as declared (%s absent)"
                                    % ", ".join(inputs)))
                elif declared:
                    results.append((OK, test, declared))
                else:
                    results.append((REFUSED, test,
                                    _refusal(root, test, proc, runner, timeout)))
        return results
    finally:
        shutil.rmtree(tree, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


def scrub_git_env(env=None):
    """Drop every GIT_ variable from `env` (the process environment by default), in place. git exports GIT_DIR to its hooks;
    P.runner_env already keeps it from the tests, but the export tree is BUILT before that, in this process, and a build that
    inherits the hook's GIT_DIR reads the wrong repository.

    Hostile input is REFUSED with ValueError, never a raw interpreter
    exception: an env that is neither None nor a mutable mapping of names,
    and any env whose key is not a string, stop here before the key walk."""
    if env is None:
        env = os.environ
    elif not (hasattr(env, "keys") and hasattr(env, "__delitem__")):
        raise ValueError("scrub_git_env: env must be a mutable mapping of names, or None")
    for key in list(env.keys()):
        if not isinstance(key, str):
            raise ValueError("scrub_git_env: every env key must be a string")
    for key in [k for k in env if k.startswith("GIT_")]:
        del env[key]
    return env


# -- timing sensitive tests (TH1.b) --------------------------------------------------------------------------------------
# A test whose count or order assertion rests on a real clock passes on a fast machine and fails on a starved runner
# (1.1.2 release check, 2026-10-11, GitHub run 38146264768). timing_hunt.py finds them statically; the push refuses a NEW
# uncovered one, and --slow-host runs the flagged files under load to find what the static rule cannot see.

TIMING_REMEDY = ('drive the time through tick(, clock=, or a fake sleep, or add the one line comment '
                 '"# timing: deterministic because <seam>"')


def _prior_baseline(root):
    """(ids, problem) of the timing baseline BEFORE its latest change: HEAD's copy when the working tree changed it, else
    the copy of the previous commit that touched it. (None, None) when there is no earlier version (a first baseline)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args):
        return P._run(["git", "-C", root] + list(args), root, None, env=env, timeout=60)
    status = git("status", "--porcelain", "--", TH.BASELINE)
    log = git("log", "--format=%H", "-n", "2", "--", TH.BASELINE)
    for proc in (status, log):
        if proc.returncode != 0:
            return None, "git could not say what the baseline was before (%s)" % P._last(proc)
    commits = (log.stdout or "").split()
    ref = commits[0] if (status.stdout or "").strip() and commits else commits[1] if len(commits) > 1 else None
    if ref is None:
        return None, None
    shown = git("show", "%s:%s" % (ref, TH.BASELINE))
    ids = TH.parse_baseline(shown.stdout or "") if shown.returncode == 0 else None
    if ids is None:
        return None, "the baseline at %s could not be read (%s)" % (ref[:12], P._last(shown))
    return ids, None


def _baseline_growth(root):
    now = TH.baseline_ids(os.path.join(root, TH.BASELINE))
    if now is None:
        return [(NODATA, TH.BASELINE, "the changed timing baseline cannot be read")]
    prior, problem = _prior_baseline(root)
    if problem:
        return [(NODATA, TH.BASELINE, problem)]
    if prior is None:
        return [(OK, TH.BASELINE, "a first timing baseline, %d id(s)" % len(now))]
    grown = sorted(set(now) - set(prior))
    if grown:
        return [(REFUSED, TH.BASELINE, "the timing baseline may only shrink: %d id(s) added (%s); cover the test instead"
                 % (len(grown), ", ".join(grown[:3])))]
    return [(OK, TH.BASELINE, "the timing baseline went from %d to %d id(s)" % (len(prior), len(now)))]


def timing_results(paths, root):
    """[(verdict, name, detail)] in check's shape for the changed test files in `paths`: REFUSED for each flagged
    UNCOVERED timing test whose id is not in the baseline, NODATA for a file that cannot be read or parsed (and for a
    baseline that cannot be read when an uncovered test needs it), OK otherwise. A changed baseline that grows is REFUSED."""
    _checked_root(root, "timing_results")
    changed = sorted({p.strip() for p in paths if isinstance(p, str)})
    rows = _baseline_growth(root) if TH.BASELINE in changed else []
    tests = sorted(set(changed) & set(TH.test_files(root)))
    found, bad = TH.scan_paths(tests, root)
    rows += [(NODATA, rel, "could not be read or parsed, so its timing tests are unknown") for rel in bad]
    uncovered = [f for f in found if not f.covered]
    known = TH.baseline_ids(os.path.join(root, TH.BASELINE)) if uncovered else []
    if known is None:
        rows.append((NODATA, "timing-tests", "the timing baseline %s cannot be read, so whether %d uncovered timing "
                     "test(s) are new is unknown" % (TH.BASELINE, len(uncovered))))
        known = [f.id for f in uncovered]
    new = [f for f in uncovered if f.id not in known]
    rows += [(REFUSED, f.path, "NEW uncovered timing test: %s; %s" % (f.id, TIMING_REMEDY)) for f in new]
    if (tests or not rows) and not any(r[0] != OK for r in rows):
        rows.append((OK, "timing-tests", "%d changed test file(s) scanned: %d flagged, %d covered, %d already in the "
                     "baseline" % (len(tests), len(found), len(found) - len(uncovered), len(uncovered))))
    return rows


SLOW_REPEATS = 3
SLOW_TOTAL_CAP_S = 3600
SLOW_REEXEC_ENV = "BROTHER_TIMING_SLOW_HOST_REEXEC"
HOG_ARGV = [sys.executable, "-c", "while True: pass"]
_LIVE_HOGS = []   # every busy loop not yet reaped, so a SIGTERM handler can reap them too


def start_hogs(n, popen=None):
    """n busy loop processes. Raises OSError after reaping any it started, so a half started set never leaks."""
    popen = popen or subprocess.Popen
    hogs = []
    try:
        for _ in range(n):
            hogs.append(popen(HOG_ARGV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL))
            _LIVE_HOGS.append(hogs[-1])
    except OSError:
        reap_hogs(hogs)
        raise
    return hogs


def reap_hogs(hogs):
    """Kill and wait each busy loop; the number still alive afterwards (0 expected)."""
    for hog in hogs:
        try:
            hog.kill()
        except OSError:
            pass  # sbe: allow-silent already gone; poll() below is the count that matters
        try:
            hog.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass  # sbe: allow-silent counted below as a survivor
        if hog in _LIVE_HOGS and hog.poll() is not None:
            _LIVE_HOGS.remove(hog)
    return sum(1 for hog in hogs if hog.poll() is None)


def _slots_held(held):
    import heavy_slot   # only the slow host needs it; the push check never imports it
    want = heavy_slot.slot_count(os.environ)
    got = [i for i in (held or "").split(",") if i.strip().isdigit()]
    return len(got) >= want, want


def _one_file(tree, home, test, n, repeats, runner, hog_starter):
    """The verdict row of one inventory file run up to `repeats` times, each under n fresh busy loops."""
    for r in range(1, repeats + 1):
        try:
            hogs = hog_starter(n)
        except OSError as exc:
            return (NODATA, test, "a busy loop could not start (%s); nothing more was run" % exc)
        proc = problem = None
        try:
            alive = sum(1 for hog in hogs if hog.poll() is None)
            if alive != n:
                problem = "only %d of %d busy loops were running" % (alive, n)
            else:
                cmd, env = _confined([sys.executable, os.path.join(tree, test)], tree,
                                     tempfile.mkdtemp(prefix="t-", dir=home))
                proc = P._run(cmd, tree, runner, env=env, timeout=declared_budget(os.path.join(tree, test)))
        except G.SandboxRefused as exc:
            problem = "could not be run: %s" % exc
        finally:
            left = reap_hogs(hogs)
        if left:
            return (NODATA, test, "%d busy loop(s) survived the reap; the machine is left loaded" % left)
        if problem:
            return (NODATA, test, problem)
        if proc.returncode == 124:
            return (REFUSED, test, "repeat %d of %d timed out under load (%d busy loops)" % (r, repeats, n))
        if proc.returncode == 127:
            return (NODATA, test, "could not be run: %s" % P._last(proc))
        if proc.returncode != 0:
            return (REFUSED, test, "repeat %d of %d failed under %d busy loops: %s%s"
                    % (r, repeats, n, P._last(proc), _failing_names(proc)))
    return (OK, test, "passed %d of %d repeats under %d busy loops" % (repeats, repeats, n))


def slow_host(root, files, n, repeats=SLOW_REPEATS, runner=None, build=None, held=None, hog_starter=None,
              clock=None, cap=SLOW_TOTAL_CAP_S):
    """[(verdict, file, detail)]: each flagged inventory file run up to `repeats` times under n busy loops, only inside
    held machine slots (`held` is BROTHER_HEAVY_SLOT_HELD; absent, unqueued or short is NODATA and nothing runs). New
    files stop starting after `cap` seconds; the rest read NODATA, never passed."""
    _checked_root(root, "slow_host")
    ok, want = _slots_held(held)
    if not ok:
        return [(NODATA, "timing-slow-host", "could not take the slots; nothing was run (held %r of %d)"
                 % (held, want))]
    if not files:
        return [(OK, "timing-slow-host", "no flagged test in the inventory")]
    clock = clock or time.monotonic
    hog_starter = hog_starter or start_hogs
    build = build or P.export_tree_builder(root, cache=True)   # SP4: leased like check()'s, the same rules
    try:
        tree, home = _export_pair("slow-")
    except OSError as exc:
        return [(NODATA, "timing-slow-host", "no scratch folder for the export tree could be made: %s" % exc)]
    try:
        try:
            build(tree)
        except Exception as exc:  # sbe: allow-silent reported as NO-DATA below, never swallowed
            return [(NODATA, "timing-slow-host", "the export tree could not be built: %s" % exc)]
        rows, start = [], clock()
        for i, test in enumerate(files):
            if clock() - start >= cap:
                rows += [(NODATA, rest, "not run: the time cap (%ds)" % cap) for rest in files[i:]]
                break
            if not os.path.isfile(os.path.join(tree, test)):
                rows.append((OK, test, "not shipped in the export tree, so the public runner never runs it"))
                continue
            rows.append(_one_file(tree, home, test, n, repeats, runner, hog_starter))
        return rows
    finally:
        shutil.rmtree(tree, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


def _through_slots(argv):
    """Re-execute this command through scripts/heavy_slot.py taking every slot, once (the marker stops a second round)."""
    import heavy_slot
    env = dict(os.environ)
    env["BROTHER_HEAVY_SLOT_WEIGHT"] = str(heavy_slot.slot_count(os.environ))
    env[SLOW_REEXEC_ENV] = "1"
    return subprocess.call([sys.executable, os.path.join(HERE, "heavy_slot.py"), sys.executable,
                            os.path.abspath(__file__)] + list(argv), env=env)


def _slow_host_main(n):
    def on_term(signum, _frame):
        reap_hogs(list(_LIVE_HOGS))
        sys.exit(128 + signum)
    signal.signal(signal.SIGTERM, on_term)
    began = time.monotonic()
    try:
        rows = slow_host(ROOT, TH.inventory_files(ROOT), n, held=os.environ.get("BROTHER_HEAVY_SLOT_HELD"))
    finally:
        left = reap_hogs(list(_LIVE_HOGS))
    for verdict, name, detail in rows:
        print("%-8s %s  %s" % (verdict, name, detail))
    print("slow host: %d busy loops, %.0f s wall, %d busy loop(s) alive after" % (n, time.monotonic() - began, left))
    return P.exit_code(rows)


class _InputParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "NO-DATA: %s\n" % message)


def main(argv=None):
    scrub_git_env()
    ap = _InputParser(description="Run each changed scripts/ test on "
                     "an export shaped tree under an empty HOME.")
    ap.add_argument("--range", default=None,
                    help="a git range, e.g. hub/main..HEAD; its changed files "
                         "choose the tests")
    ap.add_argument("--reach", action="store_true",
                    help="list impacted tests and selftest commands without running them")
    ap.add_argument("--slow-host", nargs="?", const="cpus", default=None, type=int, metavar="N",
                    help="run every flagged timing test file up to %d times under N busy loops (default: the core "
                         "count), inside every machine slot" % SLOW_REPEATS)
    ap.add_argument("paths", nargs="*", help="or name the changed files directly")
    probe = sys.argv[1:] if argv is None else list(argv)
    if any(a in ("-h", "--help") for a in probe):
        ap.print_help()
        return 0
    args = ap.parse_args(argv)
    paths = list(args.paths)
    if args.slow_host is not None:
        n = (os.cpu_count() or 1) if args.slow_host == "cpus" else args.slow_host
        if n < 1:
            print("NO-DATA: --slow-host needs a positive number of busy loops")
            return 2
        if not os.environ.get("BROTHER_HEAVY_SLOT_HELD") and not os.environ.get(SLOW_REEXEC_ENV):
            return _through_slots(probe)
        return _slow_host_main(n)
    if args.reach:
        try:
            if not paths or args.range:
                raise ValueError("NO-DATA: --reach needs changed paths and cannot use --range")
            selected = tests_reaching(paths, ROOT)
        except ValueError as exc:
            print(str(exc))
            return 2
        for test in selected:
            print(test)
        print("REACH: %d test(s) from %d changed path(s)" % (len(selected), len(paths)))
        return 0
    if args.range:
        found, problem = changed_paths(ROOT, [args.range])
        if found is None:
            print("%-8s %-16s %s" % (NODATA, "hermetic-tests", problem))
            return P.EXIT_NODATA
        paths += found
    results = timing_results(paths, ROOT) + check(ROOT, tests_for(paths, ROOT))
    for verdict, name, detail in results:
        print("%-8s %s  %s" % (verdict, name, detail))
    return P.exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
