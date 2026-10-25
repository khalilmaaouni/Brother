"""product_manifests: the landing gate that keeps every product's CHECKSUMS.sha256 current (1.1.8 H1).

Each fixture is a throwaway git repository holding one product built from the REAL products/brothermode checksums.sh
and verify-install.sh, committed with its manifest, and changed in exactly one way, so each case is refused by one
guard only. The last class pins the GATES entry land_batch runs, so the gate cannot be dropped from the landing.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "product_manifests.py")
REAL = os.path.join(REPO, "products", "brothermode", "scripts")


def run(args, cwd):
    return subprocess.run([sys.executable, "-B", SCRIPT] + args, cwd=cwd, capture_output=True, text=True, timeout=300)


def git(args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git"] + args, cwd=cwd, env=env, check=True, capture_output=True, timeout=60)


class Fixture(unittest.TestCase):
    """One repo, one shipped product (products/brothermode), committed with a current manifest."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="product-manifests-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.prod = os.path.join(self.root, "products", "brothermode")
        os.makedirs(os.path.join(self.prod, "scripts"))
        os.makedirs(os.path.join(self.prod, "tools"))
        for name in ("checksums.sh", "verify-install.sh"):
            shutil.copy2(os.path.join(REAL, name), os.path.join(self.prod, "scripts", name))
        self.write("tools/a.py", "A = 1\n")
        self.write("README.md", "demo\n")
        git(["init", "-q"], self.root)
        git(["add", "-A"], self.root)
        r = subprocess.run(["sh", "scripts/checksums.sh", "CHECKSUMS.sha256"], cwd=self.prod, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "base"], self.root)

    def write(self, rel, text):
        with open(os.path.join(self.prod, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def manifest(self):
        with open(os.path.join(self.prod, "CHECKSUMS.sha256"), encoding="utf-8") as fh:
            return fh.read()


class CleanTree(Fixture):
    def test_a_current_manifest_passes(self):
        r = run(["--check"], self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS products/brothermode", r.stdout)


class NewUntrackedFile(Fixture):
    def test_a_new_file_fails_then_regenerating_names_it(self):
        self.write("tools/new_tool.py", "B = 2\n")          # untracked, as a build's new file is in the landing copy
        self.assertEqual(run(["--check"], self.root).returncode, 1)
        r = run([], self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("tools/new_tool.py", self.manifest())  # walk mode: git mode lists tracked files only
        self.assertEqual(run(["--check"], self.root).returncode, 0)


class ChangedFile(Fixture):
    def test_a_changed_file_fails_then_regenerates(self):
        self.write("tools/a.py", "A = 3\n")
        self.assertEqual(run(["--check"], self.root).returncode, 1)
        self.assertEqual(run([], self.root).returncode, 0)
        self.assertEqual(run(["--check"], self.root).returncode, 0)


class DeletedFile(Fixture):
    def test_a_deleted_file_fails_then_regenerates(self):
        os.remove(os.path.join(self.prod, "tools", "a.py"))
        self.assertEqual(run(["--check"], self.root).returncode, 1)
        self.assertEqual(run([], self.root).returncode, 0)
        self.assertNotIn("tools/a.py", self.manifest())
        self.assertEqual(run(["--check"], self.root).returncode, 0)


class MissingVerifier(Fixture):
    def test_a_product_without_its_verifier_is_red_and_says_no_data(self):
        os.remove(os.path.join(self.prod, "scripts", "verify-install.sh"))
        r = run(["--check"], self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("NO-DATA products/brothermode", r.stdout)


class FailingGenerator(Fixture):
    def test_a_generator_that_exits_nonzero_is_red(self):
        with open(os.path.join(self.prod, "scripts", "checksums.sh"), "w", encoding="utf-8") as fh:
            fh.write("exit 3\n")
        r = run([], self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL products/brothermode", r.stdout)


class APlantedProductIsNeverRun(Fixture):
    """Security review 2026-10-23: a build could add products/<new>/scripts/checksums.sh, which no grader rule protects;
    the gate runs only the shipped, protected products, so the planted script never runs, either way."""

    def test_a_planted_products_scripts_never_run(self):
        evil = os.path.join(self.root, "products", "evil", "scripts")
        os.makedirs(evil)
        marker = os.path.join(self.root, "PLANTED-RAN")
        for name in ("checksums.sh", "verify-install.sh"):
            with open(os.path.join(evil, name), "w", encoding="utf-8") as fh:
                fh.write("touch %s\nexit 0\n" % marker)
        self.assertEqual(run(["--check"], self.root).returncode, 0)
        self.assertEqual(run([], self.root).returncode, 0)
        self.assertFalse(os.path.exists(marker), "a planted product's script ran inside the gate")


class NoProducts(unittest.TestCase):
    def test_a_tree_with_no_product_is_red_both_ways(self):
        root = tempfile.mkdtemp(prefix="product-manifests-empty-")
        self.addCleanup(shutil.rmtree, root, True)
        for args in (["--check"], []):
            r = run(args, root)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn("NO-DATA", r.stdout)


class TheLandingRunsIt(unittest.TestCase):
    """The gate exists only if land_batch's GATES names it, with exactly the two manifests as its output set."""

    def test_gates_carry_the_product_manifest_entry(self):
        sys.path.insert(0, os.path.join(HERE, "loop"))
        try:
            import land_batch
        finally:
            sys.path.pop(0)
        entry = [g for g in land_batch.GATES if g[0] == "product_manifests"]
        self.assertEqual(len(entry), 1, [g[0] for g in land_batch.GATES])
        name, check, regen, outputs = entry[0]
        self.assertEqual(check, ["scripts/product_manifests.py", "--check"])
        self.assertEqual(regen, ["scripts/product_manifests.py"])
        self.assertEqual(sorted(outputs), ["products/brothermode/CHECKSUMS.sha256", "products/brothersbe/CHECKSUMS.sha256"])


class ABuildCannotRewriteTheGate(unittest.TestCase):
    """The gate runs scripts the build's tree holds, so the grader must refuse a build that writes any of them."""

    def test_the_gate_and_both_products_scripts_are_refused_and_a_product_file_is_not(self):
        sys.path.insert(0, os.path.join(HERE, "loop"))
        try:
            import grade_build
        finally:
            sys.path.pop(0)
        for p in ("scripts/product_manifests.py", "products/brothermode/scripts/checksums.sh",
                  "products/brothermode/scripts/verify-install.sh", "products/brothersbe/scripts/checksums.sh",
                  "products/brothersbe/scripts/verify-install.sh"):
            self.assertIsNone(grade_build.dest(REPO, p)[0], p)
        self.assertIsNotNone(grade_build.dest(REPO, "products/brothermode/tools/bm_vault.py")[0])


class AtTheLandingEntryPoint(unittest.TestCase):
    """land_batch.main as the loop runs it (test_land_batch_landings.Fixture), with the REAL gate script and the REAL
    product scripts: a build adds one product file, and the landed commit's manifest names it."""

    def test_a_new_product_file_lands_with_its_manifest_regenerated(self):
        sys.path.insert(0, os.path.join(HERE, "loop"))
        try:
            from test_land_batch_landings import Fixture
            import grade_build as GB
        finally:
            sys.path.pop(0)
        # the landing grades under the grader's sandbox; inside another sandbox (the push gate's hermetic run) the kernel
        # refuses it, the same NO-DATA scripts/loop/test_gate_order_child.py reports
        ready = GB.sandbox_ready()
        if ready:
            self.skipTest("NO-DATA: the grader's sandbox cannot run here (%s)" % ready)
        prod = "products/brothermode/"
        with open(SCRIPT, encoding="utf-8") as fh:
            files = {"scripts/product_manifests.py": fh.read(), prod + "tools/a.py": "A = 1\n"}
        for name in ("checksums.sh", "verify-install.sh"):
            with open(os.path.join(REAL, name), encoding="utf-8") as fh:
                files[prod + "scripts/" + name] = fh.read()
        # the base manifest, written by the product's own generator over exactly these bytes in a scratch repository
        scratch = tempfile.mkdtemp(prefix="product-manifests-base-")
        self.addCleanup(shutil.rmtree, scratch, True)
        for rel, text in files.items():
            if rel.startswith(prod):
                os.makedirs(os.path.dirname(os.path.join(scratch, rel)), exist_ok=True)
                with open(os.path.join(scratch, rel), "w", encoding="utf-8") as fh:
                    fh.write(text)
        git(["init", "-q"], scratch)
        git(["add", "-A"], scratch)
        subprocess.run(["sh", "scripts/checksums.sh", "CHECKSUMS.sha256"], cwd=os.path.join(scratch, prod), check=True, capture_output=True)
        with open(os.path.join(scratch, prod, "CHECKSUMS.sha256"), encoding="utf-8") as fh:
            files[prod + "CHECKSUMS.sha256"] = fh.read()
        root = tempfile.mkdtemp(prefix="product-manifests-landing-")
        self.addCleanup(shutil.rmtree, root, True)
        fx = Fixture(root, tree_files=files)
        fx.edits({"edits": [{"path": prod + "tools/new_tool.py", "new_file_content": "B = 2\n"}], "tests": []})
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-3000:])
        landed = fx.git("show", "main:" + prod + "CHECKSUMS.sha256", cwd=fx.hub)
        self.assertIn("tools/new_tool.py", landed, "the landed manifest names the build's new file")
        self.assertIn("tools/a.py", landed)


if __name__ == "__main__":
    unittest.main()
