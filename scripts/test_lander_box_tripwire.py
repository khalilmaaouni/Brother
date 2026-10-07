#!/usr/bin/env python3
"""THE LANDER RUNS NO TREE CODE OUTSIDE THE BOX (D13, review 17, 2026-10-03): one generic tripwire over every lander entry.

WHY GENERIC. Three review rounds each found a new file a build could write that the lander then ran unconfined (a package
__init__ on the D13 policy's import path, a package directory shadowing a protected module, a stdlib named module in the
script directory, the landing tree as the pass's code root). Each was closed by a longer protected list and the next
round found the next name. This test does not name the hole: it writes a marker payload into EVERY place a build could
stage importable or executable code, then runs every lander entry, and asserts that no marker appears outside the
sandbox's write area. A payload that runs inside the box tries the same write and is refused there.

THE PLACES (candidates()): every package __init__.py under plugin/ and scripts/ (plus scripts/ and scripts/loop/ made
packages); a shadow package <module>/__init__.py beside every protected module; a module named like the standard library
at the top of scripts/, scripts/loop/ and plugin/runtime/brother/core/; and every member of the frozen checks' import
closure a build may write, and every other Python file it may write (an entry may run one as a child process).

TWO COPIES, by what each entry claims. ALL holds every candidate, whether or not grade_build.dest would let a build write
it: the boxed entries (gate_order, record_gate, the pre-commit check, the failure ledger) and the pinned closer claim
containment whatever the tree holds. BUILD holds what dest lets a build write plus the stdlib shadows regardless: the push
gate cannot be boxed (its hermetic rerun boxes tests, and a sandbox cannot nest), so its claim is isolation (-I, the
script directory after the standard library) over files a build can actually write. The pass's code root selection is
the third claim: outside a proof the landing tree's sizing, diagnosis and sentry scripts never run unboxed.

A host whose sandbox cannot apply (inside the hermetic push check, for one) skips with NO-DATA by name: that is not a pass.
Run: python3 -B scripts/test_lander_box_tripwire.py   (and /usr/bin/python3, one at a time)"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
SHADOW_DIRS = ("scripts", "scripts/loop", "plugin/runtime/brother/core")
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = load("tw_box_grade_build", os.path.join(LOOP, "grade_build.py"))   # the loop's grader by path, never scripts/grade_build.py
GUARD = load("tw_box_guard", os.path.join(HERE, "test_grade_build_guard.py"))
PASSES = load("tw_box_passes", os.path.join(HERE, "test_loop_pass_code_root.py"))


def stdlib_names():
    names = set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names)
    if not hasattr(sys, "stdlib_module_names"):
        import sysconfig
        for folder in (sysconfig.get_path("stdlib"), os.path.join(sysconfig.get_path("platstdlib"), "lib-dynload")):
            names.update(n.split(".")[0] for n in (os.listdir(folder) if os.path.isdir(folder) else ()))
    return sorted(n for n in names if n and n.isidentifier())


def payload(marks, rel):
    """A marker write aimed outside every sandbox write area; refused (and survived) inside the box."""
    return "try:\n    open(%r, 'w').write(%r)\nexcept OSError:\n    pass\n" % (os.path.join(marks, rel.replace("/", "__")), rel)


def candidates(frozen):
    """({relative path: kind}, {the paths a build may write, plus every stdlib shadow}) for a frozen copy."""
    out = {}
    for top in ("plugin", "scripts"):
        for dirpath, dirs, files in os.walk(os.path.join(frozen, top)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            if "__init__.py" in files:
                out[os.path.relpath(os.path.join(dirpath, "__init__.py"), frozen)] = "init"
    out["scripts/__init__.py"] = out["scripts/loop/__init__.py"] = "init"
    for prot in G.PROTECTED_FILES:
        if prot.endswith(".py"):
            out[prot[:-3] + "/__init__.py"] = "shadow"
    names = stdlib_names()
    for d in SHADOW_DIRS:
        for n in names:
            rel = "%s/%s.py" % (d, n)
            if not os.path.exists(os.path.join(frozen, rel)) and not os.path.isdir(os.path.join(frozen, d, n)):
                out[rel] = "stdlib"
    probe = tempfile.mkdtemp(prefix="tw-dest-")
    try:
        for rel in GUARD.TheFrozenChecksImportClosureIsProtected.closure():
            if G.dest(probe, rel)[0] is not None:
                out[rel] = "closure"
        # every other Python file a build may write: an entry that runs one as a child process (not an import) is caught too
        for top in ("plugin", "scripts"):
            for dirpath, dirs, files in os.walk(os.path.join(frozen, top)):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    rel = os.path.relpath(os.path.join(dirpath, name), frozen)
                    if name.endswith(".py") and rel not in out and G.dest(probe, rel)[0] is not None:
                        out[rel] = "writable"
        build = {rel for rel, kind in out.items() if kind == "stdlib" or G.dest(probe, rel)[0] is not None}
    finally:
        shutil.rmtree(probe, True)
    return out, build


def poison(frozen, marks, rels):
    for rel in sorted(rels):
        p = os.path.join(frozen, rel)
        text = payload(marks, rel)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines(True)
            futures = [i for i, l in enumerate(lines) if l.startswith("from __future__")]
            cut = futures[-1] + 1 if futures else 0
            text = "".join(lines[:cut]) + text + "".join(lines[cut:])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)


DRIVER = r"""import io, inspect, sys
sys.path.insert(0, sys.argv[1]); import land_batch as LB
entry, frozen, arg = sys.argv[2], sys.argv[3], sys.argv[4]
log = io.StringIO()
kw = lambda fn: {"frozen": frozen} if "frozen" in inspect.signature(fn).parameters else {}
if entry == "gate_order": print(LB.gate_order(runs_root=arg, frozen=frozen)[1])
elif entry == "record_gate": print(LB.record_gate(arg, "landing.x", 0, 5, **kw(LB.record_gate)))
elif entry == "closing_pass": print(LB.closing_pass(["U1"], LB.sh, log, "hub", "main", frozen=frozen))
elif entry == "precommit": print("exit", LB.precommit_check(frozen, log)[1].returncode)
elif entry == "push_gate": print("exit", LB.push_gate(frozen, "hub", "main", log)[1].returncode)
sys.stderr.write(log.getvalue()[-2500:])
"""


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null"] + list(args), cwd=cwd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout


class LanderRunsNoTreeCodeUnboxed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        os.makedirs(scratch, exist_ok=True)
        cls.root = tempfile.mkdtemp(prefix="run-lander-box-", dir=scratch)
        cls.marks, cls.home, cls.tree, cls.runs = (os.path.join(cls.root, n) for n in ("marks", "home", "tree", "runs"))
        for d in (cls.marks, os.path.join(cls.home, ".claude", "evidence"), cls.runs):
            os.makedirs(d)
        files = subprocess.run(["git", "ls-files", "-z", "scripts", "plugin"], cwd=REPO, capture_output=True, text=True,
                               check=True).stdout.split("\0")
        cls.frozen = {}
        for mode in ("all", "build"):
            f = os.path.join(cls.root, "frozen-" + mode)
            for rel in filter(None, files):
                src = os.path.join(REPO, rel)
                if os.path.isfile(src) and not os.path.islink(src):
                    os.makedirs(os.path.dirname(os.path.join(f, rel)), exist_ok=True)
                    shutil.copy2(src, os.path.join(f, rel))
            cls.frozen[mode] = f
        cands, build = candidates(cls.frozen["all"])
        cls.counts = {k: sum(1 for v in cands.values() if v == k) for k in ("init", "shadow", "stdlib", "closure", "writable")}
        poison(cls.frozen["all"], cls.marks, cands)
        poison(cls.frozen["build"], cls.marks, build)
        # the landing tree: a plan whose one unit has a sub unit not yet landed (the closer refuses and the ledger
        # records it), a staged change (the pre-commit check reads it), and hub equal to HEAD (an empty push range)
        hub = os.path.join(cls.root, "hub.git")
        git(cls.root, "init", "-q", "--bare", "-b", "main", hub)
        git(cls.root, "init", "-q", "-b", "main", cls.tree)
        for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(cls.tree, "config", k, v)
        os.makedirs(os.path.join(cls.tree, "docs", "plan"))
        with open(os.path.join(cls.tree, PLAN), "w", encoding="utf-8") as fh:
            fh.write('{"units": [{"id": "U1", "sub_units": ["U1.a"], "state": "OPEN", "evidence": "", '
                     '"done_check": "python3 -B scripts/test_u1.py"}]}\n')
        git(cls.tree, "add", "-A")
        git(cls.tree, "commit", "-q", "-m", "base")
        git(cls.tree, "remote", "add", "hub", hub)
        git(cls.tree, "push", "-q", "-u", "hub", "main")
        with open(os.path.join(cls.tree, "note.txt"), "w", encoding="utf-8") as fh:
            fh.write("staged\n")
        git(cls.tree, "add", "note.txt")
        os.makedirs(os.path.join(cls.runs, "r0"))
        with open(os.path.join(cls.runs, "r0", "gates.tsv"), "w", encoding="utf-8") as fh:
            fh.write("landing.registration\t0\t50\nlanding.system_doc\t1\t50\nlanding.bundle\t0\t50\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        if G.sandbox_ready():
            self.skipTest("NO-DATA: %s" % G.sandbox_ready())
        for name in os.listdir(self.marks):
            os.remove(os.path.join(self.marks, name))

    def escaped(self):
        out = []
        for n in sorted(os.listdir(self.marks)):
            with open(os.path.join(self.marks, n), encoding="utf-8") as fh:
                out.append(fh.read())
        return out

    def entry(self, name, mode, arg=""):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON", "GIT_"))}
        env.update(HOME=self.home, TMPDIR=os.path.join(self.home, "tmp"))
        os.makedirs(env["TMPDIR"], exist_ok=True)
        r = subprocess.run([sys.executable, "-B", "-c", DRIVER, LOOP, name, self.frozen[mode], arg], cwd=self.tree, env=env,
                           capture_output=True, text=True, timeout=900)
        got = self.escaped()
        self.assertEqual(got, [], "%s ran tree code outside the box: %d marker(s) %s\n--- stdout\n%s\n--- stderr\n%s"
                         % (name, len(got), got[:8], r.stdout[-1500:], r.stderr[-2500:]))
        return r

    def test_the_fixture_stages_every_kind(self):
        # each kind of place is present, so a green below is not a green over an empty fixture
        self.assertGreater(self.counts["init"], 4, self.counts)
        self.assertGreater(self.counts["shadow"], 50, self.counts)
        self.assertGreater(self.counts["stdlib"], 300, self.counts)
        self.assertGreater(self.counts["writable"], 500, self.counts)

    def test_gate_order(self):
        r = self.entry("gate_order", "all", self.runs)
        self.assertIn("order", r.stdout)

    def test_record_gate(self):
        self.entry("record_gate", "all", tempfile.mkdtemp(dir=self.root))

    def test_closing_pass_closer_and_failure_ledger(self):
        r = self.entry("closing_pass", "all")
        self.assertIn("CLOSE-RED U1", r.stdout, r.stderr[-1500:])

    def test_precommit_check(self):
        self.entry("precommit", "all")

    def test_push_gate(self):
        self.entry("push_gate", "build")

    def test_loop_pass_code_root_selection(self):
        # outside a proof, no BROTHER_CODE_ROOT: the landing tree's sizing, diagnosis and sentry scripts each carry a payload
        t = PASSES.PassCodeRoot("test_a_proof_phase_without_a_code_root_runs_nothing")
        t.setUp()
        try:
            for site in ("adaptive_sizing.py", "diag_round.py", "worktree_sentry.py"):
                body = "#!/usr/bin/env python3\nimport sys\n" + payload(self.marks, "tree/scripts/" + site)
                if site == "worktree_sentry.py":
                    body += 'print("fp-1") if sys.argv[1:2] == ["snapshot"] else None\n'
                PASSES.w(os.path.join(t.tree, "scripts", site), body, 0o755)
            PASSES.git(t.tree, "add", "-A")
            PASSES.git(t.tree, "commit", "-q", "-m", "payloads")
            PASSES.git(t.tree, "push", "-q")
            r = t.run_pass(BROTHER_TUNING="on")
            got = self.escaped()
            self.assertEqual(got, [], "loop_pass.sh ran landing tree code unboxed: %s\n%s" % (got, (r.stdout + r.stderr)[-2000:]))
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()
