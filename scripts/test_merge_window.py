#!/usr/bin/env python3
"""scripts/merge_window.sh, run for real at its entry point over a throwaway repository (unit A2.e).

THE FIXTURE: a git repository holding scripts/close_unit.py's whole import closure (kernel_closure.py --roots, the
derivation the preflight uses), the sandbox profile it boxes done checks with, merge_window.sh itself and a two unit
plan; two landed branches add one done check each, the second with one skipped test. A plan generator named by
MW_PLAN_GEN writes a file, so the regeneration commit is observed too.

WHAT IT PROVES: the clean unit reads DONE on both interpreters and the skipping unit PARTIAL (A1: a skip is not a
pass), so the window ends FAIL and exits 1; with the skipping branch left out the window ends PASS and exits 0; the
refusals before any merge (dirty tree, unknown unit, unresolvable ref, a unit named twice) exit 2 and merge nothing; a
conflicting branch is aborted with the earlier merges named. INSIDE A SANDBOX (this file run boxed, where a nested
sandbox is refused) close_unit answers NO-DATA, and the window must then say NOT DONE, never DONE: that branch is
asserted instead, so no case is ever skipped. Standard library only; the fixture lives in a temp directory with its
own HOME and is removed afterwards."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
import grade_build as G  # noqa: E402  (sandbox_ready: whether a sandbox may run here, the one probe)
import export_public  # noqa: E402  (the one scratch root; imported AFTER grade_build because it puts scripts/ first on sys.path)

NESTED = bool(G.sandbox_ready())
PLAN = "docs/plan/MW-FIXTURE-WBS.json"
GOOD = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n\n" \
       "if __name__ == '__main__':\n    unittest.main()\n"
SKIPS = GOOD.replace("    def test_ok(self):", "    @unittest.skip('not yet')\n    def test_later(self):\n        pass\n\n"
                     "    def test_ok(self):")
UNITS = """{"units": [
 {"id": "MA", "state": "OPEN", "sub_units": ["MA.a"], "evidence": "MA.a landed", "done_check": "python3 -B scripts/test_mw_a.py"},
 {"id": "MB", "state": "OPEN", "sub_units": ["MB.a"], "evidence": "MB.a landed", "done_check": "python3 -B scripts/test_mw_b.py"},
 {"id": "MC", "state": "OPEN", "sub_units": ["MC.a"], "evidence": "MC.a landed", "done_check": "python3 -B scripts/test_mw_c.py"}
]}
"""
GEN = "with open('GENERATED.txt', 'w') as fh:\n    fh.write('regenerated\\n')\n"


def closure():
    r = subprocess.run([sys.executable, "-I", "-B", os.path.join(HERE, "kernel_closure.py"), "--roots",
                        "scripts/close_unit.py"], cwd=REPO, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("close_unit's closure could not be derived: " + r.stdout + r.stderr)
    return r.stdout.split() + ["scripts/loop/sandbox.sb", "scripts/merge_window.sh"]


class Fixture(unittest.TestCase):
    """The throwaway repository both suites run the window over; subclasses add their own branches."""

    def setUp(self):
        scratch = export_public.scratch_root()   # the one scratch root, never the system temp folder
        os.makedirs(scratch, exist_ok=True)
        self.box = tempfile.mkdtemp(prefix="merge-window-", dir=scratch)
        self.addCleanup(shutil.rmtree, self.box, True)
        self.root, home = os.path.join(self.box, "repo"), os.path.join(self.box, "home")
        os.makedirs(os.path.join(home, "tmp"))
        for rel in closure():
            if os.path.isfile(os.path.join(REPO, rel)):
                os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
                shutil.copy2(os.path.join(REPO, rel), os.path.join(self.root, rel))
        self.write({PLAN: UNITS, "gen_fixture.py": GEN})
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER", "MW_"))}
        self.env.update(HOME=home, TMPDIR=os.path.join(home, "tmp"), BROTHER_WBS=PLAN, MW_PLAN_GEN="gen_fixture.py",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                        GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.seed()
        self.commit("base")
        self.branches()
        self.git("checkout", "-q", "main")
        self.base = self.git("rev-parse", "HEAD").strip()

    def seed(self):
        """Extra files in the base commit."""

    def branches(self):
        """Branches cut from main."""

    def write(self, files):
        for rel, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)) or self.root, exist_ok=True)
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write(body)

    def git(self, *argv):
        return subprocess.run(["git"] + list(argv), cwd=self.root, env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def commit(self, why):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", why)

    def window(self, *pairs):
        r = subprocess.run(["sh", "scripts/merge_window.sh"] + list(pairs), cwd=self.root, env=self.env,
                           capture_output=True, text=True, timeout=900)
        return r.returncode, r.stdout + r.stderr

    def line(self, out, unit):
        got = [l for l in out.splitlines() if l.startswith(unit + " merged ")]
        self.assertEqual(len(got), 1, out)
        return got[0]

class MergeWindow(Fixture):

    def branches(self):
        for branch, name, body in (("a", "scripts/test_mw_a.py", GOOD), ("b", "scripts/test_mw_b.py", SKIPS),
                                   ("clash", "scripts/test_mw_a.py", GOOD.replace("True", "1")),
                                   # verdict spoofs: a closer line that merely CONTAINS " DONE: ". sp1 fails and its last
                                   # result line says it; sp2 exits 0 and its last result line is a NO-DATA that says it
                                   ("sp1", "scripts/test_mw_a.py", "print('FAIL: x DONE: y')\nraise SystemExit(1)\n"),
                                   ("sp2", "scripts/test_mw_a.py", "print('NO-DATA: x DONE: y')\n")):
            self.git("checkout", "-q", "-b", branch, "main")
            self.write({name: body})
            self.commit(branch)
            self.git("checkout", "-q", "main")

    def test_a_clean_unit_closes_and_a_skipping_one_is_partial(self):
        code, out = self.window("MA=a", "MB=b")
        self.assertEqual(code, 1, out)
        a, b = self.line(out, "MA"), self.line(out, "MB")
        self.assertRegex(a, r"^MA merged [0-9a-f]{12} py3=0 py3\.9=0 ")
        self.assertIn("REGENERATE python3 gen_fixture.py", out)
        self.assertIn("regenerated after the merges", self.git("log", "--format=%s"))
        if NESTED:   # close_unit cannot box inside a box: NO-DATA, and the window never says DONE
            self.assertRegex(a, r" NOT DONE: .*NO-DATA")
            self.assertRegex(b, r" NOT DONE: .*NO-DATA")
        else:
            self.assertTrue(a.endswith(" DONE"), a)
            self.assertRegex(b, r" NOT DONE: .*PARTIAL: 1 test\(s\) skipped")
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("FAIL: merge window"), out)

    def test_a_line_that_only_contains_done_is_not_the_verdict(self):
        for branch in ("sp1", "sp2"):
            self.git("reset", "-q", "--hard", self.base)
            code, out = self.window("MA=" + branch)
            self.assertEqual(code, 1, out)
            line = self.line(out, "MA")
            self.assertRegex(line, r" NOT DONE: ", out)
            if not NESTED:   # the spoofing text really reached the closer's line, so the case is not vacuous
                self.assertIn("x DONE: y", line, out)
            self.assertTrue(out.rstrip().splitlines()[-1].startswith("FAIL: merge window"), out)
            self.assertNotIn('"state": "DONE"', self.git("show", "HEAD:" + PLAN), branch)

    def test_a_needs_input_closer_line_reads_not_done(self):
        stub = lambda text: "print(%r)\n" % text
        # the closer asks a person: the verdict must be NOT DONE, with and without NEEDS_INPUT in the grep alternation
        # that STAB1.f adds (simulated by patching this fixture's copy of the script by one token)
        script = os.path.join(self.root, "scripts", "merge_window.sh")
        with open(script, encoding="utf-8") as fh:
            original = fh.read()
        needle = "(DONE|NOT CLOSED|not )"
        with_ask = "(DONE|NOT CLOSED|NEEDS_INPUT|not )"
        # once STAB1.f has landed the script already carries NEEDS_INPUT; exactly one of the two forms must be present,
        # and both forms are run below either way
        self.assertEqual(original.count(needle) + original.count(with_ask), 1)
        unpatched = original.replace(with_ask, needle)
        for line in ("MA    NEEDS_INPUT: a person must decide", "MA    NOT CLOSED: NEEDS_INPUT: ask DONE: y"):
            for patched in (False, True):
                self.git("reset", "-q", "--hard", self.base)
                with open(script, "w", encoding="utf-8") as fh:
                    fh.write(unpatched.replace(needle, with_ask) if patched else unpatched)
                self.write({"scripts/close_unit.py": stub(line)})
                self.commit("stub closer")
                code, out = self.window("MA=a")
                self.assertEqual(code, 1, out)
                got = self.line(out, "MA")
                self.assertRegex(got, r" NOT DONE: ", (line, patched, got))
                self.assertNotRegex(got, r" DONE$", got)
                self.assertTrue(out.rstrip().splitlines()[-1].startswith("FAIL: merge window"), out)

    def test_the_pass_line_needs_every_unit_done(self):
        code, out = self.window("MA=a")
        last = out.rstrip().splitlines()[-1]
        if NESTED:
            self.assertEqual(code, 1, out)
            self.assertTrue(last.startswith("FAIL: "), out)
        else:
            self.assertEqual(code, 0, out)
            self.assertTrue(re.match(r"PASS: merge window, MA DONE at [0-9a-f]{12}; start the gate", last), out)
            self.assertIn('"state": "DONE"', self.git("show", "HEAD:" + PLAN))

    def test_a_red_or_missing_second_interpreter_stops_the_pass_line(self):
        """Orthogonal to the closer: close_unit runs python3 and says DONE; only the 3.9 column is red or absent."""
        for py39, want in (("/usr/bin/false", "py3.9=1"), ("/no/such/python", "py3.9=NO-DATA(/no/such/python missing)")):
            self.git("reset", "-q", "--hard", self.base)   # the fixture's own branch, back to its base commit
            self.env["MW_PY39"] = py39
            code, out = self.window("MA=a")
            self.assertEqual(code, 1, out)
            self.assertIn(want, self.line(out, "MA"))
            self.assertTrue(out.rstrip().splitlines()[-1].startswith("FAIL: merge window"), out)

    def test_refusals_before_any_merge_merge_nothing(self):
        head = self.git("rev-parse", "HEAD")
        for pairs, why in ((["MZ=a"], "not a unit of"), (["MA=nowhere"], "cannot resolve"),
                           (["MA=a", "MA=b"], "named twice"), (["MA"], "is not UNIT=REF"), ([], "at least one")):
            code, out = self.window(*pairs)
            self.assertEqual((code, why in out), (2, True), (pairs, out))
        self.write({"stray.txt": "x"})
        code, out = self.window("MA=a")
        self.assertEqual((code, "not clean" in out), (2, True), out)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_a_red_generator_stops_the_window_before_any_close(self):
        self.write({"gen_fixture.py": "raise SystemExit(3)\n"})
        self.commit("a red generator")
        code, out = self.window("MA=a")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL: the generator 'python3 gen_fixture.py' exited 3", out)
        self.assertNotIn("MA merged", out)
        self.assertIn('"state": "OPEN"', self.git("show", "HEAD:" + PLAN))

    def test_a_conflict_aborts_and_names_the_merges_before_it(self):
        code, out = self.window("MA=a", "MB=clash")
        self.assertEqual(code, 2, out)
        self.assertIn("MB MERGE-CONFLICT clash: aborted; merged before it: MA", out)
        self.assertEqual(self.git("status", "--porcelain"), "")



# Fixture generators: small stand-ins with the real ones' contract (write the files; --check exits 1 on drift). Each
# output is ONE line, so two branches that both regenerate it conflict textually, which is the case under test.
# FIXTURE_GEN_FAIL=<bundle|system> makes that generator exit 5 and FIXTURE_CHECK_FAIL=<bundle|system> makes its --check
# red, FIXTURE_VERIFY_FAIL=1 makes the product's verify-install.sh red and FIXTURE_CHECK_LEAK=1 makes the system --check
# modify a tracked file, so one guard is refused at a time. COUNT is the one output a textual merge gets WRONG without a
# conflict: both branches change it to the same line, the merged docs need a different one.
GEN_BUNDLE = """import hashlib, os, sys
body = " ".join(n + ":" + hashlib.sha256(open(os.path.join("scripts", "loop", n), "rb").read()).hexdigest()[:8]
                for n in sorted(os.listdir(os.path.join("scripts", "loop"))) if n.startswith("zz_")) + "\\n"
outs = {os.path.join("bundle", "runtime", "RUNTIME-MANIFEST.json"): body,
        os.path.join("bundle", "cursor-hooks", "hooks.json"): body,
        os.path.join("bundle", "codex-skills", "INDEX"): " ".join(sorted(os.listdir(os.path.join("bundle", "skills")))) + "\\n",
        os.path.join("bundle", "runtime", "COUNT"): "n=%d\\n" % len(os.listdir("docs"))}
if "--check" in sys.argv:
    sys.exit(0 if os.environ.get("FIXTURE_CHECK_FAIL") != "bundle" and all(open(p).read() == b for p, b in outs.items()) else 1)
if os.environ.get("FIXTURE_CONCURRENT") == "1":   # another actor writes while the window runs
    open("notes.txt", "w").write("mine\\n")
    open(os.path.join("docs", "base.txt"), "a").write("concurrent\\n")
if os.environ.get("FIXTURE_EXTRA") == "1":   # a generator creates a new generated file, then something fails
    open(os.path.join("bundle", "runtime", "EXTRA"), "w").write("extra\\n")
if os.environ.get("FIXTURE_RO") == "1":   # a new generated file in a directory nothing can delete from
    os.makedirs(os.path.join("bundle", "runtime", "ro"))
    open(os.path.join("bundle", "runtime", "ro", "f"), "w").write("f\\n")
    os.chmod(os.path.join("bundle", "runtime", "ro"), 0o555)
if os.environ.get("FIXTURE_MKTMP") == "1":   # the temp folder the window could not use at first appears mid-run
    os.makedirs(os.environ["TMPDIR"], exist_ok=True)
if os.environ.get("FIXTURE_MOVE_HEAD"):   # another actor moves the branch while the window runs
    import subprocess
    subprocess.run(["git", "update-ref", "HEAD", os.environ["FIXTURE_MOVE_HEAD"]], check=True)
if os.environ.get("FIXTURE_GEN_FAIL") == "bundle":
    sys.exit(5)
for path, text in outs.items():
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)
"""
GEN_SYSTEM = """import os, sys
body = " ".join(sorted(os.listdir("docs"))) + "\\n"
if "--check" in sys.argv:
    if os.environ.get("FIXTURE_CHECK_LEAK") == "1":
        open(os.path.join("docs", "base.txt"), "a").write("leaked\\n")
    sys.exit(0 if os.environ.get("FIXTURE_CHECK_FAIL") != "system" and open("SYSTEM.md").read() == body else 1)
if os.environ.get("FIXTURE_GEN_FAIL") == "system":
    sys.exit(5)
open("SYSTEM.md", "w").write(body)
"""
CHECKSUMS = """#!/bin/sh
# run from the product root: scripts/checksums.sh CHECKSUMS.sha256 writes it, no argument only prints
out=$(for f in $(git ls-files . | grep -v '^CHECKSUMS'); do printf '%s  %s\\n' "$(git hash-object "$f")" "$f"; done)
[ -n "${1:-}" ] || { printf '%s\\n' "$out"; exit 0; }
printf '%s\\n' "$out" > "$1"
"""
VERIFY = """#!/bin/sh
# stand-in for the product's verify-install.sh: no argument, from the product root; red when the manifest is stale
[ "${FIXTURE_VERIFY_FAIL:-}" != 1 ] || exit 1
[ "$(sh scripts/checksums.sh)" = "$(cat CHECKSUMS.sha256)" ]
"""


class GeneratedFilesRegenerate(Fixture):
    """UNIT MERGE1.b: a merge conflicting ONLY on generated files is regenerated, checked and committed; anything else
    still stops. Each case isolates one condition.

    NAMED LIMIT (review F3): which side seeds the regeneration (OUR side, the baseline) is not observable here. The real
    bundle_runtime.py carries source_revision and source_describe forward from the baseline, but --check ignores both
    stamp fields by design and the fixture generator stamps nothing, so no check can tell the two sides apart."""

    def seed(self):
        self.write({"scripts/bundle_runtime.py": GEN_BUNDLE, "scripts/system_doc.py": GEN_SYSTEM,
                    "products/p1/scripts/checksums.sh": CHECKSUMS, "products/p1/scripts/verify-install.sh": VERIFY,
                    "products/p1/a.txt": "a\n", "products/p1/b.txt": "b\n",
                    "products/a/b/scripts/checksums.sh": CHECKSUMS, "products/a/b/CHECKSUMS.sha256": "base\n",
                    "bundle/skills/base/SKILL.md": "base\n", "bundle/runtime/ab": "ab\n", "docs/base.txt": "base\n", "bundle/runtime/x": "",
                    "SYSTEM.md": ""})
        self.regenerate()

    def regenerate(self):
        for gen in ("scripts/bundle_runtime.py", "scripts/system_doc.py"):
            subprocess.run([sys.executable, gen], cwd=self.root, env=self.env, check=True)
        self.git("add", "-A")
        subprocess.run(["sh", "scripts/checksums.sh", "CHECKSUMS.sha256"], cwd=os.path.join(self.root, "products/p1"),
                       env=self.env, check=True, capture_output=True)

    def branch(self, name, files, test):
        self.git("checkout", "-q", "-b", name, "main")
        self.write(dict(files, **{test: GOOD}))
        self.regenerate()
        self.commit(name)
        self.git("checkout", "-q", "main")

    def branches(self):
        # s1, s2: SYSTEM.md and COUNT conflict (each adds one docs file); COUNT merges to a WRONG value without a conflict
        # of its own only when both sides agree, which the window must repair
        self.branch("s1", {"docs/s1.txt": "1\n"}, "scripts/test_mw_a.py")
        self.branch("s2", {"docs/s2.txt": "2\n"}, "scripts/test_mw_b.py")
        # c: a third ordinary unit, one more docs file
        self.branch("c", {"docs/c.txt": "3\n"}, "scripts/test_mw_c.py")
        # g1, g2: the manifest, cursor hooks, SYSTEM.md and the product checksums are touched by both
        self.branch("g1", {"docs/g1.txt": "1\n", "scripts/loop/zz_1.py": "x = 1\n", "products/p1/a.txt": "a1\n"},
                    "scripts/test_mw_a.py")
        self.branch("g2", {"docs/g2.txt": "2\n", "scripts/loop/zz_2.py": "x = 2\n", "products/p1/b.txt": "b2\n"},
                    "scripts/test_mw_b.py")
        # m2 against g1: a SOURCE conflict (docs/g1.txt added with different bytes) beside the generated one
        self.branch("m2", {"docs/g1.txt": "other\n", "scripts/loop/zz_2.py": "x = 2\n"}, "scripts/test_mw_b.py")
        # k1, k2: only bundle/codex-skills/INDEX conflicts (each adds a skill, the mirror lists the names)
        self.branch("k1", {"bundle/skills/k1/SKILL.md": "k1\n"}, "scripts/test_mw_a.py")
        self.branch("k2", {"bundle/skills/k2/SKILL.md": "k2\n"}, "scripts/test_mw_b.py")
        # sk1, sk2: a HAND WRITTEN bundle file (the source of the codex mirror) edited both ways
        self.branch("sk1", {"bundle/skills/base/SKILL.md": "one\n"}, "scripts/test_mw_a.py")
        self.branch("sk2", {"bundle/skills/base/SKILL.md": "two\n"}, "scripts/test_mw_b.py")
        # gl1, gl2: a file literally named SYSTEM.m[d], which a glob or a pathspec would read as SYSTEM.md
        self.branch("gl1", {"SYSTEM.m[d]": "one\n"}, "scripts/test_mw_a.py")
        self.branch("gl2", {"SYSTEM.m[d]": "two\n"}, "scripts/test_mw_b.py")
        # q1, q2: a generated path whose NAME is also a glob that matches another file (bundle/runtime/ab)
        self.branch("q1", {"bundle/runtime/a[b]": "one\n"}, "scripts/test_mw_a.py")
        self.branch("q2", {"bundle/runtime/a[b]": "two\n"}, "scripts/test_mw_b.py")
        # nl1, nl2: a conflicted file whose NAME holds a newline and reads as two generated lines
        self.branch("nl1", {"SYSTEM.md\nSYSTEM.md": "one\n"}, "scripts/test_mw_a.py")
        self.branch("nl2", {"SYSTEM.md\nSYSTEM.md": "two\n"}, "scripts/test_mw_b.py")
        # h1, h2: hostile names, a leading dash and a space, both conflicting
        self.branch("h1", {"-dash.txt": "one\n", "sp ace.txt": "one\n"}, "scripts/test_mw_a.py")
        self.branch("h2", {"-dash.txt": "two\n", "sp ace.txt": "two\n"}, "scripts/test_mw_b.py")
        # n1, n2: a nested products/a/b/CHECKSUMS.sha256 is hand written data, not a product manifest
        self.branch("n1", {"products/a/b/CHECKSUMS.sha256": "one\n"}, "scripts/test_mw_a.py")
        self.branch("n2", {"products/a/b/CHECKSUMS.sha256": "two\n"}, "scripts/test_mw_b.py")

    def assert_regenerated(self, out, conflicts=1):
        self.assertEqual(out.count("GENERATED-CONFLICT"), conflicts, out)
        self.assertNotIn("MERGE-CONFLICT", out)
        self.assertEqual(self.git("status", "--porcelain"), "")
        for gen in ("scripts/bundle_runtime.py", "scripts/system_doc.py"):   # the generators' own --check, after
            self.assertEqual(subprocess.run([sys.executable, gen, "--check"], cwd=self.root, env=self.env).returncode, 0, gen)
        for rel in ("SYSTEM.md", "bundle/runtime/RUNTIME-MANIFEST.json", "products/p1/CHECKSUMS.sha256"):
            self.assertNotRegex(self.git("show", "HEAD:" + rel), r"(?m)^(<{7}|={7}|>{7})", rel)

    def assert_commit_is_checked(self, subject):
        """The checks pass on the MERGE COMMIT the window made (not only on the later HEAD that step 2 may repair)."""
        sha = [l.split("\t")[0] for l in self.git("log", "--format=%H%x09%s").splitlines() if l.endswith(subject)]
        self.assertEqual(len(sha), 1, subject)
        wt = os.path.join(self.box, "wt-" + sha[0][:8])
        self.git("worktree", "add", "-q", "--detach", wt, sha[0])
        for gen in ("scripts/bundle_runtime.py", "scripts/system_doc.py"):
            self.assertEqual(subprocess.run([sys.executable, gen, "--check"], cwd=wt, env=self.env).returncode, 0,
                             gen + " red on the merge commit " + subject)
        self.assertEqual(subprocess.run(["sh", "scripts/verify-install.sh"], cwd=os.path.join(wt, "products/p1"),
                                        env=self.env).returncode, 0, "verify-install red on the merge commit")

    def refused(self, *pairs, **env):
        """The window must stop with exit 2 and MERGE-CONFLICT, leaving a clean tree and no merge in progress."""
        self.env.update(env)
        code, out = self.window(*pairs)
        for key in env:
            self.env.pop(key)
        self.assertEqual(code, 2, out)
        self.assertIn("MERGE-CONFLICT", out)
        self.assertEqual(self.git("status", "--porcelain"), "", out)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".git", "MERGE_HEAD")), out)
        return out

    def test_one_generated_conflict_is_regenerated(self):
        code, out = self.window("MA=s1", "MB=s2")
        self.assert_regenerated(out)
        self.assertEqual(code, 1 if NESTED else 0, out)
        listing = self.git("show", "HEAD:SYSTEM.md").split()
        self.assertTrue({"s1.txt", "s2.txt"} <= set(listing), listing)

    def test_the_merge_commit_is_the_checked_tree(self):
        # F1: COUNT merges without conflict to n=3 and is only right at n=4 once regenerated; the generator output outside
        # the conflicted set must be in the merge commit itself
        self.window("MA=s1", "MB=s2")
        self.assertEqual(self.git("show", "HEAD:bundle/runtime/COUNT"), "n=4\n")  # docs/ holds plan, base, s1, s2
        self.assert_commit_is_checked("merge window: MB from s2")

    def test_a_third_unit_after_a_generated_conflict_merges_and_leaves_a_clean_tree(self):
        code, out = self.window("MA=s1", "MB=s2", "MC=c")
        self.assert_regenerated(out, conflicts=2)
        self.assertEqual(code, 1 if NESTED else 0, out)
        for unit in ("MA", "MB", "MC"):
            self.line(out, unit)
        self.assert_commit_is_checked("merge window: MC from c")

    def test_several_generated_conflicts_are_regenerated(self):
        code, out = self.window("MA=g1", "MB=g2")
        self.assert_regenerated(out)
        for rel in ("bundle/runtime/RUNTIME-MANIFEST.json", "bundle/cursor-hooks/hooks.json"):
            manifest = self.git("show", "HEAD:" + rel)
            self.assertTrue("zz_1.py" in manifest and "zz_2.py" in manifest, rel + ": " + manifest)
        sums = self.git("show", "HEAD:products/p1/CHECKSUMS.sha256")
        self.assertEqual(sums.count("\n"), 4, sums)   # a.txt, b.txt and the two scripts, from BOTH branches
        self.assertIn(self.git("hash-object", "products/p1/a.txt").strip() + "  a.txt", sums)
        self.assertIn(self.git("hash-object", "products/p1/b.txt").strip() + "  b.txt", sums)

    def test_a_codex_skills_mirror_conflict_is_regenerated(self):
        code, out = self.window("MA=k1", "MB=k2")
        self.assert_regenerated(out)
        self.assertEqual(self.git("show", "HEAD:bundle/codex-skills/INDEX"), "base k1 k2\n")

    def test_a_generated_path_named_like_a_glob_is_reset_literally(self):
        code, out = self.window("MA=q1", "MB=q2")
        self.assert_regenerated(out)

    def test_a_mixed_conflict_stops(self):
        out = self.refused("MA=g1", "MB=m2")
        self.assertIn("is not a generated file", out)
        self.assertIn("merged before it: MA", out)

    def test_a_hand_written_bundle_file_conflict_stops(self):
        out = self.refused("MA=sk1", "MB=sk2")
        self.assertIn("bundle/skills/base/SKILL.md is not a generated file", out)

    def test_a_nested_checksums_path_is_not_a_product_manifest(self):
        out = self.refused("MA=n1", "MB=n2")
        self.assertIn("products/a/b/CHECKSUMS.sha256 is not a generated file", out)

    def test_a_path_that_looks_like_a_glob_is_data_not_a_pattern(self):
        out = self.refused("MA=gl1", "MB=gl2")
        self.assertIn("SYSTEM.m[d] is not a generated file", out)

    def test_hostile_names_are_refused_whole(self):
        out = self.refused("MA=h1", "MB=h2")
        self.assertIn("is not a generated file", out)

    def test_a_path_holding_a_newline_stops(self):
        out = self.refused("MA=nl1", "MB=nl2")
        self.assertIn("holds a newline", out)

    def test_a_generator_that_fails_stops(self):
        for which in ("bundle", "system"):
            self.git("reset", "-q", "--hard", self.base)
            out = self.refused("MA=g1", "MB=g2", FIXTURE_GEN_FAIL=which)
            self.assertIn("exited 5", out)

    def test_a_check_that_fails_after_regeneration_stops(self):
        for which, why in (("system", "system_doc.py --check failed"), ("bundle", "bundle_runtime.py --check failed")):
            self.git("reset", "-q", "--hard", self.base)
            self.assertIn(why, self.refused("MA=g1", "MB=g2", FIXTURE_CHECK_FAIL=which))

    def test_a_product_check_that_fails_stops(self):
        self.assertIn("verify-install.sh failed after regeneration", self.refused("MA=g1", "MB=g2", FIXTURE_VERIFY_FAIL="1"))

    def test_a_product_without_its_check_stops(self):
        self.git("rm", "-q", "products/p1/scripts/verify-install.sh")
        self.git("commit", "-q", "-m", "no product check")
        self.assertIn("has no scripts/verify-install.sh", self.refused("MA=g1", "MB=g2"))

    def test_files_the_checks_leave_outside_the_merge_commit_stop(self):
        # the leak lands in a tracked, NOT generated file: the window cannot tell whose it is, so it stops and keeps it
        self.env["FIXTURE_CHECK_LEAK"] = "1"
        code, out = self.window("MA=g1", "MB=g2")
        self.env.pop("FIXTURE_CHECK_LEAK")
        self.assertEqual(code, 2, out)
        self.assertIn("the checks left files outside the merge commit", out)
        self.assertIn("MERGE-STOPPED", out)
        self.assertEqual(self.read("docs/base.txt"), "base\nleaked\n")

    # ---- a refusal never discards work the window did not create (review rule 1c) ----

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return fh.read()

    def lock_dir(self):
        return os.path.join(self.root, ".git", "merge-window.lock")

    def test_a_pre_existing_edit_is_refused_and_survives_byte_for_byte(self):
        self.write({"docs/base.txt": "base\nmine\n", "stray.txt": "stray\n"})
        code, out = self.window("MA=g1", "MB=g2")
        self.assertEqual((code, "not clean" in out), (2, True), out)
        self.assertEqual((self.read("docs/base.txt"), self.read("stray.txt")), ("base\nmine\n", "stray\n"))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base)
        self.assertFalse(os.path.exists(self.lock_dir()), "the lock outlived a refused start")

    def test_a_concurrent_edit_during_a_refused_merge_survives(self):
        code, out = self.run_with(FIXTURE_CONCURRENT="1", FIXTURE_GEN_FAIL="system")
        self.assertEqual(code, 2, out)
        self.assertIn("MERGE-STOPPED", out)
        self.assertIn("notes.txt", out)
        self.assertEqual((self.read("notes.txt"), self.read("docs/base.txt")), ("mine\n", "base\nconcurrent\n"))
        self.assertTrue(os.path.exists(os.path.join(self.root, ".git", "MERGE_HEAD")), "the merge must be left for the owner")

    def test_a_branch_moved_during_a_refused_merge_is_not_reset(self):
        moved = self.git("rev-parse", "c").strip()
        code, out = self.run_with(FIXTURE_MOVE_HEAD=moved, FIXTURE_GEN_FAIL="system")
        self.assertEqual(code, 2, out)
        self.assertIn("MERGE-STOPPED", out)
        self.assertIn("someone else moved it", out)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), moved)

    def test_a_refusal_restores_exactly_the_pre_merge_head(self):
        out = self.refused("MA=g1", "MB=g2", FIXTURE_GEN_FAIL="system")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "merge window: MA from g1", out)
        self.assertEqual(self.git("rev-parse", "HEAD^1").strip(), self.base)

    def test_a_new_generated_file_left_by_a_failed_generator_is_removed(self):
        self.refused("MA=g1", "MB=g2", FIXTURE_EXTRA="1", FIXTURE_GEN_FAIL="system")
        self.assertFalse(os.path.exists(os.path.join(self.root, "bundle", "runtime", "EXTRA")))

    def test_an_undo_that_cannot_finish_says_so(self):
        ro = os.path.join(self.root, "bundle", "runtime", "ro")
        self.addCleanup(lambda: os.path.isdir(ro) and os.chmod(ro, 0o755))   # runs before the box is removed
        code, out = self.run_with(FIXTURE_RO="1", FIXTURE_GEN_FAIL="system")
        self.assertEqual(code, 2, out)
        self.assertIn("MERGE-STOPPED", out)
        self.assertIn("the undo left the checkout different", out)

    def test_an_unreadable_checkout_is_not_undone(self):
        # mktemp cannot make the fingerprint file, so the window cannot tell its own work from anyone's: it stops
        code, out = self.run_with(TMPDIR=os.path.join(self.box, "no", "such", "dir"), FIXTURE_GEN_FAIL="system")
        self.assertEqual(code, 2, out)
        self.assertIn("MERGE-STOPPED", out)
        self.assertIn("could not be fingerprinted", out)
        self.assertTrue(os.path.exists(os.path.join(self.root, ".git", "MERGE_HEAD")))

    def test_a_fingerprint_that_failed_at_the_start_is_not_trusted_later(self):
        # the first fingerprint fails (no temp folder), a generator then creates the folder so the second would succeed:
        # comparing against a missing first fingerprint would call everything foreign-free and undo it
        code, out = self.run_with(TMPDIR=os.path.join(self.box, "late", "tmp"), FIXTURE_MKTMP="1", FIXTURE_CONCURRENT="1",
                                  FIXTURE_GEN_FAIL="system")
        self.assertEqual(code, 2, out)
        self.assertIn("MERGE-STOPPED", out)
        self.assertIn("could not be fingerprinted", out)
        self.assertEqual(self.read("notes.txt"), "mine\n")

    def run_with(self, **env):
        self.env.update(env)
        try:
            return self.window("MA=g1", "MB=g2")
        finally:
            for key in env:
                self.env.pop(key)

    def test_a_held_lock_refuses_and_touches_nothing(self):
        os.makedirs(self.lock_dir())
        with open(os.path.join(self.lock_dir(), "pid"), "w") as fh:
            fh.write(str(os.getpid()) + "\n")   # this test process is alive
        code, out = self.run_with(MW_LOCK_WAIT="1")
        self.assertEqual(code, 2, out)
        self.assertIn("another merge window holds", out)
        self.assertEqual((self.git("status", "--porcelain"), self.git("rev-parse", "HEAD").strip()), ("", self.base))
        self.assertTrue(os.path.exists(os.path.join(self.lock_dir(), "pid")), "a refused window must not release the holder's lock")

    def test_a_dead_holders_lock_is_reclaimed_and_released(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        os.makedirs(self.lock_dir())
        with open(os.path.join(self.lock_dir(), "pid"), "w") as fh:
            fh.write(str(dead.pid) + "\n")
        code, out = self.window("MA=s1", "MB=s2")
        self.assert_regenerated(out)
        self.assertFalse(os.path.exists(self.lock_dir()), "the lock outlived a finished window")

    def test_a_missing_generator_stops(self):
        self.git("rm", "-q", "scripts/system_doc.py")
        self.git("commit", "-q", "-m", "no system generator")
        self.assertIn("no generator here owns SYSTEM.md", self.refused("MA=s1", "MB=s2"))

    def test_a_dirty_tree_is_still_refused_before_any_merge(self):
        self.write({"stray.txt": "x"})
        code, out = self.window("MA=g1", "MB=g2")
        self.assertEqual((code, "not clean" in out), (2, True), out)


if __name__ == "__main__":
    unittest.main()
