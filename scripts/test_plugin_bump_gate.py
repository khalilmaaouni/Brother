"""plugin_bump_gate.py driven both ways on REAL git repositories in a temp
directory: a local bare-less "public remote" carrying a release tag, and a
candidate tree built the way the exporter builds one (git init, one commit).
No network, no home folder, nothing from the tree this runs in, so it reads
the same on the public runner. Each refusal is shown red on the defect and
green without it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugin_bump_gate as G  # noqa: E402

ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(cwd, *args):
    proc = subprocess.run(("git", "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false")
                          + args, cwd=cwd, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, (args, proc.stderr)
    return proc.stdout


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def marketplace(**versions):
    """One entry per keyword: name -> version. The umbrella lives in bundle/,
    every other plugin in products/<name>, the shapes the real file uses."""
    plugins = []
    for name, version in versions.items():
        path = "bundle" if name == G.UMBRELLA else "products/%s" % name
        plugins.append({"name": name, "version": version,
                        "source": {"source": "git-subdir", "path": path,
                                   "ref": "v0.0.0"}})
    return json.dumps({"name": "m", "plugins": plugins})


def tree(root, versions, files):
    write(root, ".claude-plugin/marketplace.json", marketplace(**versions))
    for rel, text in files.items():
        write(root, rel, text)


RELEASED = {"products/alpha/tool.py": "print(1)\n",
            "products/beta/tool.py": "print(2)\n",
            "bundle/run.py": "print(3)\n"}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-plugin-bump-gate-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.remote = os.path.join(self.tmp, "remote")
        os.makedirs(self.remote)
        git(self.remote, "init", "-q")
        tree(self.remote, {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20"},
             RELEASED)
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-q", "-m", "release")
        git(self.remote, "tag", "-a", "v1.0.20", "-m", "v1.0.20")

    def builder(self, versions, files, commit=True):
        def build(dest):
            git(dest, "init", "-q")
            tree(dest, versions, files)
            if commit:
                git(dest, "add", "-A")
                git(dest, "commit", "-q", "-m", "candidate")
        return build

    def check(self, versions, files, version="1.0.21", **kw):
        kw.setdefault("build", self.builder(versions, files))
        return G.check_plugin_bumps(self.tmp, version, self.remote, **kw)


class AChangedPluginMustCarryANewVersion(Case):
    def test_a_file_changed_with_no_bump_refuses_and_names_the_plugin(self):
        files = dict(RELEASED, **{"products/alpha/tool.py": "print('changed')\n"})
        verdict, name, detail = self.check(
            {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20"}, files)
        self.assertEqual((verdict, name), (G.REFUSED, "plugin-bumps"), detail)
        self.assertIn("alpha", detail)
        self.assertIn("3.4.5", detail)
        self.assertIn("v1.0.20", detail)
        self.assertNotIn("beta", detail)

    def test_the_same_change_with_a_bump_is_ok(self):
        files = dict(RELEASED, **{"products/alpha/tool.py": "print('changed')\n"})
        verdict, _, detail = self.check(
            {"alpha": "3.4.6", "beta": "3.7.4", G.UMBRELLA: "1.0.20"}, files)
        self.assertEqual(verdict, G.OK, detail)

    def test_an_added_file_and_a_deleted_file_each_count_as_a_change(self):
        added = dict(RELEASED, **{"products/alpha/new.py": "x\n"})
        deleted = {k: v for k, v in RELEASED.items() if k != "products/beta/tool.py"}
        deleted["products/beta/other.py"] = "y\n"
        same = {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20"}
        self.assertEqual(self.check(same, added)[0], G.REFUSED)
        verdict, _, detail = self.check(same, deleted)
        self.assertEqual(verdict, G.REFUSED)
        self.assertIn("beta", detail)

    def test_nothing_changed_and_nothing_bumped_is_ok(self):
        verdict, _, detail = self.check(
            {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20"}, RELEASED)
        self.assertEqual(verdict, G.OK, detail)

    def test_the_umbrella_is_judged_at_the_version_being_cut(self):
        """Before the bump the umbrella still reads the old number; the cut
        itself moves it to the version being cut, so that is what ships."""
        files = dict(RELEASED, **{"bundle/run.py": "print('changed')\n"})
        same = {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20"}
        self.assertEqual(self.check(same, files, version="1.0.21")[0], G.OK)

    def test_a_plugin_the_previous_release_did_not_list_is_ok(self):
        files = dict(RELEASED, **{"products/gamma/tool.py": "z\n"})
        verdict, _, detail = self.check(
            {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20", "gamma": "0.1.0"},
            files)
        self.assertEqual(verdict, G.OK, detail)
        self.assertIn("gamma", detail)


class UnknownIsNeverAPass(Case):
    SAME = {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20"}

    def test_an_unreachable_remote_is_no_data(self):
        verdict, _, detail = G.check_plugin_bumps(
            self.tmp, "1.0.21", os.path.join(self.tmp, "no-such-remote"),
            build=self.builder(self.SAME, RELEASED))
        self.assertEqual(verdict, G.NODATA, detail)

    def test_no_release_tag_below_the_version_is_no_data(self):
        verdict, _, detail = self.check(self.SAME, RELEASED, version="1.0.20")
        self.assertEqual(verdict, G.NODATA, detail)
        self.assertIn("no release tag below v1.0.20", detail)

    def test_a_version_that_is_not_a_release_number_is_no_data(self):
        self.assertEqual(self.check(self.SAME, RELEASED, version="next")[0], G.NODATA)

    def test_the_newest_lower_tag_is_the_previous_release(self):
        """v1.0.9 sorts after v1.0.20 as text, and a product tag (v3.4.2) is
        above every umbrella release: neither may be picked."""
        for tag in ("v1.0.9", "v3.4.2", "v1.0.20-rc1"):
            git(self.remote, "tag", "-a", tag, "-m", tag)
        tag, why = G.previous_release_tag(self.tmp, "1.0.21", self.remote)
        self.assertEqual(tag, "v1.0.20", why)

    def test_a_tree_that_cannot_be_built_is_no_data(self):
        def boom(dest):
            raise RuntimeError("allowlist unreadable")
        self.assertEqual(self.check(self.SAME, RELEASED, build=boom)[0], G.NODATA)

    def test_a_candidate_with_no_commit_is_no_data(self):
        verdict, _, detail = self.check(
            self.SAME, RELEASED, build=self.builder(self.SAME, RELEASED, commit=False))
        self.assertEqual(verdict, G.NODATA, detail)

    def test_an_unreadable_candidate_marketplace_is_no_data(self):
        def build(dest):
            self.builder(self.SAME, RELEASED)(dest)
            write(dest, ".claude-plugin/marketplace.json", "{not json")
        self.assertEqual(self.check(self.SAME, RELEASED, build=build)[0], G.NODATA)

    def test_an_empty_plugin_list_is_no_data(self):
        self.assertEqual(self.check({}, RELEASED)[0], G.NODATA)

    def test_a_previous_release_with_no_marketplace_is_no_data(self):
        git(self.remote, "rm", "-q", ".claude-plugin/marketplace.json")
        git(self.remote, "commit", "-q", "-m", "gone")
        git(self.remote, "tag", "-a", "v1.0.21", "-m", "v1.0.21")
        self.assertEqual(self.check(self.SAME, RELEASED, version="1.0.22")[0], G.NODATA)

    def test_a_plugin_with_no_subdirectory_is_no_data_and_is_named(self):
        def build(dest):
            self.builder(self.SAME, RELEASED)(dest)
            doc = json.loads(marketplace(**self.SAME))
            doc["plugins"][0]["source"] = {"source": "url", "url": "https://x"}
            write(dest, ".claude-plugin/marketplace.json", json.dumps(doc))
            git(dest, "add", "-A")
            git(dest, "commit", "-q", "-m", "shape")
        verdict, _, detail = self.check(self.SAME, RELEASED, build=build)
        self.assertEqual(verdict, G.NODATA, detail)
        self.assertIn("alpha", detail)

    def test_a_path_that_leaves_the_tree_is_no_data(self):
        for bad in ("../x", "/abs", "", ".", "a/../../b"):
            self.assertIsNone(G.plugin_path({"source": {"path": bad}}), bad)
        self.assertEqual(G.plugin_path({"source": "./products/alpha/"}), "products/alpha")

    def test_a_refusal_outranks_a_no_data_in_the_same_run(self):
        def build(dest):
            files = dict(RELEASED, **{"products/beta/tool.py": "changed\n"})
            self.builder(self.SAME, files)(dest)
            doc = json.loads(marketplace(**self.SAME))
            doc["plugins"][0]["source"] = "https://elsewhere"
            write(dest, ".claude-plugin/marketplace.json", json.dumps(doc))
            git(dest, "add", "-A")
            git(dest, "commit", "-q", "-m", "both")
        verdict, _, detail = self.check(self.SAME, RELEASED, build=build)
        self.assertEqual(verdict, G.REFUSED, detail)
        self.assertIn("beta", detail)
        self.assertIn("alpha", detail)


#: What the gate's own process imports beside itself, copied byte for byte into
#: the fixture hub, so the subprocess below runs the real files.
SHIPPED = ("plugin_bump_gate.py", "cut_preflight.py", "tmp_sandbox.py",
           "git_location_guard.py", "retire_catalogs.py", "version_source.py",
           # What retire_catalogs.py and version_source.py themselves import since the one plugin unit: the
           # catalog end state reader and the host manifest discovery. A fixture without them cannot load the
           # retirement rule at all, and every case would read the same NO-DATA for the wrong reason.
           "donecheck_u8.py", "bundle_runtime.py")

#: The one collaborator that is a fixture. The real exporter needs the whole
#: hub (its allowlist, its lints, every product); this one copies the fixture
#: hub's candidate/ directory and commits it, which is all the gate reads of an
#: export tree: a commit, a marketplace file and the plugin subdirectories.
#: cut_preflight.export_tree_builder, the real one, calls it and checks the
#: tree marker.
FIXTURE_EXPORTER = '''"""The fixture exporter of scripts/test_plugin_bump_gate.py."""
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ALLOWLIST = os.path.join(ROOT, "allowlist.txt")
DEFAULT_REMOTE = "no default here: every run names --remote"


def load_allowlist(path):
    return path


def build_orphan_commit(dest, allowlist, root=ROOT):
    src = os.path.join(root, "candidate")
    for name in os.listdir(src):
        shutil.copytree(os.path.join(src, name), os.path.join(dest, name))
    marker = os.path.join(dest, "scripts", "required_fast.sh")
    os.makedirs(os.path.dirname(marker))
    open(marker, "w").close()
    for args in (("init", "-q"), ("add", "-A"),
                 ("-c", "user.name=t", "-c", "user.email=t@t",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "candidate")):
        subprocess.run(("git",) + args, cwd=dest, check=True, capture_output=True)
'''


def rule(kept, body):
    """The text of a stand in retirement rule: its kept name and what its
    applies() does."""
    return "KEEP = %r\n\n\ndef applies(version):\n    %s\n" % (kept, body)


class TheGateAtItsEntryPoint(Case):
    """The gate run the way cut.py runs it: its own file as a subprocess from a
    hub root, answering with an exit code and one printed line. Every case
    below changes ONE thing against a run that reads OK, so only the guard it
    names can be what answered.

    THE DEFECT (measured 2026-10-05 on the 1.1.0 candidate): the preflight asks
    this gate before retire_catalogs.py runs, so the three entries the cut
    itself removes from the catalog were judged as if they shipped, and
    refused for an unchanged version no shipped catalog will carry."""

    BEFORE = {"alpha": "3.4.5", "beta": "3.7.4", G.UMBRELLA: "1.0.20"}
    CHANGED = dict(RELEASED, **{"products/alpha/tool.py": "print('changed')\n"})

    def hub(self, versions, files, rule_text=True):
        """A fixture hub root. rule_text True copies the real retirement rule,
        None leaves the file out, and a string is written in its place."""
        root = tempfile.mkdtemp(prefix="hub-", dir=self.tmp)
        scripts = os.path.join(root, "scripts")
        os.makedirs(scripts)
        for name in SHIPPED:
            if name == "retire_catalogs.py" and rule_text is not True:
                continue
            shutil.copy(os.path.join(HERE, name), scripts)
        if isinstance(rule_text, str):
            write(root, "scripts/retire_catalogs.py", rule_text)
        write(root, "scripts/export_public.py", FIXTURE_EXPORTER)
        tree(os.path.join(root, "candidate"), versions, files)
        return root

    def gate(self, root, version):
        proc = subprocess.run(
            [sys.executable, "-B", os.path.join(root, "scripts", "plugin_bump_gate.py"),
             "--version", version, "--remote", self.remote],
            cwd=root, capture_output=True, text=True, env=ENV, timeout=300)
        return proc.returncode, proc.stdout, proc.stderr

    def test_an_entry_the_cut_retires_is_not_judged_and_the_gate_says_so(self):
        code, out, err = self.gate(self.hub(self.BEFORE, self.CHANGED), "1.1.0")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(out.startswith("OK "), out)
        # A skipped entry is never silent: each one is named, with the reason.
        for name in ("alpha", "beta"):
            self.assertIn("%s: not judged" % name, out)
        self.assertIn("retire_catalogs", out)
        # The kept entry was judged, at the version being cut.
        self.assertIn("%s 1.1.0, " % G.UMBRELLA, out)

    def test_the_same_tree_below_the_retirement_rule_still_refuses(self):
        """The gate's original property: an entry no cut retires, changed and
        not bumped, refuses. Nothing is retired below retire_catalogs.RETIRE_AT."""
        code, out, err = self.gate(self.hub(self.BEFORE, self.CHANGED), "1.0.21")
        self.assertEqual(code, 1, out + err)
        self.assertTrue(out.startswith("REFUSED"), out)
        self.assertIn("alpha: 1 file(s)", out)
        self.assertIn("3.4.5", out)
        self.assertNotIn("not judged", out)

    def test_the_kept_entry_is_still_judged_when_the_cut_retires_the_rest(self):
        # A previous release that already listed the umbrella at the number
        # now being cut: the one way the umbrella's own version can be stale.
        listed = dict(self.BEFORE, **{G.UMBRELLA: "1.1.0"})
        tree(self.remote, listed, RELEASED)
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-q", "-m", "release")
        git(self.remote, "tag", "-a", "v1.0.21", "-m", "v1.0.21")
        files = dict(RELEASED, **{"bundle/run.py": "print('changed')\n"})
        code, out, err = self.gate(self.hub(listed, files), "1.1.0")
        self.assertEqual(code, 1, out + err)
        self.assertIn("%s: 1 file(s) under bundle" % G.UMBRELLA, out)
        self.assertIn("still 1.1.0", out)
        # The refusal names what it did not judge as well.
        self.assertIn("alpha: not judged", out)

    def test_a_no_data_line_names_the_entries_it_did_not_judge_too(self):
        # The kept entry cannot be diffed (another repository), so the verdict
        # is NO-DATA; the retired entries are still named beside it.
        root = self.hub(self.BEFORE, self.CHANGED)
        doc = json.loads(marketplace(**self.BEFORE))
        for entry in doc["plugins"]:
            if entry["name"] == G.UMBRELLA:
                entry["source"] = {"source": "url", "url": "https://elsewhere"}
        write(root, "candidate/.claude-plugin/marketplace.json", json.dumps(doc))
        code, out, err = self.gate(root, "1.1.0")
        self.assertEqual(code, 2, out + err)
        self.assertIn("%s: no subdirectory" % G.UMBRELLA, out)
        self.assertIn("alpha: not judged", out)

    def test_a_catalog_without_the_kept_entry_judges_nothing_and_is_no_data(self):
        """Every listed entry would be skipped as retired, so nothing would be
        judged: a check that judged nothing is NO-DATA, never OK."""
        only_products = {"alpha": "3.4.5", "beta": "3.7.4"}
        code, out, err = self.gate(self.hub(only_products, self.CHANGED), "1.1.0")
        self.assertEqual(code, 2, out + err)
        self.assertTrue(out.startswith("NO-DATA"), out)
        self.assertIn(G.UMBRELLA, out)

    #: Every way the retirement rule can fail to answer. Unknown never means
    #: retired, and it never means "nothing retired" either: both are guesses.
    UNKNOWN_RULES = {
        "the file is missing": None,
        "the file is empty": "",
        "the file does not parse": "def applies(:\n",
        "there is no applies": "KEEP = %r\n" % G.UMBRELLA,
        "there is no kept name": "def applies(version):\n    return True\n",
        "the kept name is empty": rule("", "return True"),
        "the kept name is another entry": rule("alpha", "return True"),
        "applies raises": rule(G.UMBRELLA, "raise RuntimeError('rule store offline')"),
        # Leaving through an exit is still no answer: with exit code 0 it
        # ended the gate itself as a pass with no verdict line (review round 1).
        "applies exits 0": rule(G.UMBRELLA, "raise SystemExit(0)"),
        "applies exits 3": rule(G.UMBRELLA, "raise SystemExit(3)"),
        "the file exits 0 when it is imported": "raise SystemExit(0)\n",
        # Review round 2: what the gate reads OFF the rule's objects can exit
        # too. A kept name whose comparison exits, and an answer whose repr
        # exits, each ended the gate at exit 0 with no verdict line.
        "the kept name exits when it is compared": (
            "class _Kept(object):\n"
            "    def __eq__(self, other):\n        raise SystemExit(0)\n\n"
            "    def __ne__(self, other):\n        raise SystemExit(0)\n\n\n"
            "KEEP = _Kept()\n\n\ndef applies(version):\n    return True\n"),
        "the answer exits when it is shown": (
            "class _Answer(object):\n"
            "    def __repr__(self):\n        raise SystemExit(0)\n\n\n"
            "KEEP = %r\n\n\ndef applies(version):\n    return _Answer()\n" % G.UMBRELLA),
        "applies answers None": rule(G.UMBRELLA, "return None"),
        "applies answers a word": rule(G.UMBRELLA, "return 'yes'"),
        "applies answers a number": rule(G.UMBRELLA, "return 1"),
    }

    def test_a_rule_that_cannot_be_read_blocks_when_nothing_else_is_wrong(self):
        # The control: the same unchanged tree under the real rule reads OK,
        # so each NO-DATA below is the rule's doing and nothing else's.
        code, out, err = self.gate(self.hub(self.BEFORE, RELEASED), "1.1.0")
        self.assertEqual(code, 0, out + err)
        for label, text in sorted(self.UNKNOWN_RULES.items(), key=lambda kv: kv[0]):
            for version in ("1.1.0", "1.0.21"):
                with self.subTest(rule=label, version=version):
                    code, out, err = self.gate(
                        self.hub(self.BEFORE, RELEASED, rule_text=text), version)
                    self.assertEqual(code, 2, out + err)
                    self.assertTrue(out.startswith("NO-DATA"), out)
                    self.assertIn("retire_catalogs", out)


class TheCommandLine(Case):
    def test_exit_codes_follow_the_verdict(self):
        self.assertEqual(G.exit_code(G.OK), 0)
        self.assertEqual(G.exit_code(G.REFUSED), 1)
        self.assertEqual(G.exit_code(G.NODATA), 2)
        self.assertEqual(G.exit_code("anything else"), 2)


if __name__ == "__main__":
    unittest.main()
