#!/usr/bin/env python3
"""U10 (B5-02, objection 15): land_batch.main records every landing commit it makes, and names that commit, not its parent.

WHAT WAS WRONG, read in land_batch.py before this file: main() kept `head`, the hub head read BEFORE the landing commit,
and handed it to both mark_landed calls, so every LANDED status named the landing's PARENT. Nothing wrote a landing
record, so a proof run could only count landings by reading git history, which also holds closure commits and commits
fast forwarded in from elsewhere.

THE ENTRY POINT, NOT A HELPER. Every case runs land_batch.py's main as the loop runs it (loop_pass.sh: `python3
~/.claude/bin/land_batch.py <build>` in the launch worktree), against a scratch repository with a bare upstream. The bin
is a scratch directory holding a fresh copy of the land_batch.py beside this file (so a mutation under test is the code
that runs) with its real sibling modules, and two stubs at the tool boundary: land_apply.py applies the build's one file
and prints a green body, commit_scan.py says clear. The tree carries its own trivial gates, as a landing tree does.

ONE CONDITION PER FIXTURE. LANDED, FAST-FORWARD (a post-receive hook puts one foreign commit on top of the landing) and
UNVERIFIED (the fetch URL is a symlink the post-receive hook removes, so the push lands and the parity fetch fails) each
drive one of the two mark_landed calls; a refused push writes no record; a run with no proof directory creates none.
Run: python3 -B scripts/loop/test_land_batch_landings.py"""
import datetime, glob, json, os, re, shutil, subprocess, sys, tempfile, time, unittest, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
# proof_accept (and the two it imports) because land_batch.write_history ends every history with its range line
REAL_SIBLINGS = ("land_batch.py", "plan_store.py", "spec_check.py", "grade_build.py", "unit_ledger.py", "model_router.py",
                 "proof_accept.py", "proof_launch.py", "proof_ledger.py", "loop_switches.py",
                 # the closer imports from this bin and nowhere else (land_batch.CLOSER, review 17): its whole closure is here
                 "provenance.py", "plan_lint.py", "ev_gate.py", "breaker.py", "claude_ledger.py", "freeze_manifest.py",
                 "loop_receipt.py", "model_call.py", "model_reachability.py", "worker_mix.py",
                 "loop_hold.py",   # the one reader of the loop's stop controls, asked first by main()
                 "sandbox.sb")   # neighbours run under the sandbox (2026-10-02)
SUB, UNIT, BRANCH = "U1.a", "U1", "main"


def boxed_run(logged, inner):
    """True when the logged command is exactly `inner` run under sandbox-exec (land_batch.boxed)."""
    return logged.startswith("sandbox-exec ") and logged.endswith(" " + inner)
SUB_B = "U1.b"

LAND_APPLY_STUB = '''import json, os, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
for e in b["edits"]:
    os.makedirs(os.path.dirname(e["path"]) or ".", exist_ok=True)
    open(e["path"], "w", encoding="utf-8").write(e["new_file_content"])
print("py3 scripts/test_u1a.py exit=0 Ran 1 test")
print("VERDICT SUITES GREEN")
print("FUZZ    new modules 0 | crashes 0 | calls that returned instead of refusing 0")
for extra in b.get("plant") or []:   # a path the build's code writes without declaring it (tenth review 2026-10-02)
    os.makedirs(os.path.dirname(extra) or ".", exist_ok=True)
    open(extra, "w", encoding="utf-8").write("planted")
if b.get("readonly"):   # the rollback of this build then cannot put the earlier bytes back (the folder refuses the write)
    for e in b["edits"]:
        os.chmod(os.path.dirname(e["path"]), 0o555)
for rel, target in (b.get("symlink") or {}).items():   # a landed file replaced by a link (eleventh review 2026-10-02)
    if os.path.lexists(rel):
        os.remove(rel)
    os.symlink(target, rel)
if b.get("plan_append"):   # another plan writer appends under the lock while this build is being judged
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import plan_store
    plan_store.update_units("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json",
                            {"U1": lambda u: dict(u, evidence=(u.get("evidence") or "") + " concurrent receipt.")}, fields=("evidence",))
if b.get("reject"):
    sys.exit(1)
'''
COMMIT_SCAN_STUB = '''import os
if os.environ.get("SCAN_LOG"):
    open(os.environ["SCAN_LOG"], "a").write(os.path.abspath(__file__) + "\\n")
print("commit_scan: clear")
'''
CLOSE_UNIT_STUB = '''import json, sys
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
if len(sys.argv) < 2 or sys.argv[1] == "--dry":   # --dry lists nothing eligible here: the named units are the ones run
    print("CLOSED  0 unit(s): none"); sys.exit(1)
p = json.load(open(PLAN, encoding="utf-8"))
for u in p["units"]:
    if u["id"] == sys.argv[1]: u["state"] = "DONE"
open(PLAN, "w", encoding="utf-8").write(json.dumps(p, indent=1))
print("DONE %s: done check passed" % sys.argv[1])
'''
GREEN = "import sys\nsys.exit(0)\n"
# the spec's own done check must RUN a test (spec_check, Codex audit F1 2026-09-27): an exit 0 that ran none is red
GREEN_TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_t(self):\n        pass\n\n\nunittest.main()\n"
# THE INSTALLED HOOKS, byte for byte as .git/hooks holds them on the hub checkout (read 2026-10-02): both resolve their check
# through `git rev-parse --show-toplevel`, so a plain `git commit` or `git push` runs the TREE's copy of the check. The fixture
# installs them in the landing tree's own .git/hooks, as the real one has them; the fixture's own setup commits and pushes
# run with hooks off (an owner's commits), so only what the lander does meets them.
PRE_COMMIT_HOOK = ('#!/bin/sh\n# brother-self-check-staged\n# installed by scripts/self_check_staged.py --install-hook\n'
                   'G="$(git rev-parse --show-toplevel)/scripts/self_check_staged.py"\n[ ! -f "$G" ] || python3 "$G" || exit 1\n')
PRE_PUSH_HOOK = ('#!/bin/sh\n# brother-gate installed by scripts/install_gate_hook.sh. Remove with --uninstall.\n'
                 'exec sh "$(git rev-parse --show-toplevel)/scripts/pre_push_hook.sh" "$@"\n')
with open(os.path.join(REPO, "scripts", "pre_push_hook.sh"), encoding="utf-8") as _fh:
    PRE_PUSH_HOOK_BODY = _fh.read()   # the real, protected body: it runs <toplevel>/scripts/pre_push_gate.py --cwd --remote-name --remote-url
NO_HOOKS = ["-c", "core.hooksPath=/dev/null"]

# A gate that announces it is running (in its own cwd, the one tree a gate may write) and then waits for an OUTSIDE actor's
# signal before it answers, so whatever that actor did to the landing tree meanwhile is judged by the lander's later checks.
# Since D13 (2026-10-02) a gate runs in a sandboxed copy and cannot be the actor itself.
WAITING_GATE = '''import os, sys, time
open("GATE-RUNNING", "w").close()
end = time.time() + 90
while not os.path.exists(%(done)r) and time.time() < end:
    time.sleep(0.05)
print(%(said)r)
sys.exit(%(rc)d)
'''
# The outside actor: waits for a WAITING_GATE to start (in the lander's copy, or in the landing tree itself on a lander that
# still runs gates there), acts on the landing tree, signals the gate. Started by the test, never by anything the lander runs.
OUTSIDE_ACTOR = '''import glob, os, sys, time
sys.path.insert(0, %(bin)r)
tree, tmp, done = %(tree)r, %(tmp)r, %(done)r
end = time.time() + 90
while not (glob.glob(os.path.join(tmp, "land-copy-*", "tree", "GATE-RUNNING")) or os.path.exists(os.path.join(tree, "GATE-RUNNING"))) and time.time() < end:
    time.sleep(0.05)
os.chdir(tree)
%(action)s
open(done, "w").close()
'''


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER_"))}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mode:
        os.chmod(path, mode)


class Fixture(object):
    """One landing tree with a bare upstream, a scratch HOME, a scratch bin and one READY build.

    code_root: the directory BROTHER_CODE_ROOT names (None leaves it unset). tree_files: extra files committed into the
    landing tree, {relative path: text} (the tripwire test poisons code paths with them). hook: a post-receive hook
    body for the bare upstream. fetch_link: the fetch URL is a symlink to the upstream, so a hook can break the fetch
    after a push has landed."""

    def __init__(self, root, code_root=None, tree_files=None, hook=None, pre_receive=None, fetch_link=False,
                 proof=True, extra_env=None, drop=()):
        self.root = root
        self.env = git_env()
        self.home, self.bin, self.hub = (os.path.join(root, n) for n in ("home", "bin", "hub.git"))
        self.tree, self.runs, self.run_dir = (os.path.join(root, n) for n in ("tree", "runs", "run"))
        self.link = os.path.join(root, "hub-link.git")
        for d in (self.home, self.bin, self.runs, self.run_dir):
            os.makedirs(d)
        if proof:
            os.makedirs(os.path.join(self.run_dir, "proof"))
        for name in REAL_SIBLINGS:
            shutil.copy2(os.path.join(HERE, name), os.path.join(self.bin, name))
        write(os.path.join(self.bin, "land_apply.py"), LAND_APPLY_STUB)
        write(os.path.join(self.bin, "commit_scan.py"), COMMIT_SCAN_STUB)
        ev = os.path.join(self.home, ".claude", "evidence")
        write(os.path.join(ev, "spec-council.json"), json.dumps({UNIT: {"state": "FIX-FIRST"}}))
        write(os.path.join(ev, "spec-scores.json"), json.dumps({SUB: {"score": 9}}))
        self.git("init", "-q", "--bare", self.hub, cwd=root)
        self.git("symbolic-ref", "HEAD", "refs/heads/" + BRANCH, cwd=self.hub)
        plan = {"units": [{"id": UNIT, "sub_units": [SUB], "spec": "docs/spec-u1.md", "evidence": "", "state": "OPEN",
                           "done_check": "python3 scripts/test_u1a.py"}]}
        files = {"docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json": json.dumps(plan, indent=1),
                 "docs/spec-u1.md": "# U1\n\n### %s\n\nDone check: `python3 scripts/test_u1a.py`\n" % SUB,
                 "scripts/test_u1a.py": GREEN_TEST, "scripts/check_all.sh": "#!/bin/sh\n# LAST, on purpose\n",
                 "scripts/test_battery_registration.py": GREEN, "scripts/system_doc.py": GREEN,
                 "scripts/bundle_runtime.py": GREEN, "scripts/hermetic_test_check.py": GREEN,
                 # the two checks the installed hooks run, trivial as the gates are; the real hook body runs the gate stub
                 "scripts/self_check_staged.py": GREEN, "scripts/pre_push_gate.py": GREEN, "scripts/pre_push_hook.sh": PRE_PUSH_HOOK_BODY}
        files.update(tree_files or {})
        for rel in drop:   # a base commit without one of the defaults (a frozen copy that lacks a check)
            files.pop(rel, None)
        os.makedirs(self.tree)
        self.git("init", "-q", "-b", BRANCH, cwd=self.tree)
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
            self.git("config", k, v, cwd=self.tree)
        write(os.path.join(self.tree, ".git", "hooks", "pre-commit"), PRE_COMMIT_HOOK, 0o755)
        write(os.path.join(self.tree, ".git", "hooks", "pre-push"), PRE_PUSH_HOOK, 0o755)
        for rel, text in files.items():
            write(os.path.join(self.tree, rel), text)
        self.git("add", "-A", cwd=self.tree)
        self.git("commit", "-q", "-m", "base", cwd=self.tree)
        if fetch_link:
            os.symlink(self.hub, self.link)
            self.git("remote", "add", "hub", self.link, cwd=self.tree)
            self.git("remote", "set-url", "--push", "hub", self.hub, cwd=self.tree)
        else:
            self.git("remote", "add", "hub", self.hub, cwd=self.tree)
        self.git("push", "-q", "-u", "hub", BRANCH, cwd=self.tree)
        # the hooks go in AFTER the fixture's own first push, so they act on the landing's push only
        if hook:
            write(os.path.join(self.hub, "hooks", "post-receive"), hook, 0o755)
        if pre_receive:
            write(os.path.join(self.hub, "hooks", "pre-receive"), pre_receive, 0o755)
        self.base = self.rev("HEAD")
        self.build = os.path.join(self.runs, SUB + "-000001", "round0", "out", SUB + "-r0-build.json")
        write(self.build, json.dumps({"edits": [{"path": "docs/landed-u1a.txt", "new_file_content": "landed\n"}],
                                      "tests": []}))
        self.status = os.path.join(self.runs, SUB + "-000001", "STATUS")
        write(self.status, "READY %s (round 0)\n" % self.build)
        self.tmpdir = os.path.join(root, "tmp")   # everything the landing writes to temp stays in the fixture, and is counted
        os.makedirs(self.tmpdir)
        self.env.update(HOME=self.home, TMPDIR=self.tmpdir, BROTHER_RUN_DIR=self.run_dir, SCAN_LOG=os.path.join(root, "scan.log"))
        if code_root:
            self.env["BROTHER_CODE_ROOT"] = code_root
        self.env.update(extra_env or {})

    def git(self, *args, **kw):
        """git as the fixture's owner runs it: hooks off, so the installed hooks meet only what the lander does."""
        r = subprocess.run(["git"] + NO_HOOKS + list(args), cwd=kw.get("cwd", self.tree), capture_output=True, text=True,
                           env=self.env, timeout=120)
        if r.returncode != 0 and kw.get("check", True):
            raise AssertionError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
        return r.stdout

    def rev(self, ref, cwd=None):
        return self.git("rev-parse", ref, cwd=cwd or self.tree).strip()

    def land(self, builds=None):
        """land_batch.main on the READY builds (default the one), as loop_pass.sh runs it. Returns (exit code, output)."""
        r = subprocess.run([sys.executable, "-B", os.path.join(self.bin, "land_batch.py")] + (builds or [self.build]), cwd=self.tree,
                           env=self.env, capture_output=True, text=True, timeout=600)
        return r.returncode, r.stdout + r.stderr

    def add_second_sub(self, build):
        """U1.b beside U1.a in the plan (committed and pushed, so the tree starts clean and equal to hub), scored, with a
        READY build holding `build`. Returns the build path."""
        plan_path = os.path.join(self.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        plan["units"][0]["sub_units"] = [SUB, SUB_B]
        write(plan_path, json.dumps(plan, indent=1))
        self.git("add", "-A"); self.git("commit", "-q", "-m", "two sub units"); self.git("push", "-q", "hub", BRANCH)
        self.base = self.rev("HEAD")
        write(os.path.join(self.home, ".claude", "evidence", "spec-scores.json"), json.dumps({SUB: {"score": 9}, SUB_B: {"score": 9}}))
        path = os.path.join(self.runs, SUB_B + "-000001", "round0", "out", SUB_B + "-r0-build.json")
        write(path, json.dumps(build))
        write(os.path.join(self.runs, SUB_B + "-000001", "STATUS"), "READY %s (round 0)\n" % path)
        return path

    def edits(self, build):
        write(self.build, json.dumps(build))

    def rows(self):
        path = os.path.join(self.run_dir, "proof", "landings.jsonl")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]


def foreign_hook(once_marker):
    """post-receive: put ONE foreign commit on top of whatever was pushed to the branch, once."""
    return ('#!/bin/sh\n[ -f "%s" ] && exit 0\nwhile read old new ref; do\n'
            '  [ "$ref" = "refs/heads/%s" ] || continue\n'
            '  c=$(echo foreign | git -c user.name=f -c user.email=f@example.invalid commit-tree "$new^{tree}" -p "$new")\n'
            '  git update-ref "refs/heads/%s" "$c" "$new" && touch "%s"\ndone\n') % (once_marker, BRANCH, BRANCH, once_marker)


REVERT_AFTER_CLOSURE = '''import os, subprocess, sys
other, marker = sys.argv[1:]
env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
def git(*args, cwd=other):
    r = subprocess.run(["git"] + list(args), cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr)
    return r.stdout.strip()
for line in sys.stdin:
    old, new, ref = line.split()
    if os.path.exists(marker) or not git("show", "-s", "--format=%s", new, cwd=os.getcwd()).startswith("U1 closed:"):
        continue
    git("fetch", "-q", "origin"); git("merge", "-q", "--ff-only", "origin/main")
    # the landing and the closure both edit the plan; theirs keeps the closure's plan and drops the landed file
    git("revert", "--no-edit", "-Xtheirs", "HEAD~1"); git("push", "-q", "origin", "HEAD:main")
    open(marker, "w").write(git("rev-parse", "HEAD"))
'''


def revert_after_closure(root, hub):
    """post-receive: once the CLOSURE push arrives, a second clone reverts the landing (the closure's parent) with a real
    git revert and pushes it, between the closure push and the lander's last fetch. Local repositories only."""
    other = os.path.join(root, "other")
    subprocess.run(["git", "clone", "-q", hub, other], env=git_env(), check=True, capture_output=True)
    for k, v in (("user.name", "o"), ("user.email", "o@example.invalid"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", other, "config", k, v], env=git_env(), check=True)
    script = os.path.join(root, "revert_after_closure.py")
    write(script, REVERT_AFTER_CLOSURE)
    return "#!/bin/sh\nexec '%s' -B '%s' '%s' '%s'\n" % (sys.executable, script, other, os.path.join(root, "reverted.done"))


# what another plan writer does while the gates run: appends evidence to U1 under the plan lock (run by an OUTSIDE_ACTOR)
CONCURRENT_EVIDENCE_APPEND = ('import plan_store\nplan_store.update_units("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", '
                              '{"U1": lambda u: dict(u, evidence=(u.get("evidence") or "") + " concurrent receipt.")}, fields=("evidence",))')


def before_commit(log_text):
    """The land log up to the landing commit, whatever git options the lander puts before the verb; "" when no commit ran."""
    m = re.search(r"^\$ git .*\bcommit -q -m ", log_text, re.M)
    return log_text[:m.start()] if m else ""


def aware_utc(text):
    try:
        t = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return False
    return t.tzinfo is not None and t.utcoffset() == datetime.timedelta(0)


class Landings(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="landings-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.code = os.path.join(self.root, "code")
        write(os.path.join(self.code, "scripts", "close_unit.py"), CLOSE_UNIT_STUB)

    def fixture(self, **kw):
        return Fixture(os.path.join(self.root, "f"), code_root=self.code, **kw)

    def landing_commit(self, f):
        """The one commit whose message is the landing's, found in the upstream's history."""
        for line in f.git("log", "--format=%H %s", BRANCH, cwd=f.hub).splitlines():
            sha, subject = line.split(" ", 1)
            if subject.startswith(SUB + " land:"):
                return sha
        self.fail("no landing commit in the upstream")

    def status_names(self, f, sha):
        with open(f.status, encoding="utf-8") as fh:
            text = fh.read()
        short = f.git("rev-parse", "--short", sha).strip()
        return text.startswith("LANDED ") and text.rstrip().endswith(" commit " + short), text

    def assert_row(self, f, row, landing, verdict):
        self.assertEqual(row.get("schema"), "loop-landing-v1", row)
        self.assertEqual(row.get("commit"), landing, "the row names the landing commit")
        self.assertEqual(row.get("subs"), [SUB])
        self.assertEqual(row.get("builds"), [f.build])
        self.assertEqual(row.get("verdict"), verdict)
        self.assertEqual(row.get("remote_ref"), "hub/" + BRANCH)
        self.assertTrue(aware_utc(row.get("at")), row)

    def outside(self, action, gate_rc=0, said="gate answered"):
        """(gate stub text, start): a WAITING_GATE for the tree and a function starting the OUTSIDE_ACTOR that runs `action`
        (Python, cwd the landing tree, plan_store importable) once the gate runs, then lets the gate answer gate_rc."""
        fx, done = os.path.join(self.root, "f"), os.path.join(self.root, "outside.done")
        script = os.path.join(self.root, "outside_actor.py")
        write(script, OUTSIDE_ACTOR % {"bin": os.path.join(fx, "bin"), "tree": os.path.join(fx, "tree"), "tmp": os.path.join(fx, "tmp"),
                                       "done": done, "action": action})

        def start(f):
            p = subprocess.Popen([sys.executable, "-B", script], env=f.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(lambda: p.poll() is None and p.kill())
            return p
        return WAITING_GATE % {"done": done, "said": said, "rc": gate_rc}, start

    def marker_module(self):
        """(marker path outside every tree, module text): a module that writes the marker when it is imported and swallows
        the refusal a sandbox answers with, so where the import ran is read from the marker alone."""
        marker = os.path.join(self.root, "ran-outside.marker")
        return marker, "import os\ntry:\n    open(%r, 'w').write('ran outside the sandbox')\nexcept OSError:\n    pass\n" % marker

    # D13 (decision record 2026-10-02): no script the landing or the git hooks run outside the sandbox may come from the tree
    # a build just changed. ONE CONDITION EACH: the build edits exactly one module that exactly one of the four routed scripts
    # imports; the module writes a marker outside every tree when it runs; the landing must still land.

    # THE HOLD AT THE LANDING'S OWN DOOR (D13, 2026-10-03): only the driver read the loop's stop controls, so a direct
    # land_batch.py call landed while the loop was held. One condition per case, each through main() as loop_pass runs it.
    def held_landing(self, control):
        f = self.fixture()
        ev = os.path.join(f.home, ".claude", "evidence")
        if control == "pause":
            write(os.path.join(ev, "LOOP-PAUSE.txt"), "paused by the owner for the merge window\n")
        elif control == "unreadable":
            os.makedirs(os.path.join(ev, "LOOP-HOLD.txt"))   # a directory in the control file's place
        with open(f.status, encoding="utf-8") as fh:
            status = fh.read()
        rc, out = f.land()
        with open(f.status, encoding="utf-8") as fh:
            return f, rc, out, status, fh.read()

    def test_a_paused_loop_lands_nothing_through_the_lander_itself(self):
        f, rc, out, before, after = self.held_landing("pause")
        self.assertEqual(rc, 5, out)
        self.assertIn("HELD at land_batch: PAUSE: paused by the owner", out)
        self.assertEqual((f.rev(BRANCH, cwd=f.hub), f.rev("HEAD")), (f.base, f.base), "nothing committed or pushed")
        self.assertEqual(f.git("status", "--porcelain"), "", "nothing applied")
        self.assertEqual(after, before, "STATUS unchanged")

    def test_an_unreadable_hold_file_holds_the_lander(self):
        f, rc, out, before, after = self.held_landing("unreadable")
        self.assertEqual(rc, 5, out)
        self.assertIn("HELD at land_batch: HOLD:", out)
        self.assertEqual((f.rev(BRANCH, cwd=f.hub), after), (f.base, before))

    def test_no_control_file_lands_as_before(self):
        f, rc, out, before, after = self.held_landing(None)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("HELD", out)
        self.assertNotEqual(f.rev(BRANCH, cwd=f.hub), f.base, "the landing reached hub")

    def test_a_build_edit_to_a_module_a_gate_imports_never_runs_outside_the_sandbox(self):
        marker, code = self.marker_module()
        f = self.fixture(tree_files={"scripts/u1helper.py": "X = 0\n", "scripts/system_doc.py": "import sys, u1helper\nsys.exit(0)\n"})
        f.edits({"edits": [{"path": "scripts/u1helper.py", "new_file_content": code}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(marker), "the module the gate imports ran where it could write outside the tree")
        self.assertEqual(f.git("show", BRANCH + ":scripts/u1helper.py", cwd=f.hub), code, "the build landed")

    def test_a_build_edit_to_a_module_the_pre_commit_check_imports_never_runs_outside_the_sandbox(self):
        marker, code = self.marker_module()
        f = self.fixture(tree_files={"scripts/u1terms.py": "X = 0\n", "scripts/self_check_staged.py": "import sys, u1terms\nsys.exit(0)\n"})
        f.edits({"edits": [{"path": "scripts/u1terms.py", "new_file_content": code}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(marker), "the module the pre-commit check imports ran at the landing commit")
        self.assertEqual(f.git("show", BRANCH + ":scripts/u1terms.py", cwd=f.hub), code, "the build landed")

    def test_a_build_edit_to_a_module_the_push_gate_imports_never_runs_outside_the_sandbox(self):
        marker, code = self.marker_module()
        f = self.fixture(tree_files={"scripts/u1edition.py": "X = 0\n", "scripts/pre_push_gate.py": "import sys, u1edition\nsys.exit(0)\n"})
        f.edits({"edits": [{"path": "scripts/u1edition.py", "new_file_content": code}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(marker), "the module the push gate imports ran at the landing push")
        self.assertEqual(f.git("show", BRANCH + ":scripts/u1edition.py", cwd=f.hub), code, "the build landed")

    def test_a_build_edit_to_a_module_the_hermetic_check_imports_never_runs_outside_the_sandbox(self):
        marker, code = self.marker_module()
        f = self.fixture(tree_files={"scripts/u1cut.py": "X = 0\n", "scripts/hermetic_test_check.py": "import sys, u1cut\nsys.exit(0)\n"})
        f.edits({"edits": [{"path": "scripts/u1cut.py", "new_file_content": code}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(marker), "the module the hermetic check imports ran from the landing tree")
        self.assertEqual(f.git("show", BRANCH + ":scripts/u1cut.py", cwd=f.hub), code, "the build landed")

    def test_a_pre_commit_check_that_cannot_read_its_term_list_refuses_the_commit_by_name(self):
        # ONE CONDITION: the REAL pre-commit check and the module it imports, with a HOME that holds no term list; its own
        # NO-DATA (exit 2) refuses the landing, named as the pre-commit check's, and nothing is committed or pushed
        real = {}
        for name in ("self_check_staged.py", "private_terms_scan.py"):
            with open(os.path.join(REPO, "scripts", name), encoding="utf-8") as fh:
                real["scripts/" + name] = fh.read()
        f = self.fixture(tree_files=real)
        self.assertFalse(os.path.exists(os.path.join(f.home, ".brothersbe-private-names")), "the fixture HOME holds no term list")
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("pre-commit check exit 2", out, "the refusal names the check and its NO-DATA code: " + out)
        self.assertIn("NO-DATA", out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertEqual(f.rev("HEAD"), f.base, "nothing was committed")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")
        with open(f.status, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("QUARANTINE"), "the one build is blamed")

    def test_a_red_push_gate_refuses_the_push_by_name_and_unwinds(self):
        # ONE CONDITION: the push gate (the pre-push hook's checks, run by the lander) refuses; the push never runs, the
        # commit is kept under refs/brother/unlanded and the branch goes back on hub
        f = self.fixture(tree_files={"scripts/pre_push_gate.py": "import sys\nprint('pre-push: REFUSED', file=sys.stderr)\nsys.exit(1)\n"})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("push gate exit 1", out, "the refusal names the gate: " + out)
        self.assertIn("UNWOUND", out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertEqual(f.rev("HEAD"), f.base, "the branch is back on hub")
        self.assertIn("refs/brother/unlanded/", f.git("for-each-ref", "refs/brother/unlanded/"), "the commit is kept")
        with open(f.status, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("QUARANTINE"))

    def test_landed_records_the_landing_commit_not_its_parent_nor_the_closure(self):
        f = self.fixture()
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertIn("CLOSED  " + UNIT, out, "the closure commit is part of this fixture")
        landing = self.landing_commit(f)
        self.assertEqual(f.rev(landing + "~1"), f.base, "the landing's parent is the base this landing started from")
        self.assertNotEqual(f.rev("HEAD"), landing, "HEAD is the closure commit, so HEAD is not the landing")
        rows = f.rows()
        self.assertEqual(len(rows or []), 1, out)
        self.assert_row(f, rows[0], landing, "LANDED")
        # the record carries the LAST fetch (X1 finding 2): the closure's, which saw the closure on the landing
        remote = rows[0]["remote_sha"]
        self.assertEqual(remote, f.rev(BRANCH, cwd=f.hub), "the last fetch saw the hub head")
        self.assertEqual(f.rev(remote + "~1"), landing, "and the hub head is the closure on the landing")
        self.assertTrue(aware_utc(rows[0].get("fetched_at")), rows[0])
        named, text = self.status_names(f, landing)
        self.assertTrue(named, "STATUS names the landing commit, not its parent: %r" % text)
        hist = os.path.join(f.run_dir, "proof", "landing-history", landing + ".log")
        expected = subprocess.run(["git", "log", "--format=%H%x00%B", "%s..%s" % (landing, remote)], cwd=f.tree,
                                  capture_output=True, env=f.env).stdout
        sys.path.insert(0, HERE)
        import proof_accept
        with open(hist, "rb") as fh:
            self.assertEqual(fh.read(), expected + proof_accept.history_end(landing, remote),
                             "the whole observed range (the closure alone), then its range line")

    def test_a_build_that_writes_a_path_it_never_declared_is_dropped(self):
        # ONE CONDITION (tenth review 2026-10-02): every changed path used to be committed as the build's
        f = self.fixture()
        f.edits({"edits": [{"path": "scripts/u1a.py", "new_file_content": "X = 1\n"}], "tests": [],
                 "plant": ["scripts/pre_push_hook.sh"]})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("never declared", out)
        with open(os.path.join(f.tree, "scripts", "pre_push_hook.sh"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), PRE_PUSH_HOOK_BODY, "the planted hook is back as HEAD holds it")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_an_ignored_path_a_gate_plants_never_reaches_the_tree(self):
        # ONE CONDITION (D13, 2026-10-02): a gate runs in the disposable copy under the sandbox, so what it writes outside
        # git's sight (a .pyc a later python would load, an ignored folder) dies with the copy and never reaches the tree
        planting = ("import os, sys\nos.makedirs('scripts/loop/__pycache__', exist_ok=True)\n"
                    "open('scripts/loop/__pycache__/planted.pyc', 'wb').write(b'x')\nos.makedirs('dist', exist_ok=True)\n"
                    "open('dist/planted', 'w').write('x')\nsys.exit(0)\n")
        f = self.fixture(tree_files={".gitignore": "__pycache__/\n*.pyc\ndist/\n", "scripts/test_battery_registration.py": planting})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(f.tree, "scripts", "loop", "__pycache__")), "the planted .pyc stayed in the copy")
        self.assertFalse(os.path.exists(os.path.join(f.tree, "dist")))

    def test_a_generator_that_writes_a_protected_path_in_the_copy_refuses_the_landing(self):
        # ONE CONDITION (D13): system_doc's regenerate step runs in the copy (a build's code may run inside it) and writes
        # scripts/pre_push_hook.sh there beside SYSTEM.md; the protected path never comes out of the copy, the gate reads
        # red for it, and nothing is committed
        stub = ('import os, sys\nif "--check" in sys.argv:\n    sys.exit(0 if os.path.exists("SYSTEM.md") else 1)\n'
                'open("SYSTEM.md", "w").write("generated\\n")\nopen("scripts/pre_push_hook.sh", "w").write("#!/bin/sh\\nexit 0\\n")\n')
        f = self.fixture(tree_files={"scripts/system_doc.py": stub})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("the landing never takes", out)
        self.assertIn("scripts/pre_push_hook.sh", out)
        with open(os.path.join(f.tree, "scripts", "pre_push_hook.sh"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), PRE_PUSH_HOOK_BODY, "the protected file is untouched")
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_a_generator_that_writes_outside_its_declared_outputs_refuses_the_landing(self):
        # ONE CONDITION (review 14 finding 1, 2026-10-02): system_doc's regenerate step writes SYSTEM.md, its one declared
        # output, and scripts/u1extra.py, a path dest() lets a build write; the undeclared path is a red gate, nothing comes
        # out of the copy, nothing is committed (reproduced that day with scripts/private_terms_scan.py: the module the
        # NEXT landing's frozen pre-commit check imports, landed by a generator with no screen)
        stub = ('import os, sys\nif "--check" in sys.argv:\n    sys.exit(0 if os.path.exists("SYSTEM.md") else 1)\n'
                'open("SYSTEM.md", "w").write("generated\\n")\nopen("scripts/u1extra.py", "w").write("X = 1\\n")\n')
        f = self.fixture(tree_files={"scripts/system_doc.py": stub})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("outside its declared output set", out)
        self.assertIn("scripts/u1extra.py", out)
        self.assertFalse(os.path.exists(os.path.join(f.tree, "scripts", "u1extra.py")), "the undeclared path came out of the copy")
        self.assertFalse(os.path.exists(os.path.join(f.tree, "SYSTEM.md")), "a red gate takes nothing, declared or not")
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_a_generator_that_removes_a_tracked_file_in_the_copy_removes_it_in_the_landing(self):
        # ONE CONDITION (D13): the regenerate step deletes a clean tracked file in the copy (a stale mirrored file under
        # bundle/runtime/, as bundle_runtime does, inside the bundle gate's declared output set since review 14); the
        # removal comes out of the copy like a rewrite does, and the landing commit carries it
        stub = ('import os, sys\nif "--check" in sys.argv:\n    sys.exit(1 if os.path.exists("bundle/runtime/stale.py") else 0)\n'
                'os.remove("bundle/runtime/stale.py")\n')
        f = self.fixture(tree_files={"scripts/bundle_runtime.py": stub, "bundle/runtime/stale.py": "stale\n"})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        landing = self.landing_commit(f)
        committed = f.git("show", "--name-status", "--format=", landing, cwd=f.hub)
        self.assertIn("D\tbundle/runtime/stale.py", committed, committed)
        self.assertNotIn("bundle/runtime/stale.py", f.git("ls-tree", "-r", "--name-only", BRANCH, cwd=f.hub))

    def test_a_generator_that_removes_a_file_outside_its_declared_outputs_refuses_the_landing(self):
        # ONE CONDITION (review 14 finding 1): a removal is a write; system_doc's generator removing docs/stale.txt is as
        # undeclared as writing it, and the tracked file stays in the landing tree and on hub
        stub = ('import os, sys\nif "--check" in sys.argv:\n    sys.exit(0 if os.path.exists("SYSTEM.md") else 1)\n'
                'open("SYSTEM.md", "w").write("generated\\n")\nos.remove("docs/stale.txt")\n')
        f = self.fixture(tree_files={"scripts/system_doc.py": stub, "docs/stale.txt": "stale\n"})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("outside its declared output set", out)
        self.assertIn("docs/stale.txt", out)
        self.assertTrue(os.path.exists(os.path.join(f.tree, "docs", "stale.txt")), "the undeclared removal came out of the copy")
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")

    def test_a_generator_that_leaves_a_link_in_the_copy_refuses_the_landing(self):
        # ONE CONDITION (D13): the regenerate step replaces SYSTEM.md in the copy with a link to a file outside every tree;
        # the --check that follows passes through the link, but a link never comes out of the copy: red gate, nothing committed
        outside = os.path.join(self.root, "outside-system.md")
        write(outside, "generated\n")
        stub = ('import os, sys\nif "--check" in sys.argv:\n    sys.exit(0 if os.path.exists("SYSTEM.md") else 1)\n'
                'os.symlink(%r, "SYSTEM.md")\n' % outside)
        f = self.fixture(tree_files={"scripts/system_doc.py": stub})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("the landing never takes", out)
        self.assertFalse(os.path.lexists(os.path.join(f.tree, "SYSTEM.md")), "no link came out of the copy")
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_the_push_gate_is_handed_gits_own_ref_line_for_the_push(self):
        # the pre-push hook protocol: "<local ref> <local sha> <remote ref> <remote sha>" on stdin, which is what the gate
        # scans; a gate handed nothing reads the checkout's branch instead, a guess about the push. The stub refuses any
        # other line, so a lander that drops the line (or feeds the wrong shas) cannot land.
        stub = ("import re, sys\nw = sys.stdin.read().split()\n"
                "ok = len(w) == 4 and w[0] == 'HEAD' and w[2] == 'refs/heads/main' and all(re.fullmatch('[0-9a-f]{40}', s) for s in (w[1], w[3]))\n"
                "open(%r, 'w').write(' '.join(w))\nsys.exit(0 if ok else 1)\n" % os.path.join(self.root, "ref-line.txt"))
        f = self.fixture(tree_files={"scripts/pre_push_gate.py": stub})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        landing = self.landing_commit(f)
        with open(os.path.join(self.root, "ref-line.txt"), encoding="utf-8") as fh:
            words = fh.read().split()   # the last line handed: the closure's push, after the landing's
        self.assertEqual(words[1], f.rev("HEAD"), "the local sha is the commit being pushed (the closure)")
        self.assertEqual(words[3], landing, "the remote sha is what hub held before it (the landing)")

    def test_no_frozen_copy_refuses_before_any_build_is_judged(self):
        # ONE CONDITION (D13): the frozen copy of the base commit cannot be made; the landing says so by name and stops
        # before a build is applied, never falling back to the tree's checks. The base commit carries a path longer than
        # this volume allows, recorded through the index alone (skip-worktree, so the landing tree still reads clean), so
        # no checkout of HEAD can be written anywhere; an unwritable TMPDIR would not do, tempfile falls back in silence.
        f = self.fixture()
        deep = "/".join(["d" * 200] * 6) + "/x"
        blob = f.git("hash-object", "-w", os.path.join(f.tree, "docs", "spec-u1.md")).strip()
        f.git("update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, deep))
        f.git("update-index", "--skip-worktree", deep)
        f.git("commit", "-q", "-m", "a path no checkout can write"); f.git("push", "-q", "hub", BRANCH); f.base = f.rev("HEAD")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the landing tree reads clean")
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("no frozen copy of the base commit", out, out)
        with open(f.status, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("READY "), "no build was judged")
        self.assertFalse(os.path.exists(os.path.join(f.tree, "docs", "landed-u1a.txt")), "nothing was applied")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_a_frozen_copy_without_the_push_gate_refuses_the_push_rather_than_running_the_trees(self):
        # ONE CONDITION (D13): the base commit carries no scripts/pre_push_gate.py and the build lands one that would pass;
        # the lander answers NO-DATA for the gate (exit 125) and unwinds, and never runs the tree's copy
        f = self.fixture(drop=("scripts/pre_push_gate.py",))
        f.edits({"edits": [{"path": "docs/landed-u1a.txt", "new_file_content": "landed\n"},
                           {"path": "scripts/pre_push_gate.py", "new_file_content": GREEN}], "tests": []})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("push gate exit 125", out, out)
        self.assertIn("UNWOUND", out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")

    NEIGHBOUR_REGISTRY = '#!/bin/sh\nrun_check "u1mod" python3 scripts/test_u1mod.py\n# LAST, on purpose\n'

    def test_a_neighbour_that_writes_into_the_tree_writes_only_its_copy(self):
        # tenth review 2026-10-02: a neighbour imports the build's code and may write inside the tree it runs in; eleventh
        # review: that tree is a disposable copy, so the write never reaches the landing tree and the landing stands
        planting = "import unittest\nopen('scripts/planted.txt', 'w').write('x')\nclass T(unittest.TestCase):\n    def test_t(self):\n        pass\nunittest.main(argv=['x'], exit=True)\n"
        f = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_u1mod.py": planting, "scripts/check_all.sh": self.NEIGHBOUR_REGISTRY})
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(f.tree, "scripts", "planted.txt")), "the neighbour's write stayed in its copy")
        self.assertNotIn("scripts/planted.txt", f.git("ls-tree", "-r", "--name-only", BRANCH, cwd=f.hub))
        self.assertEqual(f.git("show", BRANCH + ":scripts/u1mod.py", cwd=f.hub), "X = 1\n")

    # REVIEW 14 FINDING 5 (2026-10-02, footprint): a red neighbour's base comparison made a THIRD checkout of the base commit
    # while the frozen copy already held exactly that; the frozen copy is the base tree while HEAD is still that commit
    def test_a_red_neighbours_base_comparison_reuses_the_frozen_copy(self):
        red = "import unittest\nclass T(unittest.TestCase):\n    def test_t(self):\n        self.fail('red on both trees')\nunittest.main(argv=['x'], exit=True)\n"
        f = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_u1mod.py": red, "scripts/check_all.sh": self.NEIGHBOUR_REGISTRY})
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("INHERITED", out, "the red is judged against the base and held: " + out)
        with open(max(glob.glob(os.path.join(f.home, ".claude", "evidence", "land-batch", "*.log")), key=os.path.getmtime), encoding="utf-8") as fh:
            log_text = fh.read()
        self.assertEqual(log_text.count("$ git read-tree "), 1, "one base checkout, the frozen copy, serves both uses")

    def test_a_base_rerun_that_writes_into_the_frozen_copy_refuses_the_landing(self):
        # ONE CONDITION: the base version of the neighbour writes scripts/planted.py beside itself (its sandbox root is the
        # frozen copy) and is red; the copy the commit and push checks run from is no longer the base commit, so the landing
        # refuses by name rather than holding the build as INHERITED on a copy it cannot trust
        red = ("import unittest\nopen('scripts/planted.py', 'w').write('X = 9\\n')\nclass T(unittest.TestCase):\n"
               "    def test_t(self):\n        self.fail('red on both trees')\nunittest.main(argv=['x'], exit=True)\n")
        f = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_u1mod.py": red, "scripts/check_all.sh": self.NEIGHBOUR_REGISTRY})
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("changed the frozen copy", out, out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_an_ignored_path_a_neighbour_plants_never_reaches_the_tree(self):
        # ONE CONDITION (eleventh review 2026-10-02, channel a): the neighbour wrote scripts/loop/__pycache__/x.pyc and
        # dist/, both gitignored, so changed_paths never saw them, the post neighbour check and the rollback missed them,
        # and a later unsandboxed python3 -B gate loaded the planted .pyc
        planting = ("import os, unittest\nos.makedirs('scripts/loop/__pycache__', exist_ok=True)\n"
                    "open('scripts/loop/__pycache__/planted.pyc', 'wb').write(b'x')\nos.makedirs('dist', exist_ok=True)\n"
                    "open('dist/planted', 'w').write('x')\nclass T(unittest.TestCase):\n    def test_t(self):\n        pass\nunittest.main(argv=['x'], exit=True)\n")
        f = self.fixture(tree_files={".gitignore": "__pycache__/\n*.pyc\ndist/\n", "scripts/u1mod.py": "X = 0\n",
                                     "scripts/test_u1mod.py": planting, "scripts/check_all.sh": self.NEIGHBOUR_REGISTRY})
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(f.tree, "scripts", "loop", "__pycache__")), "the planted .pyc stayed in the copy")
        self.assertFalse(os.path.exists(os.path.join(f.tree, "dist")))

    def test_two_accepted_builds_editing_one_path_both_land(self):
        # ONE CONDITION (twelfth review 2026-10-02): the second build's bytes never entered graded, so the batch was
        # refused at the pre-add check on every pass
        both = "# U1\n\n### %s\n\nDone check: `python3 scripts/test_u1a.py`\n\n### %s\n\nDone check: `python3 scripts/test_u1a.py`\n" % (SUB, SUB_B)
        f = self.fixture(tree_files={"docs/shared.txt": "base\n", "docs/spec-u1.md": both})
        f.edits({"edits": [{"path": "docs/shared.txt", "new_file_content": "from A\n"}], "tests": []})
        b = f.add_second_sub({"edits": [{"path": "docs/shared.txt", "new_file_content": "from B\n"},
                                        {"path": "docs/b-only.txt", "new_file_content": "only B\n"}], "tests": []})
        rc, out = f.land([f.build, b])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("changed after the lander wrote them", out)
        self.assertTrue(os.path.isfile(os.path.join(f.tree, "docs", "b-only.txt")), "the second build landed too\n" + out)
        with open(os.path.join(f.tree, "docs", "shared.txt"), encoding="utf-8") as fh:
            self.assertIn(fh.read(), ("from A\n", "from B\n"))   # whichever applied last; the batch order is the lander's

    def test_a_rollback_never_writes_through_a_link_a_dropped_build_planted(self):
        # ONE CONDITION (eleventh review 2026-10-02, channel b): the rejected build's code replaced a landed file with a
        # symlink to a file outside the tree; rollback's open(p, "wb") followed it, wrote the earlier build's bytes
        # outside the tree, read them back through the link, reported success, and the link itself was committed
        outside = os.path.join(self.root, "outside.txt")
        write(outside, "outside\n")
        f = self.fixture(tree_files={"docs/shared.txt": "base\n"})
        f.edits({"edits": [{"path": "docs/shared.txt", "new_file_content": "checked A\n"}], "tests": []})
        b = f.add_second_sub({"edits": [{"path": "docs/shared.txt", "new_file_content": "rejected B\n"}], "tests": [],
                              "symlink": {"docs/shared.txt": outside}, "reject": True})
        rc, out = f.land([f.build, b])
        self.assertIn("DROPPED " + SUB_B, out, out)
        with open(outside, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "outside\n", "nothing was written through the link")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.islink(os.path.join(f.tree, "docs", "shared.txt")))
        self.assertEqual(f.git("show", BRANCH + ":docs/shared.txt", cwd=f.hub), "checked A\n")
        self.assertIn("100644", f.git("ls-tree", BRANCH, "docs/shared.txt", cwd=f.hub), "a regular file landed, never a link")

    def test_a_child_a_neighbour_leaves_behind_cannot_rewrite_a_landed_file(self):
        # ONE CONDITION (eleventh review 2026-10-02, channel c): the neighbour started a detached child (its own session,
        # descriptors closed) that kept rewriting the landed file after subprocess.run had returned, inside the window
        # before git add. In a copy its writes have nowhere to go; the landing tree holds the graded bytes throughout.
        mark = "rewriter-%d-%d" % (os.getpid(), int(time.time()))
        child = ("import time\n# %s\nend = time.time() + 20\nwhile time.time() < end:\n"
                 "    open('scripts/u1mod.py', 'w').write('X = 666\\n')\n    time.sleep(0.05)\n" % mark)
        leaving = ("import subprocess, sys, unittest\nsubprocess.Popen([sys.executable, '-c', %r], start_new_session=True, "
                   "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                   "class T(unittest.TestCase):\n    def test_t(self):\n        pass\nunittest.main(argv=['x'], exit=True)\n" % child)
        f = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_u1mod.py": leaving, "scripts/check_all.sh": self.NEIGHBOUR_REGISTRY})
        self.addCleanup(subprocess.run, ["pkill", "-9", "-f", mark])
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertEqual(f.git("show", BRANCH + ":scripts/u1mod.py", cwd=f.hub), "X = 1\n", "the graded bytes, never the child's")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the landing tree is clean: the child never reached it")

    def test_a_child_a_neighbour_leaves_behind_is_killed_with_its_group(self):
        # the second half of channel c: a child left in the neighbour's own process group dies when the neighbour answers
        mark = "300.%d%d" % (os.getpid() % 10000, int(time.time()) % 100000)
        leaving = ("import subprocess, unittest\nsubprocess.Popen(['/bin/sleep', %r], stdin=subprocess.DEVNULL, "
                   "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\nclass T(unittest.TestCase):\n    def test_t(self):\n        pass\nunittest.main(argv=['x'], exit=True)\n" % mark)
        f = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_u1mod.py": leaving, "scripts/check_all.sh": self.NEIGHBOUR_REGISTRY})
        self.addCleanup(subprocess.run, ["pkill", "-9", "-f", "sleep " + mark])
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        ps = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
        self.assertEqual([l for l in ps.splitlines() if "sleep " + mark in l], [], "a child the neighbour left behind survived the landing")

    def test_a_dropped_build_never_erases_what_another_writer_appended_to_the_plan(self):
        # ONE CONDITION (eleventh review 2026-10-02, channel d): while the rejected build was judged, another writer
        # appended evidence to the plan under its lock; the rollback of the drop checked the whole plan file out and
        # erased it, and the clean tree hid the loss
        f = self.fixture()
        b = f.add_second_sub({"edits": [{"path": "docs/b.txt", "new_file_content": "rejected B\n"}], "tests": [],
                              "plan_append": True, "reject": True})
        rc, out = f.land([f.build, b])
        self.assertIn("DROPPED " + SUB_B, out, out)
        self.assertEqual(rc, 0, out)
        evidence = json.loads(f.git("show", BRANCH + ":docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", cwd=f.hub))["units"][0]["evidence"]
        self.assertIn(" concurrent receipt.", evidence, "the other writer's evidence survives the drop and lands with the batch")
        self.assertIn(SUB + " landed", evidence)

    def test_an_unreadable_tree_refuses_before_any_build_is_judged(self):
        # ONE CONDITION (eleventh review 2026-10-02, finding 5): git status failed and changed_paths returned an empty set,
        # which read as a clean tree; the landing went on, applied the build and quarantined it on the commit that followed
        f = self.fixture()
        # HEAD's tree object is gone: fetch, rev-parse, merge-base and ls-files still answer, only status cannot
        tree = f.rev("HEAD^{tree}")
        os.remove(os.path.join(f.tree, ".git", "objects", tree[:2], tree[2:]))
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("the tree could not be read", out)
        with open(f.status, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("READY "), "no build is judged on a tree git cannot read")
        self.assertFalse(os.path.exists(os.path.join(f.tree, "docs", "landed-u1a.txt")), "nothing was applied")

    def test_a_landed_path_rewritten_before_git_add_refuses_the_batch(self):
        # ONE CONDITION (eleventh review 2026-10-02, the check before git add): something outside the lander (a gate ran in
        # the tree unsandboxed then; since D13 the gates run in a copy, so an outside process stands in) rewrote a landed
        # file after the lander wrote it; the only comparison used to be taken AFTER the gates, so the rewritten bytes
        # were committed under the build's name
        gate, start = self.outside("open('scripts/u1mod.py', 'w').write('X = 777\\n')")
        f = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_battery_registration.py": gate})
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        start(f)
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("changed after the lander wrote them", out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_a_landed_path_replaced_by_a_link_before_git_add_refuses_the_batch(self):
        # ONE CONDITION: the landed file became a symlink to a file holding the very same bytes; a comparison that read
        # through the link saw no change and committed the link (snapshot now records a link as a link)
        outside = os.path.join(self.root, "same-bytes.py")
        write(outside, "X = 1\n")   # the graded bytes, outside the tree, so only the link itself differs
        gate, start = self.outside("os.remove('scripts/u1mod.py')\nos.symlink(%r, 'scripts/u1mod.py')" % outside)
        f = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_battery_registration.py": gate})
        # the landed file is executable (0o755, a symlink's own mode on this platform), so neither bytes nor mode differ
        # through the link: only a snapshot that records the link as a link can refuse it
        os.chmod(os.path.join(f.tree, "scripts", "u1mod.py"), 0o755)
        f.git("add", "-A"); f.git("commit", "-q", "-m", "executable"); f.git("push", "-q", "hub", BRANCH); f.base = f.rev("HEAD")
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        start(f)
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("changed after the lander wrote them", out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertNotIn("120000", f.git("ls-tree", "-r", BRANCH, cwd=f.hub), "no link was ever committed")

    def test_a_test_path_that_is_not_a_plain_name_is_refused_and_never_registered(self):
        # ONE CONDITION (ninth review 2026-10-02): the lander pasted a new test's path verbatim into check_all.sh, a shell
        # script, so a build-created scripts/test_$(cmd).py would run at the next battery
        f = self.fixture()
        bad = "scripts/test_$(touch PWNED).py"
        # the stub land_apply writes "edits" only; new_tests is chosen by name, whichever list carried the file
        f.edits({"edits": [{"path": bad, "new_file_content": "import unittest\nunittest.main()\n"}], "tests": []})
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("not plain names", out)
        with open(os.path.join(f.tree, "scripts", "check_all.sh"), encoding="utf-8") as fh:
            self.assertNotIn("PWNED", fh.read())
        self.assertFalse(os.path.exists(os.path.join(f.tree, bad)), "the refused build's file is removed")

    def test_no_run_knob_reaches_a_landing_gate(self):
        # ONE CONDITION: the run's own routing is set, and one static gate fails whenever it can see a run knob
        # (2026-10-02: under BROTHER_TRANSPORTS=claude a neighbour skipped every Jev call and RL5.a was held)
        knob_gate = "import os, sys\nsys.exit(1 if os.environ.get('BROTHER_TRANSPORTS') else 0)\n"
        f = self.fixture(tree_files={"scripts/system_doc.py": knob_gate}, extra_env={"BROTHER_TRANSPORTS": "claude"})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.landing_commit(f)

    def test_no_run_knob_reaches_the_push_hook(self):
        # ONE CONDITION: the run's own routing is set, and a pre-push hook (outside the tree) refuses whenever it sees
        # a run knob (2026-10-02 12:54: the hermetic hook refused RL5.a under BROTHER_TRANSPORTS=claude)
        f = self.fixture(extra_env={"BROTHER_TRANSPORTS": "claude"})
        hooks = os.path.join(self.root, "local-hooks")
        write(os.path.join(hooks, "pre-push"), '#!/bin/sh\n[ -z "$BROTHER_TRANSPORTS" ]\n', 0o755)
        f.git("config", "core.hooksPath", hooks)
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.landing_commit(f)

    def test_fast_forward_records_the_landing_and_the_observed_remote(self):
        f = self.fixture(hook=foreign_hook(os.path.join(self.root, "foreign.done")))
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertIn("CATCH-UP hub holds this landing and more", out, "this fixture drives the FAST-FORWARD verdict")
        landing = self.landing_commit(f)
        rows = f.rows()
        self.assertEqual(len(rows or []), 1, out)
        self.assert_row(f, rows[0], landing, "FAST-FORWARD")
        remote = rows[0]["remote_sha"]
        self.assertEqual(remote, f.rev(BRANCH, cwd=f.hub), "the record carries the last fetch, after the closure")
        self.assertEqual(f.rev(remote + "~2"), landing, "the observed remote is the closure on the foreign commit on the landing")
        self.assertTrue(aware_utc(rows[0].get("fetched_at")), rows[0])
        expected = subprocess.run(["git", "log", "--format=%H%x00%B", "%s..%s" % (landing, remote)], cwd=f.tree,
                                  capture_output=True, env=f.env).stdout
        hist = os.path.join(f.run_dir, "proof", "landing-history", landing + ".log")
        with open(hist, "rb") as fh:
            got = fh.read()
        sys.path.insert(0, HERE)
        import proof_accept
        self.assertEqual(got, expected + proof_accept.history_end(landing, remote), "the observed range's log bytes, then its range line")
        self.assertIn(f.rev(remote + "~1").encode() + b"\x00foreign", got, "the foreign commit is in the observed range")
        named, text = self.status_names(f, landing)
        self.assertTrue(named, "STATUS names the landing commit: %r" % text)

    def test_unverified_records_the_landing_with_no_observed_remote(self):
        hook = '#!/bin/sh\ncat >/dev/null\nrm -f "%s"\n' % os.path.join(self.root, "f", "hub-link.git")
        f = self.fixture(hook=hook, fetch_link=True)
        rc, out = f.land()
        self.assertIn("PARITY  NO-DATA", out, "this fixture drives the UNVERIFIED verdict")
        self.assertNotEqual(rc, 0, "parity is NO-DATA, which is never exit 0")
        landing = self.landing_commit(f)
        rows = f.rows()
        self.assertEqual(len(rows or []), 1, out)
        self.assert_row(f, rows[0], landing, "UNVERIFIED")
        self.assertIsNone(rows[0].get("remote_sha"), "no fetch succeeded, so no remote was observed")
        self.assertIsNone(rows[0].get("fetched_at"))
        # A18 (2026-09-30): the UNVERIFIED row is a NO-DATA record that names why, never a row without its facts.
        self.assertTrue(str(rows[0].get("parity", "")).startswith("NO-DATA"), "the row names why no remote was observed: %r" % rows[0].get("parity"))
        self.assertIn("fetch failed", rows[0]["parity"])
        self.assertEqual(rows[0]["files"], sorted(f.git("show", "--name-only", "--format=", landing).split()), "the files the landing commit holds")
        self.assertTrue(rows[0]["files"], "an UNVERIFIED landing still committed files")
        self.assertTrue(rows[0]["checks"] and all(isinstance(c["exit_code"], int) and c["command"].strip() for c in rows[0]["checks"]),
                        "every check that ran, with its command and code: %r" % rows[0].get("checks"))
        self.assertTrue(os.path.isfile(rows[0]["log"]), "log names the land log")
        self.assertIn(SUB, rows[0]["done_check"])
        self.assertFalse(os.path.exists(os.path.join(f.run_dir, "proof", "landing-history", landing + ".log")),
                         "no observed range means no history bytes, which the reader answers NO-DATA")
        named, text = self.status_names(f, landing)
        self.assertTrue(named, "the UNVERIFIED mark_landed call names the landing commit too: %r" % text)

    def test_a_refused_push_records_nothing(self):
        f = self.fixture(pre_receive="#!/bin/sh\ncat >/dev/null\necho refused by fixture >&2\nexit 1\n")
        rc, out = f.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("did not reach hub", out)
        self.assertIsNone(f.rows(), "a landing that never reached the remote is not a landing")

    def test_no_proof_directory_is_never_created(self):
        f = self.fixture(proof=False)
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(f.run_dir, "proof")), "outside a proof, nothing is created")
        named, _ = self.status_names(f, self.landing_commit(f))
        self.assertTrue(named)

    # FINDING B1 (2026-09-27): the rollback of a dropped build subtracted the paths an earlier build had attributed, so a
    # rejected build's edits to a file the first build also edited survived and were pushed under the first build's name
    def test_a_rejected_build_never_lands_its_bytes_on_a_path_an_earlier_build_edited(self):
        f = self.fixture(tree_files={"docs/shared.txt": "base\n", "docs/b-only.txt": "base b\n"})
        f.edits({"edits": [{"path": "docs/shared.txt", "new_file_content": "checked A\n"}], "tests": []})
        b = f.add_second_sub({"edits": [{"path": "docs/shared.txt", "new_file_content": "rejected B\n"},
                                        {"path": "docs/b-only.txt", "new_file_content": "rejected B\n"},
                                        {"path": "docs/b-new.txt", "new_file_content": "rejected B\n"}], "tests": [], "reject": True})
        rc, out = f.land([f.build, b])
        self.assertIn("DROPPED " + SUB_B, out, "this fixture drives the rejected second build")
        self.assertEqual(rc, 0, out)
        self.assertEqual(f.git("show", BRANCH + ":docs/shared.txt", cwd=f.hub), "checked A\n", "only A's bytes land")
        self.assertEqual(f.git("show", BRANCH + ":docs/b-only.txt", cwd=f.hub), "base b\n")
        self.assertNotIn("docs/b-new.txt", f.git("ls-tree", "-r", "--name-only", BRANCH, cwd=f.hub))
        self.assertEqual([r["subs"] for r in f.rows()], [[SUB]])
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")
        with open(os.path.join(f.runs, SUB_B + "-000001", "STATUS"), encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("QUARANTINE"))

    def test_a_dropped_build_that_cannot_be_rolled_back_refuses_the_whole_batch(self):
        # the rejected build leaves docs/ unwritable, so neither the rollback nor the revert after it can put A's bytes
        # back (until 2026-10-02 the fixture made the FILE read only, which a rollback that removes and recreates handles)
        f = self.fixture(tree_files={"docs/shared.txt": "base\n"})
        self.addCleanup(os.chmod, os.path.join(f.tree, "docs"), 0o755)
        f.edits({"edits": [{"path": "docs/shared.txt", "new_file_content": "checked A\n"}], "tests": []})
        b = f.add_second_sub({"edits": [{"path": "docs/shared.txt", "new_file_content": "rejected B\n"}], "tests": [],
                              "reject": True, "readonly": True})
        rc, out = f.land([f.build, b])
        self.assertIn("DROPPED " + SUB_B, out, out)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("could not be rolled back", out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")
        self.assertIsNone(f.rows(), "nothing landed, so nothing is recorded")

    # FINDING B4 (2026-09-27): the closure was committed and pushed AFTER the parity check, and a refused closure push
    # returned 0 with local ahead of hub. REVIEW 14 FINDING 3 (2026-10-02): the closure commit then stayed local, so the
    # driver's reconcile push ran the tree's installed hooks over just landed code. The lander now unwinds its own closure
    # commit the way it unwinds a refused landing: parity holds, the commit is kept under a ref, the unit stays open.
    def test_a_refused_closure_push_is_unwound_so_local_never_stays_ahead_of_hub(self):
        marker = os.path.join(self.root, "first-push.done")
        hook = '#!/bin/sh\ncat >/dev/null\nif [ -f "%s" ]; then echo closure refused >&2; exit 1; fi\ntouch "%s"\n' % (marker, marker)
        f = self.fixture(pre_receive=hook)
        rc, out = f.land()
        self.assertIn("CLOSE-RED " + UNIT, out, "this fixture refuses exactly the closure push: " + out)
        self.assertIn("UNWOUND", out, out)
        self.assertEqual(f.rev("HEAD"), f.rev(BRANCH, cwd=f.hub), "local is back on hub: " + out)
        self.assertEqual(f.rev("HEAD"), self.landing_commit(f), "the landing commit is the branch head again")
        self.assertEqual(len(f.git("for-each-ref", "refs/brother/unlanded/").splitlines()), 1, "the closure commit is kept under a ref")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean")
        plan = json.loads(f.git("show", "HEAD:docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"))
        self.assertNotEqual(plan["units"][0].get("state"), "DONE", "the unit stays open for the next closer")
        with open(f.status, encoding="utf-8") as fh:
            self.assertFalse(fh.read().startswith("QUARANTINE"), "the landed build is not blamed for its closure's push")
        self.assertEqual(rc, 0, "the landing reached hub and parity holds after the unwind: " + out)
        self.assertEqual(len(f.rows() or []), 1, "the landing itself did reach the remote and is recorded")

    # REVIEW 14 FINDING 3 (2026-10-02): the driver's reconcile push (loop_pass.sh, a local branch ahead of hub with a clean
    # tree) ran plain git push, whose installed pre-push hook execs the TREE's scripts/pre_push_hook.sh: just landed code,
    # unsandboxed. The lander's --push mode runs the push gate from a frozen copy of HUB's head and pushes with hooks off.
    def test_the_push_mode_pushes_a_local_ahead_commit_through_the_frozen_gate_with_hooks_off(self):
        # ONE CONDITION: the commit ahead of hub replaces scripts/pre_push_gate.py with a tripwire that writes a marker and
        # refuses; hub's copy (the frozen one) is green. The tree's hook would run the tripwire; the lander must not.
        marker = os.path.join(self.root, "tree-gate-ran.marker")
        f = self.fixture()
        write(os.path.join(f.tree, "scripts", "pre_push_gate.py"), "import sys\nopen(%r, 'w').write('ran')\nsys.exit(1)\n" % marker)
        f.git("add", "-A"); f.git("commit", "-q", "-m", "ahead of hub")
        ahead = f.rev("HEAD")
        self.assertNotEqual(ahead, f.rev(BRANCH, cwd=f.hub))
        rc, out = f.land(["--push"])
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(marker), "the tree's push gate ran: " + out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), ahead, "the commit reached hub: " + out)
        self.assertIn("PARITY OK", out)

    def test_the_push_mode_refuses_when_the_frozen_push_gate_refuses_and_names_the_gate(self):
        # ONE CONDITION: hub's push gate (the frozen one) refuses; nothing is pushed and the refusal is a BLOCK line the
        # driver reads as a gate refusal (never a transport retry)
        f = self.fixture(tree_files={"scripts/pre_push_gate.py": "import sys\nprint('pre-push: REFUSED by the fixture')\nsys.exit(1)\n"})
        write(os.path.join(f.tree, "docs", "note.txt"), "ahead\n")
        f.git("add", "-A"); f.git("commit", "-q", "-m", "ahead of hub")
        rc, out = f.land(["--push"])
        self.assertNotEqual(rc, 0, out)
        self.assertTrue(any(l.startswith("BLOCK ") for l in out.splitlines()), out)
        self.assertIn("push gate exit 1", out)
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")

    def test_the_push_mode_commits_a_dirty_plan_alone_after_the_frozen_pre_commit_check(self):
        # ONE CONDITION: the driver's closer left the plan dirty (and nothing else); the lander stages the plan, runs the
        # pre-commit check from the frozen copy (hub's head, green), commits and pushes with hooks off. The TREE's copy of
        # the pre-commit check, one commit ahead of hub and not yet pushed, is a tripwire: the installed hook would run it.
        marker = os.path.join(self.root, "tree-precommit-ran.marker")
        f = self.fixture()
        write(os.path.join(f.tree, "scripts", "self_check_staged.py"), "import sys\nopen(%r, 'w').write('ran')\nsys.exit(1)\n" % marker)
        f.git("add", "-A"); f.git("commit", "-q", "-m", "ahead of hub")
        plan_path = os.path.join(f.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        plan["units"][0]["state"] = "DONE"
        write(plan_path, json.dumps(plan, indent=1))
        rc, out = f.land(["--push"])
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(marker), "the tree's pre-commit check ran: " + out)
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the plan is committed")
        self.assertEqual(f.rev("HEAD"), f.rev(BRANCH, cwd=f.hub), "the closure reached hub: " + out)
        self.assertEqual(json.loads(f.git("show", BRANCH + ":docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", cwd=f.hub))["units"][0]["state"], "DONE")

    def test_the_push_mode_unwinds_its_own_plan_commit_when_the_frozen_gate_refuses(self):
        # ONE CONDITION: the lander committed the closer's plan change itself and hub's push gate then refuses; its own
        # commit is unwound (kept under a ref, the branch back on hub), so the lander never leaves local ahead of hub
        f = self.fixture(tree_files={"scripts/pre_push_gate.py": "import sys\nprint('pre-push: REFUSED by the fixture')\nsys.exit(1)\n"})
        plan_path = os.path.join(f.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        plan["units"][0]["state"] = "DONE"
        write(plan_path, json.dumps(plan, indent=1))
        rc, out = f.land(["--push"])
        self.assertNotEqual(rc, 0, out)
        self.assertIn("UNWOUND", out, out)
        self.assertEqual(f.rev("HEAD"), f.rev(BRANCH, cwd=f.hub), "local is back on hub: " + out)
        self.assertEqual(len(f.git("for-each-ref", "refs/brother/unlanded/").splitlines()), 1, "the plan commit is kept under a ref")
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the tree is left clean for the next closer")

    def test_the_push_mode_refuses_a_tree_dirty_beyond_the_plan(self):
        f = self.fixture()
        write(os.path.join(f.tree, "docs", "stray.txt"), "nobody's\n")
        rc, out = f.land(["--push"])
        self.assertNotEqual(rc, 0, out)
        self.assertIn("REFUSED", out)
        self.assertTrue(os.path.exists(os.path.join(f.tree, "docs", "stray.txt")), "the lander touches nothing it did not write")
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base)

    # X1 FINDING 2 (2026-09-27): the fetch after the closure push saw a revert of the landing and made the exit nonzero,
    # but the landing record and histories were written before it, so the receipt still counted the landing surviving
    def test_a_revert_the_last_fetch_saw_after_the_closure_removes_the_landing(self):
        sys.path.insert(0, HERE)
        import loop_receipt
        f = Fixture(os.path.join(self.root, "f"), code_root=self.code)
        write(os.path.join(f.hub, "hooks", "post-receive"), revert_after_closure(self.root, f.hub), 0o755)
        rc, out = f.land()
        self.assertIn("CLOSED  " + UNIT, out, "the closure is pushed in this fixture")
        self.assertTrue(os.path.exists(os.path.join(self.root, "reverted.done")), "the revert landed after the closure: " + out)
        self.assertNotEqual(rc, 0, "the last fetch sees the remote ahead of local: " + out)
        landing = self.landing_commit(f)
        rows = f.rows()
        self.assertEqual([r["commit"] for r in rows or []], [landing], out)
        self.assertEqual(rows[-1]["remote_sha"], f.rev(BRANCH, cwd=f.hub), "the record carries the LAST fetch")
        n, unknown, detail = loop_receipt.landings_end(f.run_dir, {"landings_record": loop_receipt.landing_record(f.run_dir)},
                                                       "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
        self.assertEqual((n, unknown, detail["reverted"]), (0, 0, [landing]), "a revert the run observed removes the landing")

    # X1 FINDING 3 (2026-09-27): a refusal restored the whole plan with an unlocked checkout, erasing evidence another
    # writer appended under the plan lock while the gates ran, and the clean tree hid the loss
    def test_a_refused_landing_keeps_evidence_another_writer_appended_under_the_plan_lock(self):
        gate, start = self.outside(CONCURRENT_EVIDENCE_APPEND, gate_rc=1, said="registration red after a concurrent evidence append")
        f = self.fixture(tree_files={"scripts/test_battery_registration.py": gate})
        start(f)
        rc, out = f.land()
        self.assertIn("registration red after a concurrent evidence append", open(max(glob.glob(os.path.join(
            f.home, ".claude", "evidence", "land-batch", "*.log")), key=os.path.getmtime), encoding="utf-8").read())
        self.assertIn("REFUSED: nothing committed", out)
        self.assertNotEqual(rc, 0, out)
        with open(os.path.join(f.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), encoding="utf-8") as fh:
            evidence = json.load(fh)["units"][0]["evidence"]
        self.assertEqual(evidence, " concurrent receipt.", "the other writer's evidence stays, this landing's line goes")
        self.assertEqual(f.rev(BRANCH, cwd=f.hub), f.base, "nothing reached the remote")

    def test_a_refused_landing_with_no_other_writer_leaves_the_plan_as_committed(self):
        f = self.fixture(tree_files={"scripts/test_battery_registration.py": "import sys\nprint('registration red')\nsys.exit(1)\n"})
        rc, out = f.land()
        self.assertIn("REFUSED: nothing committed", out)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(f.git("status", "--porcelain", "-uall"), "", "the plan is back byte for byte and the tree clean")

    # FINDING B10: the commit text claimed fast discover and the hermetic check exited 0; discovery no longer runs and the
    # hermetic check runs after the commit. Every check the text names must be a command this landing ran, with its code.
    SYSTEM_DOC_STUB = ('import os, sys\nif "--check" in sys.argv:\n    sys.exit(0 if os.path.exists("SYSTEM.md") else 1)\n'
                       'open("SYSTEM.md", "w").write("generated\\n")\n')

    def test_the_landing_commit_names_only_checks_that_ran_with_their_exit_codes(self):
        """system_doc --check exits 1 until its generator runs, and the build edits a module whose registered test runs
        as a neighbour on both Pythons, so the checks carry real codes (1, then 0) a fixed template could not match."""
        f = self.fixture(tree_files={"scripts/system_doc.py": self.SYSTEM_DOC_STUB, "scripts/u1mod.py": "X = 0\n",
                                     "scripts/test_u1mod.py": GREEN,
                                     "scripts/check_all.sh": '#!/bin/sh\nrun_check "u1mod" python3 scripts/test_u1mod.py\n# LAST, on purpose\n'})
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": []})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        msg = f.git("log", "-1", "--format=%B", self.landing_commit(f), cwd=f.hub)
        self.assertNotIn("discover", msg.lower(), msg)
        head = "Checks this landing ran before this commit, with their exit codes:"
        self.assertIn(head, msg)
        lines = msg.split(head, 1)[1].strip("\n").split("\n\n")[0].splitlines()
        claims = [re.match(r"^(.+?) exit (-?\d+)", l) for l in lines]
        self.assertTrue(claims and all(claims), msg)
        logs = sorted(glob.glob(os.path.join(f.home, ".claude", "evidence", "land-batch", "*.log")), key=os.path.getmtime)
        with open(logs[-1], encoding="utf-8") as fh:
            before = before_commit(fh.read())   # only what ran before the landing commit
        self.assertTrue(before, "the land log names the landing commit")
        ran = re.findall(r"^\$ (.*)\nenv: .*\nexit=(-?\d+)$", before, re.M)
        py3 = sys.executable + " -B scripts/test_u1mod.py"
        known = {"land_apply " + SUB: lambda c: c.endswith("/land_apply.py " + f.build),
                 "registration": lambda c: c.endswith(" -B scripts/test_battery_registration.py"),
                 "system_doc": lambda c: c.endswith(" -B scripts/system_doc.py --check"),
                 "system_doc regenerate": lambda c: c.endswith(" -B scripts/system_doc.py"),
                 "bundle": lambda c: c.endswith(" -B scripts/bundle_runtime.py --check"),
                 "commit_scan": lambda c: c.endswith("/commit_scan.py"),
                 # the pre-commit hook's check, run by the lander from the frozen copy of the base commit (D13)
                 "pre-commit check": lambda c: c.endswith("/scripts/self_check_staged.py") and "/land-base-" in c,
                 # neighbours run under the sandbox (eighth review 2026-10-02): the log holds the wrapped command
                 "neighbour scripts/test_u1mod.py on py3": lambda c: boxed_run(c, py3),
                 "neighbour scripts/test_u1mod.py on py3.9": lambda c: boxed_run(c, "/usr/bin/python3 -B scripts/test_u1mod.py")}
        said = [(m.group(1), m.group(2)) for m in claims]
        for label in sorted(set(l for l, _ in said)):
            self.assertIn(label, known, "a claim this test cannot trace to a command: %r" % label)
            self.assertEqual([c for l, c in said if l == label], [c for cmd, c in ran if known[label](cmd)],
                             "%s: the codes claimed are not the codes of the runs, in order\n%s" % (label, msg))
        self.assertEqual(len(said), sum(1 for cmd, _ in ran if any(p(cmd) for p in known.values())), "every check that ran is named\n" + msg)
        self.assertIn(("system_doc", "1"), said, "the fixture drives a real non-zero code")

    def test_the_landing_row_names_every_committed_file_and_every_check_with_its_command_and_code(self):
        """2026-09-30, AGENTS.md "The receipt contract": the row main() writes carries the files the landing commit holds
        (read BEFORE the commit, when git status still lists them) and the checks that ran before it, each with the exact
        command and the exit code the land log shows, so the run's receipt can name them per file."""
        f = self.fixture(tree_files={"scripts/system_doc.py": self.SYSTEM_DOC_STUB, "scripts/u1mod.py": "X = 0\n",
                                     "scripts/test_u1mod.py": GREEN,
                                     "scripts/check_all.sh": '#!/bin/sh\nrun_check "u1mod" python3 scripts/test_u1mod.py\n# LAST, on purpose\n'})
        f.edits({"edits": [{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}], "tests": [], "done_check": "python3 -B scripts/test_u1mod.py"})
        rc, out = f.land()
        self.assertEqual(rc, 0, out)
        landing = self.landing_commit(f)
        row = f.rows()[0]
        committed = sorted(f.git("show", "--name-only", "--format=", landing, cwd=f.hub).split())
        self.assertIn("scripts/u1mod.py", committed, "the fixture's edit is in the landing commit")
        # the generator ran in the copy (D13); what it wrote came out of the copy and landed under the gate's name
        self.assertIn("SYSTEM.md", committed, "the regenerated SYSTEM.md is in the landing commit")
        self.assertEqual(f.git("show", BRANCH + ":SYSTEM.md", cwd=f.hub), "generated\n")
        self.assertEqual(row["files"], committed, "every path the landing commit holds, and no other")
        self.assertEqual(row["done_check"], {SUB: "python3 -B scripts/test_u1mod.py"})
        self.assertTrue(os.path.isfile(row["log"]), "log names the land log, where the full output lives")
        msg = f.git("log", "-1", "--format=%B", landing, cwd=f.hub)
        said = re.findall(r"^(.+?) exit (-?\d+)", msg.split("with their exit codes:", 1)[1].strip("\n").split("\n\n")[0], re.M)
        self.assertEqual([(c["label"], str(c["exit_code"])) for c in row["checks"]], said, "the row and the commit message claim the same checks")
        with open(row["log"], encoding="utf-8") as fh:
            before = before_commit(fh.read())
        ran = re.findall(r"^\$ (.*)\nenv: .*\nexit=(-?\d+)$", before, re.M)
        commands = {c["command"] for c in row["checks"]}
        self.assertEqual([(c["command"], str(c["exit_code"])) for c in row["checks"]],
                         [(cmd, code) for cmd, code in ran if cmd in commands], "each command and code is the one the log shows, in order")
        self.assertIn(("system_doc", 1), [(c["label"], c["exit_code"]) for c in row["checks"]], "a real non-zero code is kept, not flattened")

    def test_a_build_json_with_no_readable_done_check_records_none(self):
        """build_done_check is NO-DATA (None), never a guess: an unreadable file, an unparsable one, one that is not an
        object, a fenced block that parses, and a done_check that is not a non-empty string."""
        sys.path.insert(0, HERE)
        import land_batch
        d = tempfile.mkdtemp(prefix="done-check-"); self.addCleanup(shutil.rmtree, d, True)
        def build(text):
            write(os.path.join(d, "b.json"), text)
            return land_batch.build_done_check(os.path.join(d, "b.json"))
        self.assertIsNone(land_batch.build_done_check(os.path.join(d, "missing.json")))
        self.assertIsNone(build("{not json"))
        self.assertIsNone(build("[1, 2]"))
        self.assertIsNone(build('{"done_check": 7}'))
        self.assertIsNone(build('{"done_check": "  "}'))
        self.assertIsNone(build('{"edits": []}'))
        self.assertEqual(build('```json\n{"done_check": " python3 -B scripts/test_x.py "}\n```'), "python3 -B scripts/test_x.py")

    def test_record_landing_writes_the_old_row_when_it_is_given_no_per_file_facts(self):
        sys.path.insert(0, HERE)
        import land_batch
        run = tempfile.mkdtemp(prefix="old-row-"); self.addCleanup(shutil.rmtree, run, True); os.makedirs(os.path.join(run, "proof"))
        land_batch.record_landing(run, "a" * 40, [SUB], ["/b.json"], "LANDED", "hub/main", "a" * 40, "t")
        land_batch.record_landing(run, "b" * 40, [SUB], ["/b.json"], "LANDED", "hub/main", "b" * 40, "t", None, files=["x.py"], checks=[], done_check={}, log="/l")
        with open(os.path.join(run, "proof", "landings.jsonl"), encoding="utf-8") as fh:
            old, new = [json.loads(l) for l in fh if l.strip()]
        self.assertFalse({"files", "checks", "done_check", "log"} & set(old), "a caller that names none writes the row it always wrote")
        self.assertEqual((new["files"], new["checks"], new["done_check"], new["log"]), (["x.py"], [], {}, "/l"), "an empty value that was GIVEN is kept")


class Helpers(unittest.TestCase):
    """Properties main() cannot reach because a guard upstream already decides them (measured by the sweep)."""

    def setUp(self):
        sys.path.insert(0, HERE)
        import land_batch
        self.lb = land_batch

    def test_spec_gate_refuses_a_sub_unit_in_no_unit(self):
        self.assertIn("no unit or spec file", self.lb.spec_gate("Z.9", {"units": []}))

    def test_record_gate_without_a_run_dir_records_nothing(self):
        self.assertIs(self.lb.record_gate("", "landing.x", 0, 1), False)

    def test_a_rollback_puts_back_a_file_an_earlier_build_deleted_as_absent(self):
        """snapshot records a path that does not exist as None, and rollback removes what the dropped build put there."""
        repo = tempfile.mkdtemp(prefix="rollback-"); self.addCleanup(shutil.rmtree, repo, True)
        g = lambda *a: subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, env=git_env())
        write(os.path.join(repo, "gone.txt"), "base\n"); g("init", "-q"); g("add", "-A"); g("commit", "-q", "-m", "base")
        cwd = os.getcwd(); os.chdir(repo); self.addCleanup(os.chdir, cwd)
        os.remove("gone.txt")                                  # the earlier build of the batch deleted it
        snap = self.lb.snapshot(self.lb.changed_paths())
        write(os.path.join(repo, "gone.txt"), "rejected\n")   # the dropped build put it back with its own bytes
        left = self.lb.rollback(snap, self.lb.changed_paths(), open(os.devnull, "w"))
        self.assertEqual((left, os.path.exists("gone.txt")), ("", False))

    def test_a_status_write_waits_for_the_runs_lock_salvage_takes(self):
        """Every STATUS write takes <runs>/.status.lock, the lock salvage.promote holds across its compare then write, so
        a landing's QUARANTINE can never land between promote's read and promote's write (salvage lane, 2026-09-27).
        Deterministic: the test holds the lock and the lander's flock is made non blocking, so a write that takes the
        lock raises and leaves STATUS alone, and a write that skips it goes through."""
        import fcntl
        runs = tempfile.mkdtemp(prefix="status-lock-"); self.addCleanup(shutil.rmtree, runs, True)
        st = os.path.join(runs, "U1.a-000001", "STATUS"); write(st, "READY /x/b.json\n")
        nonblocking = type("F", (), {"LOCK_EX": fcntl.LOCK_EX, "flock": staticmethod(lambda fd, op: fcntl.flock(fd, op | fcntl.LOCK_NB))})
        with open(os.path.join(runs, ".status.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with unittest.mock.patch.object(self.lb, "fcntl", nonblocking, create=True):
                with self.assertRaises(OSError):
                    self.lb.write_status(st, "QUARANTINE x\n")
            with open(st, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "READY /x/b.json\n", "nothing is written while another writer holds the lock")
        self.lb.write_status(st, "QUARANTINE x\n")
        with open(st, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "QUARANTINE x\n")
        self.assertEqual(sorted(os.listdir(os.path.join(runs, "U1.a-000001"))), ["STATUS"], "the lock lives outside every run folder")

    def test_a_landing_commit_that_could_not_be_read_writes_no_row(self):
        run = tempfile.mkdtemp(prefix="landings-empty-")
        self.addCleanup(shutil.rmtree, run, True)
        os.makedirs(os.path.join(run, "proof"))
        line = self.lb.record_landing(run, "", [SUB], ["/b.json"], "LANDED", "hub/main", "a" * 40, "t", b"")
        self.assertTrue(line.startswith("RECORD  NOT WRITTEN"), line)
        self.assertEqual(os.listdir(os.path.join(run, "proof")), [], "no row and no history for a commit nobody can name")


class SurvivalAtTheLastFetch(unittest.TestCase):
    """Codex check-in 5, finding 2 (money-wrong): each landing's history was written once, at its own fetch, so A,
    then a revert of A, then B left A's history empty and A counted as surviving. Q3 judges survival at the run's LAST
    landing fetch: every later landing rewrites the earlier histories against the remote it just observed."""

    def git(self, *args):
        r = subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True, text=True, env=git_env())
        self.assertEqual(r.returncode, 0, r.stderr); return r.stdout.strip()

    def commit(self, name):
        write(os.path.join(self.repo, name), name + "\n"); self.git("add", name); self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def setUp(self):
        sys.path.insert(0, HERE)
        import land_batch, proof_accept
        self.lb, self.pa = land_batch, proof_accept
        self.repo = tempfile.mkdtemp(prefix="survival-repo-"); self.addCleanup(shutil.rmtree, self.repo, True)
        self.run = tempfile.mkdtemp(prefix="survival-run-"); self.addCleanup(shutil.rmtree, self.run, True)
        os.makedirs(os.path.join(self.run, "proof")); self.git("init", "-q")

    def log(self, lo, hi):
        return subprocess.run(["git", "-C", self.repo, "log", "--format=%H%x00%B", "%s..%s" % (lo, hi)],
                              capture_output=True, env=git_env()).stdout

    def test_a_revert_landed_before_a_later_fetch_removes_the_earlier_landing(self):
        self.commit("base"); a = self.commit("a")
        self.lb.record_landing(self.run, a, [SUB], ["/a.json"], "LANDED", "hub/main", a, "t1", self.log(a, a))
        self.git("revert", "--no-edit", a); b = self.commit("b")
        self.lb.refresh_histories(self.run, b, lambda earlier, sha: self.log(earlier, sha))
        self.lb.record_landing(self.run, b, [SUB], ["/b.json"], "LANDED", "hub/main", b, "t2", self.log(b, b))
        proof = os.path.join(self.run, "proof")
        rows = [json.loads(l) for l in open(os.path.join(proof, "landings.jsonl"), encoding="utf-8")]
        logs = {c: open(os.path.join(proof, "landing-history", c + ".log"), "rb").read() for c in (a, b)}
        start, end = "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"
        self.assertEqual(self.pa.surviving_landings(rows, logs, start, end)[0], 1, "A was reverted before B's fetch")

    def test_a_refresh_that_could_not_write_leaves_the_earlier_landing_unknown(self):
        """Finding B2 (2026-09-27): A's history could not be rewritten at B's fetch (its folder read only, so the rename
        into place is refused), so it still says nothing of the revert that landed in between. It must read UNKNOWN."""
        self.commit("base"); a = self.commit("a")
        self.lb.record_landing(self.run, a, [SUB], ["/a.json"], "LANDED", "hub/main", a, "t1", self.log(a, a))
        folder = os.path.join(self.run, "proof", "landing-history")
        os.chmod(folder, 0o555); self.addCleanup(os.chmod, folder, 0o755)
        self.git("revert", "--no-edit", a); b = self.commit("b")
        self.assertEqual(self.lb.refresh_histories(self.run, b, lambda earlier, sha: self.log(earlier, sha)), 0)
        os.chmod(folder, 0o755)
        self.lb.record_landing(self.run, b, [SUB], ["/b.json"], "LANDED", "hub/main", b, "t2", self.log(b, b))
        proof = os.path.join(self.run, "proof")
        rows = [json.loads(l) for l in open(os.path.join(proof, "landings.jsonl"), encoding="utf-8")]
        logs = {c: open(os.path.join(proof, "landing-history", c + ".log"), "rb").read() for c in (a, b)}
        n, unknown, detail = self.pa.surviving_landings(rows, logs, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
        self.assertNotIn(a, detail["surviving"], "a history the last fetch never reached cannot prove survival")
        self.assertEqual((n, detail["unknown"]), (1, [a]))

    def test_a_refresh_write_that_fails_part_way_leaves_the_earlier_history_whole(self):
        """X1 finding 1 (2026-09-27): the refresh rewrote A's history in place, so a write that failed part way (disk full
        after the newest entry) left a history naming B's fetch that had lost the revert of A below it, and A counted.
        Written to a temp file and renamed into place, a failed write leaves A's previous history byte for byte."""
        self.commit("base"); a = self.commit("a")
        self.lb.record_landing(self.run, a, [SUB], ["/a.json"], "LANDED", "hub/main", a, "t1", self.log(a, a))
        hist = os.path.join(self.run, "proof", "landing-history", a + ".log")
        with open(hist, "rb") as fh:
            before = fh.read()
        self.git("revert", "--no-edit", a); b = self.commit("b")
        newest = self.log(a, b).split(b"\n\n", 1)[0] + b"\n\n"
        def disk_full(fd):
            os.ftruncate(fd, len(newest))
            raise OSError(28, "fixture: disk full after the newest entry")
        with unittest.mock.patch.object(os, "fsync", disk_full):
            self.assertEqual(self.lb.refresh_histories(self.run, b, lambda earlier, sha: self.log(earlier, sha)), 0)
        with open(hist, "rb") as fh:
            self.assertEqual(fh.read(), before, "a failed write leaves the previous history whole")
        self.assertEqual(sorted(os.listdir(os.path.join(self.run, "proof"))), ["landing-history", "landings.jsonl"],
                         "no temp file is left behind")
        self.assertEqual(os.listdir(os.path.dirname(hist)), [a + ".log"], "no temp file is left behind")
        self.lb.record_landing(self.run, b, [SUB], ["/b.json"], "LANDED", "hub/main", b, "t2", self.log(b, b))
        proof = os.path.join(self.run, "proof")
        rows = [json.loads(l) for l in open(os.path.join(proof, "landings.jsonl"), encoding="utf-8")]
        logs = {c: open(os.path.join(proof, "landing-history", c + ".log"), "rb").read() for c in (a, b)}
        n, unknown, detail = self.pa.surviving_landings(rows, logs, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
        self.assertEqual((n, detail["unknown"]), (1, [a]), "a history the last fetch never reached cannot prove survival")

    def test_no_later_landing_keeps_the_history_as_written(self):
        self.commit("base"); a = self.commit("a")
        self.lb.record_landing(self.run, a, [SUB], ["/a.json"], "LANDED", "hub/main", a, "t1", self.log(a, a))
        self.lb.refresh_histories(self.run, a, lambda earlier, sha: self.log(earlier, sha))
        self.assertEqual(open(os.path.join(self.run, "proof", "landing-history", a + ".log"), "rb").read(), self.pa.history_end(a, a))


if __name__ == "__main__":
    unittest.main()
