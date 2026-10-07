#!/usr/bin/env python3
"""main()'s CALL SITES: scripts/loop/land_batch.py ACTS on every decision function's answer.

THE GAP THIS CLOSES, named by the worker that wrote scripts/test_land_batch_loop_guard.py, in its own
words: "main()'s CALL SITES remain uncovered: deleting an `if why: ... return 1` after a step_refusal call
would pass all 31 cases." That sibling suite drives the seven decision functions with values and asserts
what they RETURN. Nothing asserted that main() reads the return at all. A decision function whose caller
discards its answer is the exact failure this estate keeps paying for: a result that is read and not acted
on is an audit, not a gate.

THE SEVEN, and how each is covered here:
  preflight        STATIC + BEHAVIOURAL   the wrong branch refusal is observed from a real run
  step_refusal     STATIC + BEHAVIOURAL   the failing fetch refusal is observed from a real run
  after_build      STATIC                 driving it needs a real land_build.py run, minutes per case
  refusal_reason   STATIC                 reachable only after a build actually lands
  gate_quarantine  STATIC                 same, and only on the refused path after that
  retry_alone      STATIC                 bisect_plan behind it, called at every refusal point (2026-09-28); it re-executes this file per retry
  exit_code        STATIC                 reachable only after a real push to a hub remote

WHY STATIC AT ALL, and what it cannot do. The mutation described above is a SOURCE SHAPE defect: the call
stays, the branch on its result goes. An AST check sees exactly that, costs milliseconds, and needs no
fixture. It cannot prove the branch does the RIGHT thing, and this estate measured on 2026-09-21 what a
purely source-reading gate is worth: a check that read the source text of a guard printed PASS twice while
one line upstream defeated it. So two properties are ALSO driven for real, end to end, through a git
repository built in a temp directory, and those two assert the printed refusal, not the exit code: both
mutants still exit 1 by a different route, so the exit code alone would prove nothing.

THE STATIC PROPERTY, stated exactly. For every call to a decision function inside main(), the call's
statement must be one of: a return of the call; an `if`/`while` testing the call; or an assignment whose
targets are later READ IN A BRANCH POSITION (an if/while/assert test, a return value, a for iterable, a
conditional expression) before any of them is reassigned. A bare expression statement calling a decision
function is the defect, and so is an assignment whose name is overwritten before anything branches on it,
which is what "delete the `if why: ... return 1`" produces: the next thing that touches `why` is the next
`why = ...`. Every one of the seven must also HAVE at least one call site, so deleting the call outright
is a failure and not a vacuous pass.

FIXTURES ARE ORTHOGONAL. The fetch fixture has a broken remote url but a stale remote tracking ref that
still matches HEAD, a clean tree, the right branch and a readable plan, so preflight would return "" and
ONLY the fetch guard can refuse it. The preflight fixture has a working remote and differs from the fetch
fixture in one fact, the checked out branch, so only the branch condition trips. A fixture that trips two
guards proves neither.

HERMETIC. Both fixtures are built under tempfile.mkdtemp, HOME is pointed at that directory so the run's
own log lands there and no live document is read or written, and nothing outside it is touched. NO-DATA is
never a pass: an unreadable or unparsable module, a missing main(), or a missing git exits non zero saying
so. Point the suite at a copy with LAND_BATCH=<path> to mutation prove it."""
import ast, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.environ.get("LAND_BATCH") or os.path.join(HERE, "loop", "land_batch.py")
DECISIONS = ("preflight", "landing_verdict", "after_build", "refusal_reason", "gate_quarantine", "retry_alone",
             "step_refusal", "exit_code")


def no_data(msg):
    """A module that cannot be read, parsed or navigated is a FAILURE, never a silent pass: a suite that
    reports OK on a file it never looked at is the worst outcome available to it."""
    print("NO-DATA: %s (%s)" % (msg, TARGET))
    sys.exit(2)


def parse():
    try:
        src = open(TARGET, encoding="utf-8").read()
    except OSError as exc:
        no_data("cannot read the module: %s" % exc)
    try:
        return ast.parse(src)
    except SyntaxError as exc:
        no_data("cannot parse the module: %s" % exc)


def const_of(tree, name):
    """Read a module level string constant out of the AST rather than duplicating it here, so a rename of
    BRANCH or PLAN in the module cannot leave this suite silently testing a fixture nobody reaches."""
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return n.value.value
    return None


def own_nodes(stmt):
    """The expression nodes belonging to THIS statement, never to statements nested inside it. Without the
    exclusion an `if` would swallow its whole body and every call in it would look like a branch test."""
    out, stack = [], []
    for _, value in ast.iter_fields(stmt):
        for it in (value if isinstance(value, list) else [value]):
            if isinstance(it, ast.AST) and not isinstance(it, ast.stmt):
                stack.append(it)
    while stack:
        n = stack.pop()
        out.append(n)
        for c in ast.iter_child_nodes(n):
            if not isinstance(c, ast.stmt):
                stack.append(c)
    return out


def flat_body(fn):
    """Every statement inside a function in document order. Sorting by (lineno, col_offset) is document
    order in Python source, and it keeps statements nested in a for or a try in the place a reader sees
    them, which is what a forward scan for "what happens next" has to follow."""
    stmts = [n for n in ast.walk(fn) if isinstance(n, ast.stmt) and n is not fn]
    return sorted(stmts, key=lambda n: (n.lineno, n.col_offset))


def branch_names(stmt):
    """Names read in a BRANCH POSITION of this statement: an if/while/assert test, a return value, a for
    iterable, or any conditional expression. Reading a name into another variable is not acting on it."""
    spots = []
    if isinstance(stmt, (ast.If, ast.While, ast.Assert)):
        spots.append(stmt.test)
    if isinstance(stmt, ast.Return) and stmt.value is not None:
        spots.append(stmt.value)
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        spots.append(stmt.iter)
    spots += [n.test for n in own_nodes(stmt) if isinstance(n, ast.IfExp)]
    names = set()
    for s in spots:
        names |= {n.id for n in ast.walk(s) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return names


def stored_names(stmt):
    return {n.id for n in own_nodes(stmt) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


def call_sites(fn):
    """(statement, call) for every call to a decision function inside fn."""
    sites = []
    for stmt in flat_body(fn):
        for n in own_nodes(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in DECISIONS:
                sites.append((stmt, n))
    return sites


def verdict(fn, stmt, call):
    """'' when the call's answer is acted on, else what was found instead."""
    if isinstance(stmt, ast.Return):
        return ""
    if isinstance(stmt, (ast.If, ast.While)) and call in ast.walk(stmt.test):
        return ""
    if not isinstance(stmt, ast.Assign):
        return "the result is discarded (%s statement, not assigned, branched on or returned)" % type(stmt).__name__
    names = stored_names(stmt)
    if not names:
        return "the result is assigned to nothing this suite can follow"
    body = flat_body(fn)
    for later in body[body.index(stmt) + 1:]:
        if branch_names(later) & names:
            return ""
        if stored_names(later) & names:
            return "%s is reassigned at line %d before anything branches on it" % (
                ", ".join(sorted(stored_names(later) & names)), later.lineno)
    return "%s is never branched on after the call" % ", ".join(sorted(names))


# ---- the behavioural half -------------------------------------------------------------------------
# Two properties are driven for real. Both assert the PRINTED refusal and not the exit code, because a
# mutant that ignores the guard still exits 1 further down a different path: an assertion that both the
# fixed and the broken build satisfy proves nothing.

def git(repo, *args, env=None):
    return subprocess.run(["git"] + list(args), cwd=repo, capture_output=True, text=True, env=env)


def fixture(tmp, branch, plan_path, land_branch, break_remote):
    """A repository whose ONLY departure from a landable state is the one fact under test."""
    repo, hub = os.path.join(tmp, "work"), os.path.join(tmp, "hub.git")
    os.makedirs(os.path.join(repo, os.path.dirname(plan_path)))
    env = dict(os.environ, HOME=tmp, GIT_CONFIG_GLOBAL=os.path.join(tmp, "gitconfig"),
               GIT_CONFIG_SYSTEM=os.path.join(tmp, "gitconfig"), GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid")
    for k in list(env):
        if k.startswith("GIT_") and k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_AUTHOR_NAME",
                                              "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            del env[k]
    if git(repo, "init", "-q", env=env).returncode:
        no_data("git init failed; git is required to drive main() for real")
    git(repo, "symbolic-ref", "HEAD", "refs/heads/" + branch, env=env)
    open(os.path.join(repo, plan_path), "w", encoding="utf-8").write('{"units": []}\n')
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-qm", "plan", env=env)
    git(repo, "init", "-q", "--bare", hub, env=env)
    git(repo, "remote", "add", "hub", hub, env=env)
    git(repo, "push", "-q", "hub", "%s:%s" % (branch, land_branch), env=env)
    git(repo, "fetch", "-q", "hub", env=env)   # the remote tracking ref preflight compares HEAD against
    git(repo, "branch", "--set-upstream-to=hub/%s" % land_branch, branch, env=env)   # main() reads the landing remote and branch from the upstream (2026-09-22) and pins the remote to hub (2026-09-24)
    if break_remote:
        git(repo, "remote", "set-url", "hub", os.path.join(tmp, "gone.git"), env=env)
    return repo, env


def run_lander(branch, plan_path, land_branch, break_remote, extra=None):
    """Run the module under test in its own repository and return what it printed."""
    tmp = tempfile.mkdtemp(prefix="land-callsites-")
    repo, env = fixture(tmp, branch, plan_path, land_branch, break_remote)
    env.pop("BROTHER_EXPECTED_UPSTREAM", None)
    env.update(extra or {})
    r = subprocess.run([sys.executable, TARGET, os.path.join(tmp, "nosuch-r0-build.json")],
                       cwd=repo, capture_output=True, text=True, env=env, timeout=300)
    return r.returncode, r.stdout + r.stderr


def main():
    tree = parse()
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    by_name = {n.name: n for n in fns}
    main_fn = by_name.get("main")
    if main_fn is None:
        no_data("the module defines no main()")
    land_branch, plan_path = const_of(tree, "BRANCH"), const_of(tree, "PLAN")
    if not land_branch or not plan_path:
        no_data("BRANCH or PLAN is not a module level string constant")

    cases = []

    def case(name, good, detail=""):
        cases.append((name, bool(good), detail))

    # 1. Every decision function still exists and is still consulted. Without this a mutation that deletes
    #    the call outright would leave nothing to check and the suite would report OK on a missing gate.
    # A decision may be reached through another decision main() calls (after_build sits inside landing_verdict since
    # the spec gate refactor): count call sites in main() and in every DECISIONS function main() itself calls.
    sites = [(main_fn, s, c) for s, c in call_sites(main_fn)]
    for _fn, _s, _c in list(sites):
        if _c.func.id in by_name and _c.func.id != "main":
            sites += [(by_name[_c.func.id], s, c) for s, c in call_sites(by_name[_c.func.id])]
    for fname in DECISIONS:
        case("%s is defined" % fname, fname in by_name)
        mine = [(fn, s, c) for fn, s, c in sites if c.func.id == fname]
        case("%s is called by main()" % fname, bool(mine), "call sites=%d" % len(mine))
        # 2. STATIC: every one of its call sites acts on the answer, judged inside the function that holds it.
        for fn, stmt, call in mine:
            why = verdict(fn, stmt, call)
            case("%s at line %d is acted on" % (fname, call.lineno), why == "", why)
    # 2b. STATIC: the landing's reason reaches the note. land_apply writes its refusals to stderr (2026-09-27 15:06: a
    #     "find not unique" refusal became "fuzz crashes 99: no output"); main() must hand stderr to landing_verdict.
    lv = [c for fn, s, c in sites if fn is main_fn and c.func.id == "landing_verdict"]
    case("landing_verdict in main() is given land_apply's stderr",
         bool(lv) and all(any(k.arg == "stderr" for k in c.keywords) for c in lv), "call sites=%d" % len(lv))

    # 2c. RETRY ALONE AT EVERY REFUSAL POINT (2026-09-28): the gate, the commit scan, the commit, the hermetic check and
    #     the push each hand a refused batch to retry_alone, and both unwinds quarantine only blame_paths. A batch left
    #     unretried came back every pass and ended run B UNPRODUCTIVE; a batch quarantined whole lost innocent builds.
    n_retry = sum(1 for n in ast.walk(main_fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "retry_alone")
    # six since D13 (2026-10-02): the pre-commit hook's check runs from the frozen copy as its own refusal point
    case("retry_alone is called at all six refusal points in main()", n_retry == 6, "calls=%d" % n_retry)
    unwinds = [n for n in ast.walk(main_fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "unwind"]
    case("every unwind in main() quarantines only blame_paths(...)",
         len(unwinds) == 2 and all(len(u.args) > 2 and getattr(getattr(u.args[2], "func", None), "id", "") == "blame_paths"
                                   for u in unwinds), "unwinds=%d" % len(unwinds))

    # 3. BEHAVIOURAL, step_refusal at the fetch call site. The remote url is broken so `git fetch hub`
    #    exits non zero; everything preflight looks at is good, so this fixture can only be refused here.
    #    A mutant that drops the guard runs on and prints "nothing landed", never "fetch exit".
    rc, out = run_lander(land_branch, plan_path, land_branch, True)
    case("step_refusal: a failing fetch refuses the landing, by name",
         "REFUSED: fetch exit" in out, "rc=%d out=%r" % (rc, out[:200]))
    case("step_refusal: the failing fetch does not exit 0", rc != 0, "rc=%d" % rc)

    # 3b. BEHAVIOURAL, the pinned destination (review 2026-10-05: deleting the guard's return left every check green).
    #     Same broken remote, plus a pin naming another upstream: the guard must refuse BEFORE the fetch, so a mutant
    #     that prints the refusal and runs on reaches "fetch exit" and no longer exits 2.
    rc, out = run_lander(land_branch, plan_path, land_branch, True, {"BROTHER_EXPECTED_UPSTREAM": "hub/practice/elsewhere"})
    case("expected upstream: a pinned other destination refuses, by name, before the fetch",
         "REFUSED: the upstream" in out and "fetch exit" not in out, "rc=%d out=%r" % (rc, out[:200]))
    case("expected upstream: the refusal exits 2", rc == 2, "rc=%d" % rc)

    # 4. BEHAVIOURAL, preflight. Same fixture with a working remote and one fact changed: the checked out
    #    branch. Tree clean, HEAD at hub parity, plan readable, so only the branch condition can refuse.
    rc, out = run_lander("main", plan_path, land_branch, False)
    case("preflight: the wrong branch refuses the landing, by name",
         ("REFUSED: on 'main', not %s" % land_branch) in out, "rc=%d out=%r" % (rc, out[:200]))
    case("preflight: the wrong branch does not exit 0", rc != 0, "rc=%d" % rc)

    bad = [(n, d) for n, good, d in cases if not good]
    for n, d in bad:
        print("FAIL  %s  %s" % (n, d))
    print("land_batch call sites: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: %d" % len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
