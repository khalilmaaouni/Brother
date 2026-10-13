"""JWP.a: the Jev registry and seams config ship inside bundle/runtime, and
the resolver finds them in a checkout and in an installed runtime copy.

HOME and BROTHER_JEV_STATE_DIR point at fresh temp folders before any product
import, and every subprocess inherits them, so nothing reads the real
~/.brother/jev. No test asks a model anything: the runtime copy only imports
jev_seam and loads the two files.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="jev-public-install-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")
for _name in ("BROTHER_DECISION_BRIDGE", "BROTHER_TRANSPORTS", "BROTHER_JEV_SEAMS_OFF"):
    os.environ.pop(_name, None)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME = os.path.join(ROOT, "bundle", "runtime")
sys.path.insert(0, HERE)
import bundle_runtime  # noqa: E402
import jev_seam  # noqa: E402

NAMES = ("jev-registry.json", "jev-seams.json")
PROBE = (
    "import json, jev_seam\n"
    "r = jev_seam.load_registry()\n"
    "s = jev_seam.load_seams_config()\n"
    "print(json.dumps({'count': len(r), 'modes': s.get('modes'),"
    " 'registry': jev_seam.DEFAULT_REGISTRY_PATH,"
    " 'seams': jev_seam.DEFAULT_SEAMS_CONFIG_PATH}))\n"
)


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=120, env=dict(os.environ))


def _repo_json(name):
    with open(os.path.join(ROOT, "data", name)) as fh:
        return json.load(fh)


class JevPublicInstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="jev-public-install-")
        cls.runtime = os.path.join(cls.tmp, "bundle", "runtime")
        shutil.copytree(RUNTIME, cls.runtime,
                        ignore=shutil.ignore_patterns("__pycache__"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_shipped_data_has_no_machine_paths(self):
        for name in NAMES:
            with open(os.path.join(ROOT, "data", name)) as fh:
                text = fh.read()
            for bad in ("~/.claude", "/Users/", "/home/"):
                self.assertNotIn(bad, text, "%s holds %s" % (name, bad))

    def test_the_checkout_layout_is_unchanged(self):
        self.assertEqual(jev_seam.DEFAULT_REGISTRY_PATH,
                         os.path.join(ROOT, "data", "jev-registry.json"))
        self.assertEqual(jev_seam.DEFAULT_SEAMS_CONFIG_PATH,
                         os.path.join(ROOT, "data", "jev-seams.json"))
        # A checkout with a stray data folder beside the module: the
        # checkout's own data/ wins.
        tree = tempfile.mkdtemp(prefix="jev-layout-", dir=self.tmp)
        scripts = os.path.join(tree, "scripts")
        os.makedirs(os.path.join(tree, "data"))
        os.makedirs(os.path.join(scripts, "data"))
        for name in NAMES:
            with open(os.path.join(tree, "data", name), "w") as fh:
                fh.write("{}")
            with open(os.path.join(scripts, "data", name), "w") as fh:
                fh.write('{"stray": true}')
        self.assertEqual(jev_seam._data_file("jev-registry.json", tree, scripts),
                         os.path.join(tree, "data", "jev-registry.json"))
        self.assertEqual(jev_seam._data_file("jev-seams.json", tree, scripts),
                         os.path.join(tree, "data", "jev-seams.json"))
        # Neither exists: the checkout path, today's value.
        empty = tempfile.mkdtemp(prefix="jev-empty-", dir=self.tmp)
        self.assertEqual(jev_seam._data_file("jev-registry.json", empty, empty + "/x"),
                         os.path.join(empty, "data", "jev-registry.json"))

    def test_runtime_copy_finds_its_own_data(self):
        proc = _run([sys.executable, "-B", "-c", PROBE], self.runtime)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(got["count"], len(_repo_json("jev-registry.json")))
        self.assertEqual(got["modes"], _repo_json("jev-seams.json")["modes"])
        real = os.path.realpath
        self.assertEqual(real(got["registry"]),
                         real(os.path.join(self.runtime, "data", "jev-registry.json")))
        self.assertEqual(real(got["seams"]),
                         real(os.path.join(self.runtime, "data", "jev-seams.json")))

    def test_a_runtime_copy_without_data_still_imports(self):
        bare = os.path.join(self.tmp, "bare", "bundle", "runtime")
        os.makedirs(bare)
        for name in os.listdir(self.runtime):
            if name.endswith(".py"):
                shutil.copy2(os.path.join(self.runtime, name), bare)
        probe = ("import jev_seam\n"
                 "print(repr(jev_seam.load_seams_config()))\n")
        proc = _run([sys.executable, "-B", "-c", probe], bare)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(proc.stdout.strip(), "{}")

    def test_the_verifier_passes_with_the_data_files(self):
        proc = _run([sys.executable, "-B", "verify_runtime.py"], self.runtime)
        self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr)[-2000:])
        self.assertIn("PASS", proc.stdout)
        with open(os.path.join(self.runtime, "RUNTIME-MANIFEST.json")) as fh:
            paths = {f["path"] for f in json.load(fh)["files"]}
        for name in NAMES:
            self.assertIn("data/" + name, paths)

    def test_extra_files_take_one_pair_or_many(self):
        tree = tempfile.mkdtemp(prefix="jev-extra-", dir=self.tmp)
        scripts = os.path.join(tree, "scripts")
        os.makedirs(scripts)
        os.makedirs(os.path.join(tree, "d"))
        for name in ("a.json", "b.json", "c.json"):
            with open(os.path.join(tree, "d", name), "w") as fh:
                fh.write("{}")
        saved = bundle_runtime.DATA_FILES
        bundle_runtime.DATA_FILES = {
            "one.py": ("d/a.json", "a.json"),
            "two.py": (("d/b.json", "data/b.json"), ("d/c.json", "data/c.json")),
        }
        try:
            got = bundle_runtime.compute_extra_files(["one.py", "two.py"], scripts)
            bundle_runtime.DATA_FILES = {"bad.py": ("d/a.json",)}
            with self.assertRaises(ValueError) as cm:
                bundle_runtime.compute_extra_files(["bad.py"], scripts)
            self.assertIn("bad.py", str(cm.exception))
        finally:
            bundle_runtime.DATA_FILES = saved
        self.assertEqual([d for d, _ in got], ["a.json", "data/b.json", "data/c.json"])

    def test_the_data_reader_is_in_the_closure(self):
        self.assertIn("jev_seam.py", bundle_runtime.compute_closure())
        dests = [d for d, _ in bundle_runtime.compute_extra_files(
            bundle_runtime.compute_closure())]
        for name in NAMES:
            self.assertIn("data/" + name, dests)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)
